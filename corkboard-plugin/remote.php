<?php
use dokuwiki\Extension\RemotePlugin;
use dokuwiki\Search\Indexer;
use dokuwiki\Search\MetadataSearch;

/**
 * DokuWiki Plugin corkboard (Remote Component)
 *
 * RPC methods for the Corkboard agent. Today: server-side wiki "gardening" —
 * wanted pages, orphaned pages, and unreferenced media — each returned in a
 * single call, computed against the search index instead of via N authenticated
 * round-trips from the client. Intended to grow with more methods over time.
 *
 * Public methods are auto-exported by RemotePlugin as plugin.corkboard.<method>.
 * Access is gated by the usual remote=1 / remoteuser=@api,@admin (the agent),
 * so no per-method guard is needed. Results are ACL-filtered to what the caller
 * may read, matching core.listPages / core.getPageBackLinks semantics.
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
        return $wanted;
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
