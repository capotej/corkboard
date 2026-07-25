# X-Sendfile for media delivery (`mod_xsendfile` + DokuWiki `xsendfile`)

**Date:** 2026-07-25
**Status:** Implemented

## Goal

Hand media-file delivery off from PHP to Apache using **`mod_xsendfile`**, so
large attachments viewed/downloaded by browsers are streamed by the kernel
(zero-copy `sendfile`) instead of being buffered and streamed through a PHP
worker. Concretely, three build/config changes:

1. Install + enable `mod_xsendfile` (`libapache2-mod-xsendfile` from apt) and a
   one-file `xsendfile.conf` with `XSendFile On` and a tightly-scoped
   `XSendFilePath` whitelist.
2. Set `$conf['xsendfile'] = 2` (the `X-Sendfile` variant) in
   `local.protected.php`, locked.
3. No skill change — this is server-side plumbing.

## Motivation

Today media downloads go through `lib/exe/fetch.php`, which runs as PHP: it does
ACL checks, then streams the file with `readfile()` / range handling inside a PHP
worker. For small images that is fine, but for large attachments — PDFs, videos,
archives, big images — the PHP worker holds the connection for the whole
transfer and buffers bytes through userland. Under the prefork MPM with a limited
worker count, a few concurrent large downloads can exhaust the PHP pool.

`mod_xsendfile` changes that: the script sets an `X-Sendfile: /path` header and
exits immediately, and Apache serves the file using kernel `sendfile` (or mmap),
setting correct `ETag`/`Last-Modified`/range headers as if the file were static.
The PHP worker is freed the instant delivery begins.

This repo already layers Apache-side optimizations (gzip via `mod_deflate`,
OPcache sizing, defense-in-depth `Deny` rules); offloading media delivery is the
same class of change — move the heavy lifting out of PHP and into the web server.

**Scope is browser-facing media, deliberately.** The `corkboard` agent fetches
media through `core.getMedia` (JSON-RPC, base64-encoded body) — that path is
unaffected by `X-Sendfile` and is not what this optimizes. The win is for humans
opening a large attachment in the wiki UI. (If the agent ever switches to direct,
authenticated media URLs, it would benefit too — out of scope here.)

## Background

### How DokuWiki's `xsendfile` setting works

`$conf['xsendfile']` (default `0`) selects which offload header `http_sendfile()`
emits. From `inc/httputils.php`:

```php
function http_sendfile($file) {
    global $conf;
    if      ($conf['xsendfile'] == 1) { header("X-LIGHTTPD-send-file: $file"); ob_end_clean(); exit; }
    elseif  ($conf['xsendfile'] == 2) { header("X-Sendfile: $file");           ob_end_clean(); exit; }  // Apache mod_xsendfile
    elseif  ($conf['xsendfile'] == 3) { header("X-Accel-Redirect: " . http_xaccel_url($file)); ob_end_clean(); exit; }  // nginx
}
```

So `2` is the Apache variant. When set, `http_sendfile()` emits the header,
clears the output buffer, and `exit`s — Apache takes over delivery.

**Where it is called** (the part that makes this worth doing): media downloads
flow `lib/exe/fetch.php` → `sendFile()` (`inc/fetch.functions.php:93`) →
`http_sendfile($file)`. So enabling `xsendfile = 2` hands **every media download**
to Apache, not just cache files. (`http_cached()` also calls `http_sendfile()` for
the CSS/JS cache, but those are small and incidental.)

### How `mod_xsendfile` works

From the module docs ([tn123.org/mod_xsendfile](https://tn123.org/mod_xsendfile)):

- `XSendFile On` enables processing of the `X-Sendfile` response header; when
  present, the module discards the script's output and serves the named file via
  Apache internals (`sendfile`/mmap), with `ETag`/`Last-Modified`/range handling.
- `XSendFilePath <abs path>` is a **whitelist**: only files under a whitelisted
  path are served; anything else has the header dropped. Given multiple times;
  inherited from parent contexts.
- **It bypasses Apache's own `Deny` rules** ("X-Sendfile will also send files
  that are otherwise protected, e.g. Deny from all"). This is safe here *only
  because* `XSendFilePath` is scoped to the content directory and DokuWiki runs
  its ACL checks *before* calling `http_sendfile()` (see Security below).
- The `X-Sendfile` header is consumed by the module and never reaches the client.

## Decisions

1. **Use `mod_xsendfile` (Apache `X-Sendfile`), `$conf['xsendfile'] = 2`.** The
   only alternative for Apache; the 1/3 variants target lighttpd/nginx. Install
   `libapache2-mod-xsendfile` from apt (this is a new runtime apt dep — unlike
   `mod_deflate`/`mod_filter`, this module is **not** in `apache2-bin`).

2. **Scope `XSendFilePath` to the data dir only — never `conf/`.** Whitelist the
   content directories DokuWiki serves from (`data/media`, `data/media_attic`,
   `data/cache`, …) by whitelisting `/var/www/html/data`. Credentials live in
   `conf/` (`users.auth.php`, `acl.auth.php`); they are outside the whitelist, so
   they can never be X-Sendfile'd even if a bug tried.

3. **Whitelist both the symlink and the resolved volume path.** In this
   deployment `/var/www/html/data` is a symlink to `/dokuwiki-persistent/data`
   (the Fly volume). DokuWiki emits the symlink path, but `mod_xsendfile`
   canonicalizes before its bounds check, so both forms are whitelisted to be
   robust regardless of how the module resolves the link.

4. **Lock the setting in `local.protected.php`.** `$conf['xsendfile'] = 2` can't
   be toggled from the Configuration Manager. This matters: if it were on but
   `mod_xsendfile` weren't loaded, the `X-Sendfile` header would leak to the
   client (exposing internal paths) and the body would be empty — a broken,
   information-disclosing state. Locking the setting alongside the module keeps
   them in sync on deploy.

5. **No interaction with the gzip RFC.** Media is binary and absent from the
   `mod_deflate` type list, so X-Sendfile'd media passes through uncompressed.
   CSS/JS cache files served via X-Sendfile are text and would be gzipped by
   `mod_deflate` normally — no conflict (mod_xsendfile drops a script-set
   `Content-Encoding`, which is irrelevant here since `gzip_output = 0`).

## Technical details

### `Dockerfile`

Add the module to the existing apt install and to `a2enmod`:

```dockerfile
    apt-get install -y --no-install-recommends \
        libpng-dev libjpeg62-turbo-dev libfreetype6-dev \
        libzip-dev libicu-dev \
        libapache2-mod-xsendfile \
        curl wget ca-certificates; \
```

```dockerfile
# xsendfile = offload media delivery from PHP to Apache (rfcs/2026-07-25_x-sendfile-media-delivery.md).
RUN a2enmod rewrite headers expires filter deflate xsendfile
```

```dockerfile
# Scope mod_xsendfile to the content dir.
COPY xsendfile.conf /etc/apache2/conf-enabled/xsendfile.conf
```

### `xsendfile.conf` (new, at repo root)

```apache
# Hand media delivery off to Apache via mod_xsendfile.
# See rfcs/2026-07-25_x-sendfile-media-delivery.md.
#
# XSendFilePath is a WHITELIST: only files under these paths are served.
# Scoped to the data dir (media, media_attic, cache, ...) — NOT conf/, which
# holds credentials. mod_xsendfile also bypasses Apache Deny rules, so the
# scope is what keeps apache-deny-sensitive.conf's protection meaningful.
#
# Both the webroot symlink and the resolved Fly-volume path are listed: DokuWiki
# emits the symlink path, but the module canonicalizes before its bounds check.

<IfModule mod_xsendfile.c>
    XSendFile On
    XSendFilePath /var/www/html/data
    XSendFilePath /dokuwiki-persistent/data
</IfModule>
```

### `conf-seed/local.protected.php`

```php
// Offload media delivery to Apache's mod_xsendfile. 2 = the X-Sendfile header
// (Apache); the module (libapache2-mod-xsendfile) and xsendfile.conf must ship
// with the image — if they don't, this leaks the internal path and breaks
// downloads, so it's locked here alongside the module. See
// rfcs/2026-07-25_x-sendfile-media-delivery.md.
$conf['xsendfile'] = 2;
```

## Security considerations

- **`XSendFilePath` is the security boundary.** It is scoped to `/var/www/html/data`
  and `/dokuwiki-persistent/data` only. `conf/` (credentials) and everything else
  are outside it and cannot be served via X-Sendfile.
- **mod_xsendfile bypasses `apache-deny-sensitive.conf`'s `Deny` on `data/`.**
  That `Deny` blocks *direct* browser URLs like `/data/...`; X-Sendfile is an
  internal handoff that ignores it. This is acceptable because DokuWiki performs
  its ACL check in `checkFileStatus()` **before** reaching `sendFile()` →
  `http_sendfile()` — a user with no read access on a media item never gets the
  `X-Sendfile` header; they get a 403 from PHP. The net access control is
  unchanged.
- **Module-missing failure mode is loud.** If `xsendfile = 2` is set but
  `mod_xsendfile` isn't loaded, downloads come back empty *and* the `X-Sendfile`
  header leaks the internal filesystem path to the client. Locking the setting to
  the same change that adds the module (Decision #4) prevents this; the rollout
  verification below catches it explicitly.

## Migration / rollout

- **Build-time only.** One apt line, one `a2enmod` token, a new `xsendfile.conf`,
  and one line in `local.protected.php`. No data migration, no secrets, no env
  vars; takes effect on next deploy. No skill change.
- **Verify after deploy** (run inside the container and against the live
  instance):

  ```bash
  # 1. The module is loaded:
  docker exec <app> apache2ctl -M | grep xsendfile     # expect: xsendfile_module (shared)

  # 2. A media download succeeds AND the internal header is NOT leaked to the client:
  curl -sI -u "agent:$AGENT_PASS" 'https://<app>/lib/exe/fetch.php?media=<file>' | grep -i 'x-sendfile'
  #   expect: (no output) — the module consumed the header

  # 3. The body is correct (size matches the source file):
  curl -s -u "agent:$AGENT_PASS" 'https://<app>/lib/exe/fetch.php?media=<file>' -o /tmp/out && stat -c%s /tmp/out
  ```
  If step 2 *returns* an `X-Sendfile` line, the module isn't engaging (wrong
  path whitelist, or module not loaded) and the body in step 3 will be empty —
  do not ship.

- **Cold-start impact: negligible.** One extra apt package at build time and one
  more module loaded at Apache start; no per-request cost beyond the (cheap)
  header intercept.

## Alternatives considered

- **Leave PHP serving media (`xsendfile = 0`).** Status quo. Fine for a
  text-only wiki, but holds a PHP worker per large download and buffers through
  userland. Acceptable if media traffic is trivial; rejected here because media
  uploads are a first-class feature and large attachments are expected.
- **nginx `X-Accel-Redirect` (`xsendfile = 3`).** Only relevant if we switched
  the edge from Apache; the image is `php:-apache`, so `mod_xsendfile` is the
  matching choice.
- **Serve media statically (skip PHP entirely).** Rejected: DokuWiki media
  delivery is ACL-gated and supports resizing/revisions — it must run through
  `fetch.php` for the auth and the `MEDIA_SENDFILE` event. X-Sendfile keeps that
  logic while moving only the *byte streaming* to Apache.

## Implementation notes

Implemented 2026-07-25. Build-time changes only; the gate is green locally
(`ruff check`, `ruff format --check` over 10 files, `ty check`, 65/65 tests).
Not yet built/deployed — the module-loaded / no-leaked-header / correct-body
verifications require a running instance.

- **`xsendfile.conf`** (new) — `XSendFile On` with `XSendFilePath` for both
  `/var/www/html/data` and `/dokuwiki-persistent/data`, wrapped in
  `<IfModule mod_xsendfile.c>`.
- **`Dockerfile`** — added `libapache2-mod-xsendfile` to the apt install,
  `xsendfile` to `a2enmod`, and `COPY xsendfile.conf …/conf-enabled/`.
- **`conf-seed/local.protected.php`** — added `$conf['xsendfile'] = 2;`, locked
  alongside the module so they can't drift.
- **`README.md`** — added an `xsendfile.conf` row in "What's here" and an
  "Efficient media delivery" Features bullet.

### Notes / deviations

- **Not done here (needs a live instance):** build the image, confirm
  `apache2ctl -M | grep xsendfile`, upload a large media file, and run the three
  verification steps (module loaded; no `X-Sendfile` header leaks to the client;
  body size matches the source). The symlink/resolved-path whitelist assumption
  (`/var/www/html/data` + `/dokuwiki-persistent/data`) is the thing to confirm
  first — if downloads come back empty, a path form is missing and must be added.
- **New runtime apt dep.** Unlike `mod_deflate`/`mod_filter`, `mod_xsendfile`
  isn't in `apache2-bin`; `libapache2-mod-xsendfile` adds a small amount to the
  image and one module loaded at Apache start.
