# Wikitext linter in corkboard.py (`lint` / `lint-all`)

**Date:** 2026-08-01
**Status:** Implemented

## Goal

Add a `lint` subcommand to the bundled `corkboard` skill that catches the
formatting mistakes agents make most — the ones where the wikitext **looks fine
to a human scanning raw text but renders broken on the page** — *before* the
page is saved. Proposed in [issue #4](https://github.com/capotej/corkboard/issues/4);
this RFC tracks its implementation.

The detection logic is **pure string analysis**: no network, no RPC, no
DokuWiki instance. It operates on raw wikitext the same way the existing
`apply_edits` / `locate_anchor` helpers do, so it fits the existing
dependency-free test harness in `tests/test_corkboard_logic.py` and can also run
on a local draft or in CI.

## Motivation

Agents writing DokuWiki content consistently make the same set of mistakes. They
share a trait: the wikitext reads correctly as raw text but renders broken, and
the breakage is invisible without a visual check the agent skips. A server-side
`wanted` / `orphans` sweep catches broken *links* but not *syntax-level*
rendering breakage. Every rule below is sourced from a real, observed breakage
in production wiki content (14 broken table headers across 11 pages in a single
scan was the catalyst), documented across issues #1–#3.

## Design

### Commands

```
python3 script/corkboard.py lint <page>          # lint one page (RPC fetch)
python3 script/corkboard.py lint <page> --fix    # auto-fix + save (CAS)
python3 script/corkboard.py lint --file F [--ns N]   # lint a local file (no RPC)
python3 script/corkboard.py lint --stdin [--fix]     # lint / fix from stdin
python3 script/corkboard.py lint-all             # lint every page (loops `all`)
python3 script/corkboard.py lint-all --fix       # auto-fix every page
```

`--file` / `--stdin` read local text with no RPC, so a draft can be linted
before it ever touches the wiki and the command can gate CI. `--ns` supplies the
page's namespace for DW007 when linting a file (for a page id it is derived
automatically). **Exit code is 0 if clean, 1 if any finding** — so `lint`
doubles as a CI gate. `--fix` applies the auto-fixable rules and writes back (a
page via compare-and-swap, a file in place, stdin to stdout), then reports any
remaining non-fixable findings.

Each finding is `PAGE  Lnn  DWxxx  SEVERITY  message`, with a suggested
replacement for auto-fixable rules.

### Rules

| rule | sev | catches | `--fix` |
| --- | --- | --- | --- |
| DW001 | ERROR | table header row mixing `^` and `\|` → whole table renders as literal text | `\|`→`^` |
| DW002 | ERROR | `^`/`\|` table row with leading whitespace → renders as a code block | de-indent |
| DW003 | ERROR | list-item continuation indented 4+ → renders as a code block | report only |
| DW004 | WARNING | Markdown `# Heading` → literal text | `=…=` heading |
| DW005 | WARNING | Markdown `[t](url)` → literal text | `[[url\|t]]` |
| DW006 | WARNING | Markdown ` ``` ` / `~~~` fence → literal text | `<code>`/`</code>` |
| DW007 | WARNING | namespace-relative `[[bare]]` → resolves under the page's ns | `[[:bare]]` |
| DW008 | WARNING | `[[link]]` inside a `==== heading ====` → renders raw | report only |
| DW009 | WARNING | MediaWiki `<gallery>` / `<figure>` / `<figcaption>` → literal text | report only |

### Key invariants

- **Code-block awareness.** Every rule skips lines inside `<code>`/`<file>`
  blocks *and* ` ``` /`~~~` fences — content there is literal, not wikitext to
  fix. A shared `_code_mask(content)` classifies each line once; the fence
  delimiter lines themselves stay unmasked so DW006 can still flag them. (The
  issue called this out for DW002/DW003; the same principle applies to all
  rules, so it is applied universally.)
- **Context-awareness.** DW003 (continuation) looks at the previous line and
  tracks list context across a run of continuations; DW007
  (namespace-relative) takes the page's namespace as a parameter and is a no-op
  in the root namespace (where a bare token already resolves from root).
- **Pure, composable fix.** `lint_fix` runs the auto-fixable rules to a fixpoint
  (capped) so a cascade like `[t](bare)` → `[[bare|t]]` → `[[:bare|t]]`
  converges. DW007 is applied before DW005 so a converted link isn't re-seen.
  DW003/DW008/DW009 are report-only (intent-dependent).

### Public surface (tested directly)

- `lint_wikitext(content, namespace="") -> [Finding]` — the orchestrator.
- `lint_fix(content, namespace="") -> (new_content, lines_changed)` — the fixer.
- `Finding` namedtuple: `(line, rule, severity, message, suggestion)`.

## Implementation notes

- `skills/corkboard/script/corkboard.py`: a new `# --- lint` section (regex
  constants, `_code_mask`, per-rule detectors `_dw001`…`_dw009`, `lint_wikitext`,
  `_lint_fix_once` / `lint_fix`, formatters, `cmd_lint` / `cmd_lint_all`), plus
  `lint` / `lint-all` argparse subparsers and `main()` dispatch. Stdlib only
  (`collections` added for the `Finding` namedtuple).
- `tests/test_corkboard_logic.py`: per-rule tests (positive / negative / edge),
  code-block + fence exclusion, namespace-relative context, the cascade fix, and
  the `_namespace_of` helper. 133 tests total (was 71).
- `tests/fixtures/clean_wikitext.txt`: a realistic clean page exercised by a new
  CI step so the `lint` command is smoke-tested on every push.
- `.github/workflows/ci.yml`: a `Lint fixture` step after `ty check`.
- `skills/corkboard/SKILL.md`: `lint` / `lint-all` rows in the command table and
  a `## Lint: catch rendering breakage before you save` section with the rule
  table.
- `README.md`: the linter in the skill features, the "what the agent can do"
  list, and the Tests & CI section.

## Related

- Issue #4 — the proposal this implements.
- Issues #1–#3 — the production breakages distilled into the rule set.
