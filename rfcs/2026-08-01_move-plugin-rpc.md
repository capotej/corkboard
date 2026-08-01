# Page/media move via the move plugin + a `plugin.corkboard.move` RPC

**Date:** 2026-08-01
**Status:** Accepted

## Goal

Give the corkboard skill a real `move`/`rename` capability — one that moves
pages **and media**, **preserves revision history** (the attic), and **rewrites
every backlink across the wiki** — by adopting the bundled-style
[move plugin](https://github.com/michitux/dokuwiki-plugin-move) server-side and
exposing its programmatic API over the corkboard RPC transport the skill already
speaks (`plugin.corkboard.*`). This replaces the only lever that exists today
(copy-to-destination + clear source), which **severs history** and **orphans the
source attic**, and it does so without `editx`, which is dead on PHP 8.

## Background: why a plugin, and why this one

DokuWiki's core has **no move/rename** — it's flat files, and the JSON-RPC
`core.*` surface gives `savePage`/`getPage`/`getPageInfo` but **zero access to
the attic** (`data/attic/`). So a move done over the core API necessarily:

- creates the destination as a **fresh revision 1** (no inherited history, edit
  summaries, or original authorship), and
- clears the source (`savePage("")`), which **deletes the page file but leaves
  the attic revisions stranded** in an invisible page.

That directly contradicts a stated skill value — *"Treat page history as the
audit log (edit summaries + revisions)"* — and the SKILL.md ontology argues for
shallow namespaces that survive moves, i.e. moves are expected to happen.

The three candidate plugins:

| plugin | move + rewrite + attic | RPC-callable | PHP 8 | verdict |
| --- | --- | --- | --- | --- |
| **editx** | yes, leaves a redirect text | yes (`plugin.editx.*`) | **❌ incompatible** (its own page: "Uninstall before you upgrade to PHP8") | rejected |
| **move** (michitux) | yes — pages, media, namespaces; full attic move; atomic backlink rewrite | no clean RPC — GUI/AJAX-first, but a **programmatic `helper` API** | ✅ (Release **2024-05-07**; the dokuwiki.org "not updated in 2 years" banner is stale — it tracks the .org page, not the GitHub release) | **adopt** |
| pagemove (desolat) | older lineage of `move` | no | unmaintained | rejected |

The **move** plugin is the de-facto answer; its only gap for an API-driven skill
is that it's GUI-first. The fix is exactly what this repo already does for
`cas` / `linkhealth` / `wanted` / `orphans`: ship a thin RPC in the bundled
Corkboard plugin (`corkboard-plugin/remote.php`) that calls the move plugin's
**programmatic helper** server-side — same process, same transport, no AJAX, no
extra auth path.

## The move plugin's programmatic API (what we wrap)

The entry point is `helper_plugin_move_plan` (`helper/plan.php`), loaded via
`plugin_load('helper', 'move_plan')`:

```php
$plan = plugin_load('helper', 'move_plan');
$plan->addPageMove($src, $dst);        // or addMediaMove / addPageNamespaceMove / addMediaNamespaceMove
$plan->setOption('autorewrite', true); // rewrite backlinks on affected pages
$plan->setOption('autoskip', false);   // abort on first failure (vs. skip & continue)
$plan->commit();                       // validate, lock, persist plan to $conf['metadir']/__move_*
while (($left = $plan->nextStep()) > 0) { ... }   // execute in batches of OPS_PER_RUN (10)
// nextStep() returns 0 when done, false on error (then $plan->getLastError(); $plan->abort())
```

Three properties of this API dictate the RPC's shape:

1. **Stepped, not atomic.** A move is `commit()` then loop `nextStep()` to
   completion. Each `nextStep()` does ≤10 filesystem ops / page rewrites. The
   RPC must drive that loop; it is not a single call.
2. **Global, non-reentrant plan state.** The plan lives in fixed files
   (`__move_opts`, `__move_pagelist`, `__move_medialist`, `__move_affected`, …).
   Two concurrent moves clobber each other; `commit()` throws *"plan is
   committed already"*. **Moves serialize wiki-wide.** The RPC must detect an
   in-progress plan (`$plan->inProgress()`) and refuse with a clear error rather
   than corrupt it.
3. **`autorewrite` controls backlink rewriting.** Default comes from plugin
   config; the RPC sets it explicitly (default **on** for agent moves).

It also takes a rewrite **lock** (`helper_plugin_move_rewrite::addLock()` /
`removeAllLocks()`) for the duration of commit+execution, so affected pages
can't be edited mid-rewrite — it interacts cleanly with the skill's per-page
`cas` (a concurrent `edit` on a locked affected page just retries).

## Design

### RPC contract: `plugin.corkboard.move`

```
plugin.corkboard.move(src, dst, opts?) -> {moved, src, dst, kind, steps, conflict?, error?}
```

- `src`, `dst` — page or media ids (namespaces when `opts.ns`).
- `opts` (all optional):
  - `kind` — `"page"` (default) | `"media"`.
  - `ns` — bool; move the whole namespace (`addPageNamespaceMove` /
    `addMediaNamespaceMove`) instead of a single document.
  - `rewrite` — bool, default **true** → `setOption('autorewrite', …)`. The rare
    off case is "move without touching backlinks."
  - `autoskip` — bool, default **false** (abort on first failure, mirroring the
    skill's no-silent-clobber posture; the agent re-runs after fixing).

Returns `{moved:true, src, dst, kind, steps}` on success, or
`{moved:false, src, dst, reason, …}` with `reason` ∈ `no_auth | in_progress |
not_found | exists | too_large | plugin_error` so the client can branch (e.g.
`in_progress` → retry; `exists` → dst already taken; `too_large` → "use the
web UI").

### Execution model: synchronous loop with a cap

The RPC drives `commit()` + `nextStep()`-to-completion **synchronously in one
request**, matching the skill's "one call = one logical op" model (`cas`,
`linkhealth`). To bound request time on pathological inputs, it caps the loop
(e.g. 500 steps); past the cap it `abort()`s and returns
`{moved:false, reason:'too_large'}` directing the caller to the web UI's move
GUI for genuinely huge namespace moves. Typical agent moves — a single page, or
a small namespace — are well inside the cap.

### Auth model (verified against `helper_plugin_move_op`)

The move operator's `checkPage` / `checkMedia` gates are the source of truth,
and they are **asymmetric** — which decides what ships first:

| op | `check…` requires | in agent token (READ+UPDATE)? | result |
| --- | --- | --- | --- |
| **page** move | `AUTH_EDIT` src + `AUTH_CREATE` dst | ✅ both | **ships as-is** |
| **media** move | `AUTH_DELETE` src + `AUTH_UPLOAD` dst | ❌ `AUTH_DELETE` absent today | **grant `* @api 16`** → then works (below) |

So: **page moves need no permission change** — the RPC mirrors `cas` (AUTH_EDIT
src + AUTH_CREATE dst, refuse with `reason:'no_auth'`) and the move plugin's own
`checkPage` agrees. The page source is removed by an empty `saveWikiText($src,
'')` — an *update*, not a delete-ACL op — and the attic is relocated by a
separate filesystem op (`movePageAttic`), which is how the history carries over.

**Media moves are blocked by the current token**: `checkMedia` demands
`AUTH_DELETE` (16) on the source (it does a real `io_rename` that removes it),
and the agent caps at AUTH_UPLOAD (8) — provenance confirmed end-to-end: the
agent user is in groups `user,api` (not `@admin`, so no superuser privilege),
its ACL comes solely from `* @user 8`, and delete=16 is simply absent. That's
why `core.deleteMedia` 403s — it's an **ACL cap, not an API-method allowlist**.

**Decision: grant the agent AUTH_DELETE (option a).** ACL can't scope delete to
media-only — DokuWiki's six levels apply per-namespace to *both* pages and media
— so the practical grant is a dedicated **`* @api 16`** line in `acl.auth.php`.
Because ACL takes the **highest** permission across a user's matched groups, the
agent (∈ `user,api`) resolves to max(`* @user 8`, `* @api 16`) = **16**, while
ordinary members (∈ `user` only) stay at 8. The RPC then needs **no elevation
code** — `checkMedia`'s `auth_quickaclcheck($src) >= AUTH_DELETE` passes on the
agent's own credentials, so page and media moves are uniform. **v1 ships both.**

(Granting `* @api 16` also retires `core.deleteMedia`'s 403 for the agent — by
design. The skill's Permissions section needs updating to match; see checklist.)

**Why granting delete is defensible — and the guards that make it safe.** A
DokuWiki "delete" is an empty-text save: the page file goes, but the **attic is
retained**, and purging the attic needs admin (no `core.*` for it) — so history
is tamper-evident even against a delete-capable caller; the attacker *cannot*
make their own deletes irrecoverable. Restoration is a write (AUTH_CREATE/EDIT),
which the token already holds, so a mass-delete is mechanically mass-recoverable
(enumerate deleted pages from the changelog/attic, restore each to its last
non-empty revision). And critically, a leaked **level-8** token can already
mass-*edit* (equally attic-recoverable) — so granting delete adds **no new class
of irrecoverable harm**, only the "make pages vanish" convenience on top of the
"deface everything" the token could already do. That is why (a) beats the
fragility and version-coupling of a server-side privileged-context elevation (b),
which would only *reduce the accident surface* for an autonomous caller, not
prevent disaster.

The residual risks are operational, not data-loss, and these guards bound them:

- **Lock `$conf['mediarevisions'] = 1`** in `local.protected.php`. Without it,
  *media* delete is the one genuinely irrecoverable op (no `media_attic`); with
  it, deleted media moves to `media_attic` and is restorable via the Media
  Manager. (DokuWiki's default is on; locking it stops the web Config Manager
  from flipping it.)
- **No attic auto-purge** (or a generous retention window). DokuWiki doesn't trim
  revisions by default — that's why cleanup plugins/scripts exist — but if ops
  runs a retention cron, a revision-*spamming* attacker could push good content
  past the window before recovery. Verify nothing auto-trims the attic.
- **A leak is a recovery incident, not data loss.** Recovery is forensic, not
  just mechanical, for *edits*: a clean delete is an obvious empty revision, but
  a subtle edit isn't — you must know when the attack started to pick the "last
  good" revision. (This bites mass-edit, already possible at level 8 — not
  unique to granting delete, but the real recovery cost in either leak.)

Two operator details worth recording: `setSelfMoveMeta($src)` writes a
*rename ledger* into the page's metadata ("previously called X"), so the move
is auditable beyond the attic; and `PLUGIN_MOVE_PAGE_RENAME` /
`PLUGIN_MOVE_MEDIA_RENAME` events fire on each move — the hook a redirect
companion (the `moved` plugin) would use for the future `opts.redirect`.

### Concurrency

Because plan state is global, the RPC treats `$plan->inProgress()` as a hard
`reason:'in_progress'` error (it does **not** silently `abort()` someone else's
move). This is the one place the skill's otherwise per-page concurrency model
becomes wiki-wide serialization; it's documented on the command. Corkboard is
single-agent in practice, so contention is rare — but the contract is explicit.

### Backlinks, revisions, and the redirect question

This is where the chosen design resolves the questions that prompted this RFC:

- **Backlinks.** `autorewrite=true` rewrites every `[[src]]`→`[[dst]]` (and
  `{{src}}`→`{{dst}}` for media) across the whole wiki, atomically, in the move
  plugin's own pass — using DokuWiki's parser/index, not client-side
  pattern-matching. No `apply`-driven N-page rewrite, no link-form edge cases.
- **Revisions.** The destination **inherits the source's full attic** — edit
  history, summaries, and authorship carry over (the operator's `movePageAttic`
  filesystem move), plus a rename ledger in metadata (`setSelfMoveMeta`).
  Provenance is preserved; the audit log is intact. This is the thing the core
  API structurally cannot do. (Media moves are weaker — the operator itself
  notes `FIXME this does not create a changelog entry` — so media moves are less
  auditable than page moves, a caveat to document on the command.)
- **Redirects (would agents leave their own?).** **No, and they don't need to.**
  The move plugin rewrites the internal link graph, so inbound links never
  dangle. It does **not** itself leave a redirect at the old id — that's the
  separate `moved` companion plugin, for *external* bookmarks / hardcoded URLs.
  So: internal navigation is fixed by the rewrite; a leave-behind redirect is an
  **opt-in for URL stability only**, off by default, surfaced as a future
  `opts.redirect` once the `moved`/`pageredirect` plugin is present. Agents do
  not litter the wiki with `Moved to` stubs.

### Helper command

```
python3 script/corkboard.py move <src> <dst> [--kind page|media] [--ns] [--no-rewrite] [--autoskip] [--sum S]
```

Calls the RPC, prints the result, then runs **`linkhealth` on `dst`** afterward
— the skill's existing post-write link-health habit, now also confirming the
rewrite didn't strand any link. A move shows up in Recent Changes via the move
plugin's own summary; `--sum` is appended.

### Why not also a soft-move fallback

Deliberately out of scope. A history-severing API-only "soft move" would
contradict the audit-log value this RFC exists to uphold, and the plugin path is
strictly better. If a deployment ever lacks the move plugin, the honest answer
is "install it" — not a second-class move that quietly drops provenance.

## Open questions / risks

- **Move-operator auth — RESOLVED.** Verified against `helper_plugin_move_op`:
  page move = AUTH_EDIT+CREATE (in token); media move = AUTH_DELETE+UPLOAD. ACL
  provenance confirmed: agent ∈ groups `user,api`, ACL from `* @user 8`
  (UPLOAD), delete=16 absent — an ACL cap, not an API allowlist. **Decision:
  grant `* @api 16`** (highest-wins → agent gets 16, ordinary members stay 8),
  so media moves run on the agent's own credentials and the RPC needs no
  elevation. Guards: lock `mediarevisions=1`, confirm no attic auto-purge.
  See Auth model.
- **Affected-page ACL.** `findAffectedPages()` reads the raw index without
  ACL-filtering the *affected* pages, so the rewrite pass may touch pages the
  caller can't edit. Low-risk for the broad agent token; flagged for the audit.
- **`"Class JSON not found"`** — a reported bug in older move versions (forum
  thread); verify it's gone in 2024-05-07 at install time.
- **Cap tuning.** 500 steps is a first guess; revisit once real namespace-move
  timings are measured.
- **Namespace `start` re-index.** The corkboard action plugin re-indexes each
  enclosing `start` on create/delete; confirm it also fires (or is extended to
  fire) on a move, so `orphans`/`backlinks` stay correct immediately after.

## Implementation checklist

- [ ] Install move plugin (2024-05-07) on the Corkboard wiki; pin/record the
      version; smoke-test a single-page move + backlink rewrite via the web GUI.
- [ ] `conf-seed/acl.auth.php`: add `* @api 16` (highest-wins → the agent ∈
      `user,api` resolves to 16; ordinary `@user` members stay at 8). Scope to a
      namespace (`ns:* @api 16`) only if a future deployment wants it narrower.
- [ ] `conf-seed/local.protected.php`: `$conf['mediarevisions'] = 1;` (locked on
      so a media delete/move stays `media_attic`-recoverable; web Config Manager
      can't flip it).
- [ ] Verify **no attic auto-purge** runs (no retention cron / cleanup plugin
      with a window a revision-spammer could push good content past).
- [ ] `corkboard-plugin/remote.php`: add `public function move($src, $dst, $opts=[])`
      — auth gate (AUTH_EDIT+CREATE for pages, AUTH_DELETE+UPLOAD for media, both
      satisfied by the agent's own ACL after the grant — **no elevation code**),
      `inProgress` guard, plan build (`addPageMove`/`addMediaMove`/
      `addPageNamespaceMove`/`addMediaNamespaceMove`), `setOption` calls,
      `commit()` + capped `nextStep()` loop, error → `getLastError()`+`abort()`.
- [ ] `skills/corkboard/SKILL.md` **Permissions section**: the agent now **has**
      delete (`core.deleteMedia` no longer 403s); document that deletion is
      attic-recoverable (pages always; media iff `mediarevisions`, now locked
      on) and the web Media Manager remains the cleanup path for *irreversible*
      removal.
- [ ] `skills/corkboard/script/corkboard.py`: `move` subcommand → calls the RPC,
      post-runs `linkhealth` on `dst`, prints `{moved,steps,…}`.
- [ ] `skills/corkboard/SKILL.md`: `move` row in the command table + a
      `## Move: real renames, history preserved` section documenting the
      concurrency-serial / auth / redirect-defaults-off contract.
- [ ] `README.md`: list `move` in the skill features.
- [ ] Tests: a unit test for the RPC's option→`setOption` mapping and the
      `in_progress` / `not_found` / `too_large` refusal paths (the actual move is
      exercised against a real wiki, like the gardening commands).

## Related

- The exploration that prompted this: the skill had no move; the core API can't
  reach the attic; editx is PHP-8-dead; the move plugin is maintained and
  exposes a programmatic helper.
- `corkboard-plugin/remote.php` — the existing `cas` / `linkhealth` / `wanted` /
  `orphans` / `mediaorphans` RPCs this extends, and the auth/ACL precedent
  (`auth_quickaclcheck` ≥ AUTH_EDIT/CREATE).
- `rfcs/2026-08-01_corkboard-hermes-memory-provider.md` — prior precedent for
  growing the bundled Corkboard plugin.
- move plugin source: `helper/plan.php` (`helper_plugin_move_plan`).
