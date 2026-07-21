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
- **Edit surgically** — `get` the page, make a targeted change, `put` it back
  (read-modify-write), preserving the rest. Avoid full-page rewrites, which can
  drop content or clobber concurrent edits.
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
| `put <page> [--file F\|--text T] [--sum S]` | create/replace a page | `core.savePage` |
| `append <page> [--file F\|--text T] [--sum S]` | append text (stdin ok) | `core.appendPage` |
| `delete <page>` | **clear** page content (an update) | `core.savePage` w/ `""` |
| `list <ns> [--depth N]` | page ids (recursive; `--depth N`) | `core.listPages` |
| `all` | every page id | `core.listPages("", 0)` |
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

`media-upload` reads bytes from `<file>`, base64-encodes, and calls
`core.saveMedia`. It **overwrites by default** (`--no-overwrite` to require a
fresh id). Media ids are `<ns>:<name>` (or just `<name>` for the root ns).
`list` is **recursive by default** (`--depth 0`); `--depth N` descends N levels.

## Media upload

`core.saveMedia(media, base64, overwrite)` base64-**decodes** the content, so it
works for **binary and text** and **can overwrite**. It round-trips a real PNG
byte-for-byte (verified: upload → `core.getMedia` → decode → identical bytes).
The helper's `media-upload` handles the encoding for you.

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

## DokuWiki syntax essentials

See [references/dokuwiki-syntax.md](references/dokuwiki-syntax.md) for the
everyday subset (headings, bold/italic, `monospace`, `<code>`/`<file>` blocks,
tables, internal/external links, image embedding, lists, namespaces). Use it
when authoring page content.

## Citing sources (good practice)

When a page summarizes external material, cite inline as
`[[https://example.org|Author/Title (year)]]` and/or add a `===== Sources =====`
section. Whether/where to do this is a project convention (AGENTS.md).
