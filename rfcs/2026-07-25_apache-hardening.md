# Apache hardening (fingerprint, headers, Slowloris, DoS heuristic, real client IP)

**Date:** 2026-07-25
**Status:** Implemented

## Goal

Tighten the Apache layer the wiki already runs on — without changing any
DokuWiki behavior or the `corkboard` agent. Five build/config changes, all
Apache-side:

1. **Disable the modules we don't use** — `autoindex status info cgi userdir` —
   to shrink the attack surface (and make `-Indexes` belt-and-suspenders).
2. **Stop leaking server fingerprint / TRACE** — `ServerTokens Prod`,
   `ServerSignature Off`, `TraceEnable Off`; `Options -Indexes -ExecCGI`.
3. **Set security response headers** — `X-Content-Type-Options`,
   `Referrer-Policy`, and `Strict-Transport-Security` (the last gated correctly
   for Fly's TLS-terminating proxy).
4. **Recover the real client IP** from Fly's proxy (`mod_remoteip` on the
   `Fly-Client-IP` header), so the DoS heuristic and access logs see clients,
   not the proxy.
5. **Slowloris + a rough DoS heuristic** — `mod_reqtimeout` (caps on how long a
   client may take to send a request) and `mod_evasive` (per-process rate
   limiting).

**Out of scope, deferred to a follow-up RFC:** `mod_security` + the OWASP Core
Rule Set (the WAF). It was in the original brief but is pulled out deliberately
— see *Why mod_security is deferred*.

The existing directory-`Deny` for sensitive dirs (`apache-deny-sensitive.conf`)
is extended here to also cover `vendor/` and to set the `Options`.

## Motivation

The image today enables `rewrite headers expires filter deflate xsendfile` and
nothing else hardening-wise. The default Debian Apache it inherits advertises
its version (`ServerTokens OS`), signs error pages (`ServerSignature On`),
answers `TRACE`, will happily auto-index a directory, and — because Fly
terminates TLS at the edge and proxies to the container — sees **every request
as coming from Fly's proxy**, not the client. That last point matters: any
per-client protection (rate limiting, a future WAF) and the access log are all
blind to who the client actually is until `mod_remoteip` is in place.

This is the same class of low-risk, build-time-only change as the compression
and X-Sendfile RFCs — add modules + drop a few conf files, ship on the next
deploy, no data migration, no secrets, no DokuWiki or skill change.

## Background

### Fly's proxy and the client IP

Fly terminates TLS at its edge and forwards to the container over the org's
private **6pn** IPv6 mesh. The container therefore always sees plain HTTP from
Fly's proxy, never HTTPS and never the client. Two consequences:

- **The real client IP is in a header, not the socket peer.** Fly documents this
  ([Request headers](https://fly.io/docs/networking/request-headers/)):
  `Fly-Client-IP` is *"the IP address of the client from the perspective of Fly
  Proxy"* and is the **recommended** source on Fly (one value Fly sets, not an
  `X-Forwarded-For` chain to parse defensively).
- **`Strict-Transport-Security … env=HTTPS` never fires.** Apache's own `HTTPS`
  env var is only set when *Apache* is doing TLS. Fly does the TLS, so the
  container never sees it, `HTTPS` is never set, and a bare `env=HTTPS` rule
  emits HSTS on **zero** responses. Fly does report the original protocol in
  `X-Forwarded-Proto` (`http`/`https`), so we derive `HTTPS` from that instead.

### `mod_remoteip` is fail-closed

`RemoteIPInternalProxy` (or `RemoteIPTrustedProxy`) is the list of peers trusted
to present the `RemoteIPHeader`. **mod_remoteip ignores the header entirely
unless the immediate peer is on that list** — it never trusts a header from an
untrusted source. So getting the trusted-proxy CIDR slightly wrong is a *safe
no-op* (real peer IP is used, no spoofing); the only failure mode is "client IP
not recovered." The spoofing hole would require trusting a CIDR an attacker can
appear from — and Fly's private 6pn (`fdaa::/48`, the per-org IPv6 ULA) is not
reachable from the internet, so trusting it is safe.

### `mod_evasive` under the prefork MPM

`mod_evasive` keeps its hit counts **per worker process**, and under Apache's
prefork MPM (the `php:*-apache` default) each PHP-serving child has its own
private counts — there is no shared state. So it is a **coarse, per-child
heuristic**: it catches a naive flood from one IP, it does not stop a distributed
attack or precisely cap a single client across all workers. Useful
defense-in-depth; not a hard DoS cap. (The thresholds below are deliberately
loose so the wiki and the agent never trip it — see Decisions.)

### The `Options` gotcha

`Options -Indexes -ExecCGI` uses a **relative** `+`/`-` form on purpose. An
absolute `Options -Indexes` (no sign) **resets** the option set to exactly that,
dropping `FollowSymLinks` inherited from `<Directory />`. In this deployment
`data/`, `conf/`, `lib/plugins/`, `lib/tpl/` are all **symlinks into the Fly
volume**; losing `FollowSymLinks` would 403 the entire wiki. The leading `-`
preserves the inherited set and only toggles the two off.

## Decisions

1. **Disable `autoindex status info cgi userdir` via `a2dismod`.** None are
   used: PHP runs as a module (never CGI), the wiki never needs a generated
   directory listing, and `server-status`/`server-info` are debug surfaces that
   shouldn't exist on a production box. `a2dismod` on an already-disabled module
   is a harmless no-op that exits 0, so the line is idempotent across base
   images. (Disabling `autoindex` makes the `Options -Indexes` rule
   belt-and-suspenders rather than the only line of defense.) `-f` is required
   because Debian flags `autoindex` "essential" and refuses it non-interactively.

2. **`ServerTokens Prod`, `ServerSignature Off`, `TraceEnable Off`,
   `Options -Indexes -ExecCGI`.** Strip the version banner from headers and
   error pages, disable `TRACE` (an XST vector), and forbid auto-indexing and
   CGI execution at the directory level. Debian ships these commented in
   `conf-enabled/security.conf`; we set them explicitly so the image's defaults
   can't drift on us. (`TraceEnable` and `ServerTokens` are server-config only;
   `Options` is directory-context — see the gotcha above.)

3. **Three security headers, HSTS gated on `X-Forwarded-Proto`.**
   `X-Content-Type-Options: nosniff` and `Referrer-Policy:
   strict-origin-when-cross-origin` go on every response. `Strict-Transport-Security`
   is gated via `SetEnvIf X-Forwarded-Proto "^https$" HTTPS=on` then
   `… env=HTTPS` — the only correct shape behind Fly's TLS terminator (a bare
   `env=HTTPS` emits nothing; see Background). `max-age=31536000` (1 year);
   `includeSubDomains`/`preload` are deliberately **not** added (preload
   requires submitting to the HSTS list and applies to subdomains — out of
   scope). `Header always set` so the headers attach to error responses too.

4. **`mod_remoteip` on `Fly-Client-IP`, trusting `fdaa::/48` + loopback.** Fly's
   recommended single-value client header (Background), trusted only when the
   peer is Fly's private 6pn (`RemoteIPInternalProxy fdaa::/48`) or localhost.
   Fail-closed: if the CIDR is wrong, the header is ignored and the real peer IP
   is used — no spoofing, just "no client-IP recovery," which the verification
   steps catch.

5. **`mod_reqtimeout` with Debian's defaults, stated explicitly.** Caps a client
   at 20–40s to send headers and 20s (+`minRate=500` B/s) to send the body —
   Slowloris-resistant while letting a slow uploader finish a large media file.
   The values equal the module's shipped `reqtimeout.conf`; restated in our conf
   to be explicit and independent of the base image.

6. **`mod_evasive` with loose thresholds + a localhost whitelist.**
   `DOSPageCount 10`/`DOSPageInterval 2` (same-URI) and `DOSSiteCount 100`/
   `DOSSiteInterval 2` (any-URI), `DOSBlockingPeriod 30`, `DOSWhitelist
   127.0.0.1`. These clear DokuWiki page saves and the agent's JSON-RPC bursts
   (`listPages` recursive, batch edits, `getMedia` — sequential, a few/sec) while
   still tripping on a real flood. No `DOSSystemCommand` (no iptables/MTA in the
   container) and no `DOSEmailNotify`. **`mod_remoteip` must be loaded** so the
   counts key on the real client IP — without it every request looks like Fly's
   proxy and the whole site would block itself.

7. **`mod_security` + OWASP CRS is deferred to a follow-up RFC.** Originally in
   scope; pulled out (see *Why mod_security is deferred*). When it lands, the
   recorded plan is: install CRS via the **Debian `modsecurity-crs` apt package**
   (simplest, reproducible, tracked with the base image) and ship
   `SecRuleEngine DetectionOnly` first (log-only), tune DokuWiki/agent exclusions
   from the logs, then flip to `On`. Captured here so the decision isn't lost.

## Technical details

### `Dockerfile`

One new apt package (`mod_evasive`; `reqtimeout`/`remoteip` are already in
`apache2-bin`), extend `a2enmod`, add an `a2dismod` line, and copy the new confs:

```dockerfile
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libpng-dev libjpeg62-turbo-dev libfreetype6-dev \
        libzip-dev libicu-dev \
        libapache2-mod-xsendfile \
        libapache2-mod-evasive \
        curl wget ca-certificates; \
```

```dockerfile
# Enable Apache modules.
#   rewrite = nice URLs; headers/expires = cache headers;
#   filter+deflate = on-the-wire gzip (rfcs/2026-07-25_http-compression.md);
#   xsendfile = offload media delivery (rfcs/2026-07-25_x-sendfile-media-delivery.md);
#   remoteip = recover real client IP from Fly's proxy (mod_evasive + logs need it);
#   reqtimeout = Slowloris caps; evasive = rough DoS rate-limit.
#   (rfcs/2026-07-25_apache-hardening.md)
RUN a2enmod rewrite headers expires filter deflate xsendfile remoteip reqtimeout evasive

# Disable unused modules (shrink attack surface). a2dismod on an already-disabled
# module is a harmless no-op (exit 0). cgi: PHP runs as a module, never CGI;
# autoindex/status/info/userdir: not needed (disabling autoindex also makes the
# Options -Indexes rule belt-and-suspenders). -f: Debian flags autoindex
# "essential", so a2dismod aborts it non-interactively without -f.
# (rfcs/2026-07-25_apache-hardening.md)
RUN a2dismod -f autoindex status info cgi userdir
```

```dockerfile
# Apache hardening confs (rfcs/2026-07-25_apache-hardening.md):
#   dokuwiki-security.conf = directory access control + Options (incl. vendor/)
#   apache-hardening.conf  = server fingerprint/TRACE, security headers, Slowloris
#   remoteip.conf          = recover real client IP from Fly's proxy
#   evasive.conf           = rough DoS rate-limit thresholds
COPY apache-deny-sensitive.conf /etc/apache2/conf-enabled/dokuwiki-security.conf
COPY apache-hardening.conf /etc/apache2/conf-enabled/apache-hardening.conf
COPY remoteip.conf /etc/apache2/conf-enabled/remoteip.conf
COPY evasive.conf /etc/apache2/conf-enabled/evasive.conf
```

(The first `COPY` already exists; only its source file changes to add `vendor/`
+ `Options`. The other three are new.)

### `apache-deny-sensitive.conf` (edited — directory-level access control + Options)

Adapts the brief's `<Directory>`/`<DirectoryMatch>` shape to the real webroot
(`/var/www/html`, not `/var/www/dokuwiki`) and adds `vendor/` (DokuWiki's
Composer deps, source on disk — not currently denied):

```apache
# Directory-level access control + Options for the DokuWiki webroot.
# DokuWiki ships .htaccess rules for this; enforcing it in the server config
# makes the protection independent of AllowOverride / .htaccess quirks.
# See rfcs/2026-07-25_apache-hardening.md (vendor/ added; DirectoryMatch form).

<Directory /var/www/html>
    Require all granted
    # -Indexes: never auto-list (belt-and-suspenders over disabling autoindex).
    # -ExecCGI: no CGI execution (PHP runs as a module).
    # RELATIVE +/- on purpose: toggles these off while PRESERVING FollowSymLinks
    # inherited from <Directory />. An absolute "Options -Indexes" would RESET
    # the set, drop FollowSymLinks, break the data/conf/lib symlinks into the
    # Fly volume, and break the wiki. Keep the leading +/-.
    Options -Indexes -ExecCGI
</Directory>

# data=pages/media/credentials, conf=config+credentials (users.auth.php),
# bin=CLI tools, inc/vendor=DokuWiki source. Direct URLs return 403.
<DirectoryMatch "^/var/www/html/(data|conf|bin|inc|vendor)">
    Require all denied
</DirectoryMatch>
```

### `apache-hardening.conf` (new — server-level knobs)

```apache
# Apache server-level hardening: fingerprint, TRACE, security headers, Slowloris.
# Directory-level access control + Options live in apache-deny-sensitive.conf.
# See rfcs/2026-07-25_apache-hardening.md.

# --- Server fingerprint / TRACE -----------------------------------------
# Stop advertising Apache/PHP/module versions and disable TRACE. Debian ships
# these commented in conf-enabled/security.conf; set explicitly so the image's
# defaults can't drift.
ServerTokens Prod
ServerSignature Off
TraceEnable Off

# --- Security response headers (mod_headers, already enabled) -----------
# mod_setenvif is enabled by default in the base image, so SetEnvIf needs no
# a2enmod.
#
# HSTS: Strict-Transport-Security must only go out over HTTPS. Apache's own
# HTTPS env var is NEVER set here -- Fly terminates TLS, so the container
# always sees plain HTTP and a bare `env=HTTPS` would emit HSTS on zero
# responses. Derive HTTPS from the protocol Fly reports in X-Forwarded-Proto.
SetEnvIf X-Forwarded-Proto "^https$" HTTPS=on

Header always set X-Content-Type-Options "nosniff"
Header always set Referrer-Policy "strict-origin-when-cross-origin"
Header always set Strict-Transport-Security "max-age=31536000" env=HTTPS

# --- Slowloris caps (mod_reqtimeout) ------------------------------------
# How long a client may take to send request headers/body. Equal to Debian's
# shipped module defaults; restated to be explicit and version-independent.
<IfModule mod_reqtimeout.c>
    RequestReadTimeout header=20-40,minRate=500 body=20,minRate=500
</IfModule>
```

### `remoteip.conf` (new)

```apache
# Recover the real client IP from Fly's proxy.
# See rfcs/2026-07-25_apache-hardening.md.
#
# Fly terminates TLS at its edge and forwards to the container over the org's
# private 6pn (IPv6) mesh. mod_remoteip replaces the apparent client (Fly's
# proxy) with the real client IP, which mod_evasive, access logs, and any future
# WAF then see.
#
# Fly-Client-IP: the single client IP from Fly Proxy's perspective -- Fly's
# recommended source (one value it sets, not an X-Forwarded-For chain to parse).
# https://fly.io/docs/networking/request-headers/
#
# mod_remoteip is FAIL-CLOSED: it only honors the header when the immediate peer
# is a trusted proxy. RemoteIPInternalProxy trusts Fly's private 6pn (fdaa::/48,
# the per-org IPv6 ULA) and loopback. A wrong CIDR is a safe no-op (header
# ignored, real peer used); verify the CIDR against a live deploy (see the RFC).

RemoteIPHeader Fly-Client-IP
RemoteIPInternalProxy fdaa::/48
RemoteIPInternalProxy ::1
RemoteIPInternalProxy 127.0.0.1
```

### `evasive.conf` (new)

```apache
# mod_evasive: rough per-process rate limiting (DoS / brute-force heuristic).
# See rfcs/2026-07-25_apache-hardening.md.
#
# Thresholds are deliberately LOOSE -- DokuWiki saves and the corkboard agent's
# JSON-RPC bursts (listPages recursive, batch edits, getMedia) must not trip it.
# This catches a naive flood, not a distributed attack.
#
# LIMITATION: under the prefork MPM each worker keeps its OWN counts (no shared
# state), so this is a per-child heuristic. Defense-in-depth, not a hard cap.
# mod_remoteip MUST be loaded so counts key on the real client IP -- otherwise
# every request looks like Fly's proxy and the whole site blocks itself.

<IfModule mod_evasive20.c>
    DOSHashTableSize 3097
    # Same URI: 10 hits / 2s -> block (a tight reload loop, not normal browsing).
    DOSPageCount 10
    DOSPageInterval 2
    # Any URI on the site: 100 hits / 2s -> block. Well above the agent's burst.
    DOSSiteCount 100
    DOSSiteInterval 2
    # 403 for 30s after tripping. No DOSSystemCommand (no iptables/MTA in the
    # container); no DOSEmailNotify.
    DOSBlockingPeriod 30
    # Don't block the entrypoint's JSON-RPC self-test (curl 127.0.0.1).
    DOSWhitelist 127.0.0.1
</IfModule>
```

### No `conf-seed` or skill change

All of this is Apache-side. DokuWiki sets none of these headers itself, so
`conf-seed/local.protected.php` is untouched. The `corkboard` agent's `urllib`
transport ignores advisory browser headers (HSTS / `X-Content-Type-Options` /
`Referrer-Policy`), so `corkboard.py` is untouched too.

## Why mod_security is deferred

`mod_security` + OWASP CRS was in the original brief. It is pulled into its own
follow-up RFC because **this wiki's primary traffic is exactly what CRS
false-positives on**: the `corkboard` agent edits pages over JSON-RPC
(`core.putPage`) with arbitrary content — wiki markup, fenced code blocks,
compare-and-swap writes — and humans save the same via the web UI. CRS will flag
that text as XSS/SQLi/RCE and, in `SecRuleEngine On`, **block legitimate edits
including the agent's**. Shipping it correctly therefore needs:

- a DokuWiki-specific exclusion set (the JSON-RPC endpoint, the edit/save
  actions, media upload), and
- a `DetectionOnly` soak period to collect the real false positives before
  flipping to `On`.

That is real, fiddly work that deserves its own RFC and a live instance to tune
against — it should not block the cheap, high-value hardening in this one. The
decision is recorded (Decision #7): when it lands, install CRS from the Debian
`modsecurity-crs` apt package and start in `DetectionOnly`.

Everything in *this* RFC composes cleanly with a future WAF: `mod_remoteip` gives
it the real client IP, the directory `Deny` and `Options` harden below it, and
the response headers sit alongside whatever `mod_security` adds.

## Security considerations

- **`mod_remoteip` trust is scoped to Fly's private network.** Only peers in
  `fdaa::/48` (and loopback) can set the client IP. The container is not
  directly exposed except via Fly, and `fdaa::/48` isn't internet-routable, so
  there's no spoofing path. Fail-closed by design (Background).
- **`mod_evasive` does not weaken access control.** A tripped client gets a 403
  for `DOSBlockingPeriod`; legitimate users and the agent are below the
  thresholds. The localhost whitelist keeps the entrypoint self-test clean.
- **`Options -Indexes -ExecCGI` preserves `FollowSymLinks`.** The relative `+`/`-`
  form is load-bearing — an absolute form would 403 the whole wiki by breaking
  the volume symlinks (Background, and a comment in the conf).
- **Directory `Deny` is defense-in-depth, not the only control.** DokuWiki also
  guards these via `.htaccess`; enforcing it in the server config makes it
  independent of `AllowOverride`. Adding `vendor/` closes a gap (DokuWiki's
  Composer source was previously reachable by direct URL).
- **HSTS is emitted only over HTTPS.** Gating on `X-Forwarded-Proto` means a
  direct hit on the container's HTTP port (no proxy header) does **not** get
  HSTS — correct, since HSTS on a plain-HTTP response would be nonsensical.

## Migration / rollout

- **Build-time only.** One apt package, two `a2enmod`/`a2dismod` lines, three new
  conf files, one edited conf. No data migration, no secrets, no env vars, no
  DokuWiki or skill change. Takes effect on the next deploy.
- **Verify after deploy** (run inside the container and against the live URL):

  ```bash
  # 1. The new modules are loaded; the disabled ones are gone:
  docker exec <app> apache2ctl -M | grep -E 'remoteip|reqtimeout|evasive'   # expect 3 lines
  docker exec <app> apache2ctl -M | grep -E 'autoindex|status_module|info|cgi|userdir'  # expect: (empty)

  # 2. No version banner; TRACE is off:
  curl -sI https://<app>/ | grep -i '^server:'          # expect: Server: Apache   (no version)
  curl -sI -X TRACE https://<app>/ | grep -iE '^allow|^http'  # expect: 405, no TRACE allow

  # 3. Security headers present (HSTS included, since this came in over HTTPS):
  curl -sI https://<app>/ | grep -iE 'x-content-type-options|referrer-policy|strict-transport-security'

  # 4. HSTS is GATED: a direct HTTP hit with no X-Forwarded-Proto must NOT return HSTS:
  docker exec <app> curl -sI http://127.0.0.1/ | grep -i 'strict-transport-security'  # expect: (empty)

  # 5. Sensitive dirs denied (incl. the newly-added vendor/):
  curl -sI https://<app>/data/ | grep -i '^http'         # expect: 403
  curl -sI https://<app>/conf/users.auth.php | grep -i '^http'  # expect: 403 (not 200)
  curl -sI https://<app>/vendor/ | grep -i '^http'      # expect: 403

  # 6. No directory listing anywhere:
  curl -s https://<app>/lib/ | grep -i 'index of'       # expect: (empty)

  # 7. Real client IP reaches the logs (NOT Fly's proxy IP). If this shows an
  #    fdaa::/ internal address, RemoteIPInternalProxy's CIDR is wrong -- safe
  #    (fail-closed) but ineffective; widen the trusted range and redeploy.
  docker exec <app> tail -n 5 /var/log/apache2/access.log

  # 8. The agent still works (evasive thresholds didn't trip it):
  python3 /workspace/skills/corkboard/script/corkboard.py list-pages   # or any normal op
  ```

- **Cold-start impact: negligible.** One extra apt package, two more modules
  loaded at Apache start, two fewer (`a2dismod`). No per-request cost beyond
  `mod_evasive`'s cheap hash lookup and `mod_remoteip`'s header parse.
- **Rollback:** revert the deploy; the confs are additive and touch no existing
  behavior beyond the (already-present) directory `Deny` gaining `vendor/`.

## Alternatives considered

- **`mod_security` + OWASP CRS in this RFC.** Rejected for now — see *Why
  mod_security is deferred*. It composes cleanly later; recording the
  DetectionOnly + Debian-package plan so it isn't re-litigated.
- **`X-Forwarded-For` instead of `Fly-Client-IP`.** Fly's docs recommend
  `Fly-Client-IP` when Fly is the front proxy: it's a single Fly-set value rather
  than a comma-separated chain that must be parsed right-to-left to avoid
  spoofing. If another proxy is ever put in front of Fly, switch to
  `RemoteIPHeader X-Forwarded-For` and trust *that* proxy — noted in the conf.
- **Tight `mod_evasive` thresholds (Debian defaults: 2 req/s per URI).** Rejected:
  they would block normal browsing and the agent within seconds. The values here
  are sized to the actual workload.
- **HSTS unconditional / with `preload`.** Unconditional would also tag direct
  HTTP hits to the container (harmless but wrong); `preload` requires list
  submission and affects subdomains — out of scope. Gating on
  `X-Forwarded-Proto` is the precise behavior.
- **Leave `vendor/` reachable.** Rejected: it's DokuWiki's Composer source on
  disk; no reason for it to be web-accessible. Same `Deny` class as `inc/`.

## Implementation notes

Implemented 2026-07-25. Build-time only — one apt package, two module
enable/disable lines, three new conf files and one edited conf; no DokuWiki or
skill change. The gate is green locally (`ruff check`, `ruff format --check`
over 11 files, `ty check`, 65/65 tests). Not yet built/deployed — the eight
verification steps need a running instance.

- **`Dockerfile`** — added `libapache2-mod-evasive` to apt; extended `a2enmod`
  with `remoteip reqtimeout evasive`; added `a2dismod -f autoindex status info
  cgi userdir` (`-f` because Debian flags `autoindex` essential); `COPY`s the
  three new confs into `/etc/apache2/conf-enabled/`.
- **`apache-deny-sensitive.conf`** (edited) — switched to a `<Directory>` grant
  + `<DirectoryMatch>` deny, added `vendor/`, and added `Options -Indexes
  -ExecCGI` (relative `+`/`-`, so `FollowSymLinks` is preserved).
- **`apache-hardening.conf`** (new) — `ServerTokens Prod` / `ServerSignature Off`
  / `TraceEnable Off`, the three security headers (HSTS gated via `SetEnvIf
  X-Forwarded-Proto "^https$" HTTPS=on`), and `RequestReadTimeout`.
- **`remoteip.conf`** (new) — `RemoteIPHeader Fly-Client-IP` with
  `RemoteIPInternalProxy fdaa::/48` (+ loopback).
- **`evasive.conf`** (new) — loose thresholds (`DOSPageCount 10`/`DOSPageInterval
  2`, `DOSSiteCount 100`/`DOSSiteInterval 2`), `DOSBlockingPeriod 30`,
  `DOSWhitelist 127.0.0.1`.
- **`README.md`** — added a "Hardened Apache layer" Features bullet and four
  "What's here" table rows (updated the `apache-deny-sensitive.conf` row, added
  `apache-hardening.conf` / `remoteip.conf` / `evasive.conf`).
- **`AGENTS.md`** — unchanged; it covers the corkboard skill / CI / RFC process,
  none of which this RFC touches (no new commands or workflows).

### Notes / deviations

- **Not done here (needs a live instance):** build the image and run the eight
  verification steps in *Migration / rollout*. The two highest-value checks are
  (7) the access log shows **real client IPs**, not Fly's proxy — if it shows an
  `fdaa::`/internal address, `RemoteIPInternalProxy`'s CIDR is wrong
  (fail-closed → safe but ineffective; widen the range and redeploy); and (4)
  HSTS is **absent on a direct HTTP hit** (no `X-Forwarded-Proto`) but **present
  over HTTPS via Fly**, proving the gating works.
- **`mod_evasive` is a per-process heuristic under prefork** (mod_php forces
  prefork — see *Background*). It catches a naive single-IP flood, not a
  distributed attack; a real DoS story would move to the edge (Fly) or `mod_qos`
  if ever needed.
- **`mod_security` + OWASP CRS remains deferred** to its own RFC (Decision #7);
  the plan — Debian `modsecurity-crs` package, `DetectionOnly` first — is
  recorded there.
