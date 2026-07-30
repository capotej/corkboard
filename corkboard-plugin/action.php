<?php
use dokuwiki\Extension\ActionPlugin;
use dokuwiki\Extension\Event;
use dokuwiki\Extension\EventHandler;
use dokuwiki\Search\Indexer;

/**
 * DokuWiki Plugin corkboard (Action Component)
 *
 * Keeps corkboard's `orphans` / `backlinks` immediately consistent with the
 * nspages auto-index that every namespace `start` page is meant to carry.
 *
 * Why this exists. nspages lists a namespace's children dynamically at render
 * time, and emits those links through the standard renderer (`internallink`),
 * so they DO land in the holding page's `relation.references` metadata — which
 * is exactly what `plugin.corkboard.orphans` / `MetadataSearch::backlinks`
 * query (both read the search index built from that metadata). The catch is
 * that a page's metadata is only rebuilt when THAT page is rendered for
 * indexing, and adding a new child does not change its parent's mtime. So the
 * parent's recorded references would stay stale until the parent is next
 * edited — a brand-new page would appear as an orphan even though its parent's
 * `start` visibly links it. (The manual workflow avoids this because editing
 * the parent to add the link refreshes its metadata; nspages removes that
 * edit, so the refresh has to come from somewhere else. Here.)
 *
 * What this does. On a page CREATE or DELETE, walk up the saved page's
 * namespace chain and force a re-index of each ancestor's `start` page that
 * actually uses nspages. `Indexer::addPage($id, true)` reads the start page's
 * references with `METADATA_RENDER_UNLIMITED`, which forces a fresh metadata
 * render — re-running nspages, now seeing the changed child set — and writes
 * the result into the search index. Net: `orphans` is correct immediately
 * after a create/delete, with no manual parent edit and no changelog noise.
 *
 * Notes
 *   - We hook COMMON_WIKIPAGE_SAVE AFTER: io_writeWikiPage() has already run,
 *     so the child file is on disk and nspages' directory scan sees it.
 *   - Edits/reverts are skipped: they don't change the set of children a
 *     namespace lists, so re-indexing ancestors then would be wasted work.
 *   - Indexing failures are caught and logged, never propagated: a save must
 *     not fail because a best-effort index refresh did.
 */
class action_plugin_corkboard extends ActionPlugin
{
    /**
     * Registers a callback for a given event (signature matches the parent
     * ActionPlugin exactly — no return type — to stay compatible across PHP).
     *
     * @param EventHandler $controller
     */
    public function register(EventHandler $controller)
    {
        $controller->register_hook('COMMON_WIKIPAGE_SAVE', 'AFTER', $this, 'handleSave');
    }

    /**
     * After a page is created or deleted, refresh the search-index backlinks of
     * every enclosing namespace's `start` page (that uses nspages), so a newly
     * added/removed child is reflected in `orphans` immediately.
     *
     * @param Event   $event  COMMON_WIKIPAGE_SAVE; $event->data has 'id' and
     *                        'changeType' (a DOKU_CHANGE_TYPE_* constant)
     * @param mixed[]|null $param unused (register_hook glue)
     */
    public function handleSave(Event $event, $param = null): void
    {
        $data = $event->data;
        $id = $data['id'] ?? '';
        if ($id === '') {
            return;
        }

        // Only a CREATE or DELETE changes the SET of children a namespace's
        // nspages block lists; an ordinary edit/revert leaves the set unchanged,
        // so skip it (avoids re-indexing ancestors on every edit).
        $changeType = $data['changeType'] ?? '';
        if ($changeType !== DOKU_CHANGE_TYPE_CREATE && $changeType !== DOKU_CHANGE_TYPE_DELETE) {
            return;
        }

        $indexer = new Indexer();
        foreach ($this->ancestorStarts($id) as $startId) {
            if (!page_exists($startId)) {
                continue;
            }
            // Only an nspages-bearing start can have stale references; a start
            // with a hand-maintained child list is refreshed when it is edited.
            if (strpos((string) rawWiki($startId), 'nspages') === false) {
                continue;
            }

            try {
                // force=true bypasses needsIndexing(); addPage() re-renders the
                // start page's metadata (METADATA_RENDER_UNLIMITED) so nspages
                // re-scans, then writes relation_references to the index.
                $indexer->addPage($startId, true);
            } catch (\Throwable $e) {
                // Best-effort: never let an index refresh fail the save. The
                // next save or an admin index rebuild catches it up.
                error_log('[corkboard] ancestor re-index failed for ' . $startId . ': ' . $e->getMessage());
            }
        }
    }

    /**
     * The `start` page id of every namespace enclosing $id, nearest first,
     * down to the root start — the pages whose nspages block might list $id.
     *
     * The landing-page token is $conf['start'] (corkboard prescribes `start`),
     * so this respects any intentional override too.
     *
     *   projects:foo:bar  ->  projects:foo:start, projects:start, start
     *   projects:start    ->  projects:start dropped (it's $id), -> start
     *   start             ->  []  (no ancestors; root start is $id)
     *
     * @return string[]
     */
    protected function ancestorStarts(string $id): array
    {
        global $conf;
        $start = $conf['start'] ?? 'start';

        $starts = [];
        $ns = getNS($id);                 // namespace of the saved page ('projects:foo')
        while ($ns !== false && $ns !== '') {
            $starts[] = $ns . ':' . $start;
            $ns = getNS($ns);             // parent namespace
        }
        $starts[] = $start;               // root start

        // If $id is itself a start page, its own entry (== $id) is irrelevant —
        // its own save already indexes it.
        if ($starts && $starts[0] === $id) {
            array_shift($starts);
        }
        return $starts;
    }
}
