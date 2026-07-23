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
