# Corkboard as a Hermes memory provider

**Date:** 2026-08-01
**Status:** Proposed

## Goal

Make the Corkboard (DokuWiki) instance usable as a **Hermes Agent memory
provider** — i.e. a plugin that implements Hermes's `MemoryProvider` lifecycle so
the agent gains persistent, cross-session, full-text recall backed by the same
wiki the `corkboard` skill already talks to. One plugin, `hermes memory setup`
→ `corkboard`, no new infrastructure.

This RFC specifies the lifecycle mapping, the memory model, the security
posture, and the open decisions. It is a **spec / contract**, not an
implementation: the plugin itself lives in a **separate (out-of-tree) repo**,
not this one (see *Scope and repository boundary* below). This repo owns the
wiki and the `corkboard` skill; it maintains this RFC so the out-of-tree plugin
and the skill stay consistent.

## Motivation

Hermes ships 8 external memory providers. Corkboard maps onto their lifecycle
almost 1:1 because DokuWiki already provides the two hardest primitives a memory
provider needs:

- **Server-side full-text search** (`core.searchPages`) → recall.
- **Durable, addressable, appendable documents** (pages) → storage.

Corkboard's pitch versus the existing 8 is distinctive and currently unoccupied:

- **Human-readable, human-editable, versioned memory.** Every memory is a real
  wiki page you can browse, edit, and link in a browser. Page history is a free
  audit log. None of the 8 providers are browser-editable; the closest analogs
  (Holographic's SQLite, ByteRover's local tree) are opaque files.
- **Multi-user / shared.** A wiki is inherently multi-user. Multiple Hermes
  agents (and humans) can read/write the same memory space, with per-profile and
  shared pools — a fit only Honcho emphasizes today.
- **Zero new infra.** If you already run Corkboard (this repo does), it is just
  credentials. Like Holographic's "no dependencies," but the store is already
  running and web-served.

It lands in the **lexical-recall family** (Holographic FTS5, ByteRover fuzzy
text), with the honest tradeoff that recall is **keyword/full-text, not
semantic/vector**.

| | Corkboard (proposed) | Closest analog |
| --- | --- | --- |
| Storage | Remote DokuWiki (pages) | — |
| Recall | Full-text (`searchPages`) | Holographic (FTS5), ByteRover (fuzzy) |
| Cost | Free (self-hosted wiki you run) | Holographic |
| Tools | ~4 | Holographic, Supermemory |
| Dependencies | None (stdlib HTTP) | Holographic |
| **Unique** | **Human-editable, versioned, multi-user, web-browsable** | *(none)* |

## Scope and repository boundary

The implementation does **not** live in this repo. This RFC is the
**spec/contract** the out-of-tree plugin implements against, kept here because
this repo is the source of truth for the wiki and the `corkboard` skill.

| Owned by | What |
| --- | --- |
| **This repo** (`capotej/corkboard`) | the wiki, the `corkboard` skill, server-side config (Apache/WAF/clean URLs), **this RFC**, and the conventions the plugin must respect (DokuWiki syntax, namespaces, CAS, security posture) |
| **The plugin repo** (out of tree) | the Hermes plugin code (`CorkboardMemoryProvider`, tools, `plugin.yaml`, README) |

**This repo's obligations to the plugin** (the contract surface):

- The bundled **`plugin.corkboard.cas`** RPC plugin is present and relied upon
  for concurrency-safe writes (already required by the skill's `edit`/`insert`).
- Document the **`core.searchPages` result shape** so `prefetch` formatting is
  deterministic (an open item — see checklist).
- Keep the **JSON-RPC / HTTP-Basic-auth surface** and the **READ+UPDATE (no
  DELETE) permission model** stable; changes are breaking for the plugin.

**The plugin repo's obligations**: follow this RFC's security model (Decision 1),
namespace conventions, and CAS usage; vendor its own slim `dokuwiki_rpc.py`
rather than importing the skill.

When the plugin ships, this repo's only change is a **pointer** (`README.md` +
`AGENTS.md`) to the plugin repo and moving this RFC to **Implemented**.

## Design

### Provider identity

`name = "corkboard"`. Single-select (Hermes's one-external-provider rule), lives
in `plugins/memory/corkboard/`, activated via `hermes memory setup` or
`hermes config set memory.provider corkboard`.

### Lifecycle mapping

The `MemoryProvider` ABC is a 7-method lifecycle plus optional hooks. Each maps
onto a corkboard primitive:

| Method | Corkboard primitive | Notes |
| --- | --- | --- |
| `name` | `"corkboard"` | |
| `is_available()` | check `CORKBOARD_URL/USER/PASS` env | **no network** — matches Holographic |
| `initialize(session_id, hermes_home, agent_identity, …)` | store identity → namespace prefix; optional `core.getWikiVersion` ping | `agent_identity` → **namespace** (profile isolation — see below) |
| `system_prompt_block()` | static block: memory lives in a wiki; tools available; **the wiki is low-security — never store secrets** | bake in the skill's security caveat |
| `prefetch(query, session_id)` | return **cached** `searchPages` result | turn 1 fetches synchronously (Honcho pattern); later turns serve cache |
| `queue_prefetch(query)` | daemon thread runs `searchPages` → `_prefetch_cache[session_id]` | keeps `prefetch` non-blocking |
| `sync_turn(user, assistant, messages)` | daemon-thread `append` to `sessions:<date>` page | **must be non-blocking**; **opt-in** (see Decision 1) |
| `on_session_end(messages)` | flush session page; optional client-side summary | no server-side LLM → "extraction" is opt-in client-side |
| `on_memory_write(action, target, content)` | mirror MEMORY.md/USER.md writes → `append` to `builtin_mirror` | additive; **delete → can only empty** (Decision 3) |
| `get_tool_schemas()` | search / read / remember / forget (+ optional browse) | minimal — docs' own guidance |
| `handle_tool_call(...)` | dispatch into vendored `dokuwiki_rpc.py` | reuse the skill's transport, not its CLI |
| `shutdown()` | join background threads | |

### Memory model

DokuWiki namespaces are the natural memory organizer. Because the store is
**remote and shared** (unlike Holographic's local SQLite), profile isolation
maps onto **namespace prefixes** derived from the `agent_identity` kwarg — not
`hermes_home` paths:

```
memories:                              # root memory namespace
├── start                              # index page (kills orphans, matches skill hygiene)
├── <identity>:                        # per-profile pool  (agent_identity, e.g. "coder")
│   ├── facts                          # corkboard_remember default target
│   ├── preferences
│   └── sessions:2026-08-01            # sync_turn transcript pages (opt-in)
└── shared:                            # cross-profile pool (à la Honcho's workspace)
    └── decisions
```

A single memory = one page, structured for recall (DokuWiki search weighs page
title + headings). This lets `corkboard_read` fake **tiered loading** (à la
OpenViking L0/L1/L2) by returning just the summary line or the whole page:

```
====== User prefers tabs over spaces ======
# tags: preference, formatting
**Summary:** user wants tabs in Python, spaces in JS.
...detail...
[[..:start]]
```

### Tools (minimal set)

```python
[
  corkboard_search,   # → core.searchPages          (recall; prefetch reuses this)
  corkboard_read,     # → core.getPage              (read by id, tiered)
  corkboard_remember, # → core.savePage / append    (store a structured fact)
  corkboard_forget,   # → savePage("")              (empty — no true delete)
  # optional: corkboard_browse → sitemap / list
]
```

`handle_tool_call` must return a **JSON string** (the provider contract) and only
fires for names returned by `get_tool_schemas`.

### Config

Minimal schema — secrets to `.env`, the rest to `$HERMES_HOME/corkboard.json`
(follows the Supermemory "prompt only the must-haves" example):

```python
[
  {"key":"url",  "required":True, "env_var":"CORKBOARD_URL"},
  {"key":"user", "required":True, "env_var":"CORKBOARD_USER"},
  {"key":"pass", "required":True, "secret":True, "env_var":"CORKBOARD_PASS"},
  {"key":"auto_capture", "default":False, "description":"auto-persist turns (LOW-SECURITY wiki)"},
  {"key":"shared_namespace","default":"memories:shared"},
]
```

`save_config(values, hermes_home)` writes the non-secret fields to
`$HERMES_HOME/corkboard.json`; `register(ctx)` calls
`ctx.register_memory_provider(CorkboardMemoryProvider())`.

### Out-of-tree plugin layout

The plugin lives in its **own repo**, distinct from both the `corkboard` skill
and this repo, but reusing the skill's conventions (DokuWiki syntax, CAS,
security caveats). Its expected shape:

```
plugins/memory/corkboard/
├── __init__.py        # CorkboardMemoryProvider + register()
├── dokuwiki_rpc.py    # vendored slim client (JSON-RPC, Basic auth, CAS)
├── plugin.yaml        # name / version / description / hooks
├── cli.py             # hermes corkboard status/config (optional, active-provider-gated)
└── README.md          # setup, security model, config reference, tools
```

`plugin.yaml`:

```yaml
name: corkboard
version: 0.1.0
description: "DokuWiki-backed memory — full-text recall of human-editable, versioned pages."
hooks: [on_session_end, on_memory_write]
```

### Illustrative core

Concrete enough to ground the contract; not the full implementation:

```python
class CorkboardMemoryProvider(MemoryProvider):
    @property
    def name(self): return "corkboard"

    def is_available(self):                       # NO network
        return all(os.environ.get(k) for k in
                   ("CORKBOARD_URL", "CORKBOARD_USER", "CORKBOARD_PASS"))

    def initialize(self, session_id, **kw):
        self._session_id = session_id
        self._identity = kw.get("agent_identity") or "default"
        self._ns = f"memories:{self._identity}"   # profile isolation via namespace
        self._auto_capture = self._read_cfg("auto_capture", False)
        self._cache = {}                          # session_id -> prefetch result
        self._lock = threading.Lock()

    def prefetch(self, query, *, session_id=""):
        with self._lock:
            cached = self._cache.get(session_id, "")
        if cached or session_id in self._warmed:
            return cached
        return self._do_search(query, session_id)  # synchronous on turn 1

    def queue_prefetch(self, query, *, session_id=""):
        threading.Thread(target=self._do_search,
                         args=(query, session_id), daemon=True).start()

    def sync_turn(self, u, a, *, session_id="", messages=None):
        if not self._auto_capture:
            return
        threading.Thread(target=self._append_turn,
                         args=(u, a, session_id), daemon=True).start()

    def get_tool_schemas(self):  return TOOL_SCHEMAS
    def handle_tool_call(self, name, args, **kw): ...   # -> JSON string
    def shutdown(self): ...
```

## Key decisions

These are the tradeoffs that shape the provider. Each needs sign-off.

### Decision 1 — Security ⟂ auto-capture (the make-or-break)

The `corkboard` skill is emphatic: *low-security wiki, never store secrets,
redact, ask first.* A memory provider's job is the opposite — auto-persist
conversation turns (`sync_turn`) and mirror built-in writes (`on_memory_write`).
These conflict directly.

**Resolution (proposed):**

- `auto_capture` is **opt-in, default off.** By default only explicit
  `corkboard_remember` tool calls write. The provider still does recall
  (`prefetch` + `corkboard_search`) read-only out of the box — useful with zero
  write risk.
- When `auto_capture` is on, run a **redaction pass** (regex for common secret
  shapes: API keys, bearer tokens, `password=`, connection strings, private
  keys) before any auto-write, replacing matches with `<redacted>`.
- Document the threat model in the README: turns persisted to the wiki are
  visible to anyone with wiki read access and retained in page history.

This is the single most important decision in the RFC.

### Decision 2 — Lexical, not semantic recall

`searchPages` is DokuWiki's built-in full-text engine (keyword hits, namespace,
age weighting) — **not** vector/semantic. Great for names, entities, exact terms;
weak for paraphrase/concept matching. Be honest about it in
`system_prompt_block`.

**Resolution (proposed):** ship lexical-only for v1. Enhancement path (out of
scope here): a client-side embeddings index layered over page ids, or lean on
DokuWiki tags + disciplined page titles to improve recall.

### Decision 3 — No true delete

`on_memory_write(action=delete)` and `corkboard_forget` can only **empty** a page
(it is an `savePage` update); media cannot be deleted at all (`core.deleteMedia`
→ 403). Page history is retained — arguably a *feature* for memory (auditable,
reversible), but it means "forget" is "blank, not remove."

**Resolution (proposed):** `corkboard_forget` empties the page and reports
explicitly that history is retained; point users at the web Media Manager / wiki
admin for irreversible cleanup. Document it, as the skill already does.

### Decision 4 — Concurrency / CAS

Background appends from daemon threads plus gateway multi-session can race on a
shared page. The skill ships compare-and-swap (`plugin.corkboard.cas`).

**Resolution (proposed):** all writes go **via CAS with retry on conflict**, and
`sync_turn` writes to **per-session pages** (`sessions:<date>`) to avoid
cross-session contention. `on_session_end` rolls a session page into the
per-profile `facts`/summary only if `auto_capture` is on.

## Migration / rollout

- **Nothing lands in this repo at implementation time.** The plugin is
  out-of-tree (see *Scope and repository boundary*). The wiki, its CAS plugin,
  existing pages, and the skill are untouched by the plugin existing.
- **When the plugin ships**, this repo's only change is a **pointer** in
  `README.md` and `AGENTS.md` to the plugin repo, and this RFC moves to
  **Implemented** — but here "implemented" means "exists out-of-tree and
  referenced from here," not "code merged here."
- **Namespace hygiene (plugin's responsibility).** The plugin must create/keep
  an index page (`memories:start`) and link every memory page from somewhere,
  per the skill's no-orphans rule, and run `wanted` / `orphans` after seeding.
- **Credentials.** Reuse the existing `CORKBOARD_*` env vars; the provider reads
  the same values the skill does, so no second secret to manage.
- **Reuse vs vendor.** The plugin vendors a slim `dokuwiki_rpc.py` (recommended)
  rather than importing `skills/corkboard/script/corkboard.py`, so the plugin is
  self-contained and not coupled to this repo's path. The two stay in sync on
  transport/auth conventions — which is exactly why this RFC lives here.

## Implementation checklist

**In this repo** (small — the contract surface):

- [ ] Confirm and document the `core.searchPages` result shape
      (title/snippet/score?) — fetch from the live OpenAPI spec
      (`lib/exe/openapi.php?spec=1`) and record it (skill references or here) so
      `prefetch` formatting is deterministic for the plugin.
- [ ] Document the **`plugin.corkboard.cas`** dependency as a hard requirement
      the plugin relies on (already present; just make it explicit).
- [ ] Once the plugin repo exists, add a pointer in `README.md` + `AGENTS.md`
      and move this RFC to **Implemented**.

**In the plugin repo** (out of tree — implements this spec):

- [ ] Scaffold the plugin (`__init__.py`, `dokuwiki_rpc.py`, `plugin.yaml`,
      `cli.py`, `README.md`).
- [ ] Implement `CorkboardMemoryProvider` (7 lifecycle methods + hooks).
- [ ] Implement the 4 tools + `handle_tool_call` JSON dispatch.
- [ ] Implement two-tier prefetch cache (synchronous turn-1, background after).
- [ ] Implement `auto_capture` opt-in + redaction pass (Decision 1).
- [ ] CAS-with-retry on all writes; per-session `sync_turn` pages (Decision 4).
- [ ] Config schema + `save_config` → `$HERMES_HOME/corkboard.json`.
- [ ] Namespace seeding + index page; `wanted`/`orphans` clean after seed.
- [ ] README: setup, security model, config reference, tools, threat model.
- [ ] Tests for prefetch caching, CAS retry, redaction, profile isolation.

## Related

- Hermes `MemoryProvider` ABC & `MemoryManager` —
  [developer guide: memory-provider-plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin),
  [memory providers (user guide)](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers).
- `corkboard` skill — `/workspace/skills/corkboard/SKILL.md` (transport, CAS,
  permissions, security caveats this RFC inherits).
- Closest existing providers: Holographic (FTS5), ByteRover (fuzzy tree) — the
  lexical-recall family.
