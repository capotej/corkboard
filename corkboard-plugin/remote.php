<?php
use dokuwiki\Extension\RemotePlugin;
use dokuwiki\Search\Indexer;
use dokuwiki\Search\MetadataSearch;

/**
 * DokuWiki Plugin corkboard (Remote Component)
 *
 * RPC methods for the Corkboard agent. Two families:
 *
 *   1. Wiki "gardening" queries — wanted pages, orphaned pages, unreferenced
 *      media, and per-page broken outgoing links (linkhealth) — each returned
 *      in a single call, computed against the search index instead of via N
 *      authenticated round-trips from the client.
 *   2. A compare-and-swap writer (cas) so the client's surgical edits can save
 *      only when the page revision they last read still matches — a concurrent
 *      edit can no longer be silently clobbered.
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
