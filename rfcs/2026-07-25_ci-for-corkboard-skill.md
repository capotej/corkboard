# CI for the bundled Corkboard skill: lint, type-check, and tests via mise

**Date:** 2026-07-25
**Status:** Accepted

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
| P003 (mise) | single source of truth | ✅ already used | python/ruff/ty join `php` |

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

Add `python`, `ruff`, and `ty` alongside the existing `php`:

```toml
[tools]
php = "8.5.8"
python = "3.13"
ruff = "0.13"   # P011 reference lower bound; pin exact at implementation
ty = "0.0.59"   # aqua:astral-sh/ty — latest in mise registry at time of writing
```

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

- **New files only** (`ruff.toml`, `.github/workflows/ci.yml`) plus three lines
  in `mise.toml`. No existing files change shape.
- The workflow runs only on `push` to `main` and on PRs — it will not fire on
  tag pushes or other branches.
- First run: the existing code may surface a few ruff findings (the test file
  already carries a `# noqa: E402`, signalling awareness of the rules). Run
  `ruff check --fix .` locally and resolve the rest so the gate starts green.
  ty is expected to be quiet given the lack of annotations (Decision #6 note).

## Implementation checklist

- [ ] Add `python = "3.13"`, `ruff`, `ty` to `mise.toml`; run `mise install`
      locally to confirm both resolve (ty via `aqua:astral-sh/ty`).
- [ ] Add root `ruff.toml` (config block above).
- [ ] Resolve/confirm the `actions/checkout@v5` and `jdx/mise-action@v4.2.0`
      SHAs via `gh api ...`.
- [ ] Add `.github/workflows/ci.yml`.
- [ ] Run `ruff check --fix .` and `ruff check .` locally; fix remaining
      violations so the gate is green on the first CI run.
- [ ] Run `ty check skills/corkboard/script` locally; address any findings.
- [ ] Run `python3 tests/test_corkboard_logic.py` locally; confirm it still
      passes.
- [ ] Push, open PR, confirm all three steps are green.
- [ ] Add a short **CI / Python tooling** section to `AGENTS.md` noting:
      ruff+ty are mise tools (`mise install`), config is `ruff.toml`, tests run
      via `python3 tests/test_corkboard_logic.py`, and there is no `uv`/`pyproject`.
- [ ] On acceptance, update this RFC's Status to `Implemented`, replace this
      checklist with implementation notes, and reflect the new workflow in
      `README.md` per the AGENTS.md RFC rules.
