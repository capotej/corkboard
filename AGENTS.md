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

- **Install the toolchain:** `mise install` (provides `python`, `ruff`, `ty`). PHP is **not** in mise (it never built there — see the note in `mise.toml`).
- **Lint PHP** (the Corkboard plugin / config): PHP can't be built here (no toolchain, no sudo), but you can run `php -l` against a **precompiled static `php`** matching the wiki's PHP version (the `php:<ver>-apache` base image in `Dockerfile` — currently `8.5.8`) and host arch, verifying its SHA-256 first:
  ```bash
  arch=$(uname -m)   # aarch64 | x86_64 — matches the static-php filename suffix
  curl -fSL "https://dl.static-php.dev/static-php-cli/common/php-8.5.8-cli-linux-${arch}.tar.gz" -o /tmp/php.tar.gz
  # SHA-256 (TOFU pin: the server publishes no signed checksums, and the upstream
  # GitHub release ships only the `spc` builder, not these binaries — so this
  # catches corruption / a replaced file, not a server compromise). The pinned
  # value below is aarch64/8.5.8; recompute for x86_64 or another PHP version:
  echo "af995ef6b3187b39932a00714dfb97e44bfa40a9243c4c29f61f19d583950753  /tmp/php.tar.gz" | sha256sum -c -
  mkdir -p /tmp/php-extract && tar -xzf /tmp/php.tar.gz -C /tmp/php-extract
  /tmp/php-extract/php -l corkboard-plugin/remote.php   # syntax check (or loop all *.php)
  ```
  Other versions/arches are browsable at `https://dl.static-php.dev/static-php-cli/common/`. (The `spc-bin` nightly at `…/v3/spc-bin/nightly/spc-linux-*` is the **builder tool**, not a runnable `php` — it bundles a PHP engine but its stub only exposes the `spc` console.) CI does **not** lint PHP today; this is a local-only check.
- **Run tests:** `python3 tests/test_corkboard_logic.py` (a hand-rolled harness; no pytest).
- **Lint & format:** `ruff check .` (lint) + `ruff format --check .` (format). Auto-fix: `ruff check --fix .` then `ruff format .`. Config lives in `ruff.toml` (the single config surface, since there's no `pyproject.toml`).
- **`ruff format` reaches into Markdown:** ruff also formats Python inside fenced code blocks in `.md` files (e.g. `rfcs/*.md`, `README.md`), so illustrative Python there must be format-clean — `ruff format --check .` (and CI) fails otherwise. Run `ruff format .` to fix in place.
- **Type-check:** `ty check skills/corkboard/script` (zero-config; point at the source, not `.`).

CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check`,
`ty check`, a wikitext-lint smoke test (the skill's `lint` command on a clean
fixture), and the tests on every push/PR, with the
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
