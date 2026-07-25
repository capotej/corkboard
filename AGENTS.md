# AGENTS.md

## Editing skills: use `/workspace/skills`, NEVER the baked copy

The skill files under `/home/harness/.agents/skills/` are a **baked, read-only
snapshot** materialized into the container — they are **reset on every container
restart** (and re-synced from source on other events). Any edit made there is
silently lost.

**Always edit the source of truth instead:**

- `/workspace/skills/<name>/`  ← edit here (tracked in this repo)

```
/workspace/skills/corkboard/        # SOURCE — edit this
        ↓ (baked at build/restart time)
/home/harness/.agents/skills/corkboard/   # BAKED — read-only, gets wiped
```

If you change a skill, do it in `/workspace/skills/...`, then commit (see VCS
below). To run your in-progress changes immediately, invoke the source script
directly, e.g. `python3 /workspace/skills/corkboard/script/corkboard.py ...` —
the baked copy will only pick up the change after a rebuild.

## VCS: this repo uses `jj` (not git)

This repository is managed with **jujutsu (`jj`)**. Do **not** run `git`
commands directly — use the `jj` skill / `jj ...` equivalents. See the global
agent rules and the `jj` skill for the command mapping.

## Python tooling & CI (the `corkboard` skill)

The bundled `corkboard` skill is a **stdlib-only** Python helper
(`skills/corkboard/script/corkboard.py`) plus a dependency-free test harness
(`tests/test_corkboard_logic.py`). This repo is **not** a Python project — there
is no `pyproject.toml`, no `uv`, no `pip`. Tool versions come from `mise.toml`.

- **Install the toolchain:** `mise install` (provides `python`, `ruff`, `ty`, `php`).
- **Run tests:** `python3 tests/test_corkboard_logic.py` (a hand-rolled harness; no pytest).
- **Lint & format:** `ruff check .` (lint) + `ruff format --check .` (format). Auto-fix: `ruff check --fix .` then `ruff format .`. Config lives in `ruff.toml` (the single config surface, since there's no `pyproject.toml`).
- **Type-check:** `ty check skills/corkboard/script` (zero-config; point at the source, not `.`).

CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check`,
`ty check`, and the tests on every push/PR, with the
toolchain installed by `jdx/mise-action` straight from `mise.toml`. The skill
has **no type annotations** today, so `ty` is a light pass that grows in value
as annotations are added.

## RFCs

Significant changes, architectural decisions, and new features should be proposed as RFCs in the `rfcs/` directory. RFCs use the format `rfcs/YYYY-MM-DD_short_title.md` with the following structure:

- `# Title` — short descriptive title
- `**Date:**` — proposal date (ISO format)
- `**Status:**` — `Proposed`, `Accepted`, `Implemented`, or `Rejected`
- `## Goal` — what the RFC aims to accomplish
- Remaining sections are free-form but typically include motivation, technical details, migration notes, and an implementation checklist
- When moving an RFC to `Implemented`, update `AGENTS.md` and `README.md` to reflect any new infrastructure, commands, or workflows introduced by the RFC. Also, replace the implementation checklist with implementation notes.
