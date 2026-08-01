# Corkboard — an agentic DokuWiki for Fly.io

**Corkboard** is an **agentic wiki**: a flat-file [DokuWiki](https://www.dokuwiki.org/)
instance, tuned for [Fly.io](https://fly.io), that's meant to be **read and
written by an AI agent** through its API. Clone it, deploy it, point an agent at it.

## Features

What Corkboard ships with out of the box:

- **Login-only (closed by default)** — anonymous access is denied (`@ALL 0`); reading or writing requires a login. No self-registration, no password resets.
- **JSON-RPC API** — DokuWiki's Remote API is enabled and restricted to the `@api`/`@admin` groups, for programmatic read/write over HTTP Basic auth.
- **All safe upload formats** — ~150 file types allowed (text, source code, data/config, archives, fonts, e-books, …); `html`/`htm` are blocked (XSS vector).
- **Corkboard RPC + action plugin** — a bundled server-side plugin (`plugin.corkboard.*`) that gives the agent single-call answers the core API can't: wanted/orphan pages, unreferenced media, per-page **link health**, and **compare-and-swap** writes (so a surgical edit never silently clobbers a concurrent one), plus **move/rename** (history-preserving, via the move plugin). Its action component re-indexes a namespace's `start` page whenever a page is created or deleted under it, keeping `orphans`/`backlinks` immediately consistent with the nspages auto-index below.
- **Auto-generated namespace indexes (nspages)** — every namespace's `start` page lists its children with a single `<nspages>` tag, so landing-page hubs can't drift as pages come and go. Because nspages emits links through the standard renderer, they register as backlinks — so nspages-listed pages are never false orphans. Bundled, pinned to an upstream commit, and SHA-256-verified in the image.
- **Agent skill** — a stdlib-only Python skill (`skills/corkboard/`) the agent uses to read, write, organize, and garden the wiki — including **surgical** in-place edits and anchor inserts, in-page search, batch edits across pages, **move/rename** of pages & media, and a **wikitext linter** that catches Markdown-and-table rendering breakage before a save. See `skills/corkboard/SKILL.md`.
- **No phone-home** — `updatecheck=0`; the `popularity` plugin is disabled.
- **Gzip over the wire** — Apache (`mod_deflate`) compresses text responses for browsers and the agent; the skill sends `Accept-Encoding: gzip` and decodes it. DokuWiki does no encoding of its own (`gzip_output=0`).
- **Efficient media delivery** — large attachments are streamed by Apache (`mod_xsendfile`), not buffered through a PHP worker; DokuWiki's `xsendfile=2` hands the file off after its ACL check.
- **Clean URLs** — human-readable, shareable paths (`/wiki/syntax`, `/projects/foo/bar`) instead of `/doku.php?id=…`, via `mod_rewrite` rules in server config (not `.htaccess`). DokuWiki emits path-style links (`userewrite=1`, `useslash=1`); legacy ugly URLs still resolve, so no inbound links break.
- **Hardened Apache layer** — no version banner / `TRACE`, security response headers (HSTS gated on Fly's `X-Forwarded-Proto` since Fly terminates TLS), directory listings off, `vendor/` added to the denied dirs, the real client IP recovered from Fly's proxy (`mod_remoteip`), Slowloris caps + a rough DoS heuristic (`mod_reqtimeout`/`mod_evasive`), and unused modules (`autoindex status info cgi userdir`) disabled.
- **Flat-file on a Fly volume** — no database; survives restarts and redeploys; ~0.7 s warm resume, ~7 s cold start.

## What this is

Most wikis are written by humans in a browser. Corkboard is written (mostly) by
an **agent** over DokuWiki's [JSON-RPC Remote API](https://www.dokuwiki.org/devel:xmlrpc)
(`core.*` methods, HTTP Basic auth). The bundled skill is the agent's transport;
the `agent` user (groups `user,api`) is its identity. Humans still have the full
web UI (log in as `admin`), but the first-class workflow is programmatic: an
agent creates and surgically edits pages, uploads files, and gardens links.

Because DokuWiki is flat-file, a single persistent Fly volume holds everything.
Fly machines are ephemeral, so `entrypoint.sh` relocates DokuWiki's writable
directories onto that volume on every boot — see
[How persistence works](#how-persistence-works).

## What's here

| File / dir                  | Purpose                                                                  |
| --------------------------- | ----------------------------------------------------------------------- |
| `Dockerfile`                | PHP 8.5.8 + Apache image; downloads a pinned DokuWiki release, verified against a pinned SHA-256 |
| `entrypoint.sh`             | Wires the volume, applies the lockdown by default, bootstraps `admin`/`agent` from secrets |
| `fly.toml`                  | Fly app config (**template — rename `app`**), HTTP service, volume mount, VM sizing |
| `conf-seed/`                | Locked-down config templates: closed ACL, `useacl`, JSON-RPC, disabled plugins, broad upload allowlist |
| `bootstrap-user.php`        | Creates the `admin` and `agent` accounts from Fly secrets (bcrypt, idempotent) |
| `skills/corkboard/`         | The agent skill: a Python JSON-RPC client (`script/corkboard.py`) + `SKILL.md` |
| `corkboard-plugin/`         | Server-side DokuWiki plugin (`plugin.corkboard.*`): wanted/orphans/media-orphans, per-page link health, compare-and-swap writes, history-preserving move/rename, and an action hook that re-indexes namespace `start` pages on page create/delete (keeps `orphans`/`backlinks` fresh with nspages) |
| `apache-deny-sensitive.conf`| Blocks direct HTTP access to `data/` `conf/` `bin/` `inc/` `vendor/`, and sets `Options -Indexes -ExecCGI` |
| `apache-hardening.conf`    | Server fingerprint/`TRACE` off, security response headers (HSTS gated on `X-Forwarded-Proto`), Slowloris caps |
| `remoteip.conf`            | Recover the real client IP from Fly's proxy (`mod_remoteip` on `Fly-Client-IP`) |
| `evasive.conf`             | Rough per-process DoS rate-limit (`mod_evasive`); loose thresholds + localhost whitelist |
| `compression.conf`         | Gzip text responses over the wire (`mod_deflate`); DokuWiki stays uncompressed (`gzip_output=0`) |
| `xsendfile.conf`           | Offload media delivery from PHP to Apache (`mod_xsendfile`); `XSendFilePath` scoped to `data/` |
| `rewrite.conf`             | Clean URLs (`mod_rewrite`) in server config (not `.htaccess`); pairs with `userewrite=1` + `useslash=1`. Legacy ugly URLs still resolve |
| `dokuwiki-opcache.ini`      | Sizes PHP OPcache (preload disabled — see cold-start notes)             |
| `.dockerignore`             | Keeps build context lean                                                 |

## Prerequisites

- The [`flyctl`](https://fly.io/docs/flyctl/install/) CLI, logged in
  (`flyctl auth login`).
- To drive it with an agent: the `corkboard` skill available to your agent and
  the three `CORKBOARD_*` env vars set (see
  [The agent + the bundled skill](#the-agent--the-bundled-skill)).

## Installation

Corkboard is a clone-and-deploy template. The `app` name in `fly.toml` is a
placeholder — rename it to something globally unique, then deploy.

```bash
# 1. Clone and enter the repo (any directory name you like)
git clone <this-repo-url> my-corkboard
cd my-corkboard

# 2. Pick a globally-unique app name (it becomes https://<app>.fly.dev) and set
#    it in fly.toml. Note primary_region there too (iad by default).
$EDITOR fly.toml        # app = 'corkboard-example'  ->  app = 'my-corkboard'

# 3. Register the app (does NOT deploy yet). Uses the name + region from fly.toml.
fly launch --no-deploy

# 4. Create the persistent volume that holds the whole wiki.
#    *** It MUST be in the SAME region as primary_region (iad here). ***
#    Once the app exists (step 3) flyctl defaults a new volume to the app's
#    primary region, but pass --region explicitly to be safe:
fly volumes create dokuwiki_data --size 1 --region iad

# 5. Set the two REQUIRED secrets (admin + agent — the agent is mandatory):
fly secrets set \
  CORKBOARD_ADMIN_PASS='choose-a-strong-password' \
  CORKBOARD_AGENT_PASS='choose-another-password' \
  -a my-corkboard

# 6. Deploy
fly deploy
```

Your wiki is live at `https://my-corkboard.fly.dev`.

> **Volume region matters.** A Fly machine can only mount a volume in its own
> region. `primary_region = 'iad'` in `fly.toml` sets the machine's region; the
> volume must match. If you change `primary_region`, create the volume with
> `--region <same>`. (The volume's region can't be set in `fly.toml` — it's
> chosen at `fly volumes create` time.)

### Secrets reference

| Secret                 | Required | Purpose |
| ---------------------- | -------- | ------- |
| `CORKBOARD_ADMIN_PASS` | **yes**  | Admin password — the `admin` account (superuser via `@admin`). |
| `CORKBOARD_AGENT_PASS` | **yes**  | Agent password — the `agent` account (JSON-RPC API). The agent is mandatory; this is an agentic wiki. |

If either is missing the container exits at boot with
`FATAL: required secret(s) not set: …`. Everything else about the accounts is
hardcoded — usernames (`admin`/`agent`), display names, and emails
(`admin@localhost`/`agent@localhost`).

The bootstrap is **idempotent**: redeploys never overwrite an existing user, so
password changes you make in the UI survive.

> Why a secret instead of a baked-in default password? A literal password would
> live inside the image (same for every deploy, discoverable via `docker
> history`, committed to git). The secret is encrypted in Fly and never enters
> the image or the repo.

## The agent + the bundled skill

The `agent` account (groups `user,api`) is the identity an agent uses to talk to
the wiki over JSON-RPC. It's created from the required `CORKBOARD_AGENT_PASS`
secret on first boot in `conf/users.auth.php`. It has **read + update + delete** (delete is attic-recoverable; details
in `skills/corkboard/SKILL.md`).

### Install the skill

The `corkboard` skill ships in this repo (`skills/corkboard/`): a stdlib Python
skill (`script/corkboard.py` + `SKILL.md`) that calls the `core.*` JSON-RPC
methods over HTTP Basic auth — one transport for pages **and** media. Add it to
your agent with:

```bash
npx skills add capotej/corkboard --skill corkboard
```

### Tests & CI

The skill helper ships a **stdlib-only** test suite (no live wiki, no
credentials) covering its pure-string logic — exact-match edits, anchor
placement, CAS revision handling. Run it locally:

```bash
python3 tests/test_corkboard_logic.py
```

CI (`.github/workflows/ci.yml`) runs that suite plus a lint pass (`ruff check`),
a format pass (`ruff format --check`), a type-check pass (`ty`), and a
wikitext-lint smoke test (the `lint` command on a clean fixture) on every push
and pull request. The toolchain — `python`,
`ruff`, `ty` — is declared in `mise.toml` and installed in CI by `mise-action`,
so local and CI run identical versions:

```bash
mise install                          # python, ruff, ty
ruff check .                          # lint
ruff format --check .                 # format
ty check skills/corkboard/script      # type-check the helper
python3 tests/test_corkboard_logic.py # tests (incl. the linter)
```

This isn't a Python project (no `pyproject.toml` / `uv`); ruff config lives in
`ruff.toml`. See `rfcs/2026-07-25_ci-for-corkboard-skill.md`.

### Auth env vars the skill needs

The skill never hardcodes credentials — it reads three env vars. **They map 1:1
to the secrets you set at install time:**

| Skill env var    | Set it to                                | = install secret                              |
| ---------------- | ---------------------------------------- | --------------------------------------------- |
| `CORKBOARD_URL`  | `https://<app>.fly.dev` (no trailing `/`)| (your deployed URL)                           |
| `CORKBOARD_USER` | the agent username                       | hardcoded `agent`                             |
| `CORKBOARD_PASS` | the agent password                       | `CORKBOARD_AGENT_PASS`                        |

```bash
export CORKBOARD_URL=https://my-corkboard.fly.dev
export CORKBOARD_USER=agent
export CORKBOARD_PASS='<the CORKBOARD_AGENT_PASS value>'
```

The agent then uses the skill to read/write pages, upload media, search, and
garden links — see `skills/corkboard/SKILL.md` for the full command reference.

### What the agent can do (why it's "agentic")

Because the agent is a first-class writer — not a human pasting into a browser —
the skill is built around **small, safe, targeted edits** rather than whole-page
rewrites (the bulk of real wiki work):

- **Surgical edits** — replace an exact snippet *only if it occurs once*; a
  zero- or multi-match aborts and saves nothing, so an edit never lands in the
  wrong place.
- **Anchor inserts** — add content under a heading, or after/before a specific line.
- **In-page search** with line numbers, to find the right anchor before editing.
- **Batch edits** across several pages from one plan, with a per-page report.
- **Move/rename** (`move`) a page, media file, or whole namespace — history-preserving (the attic relocates) with every backlink rewritten, via the move plugin.
- **Concurrency-safe writes** — every surgical write is a compare-and-swap: if
  the page changed under the agent, it refuses to clobber and says so.
- **Link health after every write** — right after a save, the agent is told if it
  just created a broken link, instead of finding out later in a `wanted` report.
- **A wikitext linter** (`lint` / `lint-all`) that catches the formatting
  mistakes agents make most — Markdown that renders as literal text,
  mixed-separator or indented tables that render as code blocks, list
  continuations that render as code blocks, and namespace-relative links —
  **before** the page is saved, with `--fix` for the auto-fixable ones. It's
  pure string analysis (no wiki needed), so it runs on a local draft or in CI
  too. See [skills/corkboard/SKILL.md](skills/corkboard/SKILL.md).

The last two lean on the bundled Corkboard RPC plugin (`plugin.corkboard.cas`
and `.linkhealth`), which answers in a single server-side call.

### Pointing your agent at the wiki (AGENTS.md)

The `corkboard` skill is the transport **and** carries the generic wiki hygiene
(keep pages linked / no orphans, reference uploaded media, cite sources,
experiment in `playground:` — see `skills/corkboard/SKILL.md`). So your
`AGENTS.md` only needs to add what's project-specific: which namespace to use
and what goes on which page. For example, a research-notes wiki:

```markdown
## Notes wiki
Lab notes and run records live on the Corkboard wiki. Use the `corkboard` skill for all interaction. Be proactive — add or update `ml:` pages when you learn something durable; don't gate on asking.

- **Namespace:** `ml:` (index `ml:start`). Per-run pages under `ml:runs:<name>`; concepts under `ml:concepts:<name>`; link each from its index.
- **Log gotchas/lessons** into `ml:lessons` (a running log), not just chat.
- **Every training run** gets its own `ml:runs:<name>` page (config, results, curve, artifact paths); link from `ml:results`.
- **Upload charts/transcripts as media** (skill `media-upload`) and embed them — don't paste huge blobs inline.
```

That's all `AGENTS.md` needs to add — a namespace and a page-per-thing pattern.
The generic hygiene (no orphans, citing sources, playground for experiments)
already lives in the skill; don't duplicate it here.

### Confirm the API works

```bash
curl -s -u "$CORKBOARD_USER:$CORKBOARD_PASS" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"core.whoAmI","id":1}' \
  "$CORKBOARD_URL/lib/exe/jsonrpc.php"
```

A working call returns the agent's identity (`groups` includes `user` and `api`).

> Auth is plain HTTP Basic over TLS (Fly terminates HTTPS). To restrict the API
> further, edit `$conf['remoteuser']` in `conf-seed/local.protected.php`.

## Defaults we ship (security posture)

Corkboard ships **closed and locked down** — you don't configure this, it's the
default. On every boot the entrypoint writes (and re-syncs) this config:

- **Closed wiki** — `conf/acl.auth.php`: `@ALL 0` (anonymous gets nothing),
  `@user 8` (logged-in users read+edit+create+upload).
- **ACL on** — `useacl=1`, `superuser=@admin`.
- **No self-registration / resets** — `disableactions=register,resendpwd`.
- **JSON-RPC API on**, restricted to the `@api` and `@admin` groups (`remote=1`,
  `remoteuser=@api,@admin`) — i.e. the `agent` and `admin`.
- **No phone-home** — `updatecheck=0` (DokuWiki won't fetch update.dokuwiki.org);
  the `popularity` plugin is disabled too.
- **Unused plugins disabled** — `popularity`, `authpdo`, `authldap`, `authad`
  (via `plugins.local.php`); `authplain` stays as the active auth backend.
- **Broad upload allowlist** — `mime.local.conf` adds ~150 types (text, source
  code, data/config, archives, fonts, e-books, …). `html`/`htm` are **not**
  enabled (XSS vector).
- **No web installer** — `install.php` is removed every boot; there is no
  open/first-run mode.
- **Verified download** — the DokuWiki tarball is checked against a pinned
  SHA-256 at build time; a mismatch fails the build.
- **Defense in depth** — Apache denies direct HTTP access to `data/`, `conf/`,
  `bin/`, `inc/` (on top of DokuWiki's own `.htaccess`).

`local.protected.php` (the lockdown) is re-synced from the image on **every**
boot, so changes you make to `conf-seed/` apply on the next deploy. The other
seed files (`local.php`, `acl.auth.php`, `plugins.local.php`, `mime.local.conf`)
are written once onto an empty volume and never clobbered — so edits via the web
UI survive. See [Upgrading & re-seeding](#upgrading--re-seeding).

## Configuring DokuWiki

After install, you configure the wiki the normal DokuWiki way — **through the
Admin UI, not by redeploying.** Changes are written to the user-managed files on
the volume (`local.php`, `acl.auth.php`, `users.auth.php`, `plugins.local.php`,
`mime.local.conf`), which are seeded once and never clobbered, so they survive
every redeploy.

| To change… | Do this (persists, no redeploy) |
| --- | --- |
| Title, language, and most settings | **Admin → Configuration Manager** |
| Who can read / write / upload | **Admin → Access Control List Management** |
| Users and groups (add accounts, reset passwords) | **Admin → User Manager** |
| Plugins and templates (install / update / remove) | **Admin → Extension Manager** |
| Allowed upload types | edit `conf/mime.local.conf` on the volume (over SSH) |

**You only need to re-deploy when the image itself changes:**

- a **DokuWiki upgrade** — bump `DOKUWIKI_VERSION` + `DOKUWIKI_SHA256` (see
  [Upgrading & re-seeding](#upgrading--re-seeding)); or
- edits to **image-baked files** — the `Dockerfile`, anything in `conf-seed/`,
  `entrypoint.sh`, `bootstrap-user.php`, `dokuwiki-opcache.ini`, the
  `corkboard` skill, or the `corkboard-plugin/` RPC plugin.

> **One exception — the lockdown.** `conf/local.protected.php` (the closed-ACL /
> no-self-registration / JSON-RPC lockdown) is **re-synced from `conf-seed/` on
> every boot**, so you can't change it from the web UI — your edit would be
> overwritten. To change the lockdown itself, edit
> `conf-seed/local.protected.php` and re-deploy. (Deliberate: it keeps the
> security baseline image-managed.)

## How persistence works

`entrypoint.sh` runs on every boot and, for each of `data`, `conf`,
`lib/plugins`, `lib/tpl`:

- If a symlink is already present (subsequent boots) → do nothing.
- Otherwise, seed the volume from the image's stock copy (first boot only),
  then **symlink** the webroot path to the volume.

`conf/` refreshes its **release-default** files (`dokuwiki.php`, `*.conf`,
`license.php`, …) from the image every boot so they track the running version,
while its **user-managed** files (`local.php`, `local.protected.php`,
`acl.auth.php`, `users.auth.php`, `plugins.local.php`) persist untouched.
`lib/plugins/` and `lib/tpl/` likewise refresh their **bundled** entries from the
image each boot; entries you installed via the Extension Manager persist.

(Persisting the whole `conf/` dir *without* that refresh once froze the defaults
at the first-boot release and broke an upgrade — Mort added `$conf['syntax']` to
`dokuwiki.php`, but an old volume kept the old file, so the parser read `null` →
fatal `TypeError … ModeRegistry … null given`. The refresh fixes it.)

## Upgrading & re-seeding

Upgrading = **bump two values in the Dockerfile, then `fly deploy`.** For an
instance whose volume is already seeded, your content, users, ACLs, and config
survive automatically — the entrypoint is built for this.

### Steps

```dockerfile
ARG DOKUWIKI_VERSION=<new-version>          # bump this
ARG DOKUWIKI_SHA256=<recomputed sha256>    # AND this (the URL is derived from VERSION)
```

Recompute the hash from the new tarball, then build & push the new image:

```bash
curl -sL "https://download.dokuwiki.org/src/dokuwiki/dokuwiki-<new-version>.tgz" | sha256sum
fly deploy
```

**Under `auto_stop_machines = 'suspend'`, `fly deploy` (+ `fly machine restart`)
is not a reliable way to apply the new image** — the machine can resume its
stale rootfs instead of cold-booting, so the upgrade silently never takes effect
(the entrypoint doesn't re-run; bundled plugins/config aren't refreshed). To
**guarantee** the new image boots, destroy and recreate the machine. The volume
(`dokuwiki_data`) is separate from the machine and is **not** destroyed, so your
pages, users, ACLs, and config all carry over:

```bash
fly machine list -a <app>                       # find the machine id
fly machine destroy <machine-id> -a <app>       # dokuwiki_data persists — verify with `fly volumes list`
fly deploy -a <app>                             # creates a fresh machine that cold-boots the new image
```

This applies to **any** image change — bundled or third-party plugin updates,
`entrypoint.sh` edits, DokuWiki core bumps, base-image/PHP changes — not just
version upgrades. Image/plugin updates are infrequent, and the volume is
preserved, so recreating the machine is cheap insurance.

### What happens on the upgrade boot

Once the machine actually cold-boots the new image (use the destroy-and-recreate
step above — a plain `fly deploy` + `fly machine restart` does **not** reliably
cold-start under suspend), `entrypoint.sh` re-runs against the **existing** volume:

| What | On a seeded volume at upgrade |
| --- | --- |
| `data/` (pages, media, attic, meta, cache) | Untouched — never refreshed. |
| `conf/` release-defaults (`dokuwiki.php`, `*.conf`, `license.php`) | Refreshed from the new image. |
| `conf/` user-managed (`local.php`, `acl.auth.php`, `users.auth.php`, `plugins.local.php`) | Preserved — title, ACLs, users, plugin-disables survive. |
| `conf/local.protected.php` (lockdown) | Re-synced from `conf-seed/` every boot. |
| `lib/plugins` + `lib/tpl` **bundled** entries | Refreshed from the new image. |
| `lib/plugins` + `lib/tpl` **user-installed** entries | Preserved — see watch-outs. |
| `admin` / `agent` accounts | Idempotent bootstrap sees they exist → skipped. |
| Fly secrets | Live on the app, not the volume → unaffected. |

So: new core code + new bundled plugins + new conf-defaults come from the image;
everything you created or configured is carried over.

### Watch-outs

- **Third-party plugins/templates** installed via the Extension Manager persist
  *as-is* — they are **not** upgraded and may not be compatible with the new
  release. After deploy, re-check/update them in **Admin → Extension Manager**.
  (Bundled ones always track the release.)
- The **first request after the destroy-and-recreate is a ~7 s cold start**;
  it returns to ~0.7 s resume once idle. (A plain `fly deploy` under suspend may
  instead *resume* the stale rootfs — which is exactly why the recreate step above
  is required.)
- Don't keep manual edits in the conf **release-default** files on the volume —
  the refresh overwrites them. Edit the user-managed files (or `conf-seed/`)
  instead.
- **Point/security releases** (the `a`, `b` suffixes) are drop-in — no data
  migration (DokuWiki is flat-file). A *major* release occasionally wants a
  quick admin login, but there's no DB migration step.

### Verify after deploy

```bash
fly logs    # look for "[entrypoint] JSON-RPC self-test ..." + the OPcache guard
```

Or query the running release over the API (with the skill env vars exported —
see [The agent + the bundled skill](#the-agent--the-bundled-skill)):

```bash
curl -s -u "$CORKBOARD_USER:$CORKBOARD_PASS" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"core.getWikiVersion","id":1}' \
  "$CORKBOARD_URL/lib/exe/jsonrpc.php"     # e.g. "2026-07-14a"
```

### Re-applying seed defaults to an existing volume

`local.protected.php` re-syncs every boot, so lockdown changes in `conf-seed/`
apply on the next deploy. The other seed files (`local.php`, `acl.auth.php`,
`plugins.local.php`, `mime.local.conf`) are write-once; to force them back onto
an existing volume, edit the files under `/dokuwiki-persistent/conf/` over SSH,
or reset the volume:

```bash
fly volumes destroy <volume-id>      # LOSES content!
fly volumes create dokuwiki_data --size 1 --region iad
fly deploy
```

## Suspend/resume & cold start

`fly.toml` runs `auto_stop_machines = 'suspend'`, `auto_start_machines = true`,
`min_machines_running = 0`. When idle, Fly **suspends** the machine (full VM
state to disk) and resumes it on the next request.

- **Resume (suspend → resume): ~0.7 s** — restored from a Firecracker snapshot,
  so Apache/PHP and its warm OPcache shared memory are preserved; `entrypoint.sh`
  does *not* re-run. Suspend fits well: a 512 MB VM, no DB connection pool to
  break, flat files on the volume.
- **Cold start (~7 s) is the fallback** — after a host migration, a lost snapshot,
  or (reliably) a machine recreate. A plain `fly deploy` does **not** reliably
  cold-start a new image under suspend (the machine may resume its stale rootfs),
  so image/plugin/DokuWiki updates require destroy + recreate — see
  [Upgrading & re-seeding](#upgrading--re-seeding). Kept fast by: no per-boot
  recursive `chown` of the webroot (the Dockerfile chowns it at build time),
  skipping `bootstrap-user.php` when the accounts already exist, and OPcache
  sized with preload **off**. (Preload compiles-without-executing, which breaks
  DokuWiki's `define()`-beside-a-class constants — e.g. the `HTTP_NL` constant
  in `inc/HTTP/HTTPClient.php`. With suspend enabled, the warm OPcache is
  preserved across resume anyway, so preload isn't worth the fragility.)

> Want zero resume latency (at the cost of always running one machine)? Set
> `min_machines_running = 1` in `[http_service]`.

## Backing up

Everything is on one volume:

```bash
fly volumes list                         # find the volume id
fly volumes snapshots list <volume-id>   # daily snapshots, kept ~5 days
# or pull the data down directly:
fly ssh sftp get /dokuwiki-persistent/data ./corkboard-backup
```

## Tweaks

- **Region:** change `primary_region` in `fly.toml` — **and** create the volume
  in the same region (`fly volumes create dokuwiki_data --size 1 --region <r>`).
- **Always-on:** set `min_machines_running = 1` in `[http_service]`.
- **Larger wiki:** bump VM `memory` / `size`, or `fly volumes extend dokuwiki_data --size 5`.
- **Bigger DokuWiki version:** change `DOKUWIKI_VERSION` **and** `DOKUWIKI_SHA256`
  in the Dockerfile (recompute the hash).
- **Uploads off for members:** in `conf-seed/acl.auth.php` change `@user 8` to
  `@user 4` (create = read+edit+create, no upload).
- **Restrict the API further:** edit `$conf['remoteuser']` in
  `conf-seed/local.protected.php` (e.g. `'agent'` instead of `'@api,@admin'`).
- **Enable html/htm uploads:** add them to `conf-seed/mime.local.conf` **and** set
  `$conf['iexssprotect'] = 0` in `local.protected.php` — only with fully trusted
  uploaders (XSS risk).

## Troubleshooting

- **`No volume ... found`** — you forgot `fly volumes create`. The volume name
  must match `source = "dokuwiki_data"` in `fly.toml`.
- **Volume in the wrong region / "no volume in region"** — recreate the volume in
  the same region as `primary_region` (`fly volumes create dokuwiki_data --size 1
  --region <primary_region>`). A machine can only mount a volume in its own region.
- **Container exits / won't start** — both `CORKBOARD_ADMIN_PASS` and
  `CORKBOARD_AGENT_PASS` are required. If either is unset the entrypoint prints
  `FATAL: required secret(s) not set: …` and exits. Set both and redeploy.
- **Can't log in** — confirm `CORKBOARD_ADMIN_PASS` was set before first boot
  (`fly secrets list -a <app>`). If the volume was seeded without it, set the
  secret and reset the volume (or add the user via `fly ssh console`).
- **Agent can't authenticate / API `403`** — `CORKBOARD_AGENT_PASS` provisions the
  `agent`; `remote=1` / `remoteuser=@api,@admin` live in `local.protected.php`
  (re-synced every boot). Make sure `CORKBOARD_USER` (`agent`) / `CORKBOARD_PASS`
  match the agent's username/password.
- **Permission errors / "not writable"** — Apache runs as `www-data`; if you
  override the image, keep that user and the webroot ownership.
- **Lost data after deploy** — confirm the volume is attached
  (`fly volumes list`) and `destination` is `/dokuwiki-persistent`.
