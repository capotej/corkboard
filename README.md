# DokuWiki on Fly.io

A minimal, production-ready setup for running
[DokuWiki](https://www.dokuwiki.org/) (a flat-file wiki — no database) on
[Fly.io](https://fly.io), with an **opt-in closed-wiki default** and an
auto-provisioned admin account.

DokuWiki stores everything (pages, media, config, plugins) as files on disk.
Because Fly.io machines are ephemeral, this setup mounts a persistent Fly
**volume** and relocates DokuWiki's writable directories onto it so your wiki
survives restarts and redeployments.

## What's here

| File                       | Purpose                                                             |
| -------------------------- | ------------------------------------------------------------------ |
| `Dockerfile`               | PHP 8.2 + Apache image, downloads DokuWiki "Mort" (2026-07-14) |
| `entrypoint.sh`            | Seeds the volume, symlinks `data/` `conf/` `lib/plugins/` `lib/tpl/`, and applies lockdown + bootstraps the admin user when the secret is set |
| `fly.toml`                 | Fly app config, HTTP service on :80, volume mount, VM sizing        |
| `conf-seed/`               | Locked-down config templates (closed ACL, `useacl`, no self-registration, JSON-RPC enabled) |
| `bootstrap-user.php`       | Creates the **admin** and **agent** accounts from Fly secrets (bcrypt, idempotent) |
| `test-api.sh`              | Confirms the agent can authenticate to the JSON-RPC API (`core.whoAmI`) |
| `apache-deny-sensitive.conf` | Blocks direct HTTP access to `data/` `conf/` `bin/` `inc/`        |
| `dokuwiki-opcache.ini`     | Enables + sizes PHP OPcache (preload disabled — see cold-start notes) |
| `.dockerignore`            | Keeps build context lean                                            |

## Prerequisites

- The [`flyctl`](https://fly.io/docs/flyctl/install/) CLI, logged in
  (`flyctl auth login`).

## Deploy

> Replace `dokuwiki` below with your own globally-unique app name, and edit
> `app = "..."` at the top of `fly.toml` to match.

```bash
# 1. Create the app (does NOT deploy yet)
fly launch --no-deploy

# 2. Create the persistent volume that will hold your wiki data.
#    1 GB is plenty for most wikis; grow it later with `fly volumes extend`.
fly volumes create dokuwiki_data --size 1

# 3. (Recommended) set the admin secret BEFORE first deploy to get a closed
#    wiki out of the box — see "Locking it down" below.
fly secrets set DOKU_ADMIN_PASSWORD='choose-a-strong-password' -a dokuwiki

# 4. Deploy
fly deploy
```

The app will be live at `https://<your-app>.fly.dev`.

## Locking it down (closed wiki)

There are **two modes**, selected by whether the `DOKU_ADMIN_PASSWORD` secret
is set **before first boot** (i.e. before the volume is seeded):

### Mode A — closed wiki by default (recommended)

Set the secrets, then deploy:

```bash
fly secrets set \
  DOKU_ADMIN_PASSWORD='choose-a-strong-password' \
  DOKU_ADMIN_USER=admin \
  DOKU_ADMIN_NAME='Wiki Admin' \
  DOKU_ADMIN_EMAIL='you@example.com' \
  DOKU_AGENT_PASSWORD='choose-another-password' \
  DOKU_AGENT_USER=agent \
  DOKU_AGENT_NAME='API Agent' \
  DOKU_AGENT_EMAIL='agent@example.com' \
  -a dokuwiki
```

`DOKU_ADMIN_PASSWORD` is required (it also triggers the locked-down mode).
`DOKU_AGENT_PASSWORD` is optional and provisions the API user described in
[JSON-RPC API + the agent user](#json-rpc-api--the-agent-user) below. The rest
have sensible defaults (`admin`/`Administrator`/`agent`/`API Agent`/
`<user>@localhost`). On first boot the entrypoint:

1. Writes the locked-down config to the volume **once** (never overwriting
   later edits):
   - `conf/acl.auth.php` → `@ALL 0`, `@user 8` (login required to read & write)
   - `conf/local.protected.php` → `useacl=1`, `superuser=@admin`,
     `disableactions=register,resendpwd` (no self-registration / resets), and
     `remote=1` + `remoteuser=@api,@admin` (enables the JSON-RPC API)
   - `conf/local.php` → title + language
2. Creates the accounts in `conf/users.auth.php` (bcrypt-hashed):
   - **admin** (`admin`) — groups `admin,user` (superuser via `@admin`)
   - **agent** (`agent`) — groups `user,api` (read/write pages + API access)
3. Removes `install.php` (no longer needed).

You can then log in as **admin** and create more accounts via
**Admin → User Manager**. The **agent** is for programmatic use via the
JSON-RPC API (see below). The bootstrap is **idempotent**: redeploys never
overwrite an existing user, so password changes you make in the UI survive.

> Why a secret instead of a baked-in default password? A literal password
> would live inside the image (same for every deploy, discoverable via
> `docker history`, and committed to git). The secret is encrypted in Fly and
> never enters the image or the repo.

### Mode B — standard web installer

If you deploy **without** the secret, the image's `conf/` stays pristine and
DokuWiki's web installer (`install.php`) runs normally. Open the site and:

1. Set a title.
2. Pick an **ACL policy** (e.g. "Closed wiki").
3. Create the **Superuser** + password.
4. Save.

You can lock things down afterwards via **Admin → Access Control List
Management** (the two rules are `* @ALL 0` and `* @user 8`).

### Locking down an already-deployed (open) instance

The lockdown config is only seeded onto an **empty** volume. If you already
deployed in Mode B, the quickest path to the baked default is to reset the
volume and redeploy with the secret set:

```bash
fly secrets set DOKU_ADMIN_PASSWORD='...' -a dokuwiki
fly volume destroy dokuwiki_data ...   # destroy the old volume (LOSES content!)
fly volumes create dokuwiki_data --size 1
fly deploy
```

If you want to keep existing content, apply the two ACL rules by hand instead:
**Admin → Access Control List Management**, or edit
`/dokuwiki-persistent/conf/acl.auth.php` over SSH.

## JSON-RPC API + the agent user

The locked-down mode enables DokuWiki's [JSON-RPC API](https://www.dokuwiki.org/devel:xmlrpc)
(endpoint `lib/exe/jsonrpc.php`) and restricts it to the `@api` and `@admin`
groups. Provisioning the **agent** user (groups `user,api`) lets a script read
and write pages over the API using HTTP Basic auth.

### Provisioning

```bash
fly secrets set DOKU_AGENT_PASSWORD='choose-another-password' -a dokuwiki
# optional overrides: DOKU_AGENT_USER, DOKU_AGENT_NAME, DOKU_AGENT_EMAIL
```

On the next boot the agent is created (idempotently) in `conf/users.auth.php`.
Like all seeded config, this only happens cleanly on a fresh volume — on an
existing instance, add the agent via **Admin → User Manager** (put it in the
`user` and `api` groups) or reset the volume.

### Confirm it works

`core.whoAmI` returns the authenticated identity and errors when
unauthenticated, so it's the ideal probe. Run the bundled check:

```bash
AGENT_PASS='<the DOKU_AGENT_PASSWORD value>' ./test-api.sh https://<app>.fly.dev
```

A passing run prints:

```json
{"jsonrpc":"2.0","result":{"login":"agent","name":"API Agent",
 "mail":"agent@localhost","groups":["user","api"],
 "isadmin":false,"ismanager":false},"id":1}
```
…followed by `PASS: 'agent' authenticated and is in the 'api' group.`

Equivalent one-liner:

```bash
curl -s -u "agent:$AGENT_PASS" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"core.whoAmI","id":1}' \
  https://<app>.fly.dev/lib/exe/jsonrpc.php
```

### Reading / writing pages

The agent has the `@user` ACL (`8` = read+edit+create+upload), so:

```bash
# read
curl -s -u "agent:$AGENT_PASS" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"core.getPage","params":["start"],"id":1}' \
  https://<app>.fly.dev/lib/exe/jsonrpc.php

# write
curl -s -u "agent:$AGENT_PASS" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"core.savePage","params":["notes:hello","Hello from the agent\n","api"],"id":1}' \
  https://<app>.fly.dev/lib/exe/jsonrpc.php
```

> Auth is plain HTTP Basic over TLS (Fly terminates HTTPS). Each request
> authenticates freshly — no session needed. To restrict the API further, edit
> `$conf['remoteuser']` in `/dokuwiki-persistent/conf/local.protected.php`.

## How persistence works

`entrypoint.sh` runs on every boot and, for each of
`data`, `conf`, `lib/plugins`, `lib/tpl`:

- If a symlink is already present (subsequent boots) → do nothing.
- Otherwise, seed the volume from the image's stock copy (first boot only),
  then **symlink** the webroot path to the volume.

`conf/` gets one extra step each boot: its **release-default** files
(`dokuwiki.php`, the `*.conf` files, `license.php`, …) are refreshed from the
image so they track the running version, while its **user-managed** files
(`local.php`, `local.protected.php`, `acl.auth.php`, `users.auth.php`,
`plugins.local.php`) are skipped and persist on the volume untouched. This
matters because `conf/` mixes the two: persisting the whole dir *without*
refreshing the defaults froze them at the first-boot release and broke
upgrades — Mort added `$conf['syntax']` to `dokuwiki.php`, but a volume seeded
from the prior release kept the old `dokuwiki.php` (no `'syntax'` key), so the
parser read it as `null` → fatal `TypeError … ModeRegistry … null given`. (No
per-file symlinks anywhere — the whole `conf/` dir is symlinked, like the
others.) User edits made via the web UI (Configuration Manager, ACL Manager,
User Manager) write to `local.php` / `acl.auth.php` / `users.auth.php`, which
are persisted, so they survive redeploys.

This means: **upgrading DokuWiki = rebuild + redeploy**. Your content, user
config, plugins and templates on the volume are untouched; only the core code
(and the release-default config files) are replaced. Plugins under
`lib/plugins/` are also on the volume, so they persist too (but you may want
to re-check plugin compatibility after a major upgrade).

## Suspend/resume & cold-start optimization

`fly.toml` runs `auto_stop_machines = 'suspend'`, `auto_start_machines = true`,
`min_machines_running = 0`. When the wiki is idle, Fly **suspends** the machine
(saving full VM state to disk) and resumes it on the next request.

- **Normal resume (suspend → resume): ~0.7 s.** Restore is from a Firecracker
  snapshot, so the running Apache/PHP process and its already-warm OPcache SHM
  are preserved. `entrypoint.sh` does **not**
  re-run, and the first request after resume is already warm. Measured
  end-to-end (with proxy overhead): ~0.7 s vs ~0.3 s when already running.
  Suspend is a good fit here: the VM is 512 MB (≤ 2 GB), has no swap/schedule/
  GPU, and DokuWiki is flat-file on the volume with no DB pool to break on
  resume.
- **Cold start (~7 s) is the fallback.** A true cold start happens when there's
  no snapshot to resume — after a deploy (a new image invalidates the old
  snapshot), a host migration, or a lost/corrupt snapshot. The changes below
  keep that fallback fast too (~7 s, most of which is Fly's own machine-boot
  floor):
  - **No per-boot recursive `chown` of the webroot.** The Dockerfile already
    chowns `/var/www/html` to `www-data` at build time, so on a fresh container
    the webroot is already correctly owned. A previous version re-`chown -R`'d
    it every boot, which forced overlayfs to copy up ~5 000 files — the single
    biggest cold-start cost (~20 s on a shared-cpu-1x). It's gone.
  - **Bootstrap is skipped on a cold resume.** On a stop/cold boot the
    admin/agent accounts usually already exist in `conf/users.auth.php`, so
    `entrypoint.sh` skips spawning `bootstrap-user.php` (one fewer PHP CLI
    cold start). It runs only on first boot, or if an expected account is
    missing.
  - **OPcache is enabled and sized (preload disabled).** `dokuwiki-opcache.ini`
    keeps OPcache on with sensible memory/limits; the warm OPcache SHM survives a
    suspend/resume, so resumed requests stay fast. `opcache.preload` is OFF on
    purpose: preload compiles files *without executing* them, and several
    DokuWiki files define runtime constants via a top-level `define()` in the
    same file as a class (e.g. `inc/HTTP/HTTPClient.php` does
    `define('HTTP_NL', "\r\n")` beside the `HTTPClient` class). Under preload
    the class is linked into SHM but the `define()` never runs, so at request
    time the constant is undefined → fatal `Undefined constant … HTTP_NL` (PHP 8
    errors on undefined constants). The only cost of disabling it is the first
    request after a true cold start compiling lazily — and with suspend enabled,
    the common path never recompiles anyway.

> Note: Fly's proxy decides *when* to suspend on its own ~few-minutes idle
> loop — there's no `auto_stop_after`/idle-seconds knob in `fly.toml`. For a
> single idle machine with zero traffic it suspends on the next check. The
> `concurrency.soft_limit` only matters with >1 machine.

> Why not `opcache.preload` (or baking a compiled `opcache.file_cache` into the
> image)? Preload was enabled once and disabled again — see the note above; it
> compiles-but-doesn't-execute files, which breaks DokuWiki's
> `define()`-beside-a-class constants. `opcache.file_cache` is the "right"
> mechanism in theory but has a long, unfixed history of segfaults — including in
> symlink-based deployments like this one (we symlink `data/conf/plugins/tpl` to
> the volume) — across PHP 7.4 → 8.4 (see php/php-src#19125). With suspend
> enabled the warm OPcache SHM is preserved across resume anyway, so neither
> mechanism is worth the fragility here.

If you want zero resume latency entirely (at the cost of always running one
machine), set `min_machines_running = 1` in `[http_service]`.

## Backing up

Since everything is on one volume, snapshot the machine:

```bash
fly volumes list                      # find the volume id
fly volumes snapshots list <volume-id> # snapshots are taken daily, kept ~5 days
```

Or pull the data down directly:

```bash
fly ssh sftp get /dokuwiki-persistent/data ./dokuwiki-data-backup
```

## Tweaks

- **Region:** change `primary_region` in `fly.toml` (e.g. `sin`, `fra`, `sjc`).
- **Always-on:** set `min_machines_running = 1` in `[http_service]`.
- **Larger wiki:** bump VM `memory` / `size`, or `fly volumes extend dokuwiki_data --size 5`.
- **Bigger DokuWiki version:** change `DOKUWIKI_VERSION` in the Dockerfile `ARG`.
- **Media uploads off for members:** in `conf-seed/acl.auth.php` change `@user 8`
  to `@user 4` (create = read+edit+create, no upload).
- **Restrict the API further:** edit `$conf['remoteuser']` in
  `conf-seed/local.protected.php` (e.g. `'agent'` instead of `'@api,@admin'`)

## Troubleshooting

- **`No volume ... found`** — you forgot `fly volumes create`. The volume name
  must match `source = "dokuwiki_data"` in `fly.toml`.
- **Can't log in after a "closed" deploy** — confirm the secret was set
  *before* first boot (`fly secrets list -a dokuwiki`). If the volume was
  already seeded in Mode B, the admin wasn't bootstrapped; reset the volume or
  add the user manually.
- **`install.php` says configs are modified** — expected once the lockdown
  config exists. Use the bootstrapped admin (Mode A) or reset the volume.
- **Installer loops / "not writable"** — the entrypoint chowns the webroot to
  `www-data`; if you override the image, keep that user.
- **JSON-RPC returns an error / `403`-style denial** — `remote=1` and
  `remoteuser=@api,@admin` are only seeded onto a fresh volume. On an existing
  instance add them to `/dokuwiki-persistent/conf/local.protected.php`, or put
  your user in the `api` group. The agent is created on next boot if
  `DOKU_AGENT_PASSWORD` is set.
- **Lost data after deploy** — confirm the volume is attached
  (`fly volumes list`) and that `destination` is `/dokuwiki-persistent`.
