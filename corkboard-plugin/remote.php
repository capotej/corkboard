<?php
use dokuwiki\Extension\RemotePlugin;
use dokuwiki\Search\Indexer;
use dokuwiki\Search\MetadataSearch;

/**
 * DokuWiki Plugin corkboard (Remote Component)
 *
 * RPC methods for the Corkboard agent. Three families:
 *
 *   1. Wiki "gardening" queries — wanted pages, orphaned pages, unreferenced
 *      media, and per-page broken outgoing links (linkhealth) — each returned
 *      in a single call, computed against the search index instead of via N
 *      authenticated round-trips from the client.
 *   2. A compare-and-swap writer (cas) so the client's surgical edits can save
 *      only when the page revision they last read still matches — a concurrent
 *      edit can no longer be silently clobbered.
 *   3. A move/rename (move) that delegates to the move plugin's plan helper —
 *      relocating the attic (so history is preserved) and rewriting every
 *      backlink in the plugin's own pass, run synchronously to completion.
 *
 * Public methods are auto-exported by RemotePlugin as plugin.corkboard.<method>.
 * Access is gated by the usual remote=1 / remoteuser=@api,@admin (the agent),
 * so no per-method guard is needed for reads. Writers (cas) additionally
 * check AUTH_EDIT per page, since saveWikiText() itself does not. Results are
 * ACL-filtered to what the caller may read, matching core.listPages /
 * core.getPageBackLinks semantics.
 */
class remote_plugin_corkboard extends RemotePlugin
{
    /** @return string[] page ids the caller may read */
    protected function readablePages(): array
    {
        $pages = [];
        foreach ((new Indexer())->getAllPages() as $id) {
            if (auth_quickaclcheck($id) >= AUTH_READ) {
                $pages[] = $id;
            }
        }
        return $pages;
    }

    /**
     * Internal links pointing at pages that do not exist yet.
     *
     * @return array<string, string[]>  target id => list of source pages linking to it
     */
    public function wanted()
    {
        $pages    = $this->readablePages();
        $existing = array_flip($pages);
        $wanted   = [];

        foreach ($pages as $src) {
            // Outgoing internal links from cached metadata (no re-parse); same
            // data the search index uses, so it matches core.getPageLinks output.
            // relation.references is [target_id => exists] — iterate the KEYS.
            foreach ((p_get_metadata($src, 'relation references') ?: []) as $tgt => $_) {
                $tgt = cleanID($tgt);
                if ($tgt === '' || isset($existing[$tgt])) {
                    continue;
                }
                $wanted[$tgt][] = $src;
            }
        }

        foreach ($wanted as &$srcs) {
            $srcs = array_values(array_unique($srcs));
            sort($srcs);
        }
        unset($srcs);

        ksort($wanted);
        // Cast to object so an empty result serializes as {} (object), matching the
        // non-empty {target_id: [source_pages]} shape — a plain empty array would
        // JSON-encode as [] (list), making the return type inconsistent.
        return (object) $wanted;
    }

    /**
     * Broken outgoing internal links from a single page — targets this page
     * links to that do not (yet) exist. The per-page analog of wanted(); cheap
     * (one metadata read + page_exists checks), so the client calls it right
     * after a save to surface links an edit just introduced.
     *
     * @param string $page
     * @return string[]
     */
    public function linkhealth($page)
    {
        $page = cleanID($page);
        if (auth_quickaclcheck($page) < AUTH_READ) {
            return [];
        }
        $out = [];
        foreach ((p_get_metadata($page, 'relation references') ?: []) as $tgt => $_) {
            $tgt = cleanID($tgt);
            if ($tgt !== '' && !page_exists($tgt)) {
                $out[] = $tgt;
            }
        }
        $out = array_values(array_unique($out));
        sort($out);
        return $out;
    }

    /**
     * Compare-and-swap save: write $text to $page only if its current revision
     * equals $baseRev, so a concurrent edit cannot be silently clobbered.
     *
     * The revision compared is filemtime(wikiFN($page)) — the same value
     * core.getPageInfo returns as 'version', so the client passes that straight
     * back. A brand-new page (no file yet) compares as 0; passing baseRev '0'
     * (what getPageInfo returns for a missing page) therefore allows the first
     * save. Values are compared as ints so '' / '0' / 0 all collapse to 0.
     *
     * Returns an outcome object (not a bare bool) so the client can tell a
     * genuine conflict apart from a write failure. saveWikiText() does not check
     * ACL itself, so AUTH_EDIT is enforced here.
     *
     * @param string $page
     * @param string $baseRev  revision the caller last saw (from getPageInfo)
     * @param string $text
     * @param string $summary
     * @param bool   $minor
     * @return array{saved: bool, conflict: bool, current_rev: ?string}
     */
    public function cas($page, $baseRev, $text, $summary = '', $minor = false)
    {
        $page = cleanID($page);
        $text = cleanText($text);
        // Mirror core.savePage: creating a page needs AUTH_CREATE, editing an
        // existing one needs AUTH_EDIT. saveWikiText() checks neither itself.
        $need = page_exists($page) ? AUTH_EDIT : AUTH_CREATE;
        if (auth_quickaclcheck($page) < $need) {
            return ['saved' => false, 'conflict' => false, 'current_rev' => null];
        }

        $current = (string) (int) @filemtime(wikiFN($page));

        if ((int) $baseRev !== (int) $current) {
            return ['saved' => false, 'conflict' => true, 'current_rev' => $current ?: null];
        }

        saveWikiText($page, $text, $summary, $minor);

        $newrev = (string) (int) @filemtime(wikiFN($page));
        return ['saved' => true, 'conflict' => false, 'current_rev' => $newrev ?: null];
    }

    /**
     * Move/rename a page, a media file, or a whole namespace — delegating to
     * the move plugin's programmatic API (helper_plugin_move_plan) so the attic
     * (revision history) is relocated, not stranded, and every backlink is
     * rewritten in the plugin's own pass. Synchronous: commit() + a capped
     * nextStep() loop run to completion in one call.
     *
     * Auth mirrors the move plugin's own checkPage/checkMedia: AUTH_EDIT src +
     * AUTH_CREATE dst for pages, AUTH_DELETE src + AUTH_UPLOAD dst for media —
     * both satisfied by the agent's own ACL (@api 16; see acl.auth.php), so no
     * elevation is needed. Refuses with a `reason` before touching plan state
     * when the caller lacks perms, src is missing, dst exists, the move plugin
     * is absent, or another move is already committed (plan state is global and
     * non-reentrant). Requires the move plugin (2024-05-07+) at lib/plugins/move.
     *
     * @param string $src
     * @param string $dst
     * @param array  $opts  kind:'page'|'media' (default page); ns:bool (move the
     *                      whole namespace); rewrite:bool (default true — rewrite
     *                      backlinks); autoskip:bool (default false — abort on
     *                      first failure rather than skip)
     * @return array{moved: bool, src: string, dst: string, kind: string, steps?: int, reason?: string, error?: ?string}
     */
    public function move($src, $dst, $opts = [])
    {
        $src  = cleanID($src);
        $dst  = cleanID($dst);
        $kind = ($opts['kind'] ?? 'page') === 'media' ? 'media' : 'page';
        $ns   = !empty($opts['ns']);
        $rewrite  = isset($opts['rewrite']) ? (bool) $opts['rewrite'] : true;
        $autoskip = !empty($opts['autoskip']);

        $base = ['src' => $src, 'dst' => $dst, 'kind' => $kind];

        // Auth gate — mirrors the move plugin's checkPage/checkMedia.
        if ($kind === 'media') {
            $need_src = AUTH_DELETE;
            $need_dst = AUTH_UPLOAD;
        } else {
            $need_src = AUTH_EDIT;
            $need_dst = AUTH_CREATE;
        }
        if (auth_quickaclcheck($src) < $need_src || auth_quickaclcheck($dst) < $need_dst) {
            return $base + ['moved' => false, 'reason' => 'no_auth'];
        }

        // For a single document, fail fast on existence before touching plan state.
        if (!$ns) {
            $src_exists = ($kind === 'media') ? file_exists(mediaFN($src)) : page_exists($src);
            if (!$src_exists) {
                return $base + ['moved' => false, 'reason' => 'not_found'];
            }
            $dst_exists = ($kind === 'media') ? file_exists(mediaFN($dst)) : page_exists($dst);
            if ($dst_exists) {
                return $base + ['moved' => false, 'reason' => 'exists'];
            }
        }

        /** @var helper_plugin_move_plan|null $plan */
        $plan = plugin_load('helper', 'move_plan');
        if (!$plan) {
            return $base + ['moved' => false, 'reason' => 'no_plugin'];
        }
        // Plan state is global/non-reentrant: refuse if a move is locked in.
        if ($plan->isCommited()) {
            return $base + ['moved' => false, 'reason' => 'in_progress'];
        }

        $plan->setOption('autorewrite', $rewrite);
        $plan->setOption('autoskip', $autoskip);
        if ($ns) {
            if ($kind === 'media') {
                $plan->addMediaNamespaceMove($src, $dst);
            } else {
                $plan->addPageNamespaceMove($src, $dst);
            }
        } else {
            if ($kind === 'media') {
                $plan->addMediaMove($src, $dst);
            } else {
                $plan->addPageMove($src, $dst);
            }
        }

        try {
            $committed = $plan->commit();
        } catch (Exception $e) {
            $plan->abort();
            return $base + ['moved' => false, 'reason' => 'plugin_error', 'error' => $e->getMessage()];
        }
        if (!$committed) {
            $err = $plan->getLastError();
            $plan->abort();
            return $base + ['moved' => false, 'reason' => $err ? 'plugin_error' : 'noop', 'error' => $err ?: null];
        }

        // Drive nextStep() to completion. It returns 0 when done, false on error
        // (then getLastError()), or a positive int (remaining, forced >=1 to
        // ensure one more call). Capped so a pathological namespace move can't
        // hang the request — past the cap, abort and direct to the web UI.
        $steps = 0;
        $cap   = 500;
        $left  = null;
        while ($steps < $cap) {
            $left = $plan->nextStep();
            if ($left === false) {
                $err = $plan->getLastError();
                $plan->abort();
                return $base + ['moved' => false, 'reason' => 'plugin_error', 'error' => $err ?: 'move failed', 'steps' => $steps];
            }
            $steps++;
            if ((int) $left === 0) {
                break;
            }
        }
        if ((int) $left !== 0) {
            $plan->abort();
            return $base + ['moved' => false, 'reason' => 'too_large', 'steps' => $steps];
        }

        return $base + ['moved' => true, 'steps' => $steps];
    }

    /**
     * Existing pages with no inbound links.
     *
     * Entry-point pages (start, sidebar, playground, …) are NOT excluded here —
     * the client filters those cheaply if it wants.
     *
     * @return string[]
     */
    public function orphans()
    {
        $ms  = new MetadataSearch();
        $out = [];
        foreach ($this->readablePages() as $id) {
            if (!$ms->backlinks($id)) {        // index-backed; perms respected
                $out[] = $id;
            }
        }
        sort($out);
        return $out;
    }

    /**
     * Media files in a namespace that are not referenced from any page.
     *
     * @param string $ns  namespace to scan ('' = root; recursive)
     * @return string[]
     */
    public function mediaorphans($ns = '')
    {
        global $conf;
        $ns = cleanID($ns);

        $data = [];
        search($data, $conf['mediadir'], 'search_media', ['depth' => 0], $ns);
        // search_media already drops media the caller can't read (AUTH_READ).

        $ms  = new MetadataSearch();
        $out = [];
        foreach ($data as $item) {
            $mid = $item['id'] ?? null;
            if ($mid === null || $mid === '' || str_starts_with($mid, 'wiki:')) {
                continue;                     // skip shipped logos/docs (wiki: namespace)
            }
            if (!$ms->mediause($mid)) {
                $out[] = $mid;
            }
        }
        sort($out);
        return $out;
    }
}
