# Clean DokuWiki URLs via `mod_rewrite` (server config)

**Date:** 2026-07-25
**Status:** Proposed

## Goal

Serve **human-readable, clean URLs** for the wiki — `/wiki/syntax`,
`/projects/foo/bar`, `/start` — instead of the default ugly form
`/doku.php?id=wiki:syntax`, by wiring up `mod_rewrite` at the **Apache server
config** layer (not via `.htaccess`).

Two changes do the whole job:

1. **Add a `rewrite.conf`** in `/etc/apache2/conf-enabled/` holding DokuWiki's
   canonical rewrite rules inside a `<Directory /var/www/html>` block, and switch
   DokuWiki's URL generator on with `$conf['userewrite'] = 2` + `$conf['useslash'] = 1`.
2. **Lock those two settings** in `conf-seed/local.protected.php` so the web
   Configuration Manager can't flip them off (which would instantly break every
   clean inbound link).

`mod_rewrite` is already `a2enmod`'d in the `Dockerfile` (the comment there says
"rewrite = nice URLs") — but **no rewrite rules are configured anywhere today**,
so it is currently a no-op. This RFC turns it on for real.

## Motivation

DokuWiki's default URL scheme is `userewrite=0`:

```
https://wiki.example.com/doku.php?id=projects:foo:bar
https://wiki.example.com/doku.php?id=wiki:syntax
https://wiki.example.com/doku.php?id=start
```

`userewrite` is **not set** anywhere in `conf-seed/` (confirmed: no match for
`userewrite` in `conf-seed/`, `entrypoint.sh`, or the skill), so the wiki ships
with these ugly, query-string URLs. `mod_rewrite` is enabled in the `Dockerfile`
but unused — the "nice URLs" comment is aspirational.

Clean URLs are a small, build-time-only change with outsized payoff for the
**human** side of this wiki — the part that is *not* the agent:

- **Shareable, durable links.** A human (or a doc, or a Slack message) can write
  `/projects/foo/bar` and have it resolve. The ugly form is fragile-looking and
  hard to type or read aloud.
- **Plays well with the hardening stack.** Every other Apache knob in this repo
  — directory `Deny`, security headers, gzip, X-Sendfile — is enforced in **server
  config** so it's independent of `AllowOverride`/`.htaccess` quirks (stated
  explicitly in `rfcs/2026-07-25_apache-hardening.md`). Clean URLs via `mod_rewrite`
  belong in the same place. They cost nothing at startup and nothing per request
  for real files (the rules skip existing files/dirs — see Background).
- **Lowest-risk change possible.** Old ugly URLs (`/doku.php?id=…`) **keep
  working** after this change — `doku.php` is a real file, the rules let it pass
  through, and it reads `id` from the query string regardless of `userewrite`. So
  there is **no cutover, no redirect list, no broken inbound links**; `userewrite`
  only changes the links DokuWiki *emits*, not what it *accepts*.

### Why the agent is unaffected (and why that matters)

This wiki's **primary traffic is the `corkboard` agent** talking JSON-RPC to
`/lib/exe/jsonrpc.php` — a path the skill builds verbatim
(`f"{url}/lib/exe/jsonrpc.php"` in `rpc_call()`). That endpoint is a **real file**
under `/lib/exe/`, so the `RewriteCond %{REQUEST_FILENAME} !-f` guard passes it
through untouched. The rewrite rules never touch the API path, the CSS/JS
generators (`css.php`, `js.php`), media fetches (`fetch.php`), or any template
asset. **Clean URLs are a browser/human concern here**, not an agent one — the
skill needs no change and carries no new dependency.

## Background

### DokuWiki's `userewrite` modes

`$conf['userewrite']` selects the URL form DokuWiki **generates** in the links it
emits (page links, feeds, `rel=canonical`, edit/save actions):

| `userewrite` | URL form                              | Notes                                   |
| ------------ | ------------------------------------- | --------------------------------------- |
| `0` (default)| `/doku.php?id=wiki:syntax`            | What we ship today (ugly).              |
| `1`          | `/wiki:syntax`                        | Drops `doku.php`, **keeps colons**.     |
| `2`          | `/wiki/syntax`                        | Fully clean — namespaces become path segments. Needs `useslash=1` + a rewrite that maps `/ns/page` back to `doku.php?id=ns:page`. |

We want `2`. Mode `1` still leaves colons in the URL (`/projects:foo:bar`), which
is neither as readable nor as "clean" as the path-segment form, and is a strictly
weaker stop. Mode `2` is the canonical "nice URL" mode and is exactly what
DokuWiki's own `.htaccess.dist` template implements.

### `useslash` — the other half of "clean"

`$conf['useslash'] = 1` makes DokuWiki emit `/` instead of `:` between namespaces
in URLs. It is **required** for `userewrite=2` to actually look clean — without
it you'd get `/projects:foo:bar` (path-less). The two settings are a pair; we set
both.

### Server config vs `.htaccess` (and why server config here)

DokuWiki ships a `.htaccess.dist` template at the webroot meant to be copied to
`.htaccess` and uncommented. Enabling it requires `AllowOverride All` (or at least
`FileInfo`) on `/var/www/`, which this repo deliberately does **not** do — every
Apache-side control in the image is in `/etc/apache2/conf-enabled/` server config
precisely so it doesn't depend on `AllowOverride` (see the directory-`Deny`
discussion in `rfcs/2026-07-25_apache-hardening.md`: *"enforcing it in the server
config makes the protection independent of AllowOverride / .htaccess quirks"*).

Porting the `.htaccess` rules into a `<Directory /var/www/html>` block in server
config gets the same behavior with three wins:

- **Consistency** with the rest of the hardening stack (one place to look).
- **No `AllowOverride All`**, so Apache never `stat()`s for a `.htaccess` on every
  directory in the request path (a real, if small, per-request cost).
- **Belt-and-suspenders** — an explicit `AllowOverride None` in the block means a
  stray `.htaccess` can never take effect, even if one appears on disk.

Apache **merges** multiple `<Directory /var/www/html>` blocks for the same path
(the `Options -Indexes -ExecCGI` one in `apache-deny-sensitive.conf` and the new
`RewriteEngine` one here), applying them in order — so the rewrite block is
additive to, not in conflict with, the existing access control.

### `RewriteBase` is not needed

DokuWiki's `.htaccess.dist` ships `#RewriteBase /` commented out. In **server
config** (vs `.htaccess`) and at the **webroot** (no subdirectory), mod_rewrite
derives the base path correctly on its own; `RewriteBase` is only required in
per-directory context or when the app lives below the webroot. We leave it out and
note why, mirroring the commented-out line.

### Real files/dirs are never rewritten

The two `RewriteCond`s guard the catch-all rule:

```apache
RewriteCond %{REQUEST_FILENAME} !-f
RewriteCond %{REQUEST_FILENAME} !-d
RewriteRule (.*) doku.php?id=$1 [QSA,L]
```

So **anything that exists on disk passes through**: `/doku.php`, `/lib/exe/jsonrpc.php`
(the agent's endpoint), `/lib/exe/css.php`, `/lib/exe/js.php`, `/lib/exe/fetch.php`,
`/lib/tpl/dokuwiki/…`, real media files, `favicon.ico`, etc. Only "virtual" page
IDs (which have no file) get routed to `doku.php`. This is why the agent, the CSS/JS
pipeline, and X-Sendfile media delivery all keep working unmodified.

## Decisions

1. **`userewrite=2` + `useslash=1`.** Fully clean URLs: `/wiki/syntax`,
   `/projects/foo/bar`, `/start`. Mode `2` over mode `1` for the genuinely
   path-segment form (see Background). Both locked in `local.protected.php`.

2. **Rules in server config (`rewrite.conf`), not `.htaccess`.** Matches the repo's
   posture; avoids `AllowOverride All` and its per-request `stat()` cost; an
   explicit `AllowOverride None` makes the "no `.htaccess` ever" stance explicit.

3. **Mirror DokuWiki's canonical `.htaccess.dist` rules.** Use the blessed set —
   root → `doku.php`, catch-all → `doku.php?id=$1`, plus the `_media/`, `_detail/`,
   and `_export/` mappings that make clean media/detail/export URLs resolve. These
   are the official, DokuWiki-tested patterns.

4. **No `RewriteBase`.** Not needed in server-config context at the webroot
   (Background); leaving it out avoids a footgun if the deployment path ever
   changes.

5. **No agent or skill change.** The API endpoint is a real file and passes through
   the `!-f` guard untouched (Background). The skill stays dependency-free and
   unchanged.

6. **Old ugly URLs keep working (no redirects, no cutover).** `userewrite` only
   changes what DokuWiki *emits*; `/doku.php?id=foo` still resolves because
   `doku.php` is a real file. Zero inbound-link breakage; existing bookmarks,
   feeds, and the entrypoint's self-test all keep working.

7. **`canonical=1` is recommended but separable.** With two URL forms now
   resolving to one page (clean and ugly), turning on `$conf['canonical'] = 1`
   makes DokuWiki emit absolute `https://host/...` URLs and a `rel=canonical`
   pointing at the clean form — de-duplicating for crawlers and stabilizing links
   in feeds/exports. It's included here because it directly supports the clean-URL
   story, but it can be dropped without affecting the rewrite itself. (DokuWiki
   auto-detects `baseurl` from the request; no manual setting needed behind Fly.)

## Technical details

### `Dockerfile`

`mod_rewrite` is **already** in the `a2enmod` line — no module change. Add one
`COPY` for the new conf, alongside the existing `compression.conf` /
`xsendfile.conf` copies:

```dockerfile
# Clean URLs (mod_rewrite) — DokuWiki's canonical rewrite rules in server config
# (not .htaccess). Pairs with userewrite=2 + useslash=1 in local.protected.php.
# (rfcs/2026-07-25_clean-urls-mod-rewrite.md)
COPY rewrite.conf /etc/apache2/conf-enabled/rewrite.conf
```

(The existing `a2enmod … rewrite …` line already covers the module.)

### `rewrite.conf` (new, at repo root)

DokuWiki's shipped `.htaccess.dist`, ported to a `<Directory>` server-config block.
Every rule is the canonical DokuWiki pattern — verified against the template
DokuWiki ships for `userewrite=2`:

```apache
# Clean URLs for DokuWiki via mod_rewrite, in SERVER config (not .htaccess).
# See rfcs/2026-07-25_clean-urls-mod-rewrite.md.
#
# These are DokuWiki's canonical rewrite rules (the uncommented .htaccess.dist),
# ported into a <Directory> block so they work WITHOUT AllowOverride All — matching
# the repo's posture (every other Apache knob here is server config; see
# rfcs/2026-07-25_apache-hardening.md). Apache merges this <Directory> with the one
# in apache-deny-sensitive.conf (Options -Indexes -ExecCGI); they compose.
#
# Pairs with $conf['userewrite']=2 + $conf['useslash']=1 in local.protected.php:
# those make DokuWiki EMIT clean links; these rules ROUTE clean URLs back to PHP.
# mod_rewrite is already a2enmod'd in the Dockerfile.
#
# The catch-all rule is guarded by !-f / !-d, so REAL files/dirs pass through
# untouched: /doku.php, /lib/exe/jsonrpc.php (the agent's API endpoint),
# css.php/js.php/fetch.php, template assets, real media files, favicon.ico, …
# Only virtual page IDs (no file) get routed to doku.php.

<Directory /var/www/html>
    # Explicit: never consult a .htaccess on disk, even if one appears.
    AllowOverride None

    RewriteEngine On
    # RewriteBase is intentionally omitted: not needed in server-config context at
    # the webroot (DokuWiki is at /, no subdirectory). Mirrors the commented-out
    # #RewriteBase / in DokuWiki's .htaccess.dist.

    # Clean media URLs: /_media/ns/file.png -> fetch.php (still ACL-checked; still
    # hands off to mod_xsendfile per rfcs/2026-07-25_x-sendfile-media-delivery.md).
    RewriteRule ^_media/(.*)              lib/exe/fetch.php?media=$1  [QSA,L]
    # Image detail view.
    RewriteRule ^_detail/(.*)             lib/exe/detail.php?media=$1 [QSA,L]
    # Export endpoints: /_export/html/ns:page -> doku.php?do=export_html&id=ns:page
    RewriteRule ^_export/([^/]+)/(.*)     doku.php?do=export_$1&id=$2 [QSA,L]

    # The start page at the webroot.
    RewriteRule ^$                        doku.php  [L]

    # Everything that isn't a real file or directory is a wiki page ID.
    RewriteCond %{REQUEST_FILENAME} !-f
    RewriteCond %{REQUEST_FILENAME} !-d
    RewriteRule (.*)                      doku.php?id=$1  [QSA,L]
</Directory>
```

**Flag notes:** `[QSA]` (Query String Append) preserves any query params
(`?do=edit`, `?rev=…`, `?s=…` search, `?do=export_…`). `[L]` (Last) stops
rewriting for that round so the rules don't re-process. The conditions are
AND-ed (default), so the catch-all only fires when the request is neither a file
nor a directory.

### `conf-seed/local.protected.php`

Lock both settings so the web Configuration Manager can't flip them (which would
make DokuWiki emit URLs the server can no longer route — every internal link would
404). Add alongside the existing locked settings:

```php
// Clean URLs: DokuWiki emits path-style links (/wiki/syntax, not
// /doku.php?id=wiki:syntax), and mod_rewrite (rewrite.conf) routes them back.
// userewrite=2 = fully clean (namespaces become path segments); useslash=1 =
// '/' between namespaces in URLs. Both REQUIRED together, and both must match
// the rewrite rules — locked here so the web Configuration Manager can't flip
// them off (which would 404 every clean link the wiki emits).
// Old ugly URLs (/doku.php?id=…) still resolve (doku.php is a real file).
// See rfcs/2026-07-25_clean-urls-mod-rewrite.md.
$conf['userewrite'] = 2;
$conf['useslash']   = 1;

// Emit absolute (https://host/…) URLs + rel=canonical pointing at the clean
// form, so the now-two URL shapes (clean + legacy ugly) don't read as duplicate
// content to crawlers, and links in feeds/exports are stable. baseurl is
// auto-detected from the request; no manual setting behind Fly. Separable from
// the rewrite itself — can be dropped without affecting routing.
// See rfcs/2026-07-25_clean-urls-mod-rewrite.md.
$conf['canonical']  = 1;
```

(The entrypoint already syncs `local.protected.php` from this seed on every boot,
so this takes effect on the next deploy with no migration.)

### What is NOT touched

- **The skill (`skills/corkboard/script/corkboard.py`)** — it POSTs to
  `/lib/exe/jsonrpc.php`, a real file that passes the `!-f` guard untouched. No
  change, no new dependency.
- **`mod_xsendfile` / media delivery** — `fetch.php` still does its ACL check and
  emits the `X-Sendfile` header; the `_media/` rewrite just routes the clean URL
  to it. The two RFCs compose.
- **`entrypoint.sh`** — the JSON-RPC self-test POSTs to the API endpoint, which is
  unaffected. No change.
- **Access control / directory `Deny`** — untouched; the `AllowOverride None` here
  is additive to the existing hardening.

## Migration / rollout

- **Build-time only.** One new `rewrite.conf`, one `COPY` line in the `Dockerfile`,
  and three lines in `conf-seed/local.protected.php`. No data migration, no secrets,
  no env vars, no skill change. Takes effect on the next deploy.
- **No cutover, no redirects.** Legacy ugly URLs (`/doku.php?id=…`) keep resolving
  — `userewrite` only changes what DokuWiki *emits*, and `doku.php` is a real file
  that passes the `!-f` guard. Existing bookmarks, RSS feeds, and the entrypoint
  self-test all keep working unchanged.
- **Verify after deploy** (against the live URL):

  ```bash
  # 1. The start page resolves at the clean root URL:
  curl -sI https://<app>/ | grep -iE '^http|location'          # expect: 200 OK (not 404)

  # 2. A nested page resolves as path segments (no doku.php, no colons):
  curl -sI https://<app>/wiki/syntax | grep -i '^http'         # expect: 200 OK

  # 3. Legacy ugly URLs STILL work (no breakage, no redirect):
  curl -sI 'https://<app>/doku.php?id=wiki:syntax' | grep -i '^http'  # expect: 200 OK

  # 4. The agent's API endpoint is untouched (real file, !-f guard):
  curl -sI https://<app>/lib/exe/jsonrpc.php | grep -i '^http'  # expect: 200/4xx (reached, not rewritten/404)

  # 5. CSS/JS generators pass through (real files):
  curl -sI 'https://<app>/lib/exe/css.php?t=dokuwiki' | grep -i '^http'  # expect: 200 OK

  # 6. Clean media URL routes to fetch.php (and xsendfile still offloads):
  curl -sI 'https://<app>/_media/wiki/dokuwiki-128.png' | grep -i '^http'  # expect: 200 OK

  # 7. A genuinely non-existent page ID is handled by DokuWiki (its "page not
  #    found"), not a raw Apache 404:
  curl -s 'https://<app>/this_page_does_not_exist' | grep -i 'does not exist\|create'  # expect: DokuWiki's not-found/create page

  # 8. Links DokuWiki EMITS are now clean (load the start page HTML and grep):
  curl -s https://<app>/start | grep -oE 'href="[^"]+"' | head   # expect: href="/…", NOT href="/doku.php?id=…"

  # 9. mod_rewrite is active and AllowOverride is None for the webroot:
  docker exec <app> apache2ctl -M | grep rewrite                  # expect: rewrite_module
  docker exec <app> apache2ctl -M | grep -i alias                 # sanity (no conflict)
  ```

- **Cold-start impact: none.** `mod_rewrite` is already loaded; the only change is
  the rules in a config file read once at Apache start. No per-request cost for
  real files (the `!-f`/`!-d` guards short-circuit immediately).
- **Rollback:** revert the deploy; the conf is additive and changes no existing
  behavior beyond what DokuWiki emits in its links.

## Alternatives considered

- **`userewrite=1` (semi-clean, keeps colons).** Rejected: `/projects:foo:bar` is
  neither as readable nor as genuinely "clean" as the path-segment form, and is a
  strictly weaker stop. Mode `2` is what DokuWiki's own template targets.
- **`.htaccess` via `AllowOverride All`.** Rejected: diverges from this repo's
  explicit posture (server config for every Apache knob, independent of
  `AllowOverride`), adds a per-request `stat()` cost on every directory in the path,
  and reintroduces a `.htaccess`-dependency footgun. The server-config port is
  equivalent and consistent.
- **Redirect old ugly URLs to the clean form (301s).** Not needed and not wanted:
  ugly URLs still resolve, so there's no breakage to paper over, and forcing
  redirects would add a round-trip for the agent-adjacent paths and any tooling
  that still uses the ugly form. `canonical=1` handles the SEO de-duplication
  without redirect churn.
- **`canonical` left off.** Considered, to keep the change minimal — but the
  clean-URL story is incomplete without it: with two forms now resolving, emitting
  absolute URLs + `rel=canonical` is the correct way to signal which is canonical
  to crawlers and to stabilize links in feeds/exports. Kept, but explicitly
  separable.
- **A reverse-proxy / Fly-edge rewrite.** Out of scope; Fly's proxy passes requests
  through and doesn't rewrite. Doing it in Apache keeps it with the rest of the
  stack and self-contained in the image.

## Implementation checklist

- [ ] Add `rewrite.conf` at repo root (DokuWiki's canonical rules in a
      `<Directory /var/www/html>` block, with `AllowOverride None`).
- [ ] `Dockerfile`: add `COPY rewrite.conf /etc/apache2/conf-enabled/rewrite.conf`
      (module already enabled).
- [ ] `conf-seed/local.protected.php`: add `$conf['userewrite'] = 2;`,
      `$conf['useslash'] = 1;`, `$conf['canonical'] = 1;` with comments.
- [ ] Run the gate: `ruff check`, `ruff format --check .` (note: ruff formats
      Python in `.md`; the PHP/Apache snippets here are not Python, so unaffected —
      but run it to be safe), `ty check`, and the test harness.
- [ ] On moving to **Implemented**: build the image, run the 9 verification steps,
      then update `README.md` with a "Clean URLs" Features bullet and a
      `rewrite.conf` row in "What's here". Replace this checklist with
      implementation notes (per the RFC process in `AGENTS.md`).
