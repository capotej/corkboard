# DokuWiki on Fly.io

A minimal, production-ready setup for running
[DokuWiki](https://www.dokuwiki.org/) (a flat-file wiki — no database) on
[Fly.io](https://fly.io), with a **closed-wiki default** and an admin
account auto-provisioned from a required Fly secret.

DokuWiki stores everything (pages, media, config, plugins) as files on disk.
Because Fly.io machines are ephemeral, this setup mounts a persistent Fly
**volume** and relocates DokuWiki's writable directories onto it so your wiki
survives restarts and redeployments.

## What's here

| File                       | Purpose                                                             |
| -------------------------- | ------------------------------------------------------------------ |
| `Dockerfile`               | PHP 8.5.8 + Apache image, downloads DokuWiki "Mort" (2026-07-14) |
| `entrypoint.sh`            | Seeds the volume, symlinks `data/` `conf/` `lib/plugins/` `lib/tpl/`, applies the lockdown by default, and bootstraps the admin from the required `DOKU_ADMIN_PASSWORD` secret |
| `fly.toml`                 | Fly app config, HTTP service on :80, volume mount, VM sizing        |
| `conf-seed/`               | Locked-down config templates (closed ACL, `useacl`, no self-registration, JSON-RPC enabled) |
| `bootstrap-user.php`       | Creates the **admin** and **agent** accounts from Fly secrets (bcrypt, idempotent) |
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

# 3. REQUIRED: set the admin password secret BEFORE first deploy. The wiki
#    ships closed by default and the container won't start without it.
fly secrets set DOKU_ADMIN_PASSWORD='choose-a-strong-password' -a dokuwiki

# 4. Deploy
fly deploy
```

The app will be live at `https://<your-app>.fly.dev`.

## Locking it down (closed wiki)

The wiki ships **closed by default**. On every boot the entrypoint writes the
lockdown config into the volume, bootstraps the admin account from a Fly
secret, and removes the web installer — there is no open/web-installer mode.

`DOKU_ADMIN_PASSWORD` is **required**: the admin password can't be baked into
the image (it would leak via git + `docker history`), so it must be provided as
a Fly secret. If it's missing the entrypoint fails fast with a clear error
(visible in `fly logs`) instead of coming up as an open wiki.
`DOKU_AGENT_PASSWORD` is optional and provisions the API user described in
[JSON-RPC API + the agent user](#json-rpc-api--the-agent-user) below.

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

Only `DOKU_ADMIN_PASSWORD` is required; the rest have sensible defaults
(`admin`/`Administrator`/`agent`/`API Agent`/`<user>@localhost`). On first boot
the entrypoint:

1. Writes the locked-down config to the volume **once** (never overwriting
   later edits):
   - `conf/acl.auth.php` → `@ALL 0`, `@user 8` (login required to read & write)
   - `conf/local.protected.php` → `useacl=1`, `superuser=@admin`,
     `disableactions=register,resendpwd` (no self-registration / resets),
     `remote=1` + `remoteuser=@api,@admin` (enables the JSON-RPC API), and
     `updatecheck=0` (no phone-home update/popularity checks)
   - `conf/local.php` → title + language
   - `conf/plugins.local.php` → disables the bundled `popularity`,
     `authpdo`, `authldap`, and `authad` plugins (anonymous-stats + unused
     auth backends off by default; `authplain` stays enabled)
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

### Re-applying seed defaults to an existing volume

`local.protected.php` is re-synced from the image on every boot, so lockdown
changes in `conf-seed/` take effect on the next deploy automatically. The other
seed files (`local.php`, `acl.auth.php`, `plugins.local.php`, `mime.local.conf`)
are written only onto an **empty** volume and never clobbered, so edits you make
via the web UI (or by hand) survive. To force the current seed defaults back
onto an existing volume, edit the files under `/dokuwiki-persistent/conf/` over
SSH, or reset the volume:

```bash
fly volumes destroy <volume-id>      # LOSES content!
fly volumes create dokuwiki_data --size 1
fly deploy
```

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
unauthenticated, so it's the ideal probe:

```bash
curl -s -u "agent:$AGENT_PASS" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"core.whoAmI","id":1}' \
  https://<app>.fly.dev/lib/exe/jsonrpc.php
```

A successful call returns the authenticated user — confirming the credentials
work and that the account is in the `api` group the API is restricted to:

```json
{"jsonrpc":"2.0","result":{"login":"agent","name":"API Agent",
 "mail":"agent@localhost","groups":["user","api"],
 "isadmin":false,"ismanager":false},"id":1}
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

`lib/plugins/` and `lib/tpl/` get the same treatment for their **bundled**
entries: each boot the entries that ship with the release (the `config`,
`authldap`, `usermanager`, … plugins; the `doku` template, …) are refreshed
from the image so they track the running version, while entries present on the
volume but *not* in the image — i.e. plugins/templates you installed via the
Extension Manager — are left untouched and persist.

This means: **upgrading DokuWiki = rebuild + redeploy**. Your content, user
config, and **user-installed** plugins/templates on the volume are untouched;
only the core code, the release-default config files, and the **bundled**
plugins/templates are replaced (refreshed from the new image). Third-party
plugins you installed via the Extension Manager persist, so after a major
upgrade you may want to re-check *their* compatibility — the bundled ones stay
in sync with the running release automatically.

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
> the volume) — across PHP 7.4 → 8.5 (see php/php-src#19125). With suspend
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
- **Bigger DokuWiki version:** change `DOKUWIKI_VERSION` in the Dockerfile `ARG`
  **and** the pinned `DOKUWIKI_SHA256` (recompute with
  `curl -sL <DOKUWIKI_URL> | sha256sum`). The download is always verified against
  that checksum; a mismatch fails the build.
- **Media uploads off for members:** in `conf-seed/acl.auth.php` change `@user 8`
  to `@user 4` (create = read+edit+create, no upload).
- **Restrict the API further:** edit `$conf['remoteuser']` in
  `conf-seed/local.protected.php` (e.g. `'agent'` instead of `'@api,@admin'`)

## Troubleshooting

- **`No volume ... found`** — you forgot `fly volumes create`. The volume name
  must match `source = "dokuwiki_data"` in `fly.toml`.
- **Container exits / won't start** — `DOKU_ADMIN_PASSWORD` is required. If
  it's unset the entrypoint prints `FATAL: DOKU_ADMIN_PASSWORD is not set.` and
  exits. Set it (`fly secrets set DOKU_ADMIN_PASSWORD='...' -a <app>`) and
  redeploy.
- **Can't log in** — confirm `DOKU_ADMIN_PASSWORD` was set before first boot
  (`fly secrets list -a <app>`). The admin is bootstrapped only when the secret
  is present; if the volume was seeded without it, set the secret and reset the
  volume (or add the user via `fly ssh console`).
- **Permission errors / "not writable"** — Apache runs as `www-data`; if you
  override the image, keep that user and the webroot ownership.
- **JSON-RPC returns an error / `403`-style denial** — `remote=1` and
  `remoteuser=@api,@admin` live in `local.protected.php`, which is re-synced
  from the image every boot, so they're always in effect. If you've restricted
  `remoteuser` further, make sure your user is in an allowed group. The agent
  is created on next boot if `DOKU_AGENT_PASSWORD` is set.
- **Lost data after deploy** — confirm the volume is attached
  (`fly volumes list`) and that `destination` is `/dokuwiki-persistent`.
