---
name: corkboard
description: >-
  Read, write, and upload to a DokuWiki (e.g. the "Corkboard" instance) through its
  built-in JSON-RPC Remote API (`core.*` methods) over HTTP Basic auth — one
  transport for pages AND media. Use when the user wants to create or edit wiki
  pages, upload images/files, list or search the wiki, or mentions "corkboard",
  "dokuwiki", or "the wiki". Ships a stdlib-only Python CLI helper. Project-specific
  conventions (namespaces, naming, page layout) belong in the repo's AGENTS.md, not here.
---

# Corkboard (DokuWiki) Skill

Corkboard is a **DokuWiki** instance. This skill talks to its built-in
**Remote API** (`ApiCore`) over **JSON-RPC** at `<url>/lib/exe/jsonrpc.php` with
**HTTP Basic auth**, using the **`core.*` methods**. One transport, one auth
method, for **everything** — pages and media alike. No cookies, no CSRF tokens,
no FineUploader.

> **These are generic defaults.** Project-specific wiki conventions — which
> namespace to use, page-naming, layout templates, citation style — live in the
> repo's **`AGENTS.md`** and **take precedence** over anything here. (`AGENTS.md`
> is already in your context; no extra step needed.) Follow those when present.

## Author in DokuWiki syntax, NOT Markdown

Agents reach for Markdown by habit; Corkboard is **DokuWiki**, so Markdown
renders as literal text. Author page bodies in DokuWiki markup — the everyday
subset (headings, code, lists, links, tables, images), plus a Markdown→DokuWiki
translation table for the common traps, is in
[references/dokuwiki-syntax.md](references/dokuwiki-syntax.md).

## Security: treat Corkboard as LOW-SECURITY

Corkboard is password-protected, but it is a **low-security** area. Before
posting any page or uploading any file:

- **Never** post secrets, credentials, API keys, tokens, passwords, private
  keys, connection strings, internal hostnames/URLs, or personally-identifying
  data.
- **Redact** anything that could be sensitive — use placeholders like
  `<redacted>`, `****`, or `$ENV_VAR` instead of real values.
- **If you are unsure whether something is sensitive, ask the user before
  posting it.** Err on the side of caution.

## Wiki hygiene

Conventions that keep an agent-driven wiki navigable, auditable, and clean.

**Editing**

- **Set an edit summary every time** (`--sum`) — it populates page history / Recent
  Changes, so edits are auditable and reversible. Never leave it blank.
- **Edit surgically** — prefer `edit` (exact-match replace that asserts a unique
  anchor) or `insert` (anchor-targeted) over full-page rewrites. Both fetch the
  page, apply a targeted change, and save it back preserving the rest, and both
  are concurrency-safe by default (compare-and-swap). See
  [Surgical edits](#surgical-edits).
- **Prefer idempotent writes** — `savePage`/`saveMedia` overwrite, so use a stable
  id and re-run safely. Failed retries are harmless, and you avoid inventing
  versioned throwaway names (`foo_v2`, `foo_final`).

**Structure**

- **One topic per page**, grouped by namespace; give each namespace an **index
  page** that lists and links its children.
- **Keep pages linked both ways.** Cross-link related pages and add a **nav
  footer** ("Back to [[index]] / [[start]]") so no page is a dead-end (outgoing);
  conversely, **every page should be linked *from* somewhere** (an index or another
  page) — no orphans. Run `wanted` / `orphans` / `media-orphans` (see Gardening) to
  check.
- **Split long pages** into sub-pages — large pages render slowly and can hit
  parser limits.

**Content & media**

- **Reference every uploaded media file from a page** — media can't be deleted via
  the API, so orphan files accumulate. Don't upload throwaways.
- **Experiment in `playground:`.** Scratch pages and test uploads go in the
  `playground:` namespace (treated as disposable), not in real namespaces. This
  keeps `wanted` / `orphans` / `media-orphans` focused on real content and avoids
  orphaning things where they don't belong.
- **Treat page history as the audit log** (edit summaries + revisions) — don't
  duplicate a manual changelog inside pages.

## Setup: credentials via env

Never hardcode the password. Export:

```bash
export CORKBOARD_URL=https://wiki.example.com   # no trailing slash
export CORKBOARD_USER=me
export CORKBOARD_PASS=...
```

## The Python helper

`script/corkboard.py` (Python 3 stdlib only). Resolve its path relative to this
skill dir: `<skill>/script/corkboard.py`.

| command | what it does | API method |
| --- | --- | --- |
| `get <page>` | print raw wikitext | `core.getPage` |
| `find <page> <pattern> [-E] [-i]` | **in-page search** w/ line numbers (`grep -n` style; `-E` regex, `-i` ignore-case) | `core.getPage` |
| `put <page> [--file F\|--text T] [--sum S]` | create/replace a page | `core.savePage` |
| `append <page> [--file F\|--text T] [--sum S]` | append text (stdin ok) | `core.appendPage` |
| `edit <page> (--old O --new N)+ [--edits F] [--sum S] [--show-context]` | **surgical replace** — asserts `--old` is unique; repeat pairs for multi-edit | `getPage`→`cas` |
| `insert <page> (--under H\|--after L\|--before L) [--text T\|--file F] [--sum S]` | **insert at an anchor** (heading or line) | `getPage`→`cas` |
| `apply <plan.json>` | **batch** edits/inserts/replaces across pages | `getPage`→`cas` |
| `delete <page>` | **clear** page content (an update) | `core.savePage` w/ `""` |
| `list <ns> [--depth N]` | page ids (recursive; `--depth N`) | `core.listPages` |
| `all` | every page id | `core.listPages("", 0)` |
| `sitemap [--ns X] [--depth N] [--json]` | **page tree** (one call) — bird's-eye view for orientation & placement | `core.listPages` |
| `search <query>` | full-text search | `core.searchPages` |
| `version` | DokuWiki version | `core.getWikiVersion` |
| `media-upload <file> <ns> <name> [--no-overwrite]` | **upload binary or text** | `core.saveMedia` |
| `media-get <mediaid> [-o OUT]` | download (decodes base64) | `core.getMedia` |
| `media-list <ns>` | media ids in a namespace | `core.listMedia` |
| `media-info <mediaid>` | size / type / revision | `core.getMediaInfo` |
| `media-delete <mediaid>` | delete (**403** — no delete perm) | `core.deleteMedia` |
| `wanted` | broken internal links (linked, not existing) | `core.getPageLinks` |
| `orphans` | pages with no inbound links | `core.getPageBackLinks` |
| `media-orphans <ns>` | unreferenced media in a namespace | `core.getMediaUsage` |
| `links <page>` | outgoing internal links from a page | `core.getPageLinks` |
| `backlinks <page>` | pages linking TO a page | `core.getPageBackLinks` |
| `raw <method> '<json-params>'` | escape hatch (any method) | — |

```bash
python3 script/corkboard.py get some:page
python3 script/corkboard.py put some:page --file body.txt --sum "edit"
printf 'appended line\n' | python3 script/corkboard.py append some:page
python3 script/corkboard.py media-upload chart.png reports chart.png   # -> reports:chart.png
python3 script/corkboard.py media-get reports:chart.png -o chart.png
```

## Surgical edits

The most common wiki task is a small, targeted change — not a full-page
rewrite. `get`/`put` force a read-modify-write you do by hand; `edit`, `insert`,
`apply`, and `find` do it for you, surgically, with guards that **never silently
clobber**.

### `edit` — exact-match replace

```bash
python3 script/corkboard.py edit some:page --old "Status: pending" --new "Status: done" --sum "mark done"
# multiple edits in one call (applied in order; each --old must be unique at its step):
python3 script/corkboard.py edit some:page --old "a=1" --new "a=2" --old "b=1" --new "b=2"
# big edit sets from a file: [{old,new}, ...]
python3 script/corkboard.py edit some:page --edits edits.json --sum "batch fixes"
# preview where each --old lands (with line numbers) without saving:
python3 script/corkboard.py edit some:page --old "Status: pending" --show-context
```

- **Asserts a unique match.** If `--old` occurs **0** or **>1** times, the edit
  **aborts and saves nothing** (never an ambiguous clobber). Use `--show-context`
  to craft the shortest snippet that's still unique.
- **Multi-edit applies sequentially** — edit N sees edit N-1's result, so a later
  edit can anchor on text an earlier one produced.

### `insert` — anchor-targeted

```bash
python3 script/corkboard.py insert some:page --under "Lessons" --text "- new lesson" --sum "add lesson"
python3 script/corkboard.py insert some:page --after "===== Results =====" --text "| run | score |"
python3 script/corkboard.py insert some:page --before "nav footer" --file note.txt
```

- `--under HEADING` inserts right after the heading (as the section's first
  content). `--after`/`--before` insert relative to the unique line containing
  the given text. Each anchor must match **exactly one** line, or it aborts.

### `apply` — batch across pages

One logical change often touches several pages. `apply` runs a JSON plan and
reports per-page results (`ok`/`noop`/`conflict`/`failed`):

```json
[
  {"page": "run:2024q1", "sum": "record result", "edits": [{"old": "status: tbd", "new": "status: pass"}]},
  {"page": "results:index", "sum": "link run", "insert": {"after": "===== Q1 =====", "text": "  - [[run:2024q1]]"}},
  {"page": "lessons", "sum": "lesson", "insert": {"under": "Lessons", "text": "- retry on 5xx"}}
]
```

```bash
python3 script/corkboard.py apply plan.json
```

Each entry is `edits` (a list of `{old,new}`), `insert` (`{under|after|before,
text}`), or `text`/`file` (full replace). A page that errors (incl. RPC failures)
is reported as `failed` and the run **continues** — the per-page report always
prints, and `apply` exits non-zero if any page didn't apply. Pass
`--stop-on-first-error` to halt after the first failure.

> **`apply` is not atomic.** DokuWiki has no transactions, so an entry that
> committed before a later failure stays committed. On a partial failure, read
> the per-page report and re-run only the entries that didn't apply — not the
> whole plan: a bare `text`/`file` replace re-applied would overwrite whatever's
> there now, and an `insert` would add a duplicate. (`edit` is safe to re-run: a
> repeat no longer matches and just reports `failed`.)

### `find` — locate before you edit

```bash
python3 script/corkboard.py find some:page "Status:"        # substring; prints "N:line"
python3 script/corkboard.py find some:page "^====" -E        # regex: every heading
python3 script/corkboard.py find some:page "status:" -i      # case-insensitive
```

Prints matching lines with 1-based numbers (`grep -n` style) — use it to pick a
unique anchor for `edit`/`insert`.

### Concurrency & link-health (on by default)

`edit`, `insert`, and `apply` are **concurrency-safe by default** and report
**link-health after writing**:

- **Compare-and-swap.** They read the page's current revision, then save via
  `plugin.corkboard.cas`, which writes **only if the revision is unchanged**. If
  the page was edited concurrently you get a clear `CONFLICT … NOT saved` and a
  non-zero exit — re-run to re-fetch and retry. `--no-cas` forces a blind
  overwrite (rarely wanted). `put --rev <rev>` opts in for full-page writes.
- **Link-health.** After saving they call `plugin.corkboard.linkhealth` and print
  `✓ no broken outgoing links` or `⚠ N broken outgoing link(s): [...]` — so a
  typo'd `[[link]]` is caught inline instead of surfacing in `wanted` later.
  `--no-check` skips it; `put`/`append` opt in with `--check`.

> Both use the bundled Corkboard RPC plugin (`plugin.corkboard.cas` /
> `.linkhealth`), assumed present on every Corkboard wiki.

`media-upload` reads bytes from `<file>`, base64-encodes, and calls
`core.saveMedia`. It **overwrites by default** (`--no-overwrite` to require a
fresh id). Media ids are `<ns>:<name>` (or just `<name>` for the root ns).
`list` is **recursive by default** (`--depth 0`); `--depth N` descends N levels.

## Orientation: `sitemap` before you place a page

Before deciding where a new page belongs (or just to see the wiki at a glance),
run `sitemap` — it issues **one `core.listPages` call** and renders an ASCII
**tree** of namespaces with per-namespace page counts, page titles (when set),
`*` on each namespace's `index`/`start` landing page, and `[system]` on the
built-in `wiki:` docs:

```bash
python3 script/corkboard.py sitemap                 # full tree
python3 script/corkboard.py sitemap --depth 1       # bird's-eye: top level only
python3 script/corkboard.py sitemap --ns reports    # expand one subtree
python3 script/corkboard.py sitemap --json          # structured nested tree
```

```
(root) — 9 pages
├── start  "Welcome to the Wiki"
├── playground/ — 1 pages
│   └── notes
├── reports/ — 4 pages
│   ├── index *  "Reports"
│   ├── 2024/ — 2 pages
│   │   ├── q1  "Q1 Summary"
│   │   └── q2  "Q2 Summary"
│   └── archive/ — 1 pages
│       └── old  "Legacy Reports"
└── wiki/ — 2 pages  [system]
(* = namespace index/start page; [system] = DokuWiki built-in pages)
```

Prefer `sitemap` for orientation and placement; reach for flat `list`/`all`
when you just need the raw id list to loop over. `--depth 1` is the compact
"what namespaces exist and how big is each?" view for big wikis.

## Post-creation audit

Creating a page is not the end — a brand-new page with no inbound link is an
orphan, and a nav footer that escapes the wrong number of namespaces is a
broken link. After a create or a batch of edits:

1. **Link the new page from its parent / section index.** If you just created
   `projects:foo:architecture`, add a link to it on `projects:foo` (or
   `projects:foo:index`). An unlinked page is invisible until something points
   at it — `backlinks`/`orphans` only surface it once linked.
2. **Verify nav footers escape to the right level.** `[[..:start]]` resolves
   relative to the *current page's* namespace, so its target depends on depth —
   a footer copied from a top-level page breaks when pasted two levels deep.
   Prefer the absolute form `[[:start]]` in nav footers; it's depth-independent.
   See the namespace gotcha below and [references/dokuwiki-syntax.md](references/dokuwiki-syntax.md).
3. **Run `wanted`** after a batch of creates/edits to catch typo'd `[[link]]`s
   and mis-escaped nav footers before they silently become stub pages.
   `edit`/`insert`/`put --check` already run per-page link-health inline; `wanted`
   is the wiki-wide sweep.

## Media upload

`core.saveMedia(media, base64, overwrite)` base64-**decodes** the content, so it
works for **binary and text** and **can overwrite**. It round-trips a real PNG
byte-for-byte (verified: upload → `core.getMedia` → decode → identical bytes).
The helper's `media-upload` handles the encoding for you.

### Upload size limit (HTTP 413)

`core.saveMedia` ships the file **base64-encoded inside the JSON-RPC POST body**
(~33% larger than the raw bytes), and the server enforces a **request-body size
limit** that rejects oversize uploads with `413 Request Entity Too Large` before
DokuWiki ever sees them. On Corkboard the limit that actually bites is
**`SecRequestBodyNoFilesLimit`** — mod_security's cap on the *non-file* body
portion, which the whole base64 payload counts as (there are no multipart file
parts) — whose default is only 1 MiB; it has been **raised server-side** to track
the overall body limit (see `rfcs/2026-07-25_owasp-crs-waf.md`). On other
DokuWiki deployments the rejecting layer could instead be Apache
`LimitRequestBody`, nginx `client_max_body_size`, or PHP `post_max_size`.

There is **no client-side workaround** that makes an oversize upload succeed —
the limit is server-side. So:

- **Resize / compress images before uploading** — downscale to display width,
  strip metadata, pick the right format (PNG for screenshots, JPEG for photos).
  A ~1 MB PNG is usually far larger than it needs to be.
- **Read the full error on failure.** `media-upload` surfaces the HTTP status and
  the (tag-stripped) error body, so you can tell *which* layer rejected it — e.g.
  `HTTP 413 (not a JSON-RPC response): …nginx…` — instead of guessing. A `413`
  means a body limit; a JSON-RPC error code means DokuWiki itself rejected it.

## Permissions: read + update, NOT delete

The token has **READ + UPDATE** but **NOT DELETE**. Concretely:

- **Pages:** read ✓, write/replace ✓ (`core.savePage`), append ✓, and emptying
  ✓. There is no `core.deletePage`; the helper's `delete` clears a page by
  saving empty text — that's an **update** (it empties current content), not a
  true delete. It's the only page-removal lever the token has.
- **Media:** upload ✓ (incl. overwrite), list/read ✓. **`core.deleteMedia`
  returns 403** (the token can't delete). To remove stray media, use the **web
  Media Manager** (`doku.php?do=media&ns=<ns>`). Plan uploads to overwrite
  rather than create throwaways.

## Authoritative method reference

The instance publishes an **OpenAPI spec** at `lib/exe/openapi.php?spec=1` — the
ground truth for every `core.*` method's parameters (names + order). Fetch and
list them when unsure:

```bash
curl -sS -u "$CORKBOARD_USER:$CORKBOARD_PASS" "$CORKBOARD_URL/lib/exe/openapi.php?spec=1" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(p, list(list(o.values())[0].get('requestBody',{}).get('content',{}).get('application/json',{}).get('schema',{}).get('properties',{}).keys())) for p,o in sorted(d['paths'].items()) if p.strip('/').startswith('core.')"
```

## Gardening: keep links healthy

An agent-driven wiki drifts toward orphans and broken links. Audit it with:

```bash
python3 script/corkboard.py wanted               # internal links to non-existent pages
python3 script/corkboard.py orphans              # pages with no inbound links
python3 script/corkboard.py media-orphans <ns>    # media not referenced from any page
python3 script/corkboard.py links <page>         # outgoing internal links from a page
python3 script/corkboard.py backlinks <page>     # pages linking TO a page
```

> **Fast path on Corkboard wikis.** When the bundled **Corkboard RPC** plugin
> (`plugin.corkboard.*`) is present, `wanted` / `orphans` / `media-orphans`, and
> the per-page `linkhealth` used by `edit`/`insert`/`put --check`, are computed
> **server-side in a single call** (against the search index) instead of
> walking every page from the client. The helper falls back to the per-page
> `core.*` walk automatically if the plugin isn't installed.

`wanted` / `orphans` / `media-orphans` scan every page or media file (seconds to
a minute on a small wiki) and print to stdout, with progress on stderr. Run them
periodically and after big edits. **`media-orphans` is especially useful** since
media can't be deleted via the API — it surfaces stray uploads to clean up in the
web Media Manager.

DokuWiki also ships built-in **Wanted Pages** / **Orphaned Pages** admin reports
(`doku.php?do=admin`), computed server-side; these commands expose the same
signal over the API.

## Gotchas

- **No delete permission** → `core.deleteMedia` is 403; pages can only be
  *emptied* (an update), not truly deleted. Web Media Manager for media cleanup.
- **IDs are lowercased.** `Foo.png` is stored as `foo.png`; `Page` as `page`.
  Fetch/overwrite by the lowercased id.
- **`[[links]]` render raw inside `===== headings =====`** on this build — keep
  heading text plain; put links in the body.
- **Always verify a page renders** after a non-trivial edit (re-`get` it) — a
  stray syntax char can quietly break a table or code block.
- **Check pages exist before linking** — DokuWiki auto-creates a page on first
  save, so a typo'd link silently makes a stub.
- **Namespace-relative links resolve against the current page's namespace.** A
  bare `[[start]]` written on a page in `projects:` resolves to `projects:start`,
  **not** root `start`; `[[projects]]` on a page in `projects:` resolves to
  `projects:projects`, not `projects:index`. Escape with `[[..:start]]` (parent
  namespace) or `[[:start]]` (absolute from root), and write `[[ns:index]]`
  explicitly for namespace index pages. `--check` / `wanted` catch these as
  broken outgoing links.
- **`raw` is display-only.** Its output is JSON (`json.dumps`), so text comes back
  escaped — newlines as `\n`, quotes escaped. Never feed `raw core.getPage`
  into a write (it collapses the page to one line). Use `get` to fetch page text
  for writes; reserve `raw` for reading structured method results
  (`core.getMediaInfo`, `plugin.corkboard.*`, …).

## DokuWiki syntax essentials

See [references/dokuwiki-syntax.md](references/dokuwiki-syntax.md) for the
everyday subset (headings, bold/italic, `monospace`, `<code>`/`<file>` blocks,
tables, internal/external links, image embedding, lists, namespaces). Use it
when authoring page content.

## Citing sources (good practice)

When a page summarizes external material, cite inline as
`[[https://example.org|Author/Title (year)]]` and/or add a `===== Sources =====`
section. Whether/where to do this is a project convention (AGENTS.md).
