# CI for the bundled Corkboard skill: lint, type-check, and tests via mise

**Date:** 2026-07-25
**Status:** Implemented

## Goal

Add a single GitHub Actions workflow that, on every push and pull request,
lints and type-checks the bundled `corkboard` skill's Python helper and runs
its test suite — using the **exact** tool versions declared in `mise.toml`,
installed in CI by `jdx/mise-action` (P016), with every action SHA-pinned (P002).

Concretely, the job runs three steps after checkout + mise setup:

1. `ruff check .` — lint (P011)
2. `ty check skills/corkboard/script` — type-check (P012)
3. `python3 tests/test_corkboard_logic.py` — the existing test harness

## Motivation

The `corkboard` skill ships a stdlib-only Python helper
(`skills/corkboard/script/corkboard.py`) and a hand-rolled test suite
(`tests/test_corkboard_logic.py`). Today there is **no CI at all** (no
`.github/` directory): regressions in the helper's pure-string logic
(`apply_edits`, `locate_anchor`, `insert_block`, `find_matching_lines`, the CAS
rev handling, etc.) are only caught when someone runs the tests locally. A
locked-down, version-pinned CI gate closes that gap and makes the conventions
documented in the patterns library real for this repo.

## Background: the patterns and how this repo adapts them

P010 / P011 / P012 are written for **Python projects** — they assume a
`pyproject.toml`, a committed `uv.lock`, dev dependencies installed via
`uv sync --extra dev`, and everything invoked as `uv run ...`.

**This repo is not a Python project.** It is a DokuWiki deployment with one
stdlib-only helper script and a dependency-free test harness. There is no
`pyproject.toml` and there will not be one. So we keep the *intent* of the
patterns (ruff lints, ty type-checks, both gated in CI) but drop the
uv/pyproject machinery:

| Pattern | Assumes | This repo | Adaptation |
| --- | --- | --- | --- |
| P016 (mise-action) | any project with `mise.toml` | ✅ has `mise.toml` | applies directly |
| P011 (ruff) | dev dep in pyproject, `uv run` | no pyproject | ruff is a **mise tool** (P003), bare `ruff check` |
| P012 (ty) | dev dep in pyproject, `uv run` | no pyproject | ty is a **mise tool** (P003), bare `ty check` |
| P010 (uv) | Python project | n/a | **not adopted** — no uv, no lockfile |
| P002 (SHA-pin) | any GH Actions | ✅ new workflow | applies directly |
| P003 (mise) | single source of truth | ✅ already used | python/ruff/ty (`php` later removed — never built under mise) |

The key move: ruff and ty are **non-Python tools** (standalone Rust binaries).
Per P003 they belong in `mise.toml`, not in a Python dependency manifest. mise
resolves both from its registry — `ruff` and `ty` (`aqua:astral-sh/ty`).

## Decisions

1. **`python`, `ruff`, and `ty` go in `mise.toml`** — the single source of
   truth for tool versions, local and CI (P003). `mise-action` (P016) installs
   all three; there is **no** `actions/setup-python`, `astral-sh/setup-uv`, or
   per-tool `curl`. The Python interpreter is mise-managed too, so the runner's
   system `python3` is never the thing the tests run under — local and CI agree.
   (Alternative considered: rely on the runner's system Python and only put
   ruff/ty in mise. Rejected — it leaves the *test* interpreter unpinned and
   unversioned, which is exactly the drift P003/P016 exist to prevent.)

2. **ruff config lives in a root `ruff.toml`.** P011 prefers `[tool.ruff]`
   inside `pyproject.toml`, but its prohibition is against a *second* config
   file alongside pyproject (a drift risk). With **no** `pyproject.toml`, a
   standalone `ruff.toml` is the legitimate single config surface — the same
   "one home for the config" rule, different file. No `.ruff.toml` alias.

3. **ty stays zero-config** (P012): no `[tool.ty]` anywhere, no `ty.toml`. It is
   pointed at the source dir only — `skills/corkboard/script` — never `.` (the
   tests are intentionally loose and would add noise).

4. **Keep the existing stdlib test harness, not pytest.** P010's example uses
   `uv run pytest`; we keep `tests/test_corkboard_logic.py`'s self-contained
   `check()`/`check_raises()` runner invoked as bare
   `python3 tests/test_corkboard_logic.py`. (Adopting pytest is a separate,
   out-of-scope change.) The harness needs **no** installed packages — only a
   Python interpreter — which is why no dependency step is required.

5. **SHA-pin every action** (P002): `actions/checkout` and `jdx/mise-action`,
   full 40-char SHA with a `# tag` comment.

6. **`target-version = "py313"`** for ruff, matching the interpreter the skill
   is developed and run under (a `cpython-313` bytecode artifact is present).
   This also satisfies ty's zero-config Python-version inference.

> **Known limitation, stated plainly:** `corkboard.py` currently has **zero**
> type annotations, so `ty check` is a light pass today. The gate is still
> worth establishing — it catches the regressions ty *can* infer, and its value
> grows as annotations are added. This RFC does not add annotations; that is
> future work.

## Technical details

### `mise.toml` (additions)

Add `python`, `ruff`, and `ty`:

```toml
[tools]
python = "3.13"
ruff = "0.16.0"
ty = "0.0.63"   # aqua:astral-sh/ty
```

> **`php` was removed from `mise.toml` during implementation.** The original
> `mise.toml` carried `php = "8.5.8"`, but the mise PHP (vfox) plugin compiles
> from source (needs autoconf/libcurl) and never installed — and since
> `mise-action` installs *every* tool in the file, that broken entry failed
> the whole CI step. Production PHP comes from the Dockerfile's
> `php:8.5.8-apache` base image, not mise, so removing it costs nothing.

Exact versions are resolved and pinned at implementation time (mise resolves
`ty` from `aqua:astral-sh/ty`). Bumping any of these is a one-line `mise.toml`
change — there is no CI-side version to keep in sync (P016).

### `ruff.toml` (new, at repo root)

```toml
# Single config surface for ruff (this repo has no pyproject.toml).
# See rfcs/2026-07-25_ci-for-corkboard-skill.md, Decision #2.
line-length = 99
target-version = "py313"

[lint]
# pyflakes + pycodestyle + import sorting + modernization + common pitfalls.
select = ["E", "F", "I", "UP", "B", "SIM"]
```

Adopted verbatim from the P011 reference repo (`line-length = 99`,
`target-version = "py313"`, the same `select` set). `target-version` matches
the mise-managed interpreter (Decision #6).

### `.github/workflows/ci.yml` (new)

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read   # mise-action reads GitHub-backend tool releases (ruff/ty)

jobs:
  skill:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd # v5
      - name: Setup mise
        uses: jdx/mise-action@e6a8b3978addb5a52f2b4cd9d91eafa7f0ab959d # v4.2.0
      - name: Ruff check
        run: ruff check .
      - name: Ruff format
        run: ruff format --check .
      - name: ty check
        run: ty check skills/corkboard/script
      - name: Tests
        run: python3 tests/test_corkboard_logic.py
```

What each piece does, per the patterns:

- **`permissions: contents: read`** — `mise-action` uses the job's default
  `GITHUB_TOKEN` to download GitHub-backend tool releases (ruff/ty). Plain read
  is enough; no explicit `token:` input (P016).
- **`jdx/mise-action`** after checkout reads `mise.toml` and puts `python`,
  `ruff`, and `ty` on `PATH` for every later step. One step, no per-tool
  install (P016).
- **`ruff check .`** — lints every `.py` in the repo (the helper and the
  tests). Bare command, no `uv run` (P011, adapted).
- **`ruff format --check .`** — the formatter, gated too. This is a deliberate
  departure from P011's "formatter is optional, not part of the lint gate"
  default (see Implementation notes): we gate it so the style stays enforced.
- **`ty check skills/corkboard/script`** — type-checks the shipped source only,
  zero-config (P012).
- **`python3 tests/test_corkboard_logic.py`** — the existing harness; exits
  non-zero on any failure (its `main()` does `sys.exit(1 if _failed else 0)`),
  so it gates correctly.

The SHAs above are the canonical pins from the patterns library (P002 table /
P016 example). Resolve/confirm at implementation with:

```bash
gh api repos/actions/checkout/commits/v5 --jq '.sha'
gh api repos/jdx/mise-action/commits/v4.2.0 --jq '.sha'
```

## Migration notes

- New files (`ruff.toml`, `.github/workflows/ci.yml`) plus additions to
  `mise.toml`. The existing skill helper and test harness were reformatted to
  clear the lint gate (see Implementation notes) — behavior unchanged.
- The workflow runs only on `push` to `main` and on PRs — it will not fire on
  tag pushes or other branches.
- First run: the existing code may surface a few ruff findings (the test file
  already carries a `# noqa: E402`, signalling awareness of the rules). Run
  `ruff check --fix .` locally and resolve the rest so the gate starts green.
  ty is expected to be quiet given the lack of annotations (Decision #6 note).

## Implementation notes

Implemented 2026-07-25. The gate is live and green locally; CI runs on push/PR.

- **`mise.toml`** — added `python = "3.13"`, `ruff = "0.16.0"`, `ty = "0.0.63"`.
  `mise install` resolves all three (ty via `aqua:astral-sh/ty`). Versions pinned
  to the exact resolutions at implementation time. The pre-existing
  `php = "8.5.8"` entry was **removed** (see the note under `### mise.toml`
  above): the vfox plugin can't build it, and it broke CI.
- **`ruff.toml`** — added at repo root (`line-length = 99`, `target-version = "py313"`,
  `select = ["E","F","I","UP","B","SIM"]`).
- **`.github/workflows/ci.yml`** — added. Action SHAs resolved live via `git ls-remote`
  (the runner's `gh` is unauthenticated): `actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5`
  and `jdx/mise-action@e6a8b3978addb5a52f2b4cd9d91eafa7f0ab959d # v4.2.0`. (The
  checkout SHA differs from the P002 table placeholder — resolved, not copied.)
- **`AGENTS.md`** — added a "Python tooling & CI" section.
- **`README.md`** — added a "Tests & CI" subsection under "The agent + the bundled skill".

### Lint pass on the existing code

`ruff check .` initially reported **49** violations across the helper and tests.
Resolved with **zero `ignore`/`noqa` additions** — the gate is genuinely clean:

- Auto-fixed import style (E401 multiple-imports-on-one-line, I001 unsorted) on the helper.
- Reformatted the compact `add_argument(...); add_argument(...)` argparse block to one
  statement per line (E702), which also retired the attendant E501s.
- Renamed the `list` subparser's variable `l` → `lst` (E741 ambiguous name); purely
  internal, no CLI change (verified with `--help`).
- Added `strict=False` to one `zip(...)` whose lengths are already guarded equal (B905).
- Wrapped ~20 long `add_argument`/`add_parser` `help=` lines and three standalone long
  lines (a docstring, an `avail = …` expression, a `print`) to ≤ 99 chars, plus two long
  lines in the test harness.

`ruff format` was initially deferred to keep the lint-fix diff focused, then
applied to both Python files and **added to the CI gate** as
`ruff format --check .` — a deliberate departure from P011's "formatter is
optional, not part of the lint gate" default, so the style stays enforced. The
formatter does reflow logic functions (comprehension wrapping, boolean-expression
line breaks, `for x in (y or [])`), so the format commit is a broad but purely
mechanical normalization; behavior is unchanged (ruff check / ty / 65 tests /
argparse `--help` all re-verified).

### ty

`ty check skills/corkboard/script` passes clean. As predicted in Decision #6, the
helper has no type annotations, so ty is a light pass today; the gate is established
for when annotations arrive.

### Tests

`python3 tests/test_corkboard_logic.py` → **65 passed, 0 failed.** The argparse
reformatting is not covered by the suite, so the wiring was verified separately with
`corkboard.py --help` / `edit --help` / `list --help`.
