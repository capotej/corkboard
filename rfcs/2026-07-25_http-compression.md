# HTTP gzip compression at the Apache layer (`mod_deflate`)

**Date:** 2026-07-25
**Status:** Implemented

## Goal

Compress text responses over the wire with **gzip, applied at the Apache layer
by `mod_deflate`**, for both human browsers and the `corkboard` agent. DokuWiki
does no content-encoding of its own — `$conf['gzip_output']` stays `0` (locked)
— so Apache is the sole compressor. The agent opts into gzip
(`Accept-Encoding: gzip`) and decompresses responses itself, since `urllib` has
no transparent content-encoding handling and Brotli has no stdlib decoder.

Concretely, four small changes:

1. Enable `mod_deflate` (gzip only — no Brotli) via a one-file `compression.conf`
   using the standard `AddOutputFilterByType` recipe.
2. Add `Accept-Encoding: gzip` + gzip decode to the skill's `rpc_call()`.
3. Lock `$conf['gzip_output'] = 0` in `local.protected.php`.
4. Keep `$conf['compress'] = 1` (CSS/JS minification — see Background).

## Motivation

The wiki currently sends text responses **uncompressed**. The Dockerfile enables
only `rewrite headers expires`, and `$conf['gzip_output']` defaults to `0`. Pages,
CSS, JS, JSON-RPC, and feeds all go out as raw text.

This repo is deliberately performance-conscious — the comments in `entrypoint.sh`
and `dokuwiki-opcache.ini` recount real cold-start wins (dropping the blanket
`chown -R` to avoid ~20s of overlayfs copy-up, sizing OPcache for fast cold
starts). On-the-wire compression is the same class of low-risk, high-payoff
change: it costs nothing at startup and trims text transfer size materially
(text often compresses 70–80%).

**The agent, not browsers, is the bulk of the workload here** — and the API is
where compression pays off most. The `corkboard` skill's RPC calls routinely
move large payloads: `core.listPages` (recursive, with hashes),
`plugin.corkboard.*` `wanted`/`orphans`/`media-orphans` over a whole wiki, and
`core.getMedia` (base64 of an entire file). These are JSON text that compresses
extremely well, and they cross the wire on every gardening/edit run. Opting the
agent into gzip makes the hot path the main beneficiary, not a browser nicety.

## Background

### How this fits with DokuWiki's own settings

DokuWiki exposes two settings whose names mention "compress"; only one of them
is an on-the-wire *encoding*:

- **`$conf['gzip_output']`** (default `0`) — the only setting that produces a
  `Content-Encoding`. When `1`, DokuWiki gzips page XHTML *and* its own CSS/JS
  (via `inc/httputils.php`'s `http_cached` / `http_cached_finish`, which gate on
  this setting). **At `0` it sends nothing pre-encoded** — verified in source:

  ```php
  function http_cached_finish($file, $content) {
      ...
      if ($conf['gzip_output'] && DOKU_HAS_GZIP) {   // gated on gzip_output
          header('Content-Encoding: gzip');
          echo gzencode($content, 9, FORCE_GZIP);
      } else {
          echo $content;                              // raw, uncompressed
      }
  }
  ```

  We keep it `0` and lock it. This is the single fact that makes the whole design
  simple: with nothing arriving pre-encoded, `AddOutputFilterByType DEFLATE`
  needs no `Content-Encoding` guard and no `mod_filter` negotiation.

- **`$conf['compress']`** (default `1`) — **minification**, not encoding:
  `css.php`/`js.php` strip whitespace and comments (`css_compress`). It is
  complementary to gzip (minified text compresses smaller still), causes no
  double-encoding, and is strictly beneficial. **It stays `1`.** (The css.php
  comment `define('DOKU_DISABLE_GZIP_OUTPUT', 1) // we gzip ourself here` refers
  to disabling the *page-level* output buffer for these scripts, not to always
  gzipping — the actual gzip is the `gzip_output`-gated branch above.)

### The agent's stdlib constraints

The `corkboard` skill talks to the API through Python's **`urllib.request`**
(`skills/corkboard/script/corkboard.py`):

- **No transparent decompression.** Unlike `requests`/`httpx`/curl, `urllib`
  ignores `Content-Encoding`. If the skill sends `Accept-Encoding: gzip`, it must
  `gzip.decompress()` the body itself.
- **No Brotli in the stdlib.** The stdlib ships `gzip`, `zlib`, `bz2`, `lzma` —
  but decoding Brotli needs the third-party `Brotli`/`brotlicffi` package. The
  skill is stdlib-only by design (no `pyproject.toml`, no install step — see
  `AGENTS.md`), so the agent is gzip-only regardless of the server.

## Decisions

1. **Gzip only, via `mod_deflate`.** `AddOutputFilterByType DEFLATE` over the
   compressible text types. This is the boring, universally-deployed recipe; it
   covers pages, CSS, JS, JSON-RPC, feeds, and SVG. (See *Why gzip only* below
   for why Brotli is out.)

2. **Apache is the sole compressor; DokuWiki does none.** Lock
   `$conf['gzip_output'] = 0` so the Configuration Manager can't flip it on
   (which would double-encode). Because nothing DokuWiki serves arrives
   pre-encoded, no `Content-Encoding` guard is needed.

3. **Keep `$conf['compress'] = 1`.** It's minification, not encoding — see
   Background. Turning it off is a strict size loss with no conflict benefit.

4. **The agent opts into gzip.** `rpc_call()` sends `Accept-Encoding: gzip` and
   decodes gzip bodies with the stdlib `gzip` module, in **both** the success
   and HTTP-error paths (mod_deflate compresses error responses too). The skill
   stays dependency-free.

5. **No new runtime dependencies.** `mod_deflate` (and `mod_filter`, which
   provides `AddOutputFilterByType`) ship with `apache2-bin` in the base image.
   `a2enmod` is a no-op if already enabled.

## Technical details

### `Dockerfile`

Add `filter deflate` to the existing module-enable line and copy the conf:

```dockerfile
# rewrite = nice URLs/.htaccess; headers/expires = cache headers;
# filter+deflate = on-the-wire gzip (see rfcs/2026-07-25_http-compression.md).
RUN a2enmod rewrite headers expires filter deflate
```

```dockerfile
# Gzip text responses at the Apache layer.
COPY compression.conf /etc/apache2/conf-enabled/compression.conf
```

(Place the `COPY` alongside the existing
`COPY apache-deny-sensitive.conf /etc/apache2/conf-enabled/dokuwiki-security.conf`.)

### `compression.conf` (new, at repo root)

```apache
# Gzip text responses at the Apache layer.
# See rfcs/2026-07-25_http-compression.md.
#
# DokuWiki does NO content-encoding of its own: css.php/js.php/pages only
# self-gzip when $conf['gzip_output']=1 (verified in inc/httputils.php's
# http_cached_finish), and we keep gzip_output=0 (locked in local.protected.php).
# So nothing DokuWiki serves arrives already-encoded, and the simple
# AddOutputFilterByType recipe needs no Content-Encoding guard.

<IfModule mod_deflate.c>
    AddOutputFilterByType DEFLATE \
        text/html text/plain text/css text/xml text/javascript text/csv \
        application/javascript application/json application/xml \
        application/xhtml+xml application/rss+xml application/atom+xml \
        image/svg+xml
</IfModule>

# mod_deflate already emits `Vary: Accept-Encoding` so caches key correctly;
# nothing else is required.
```

Binary / already-compressed types (PNG, JPEG, WEBP, ZIP, gz media uploads, fonts)
are absent from the list on purpose, so they pass through untouched. Media
downloads via `lib/exe/fetch.php` set binary types and are skipped too.

### `skills/corkboard/script/corkboard.py` (agent opts into gzip)

`gzip` is stdlib, so the skill stays dependency-free. Add the import, advertise
gzip, and decode in both response paths:

```python
import base64
import gzip  # stdlib — gzip only; brotli isn't in the stdlib
import json
import os
import re
import sys
import urllib.error
import urllib.request


def _gunzip_if_needed(raw, headers):
    """Decode a response body, gunzipping when the server used Content-Encoding.
    urllib has no transparent content-encoding handling (unlike requests/curl),
    so we ask for gzip (Accept-Encoding below) and decode it here. Applied to
    both 200 and 4xx bodies: mod_deflate compresses error responses too."""
    if "gzip" in headers.get("Content-Encoding", "").lower():
        return gzip.decompress(raw)
    return raw
```

```python
def rpc_call(method, params=None):
    ...
    req = urllib.request.Request(
        f"{url}/lib/exe/jsonrpc.php",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": _b64auth(),
            # Ask the server to gzip the response. urllib has no transparent
            # content-encoding handling, so we decode via _gunzip_if_needed.
            "Accept-Encoding": "gzip",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            obj = json.loads(_gunzip_if_needed(r.read(), r.headers).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = _gunzip_if_needed(e.read(), e.headers).decode("utf-8", "replace")[:300]
        ...  # unchanged: parse the JSON-RPC error, else surface the body
```

`gzip.decompress` buffers the whole body, but the skill already reads the entire
response into memory (`r.read()`), so there's no memory regression even for the
largest payloads (`listPages` recursive, `getMedia` base64). Streaming via
`gzip.GzipFile` for very large responses is possible later, out of scope here.

### `conf-seed/local.protected.php`

Lock the setting so Apache stays the sole compressor:

```php
// Apache (mod_deflate) is the sole on-the-wire compressor; DokuWiki must not
// also gzip its own output (it would double-encode). The default is 0; locked
// here so the web Configuration Manager can't flip it on.
// See rfcs/2026-07-25_http-compression.md.
$conf['gzip_output'] = 0;
```

(The entrypoint already syncs `local.protected.php` from this seed on every boot,
so this takes effect on the next deploy with no migration.)

## Why gzip only (no Brotli)

Brotli beats gzip by ~15–20% on text, but enabling it here costs
disproportionate complexity for this workload:

- Brotli + gzip-fallback **requires `mod_filter` provider negotiation**.
  `AddOutputFilterByType` is *additive* (per the
  [`mod_filter` docs](https://httpd.apache.org/docs/2.4/mod/mod_filter.html)),
  so registering both `DEFLATE` and `BROTLI_COMPRESS` for the same type runs
  both filters and double-encodes. The only correct shape is a `FilterProvider`
  chain with mutually-exclusive providers and a `Content-Encoding` guard — real
  config with real edge cases.
- That complexity benefits **browsers only**. The agent is gzip-only (no stdlib
  Brotli decoder), and the agent is the bulk of this wiki's traffic. Spending the
  `mod_filter` complexity to optimize the minority path is a poor trade here.
- Gzip via `AddOutputFilterByType` is the boring, robust, widely-deployed choice.
  Brotli-advertising clients simply receive gzip (every browser that sends `br`
  also sends `gzip`).

Brotli stays an additive, low-lock-in option: if it's ever wanted, swap the
`AddOutputFilterByType` block for the `mod_filter` chain and `a2enmod brotli`.

## Migration / rollout

- **Build-time only.** New `compression.conf`, a two-token `Dockerfile` edit, one
  line in `conf-seed/local.protected.php`, and the `rpc_call()` change. No data
  migration, no secrets, no env vars; takes effect on next deploy.
- **Verify after deploy** (all should hold against a live instance):

  ```bash
  # 1. Pages are gzipped for a gzip client:
  curl -sI -H 'Accept-Encoding: gzip' https://<app>/start | grep -i 'content-encoding\|vary'
  #   expect: content-encoding: gzip   and   vary: Accept-Encoding

  # 2. JSON-RPC (the agent's path) is gzipped:
  curl -s -D - -o /dev/null -H 'Accept-Encoding: gzip' -H 'Content-Type: application/json' \
    -u "agent:$AGENT_PASS" -d '{"jsonrpc":"2.0","method":"core.getWikiVersion","id":1}' \
    https://<app>/lib/exe/jsonrpc.php | grep -i 'content-encoding\|content-type'
  #   expect: content-encoding: gzip   on application/json

  # 3. CSS is gzipped exactly once (DokuWiki does NOT pre-gzip while gzip_output=0):
  curl -sI -H 'Accept-Encoding: gzip' 'https://<app>/lib/exe/css.php?t=dokuwiki' | grep -i 'content-encoding\|content-type'
  #   expect: content-encoding: gzip   (single value, from mod_deflate)

  # 4. Brotli-advertising clients still get gzip (no brotli module => graceful fallback):
  curl -sI -H 'Accept-Encoding: br,gzip' https://<app>/start | grep -i 'content-encoding'
  #   expect: content-encoding: gzip
  ```

- **Cold-start impact: none measurable.** `mod_deflate`/`mod_filter` load at
  Apache start regardless of traffic; the only cost is per-request CPU to
  compress, which is cheap (zlib is fast in C) and dwarfed by PHP execution,
  even for the larger agent payloads.
- The JSON-RPC self-test in `entrypoint.sh` uses `curl -s`, which does not
  advertise `Accept-Encoding` by default, so its response stays uncompressed and
  the startup log stays readable. Adding `--compressed` there is optional
  hardening (it also exercises the compression path) — not required.

## Alternatives considered

- **`$conf['gzip_output'] = 1` (DokuWiki does it in PHP).** Rejected: PHP-level,
  slower, and — more importantly — it is mutually exclusive with Apache
  compression (double-encodes). Picking the Apache layer is both faster and the
  single source of truth.
- **Brotli + gzip via `mod_filter`.** Rejected for this workload — see *Why gzip
  only* above. Browser-only benefit does not justify the provider-chain
  complexity and the `Content-Encoding` guard.
- **A CDN/proxy doing compression.** Out of scope; Fly's proxy passes responses
  through and does not compress. `mod_deflate`'s automatic `Vary:
  Accept-Encoding` keeps caching correct if a CDN is added later.

## Implementation notes

Implemented 2026-07-25. The change is build-time plus one skill edit; the gate
is green locally (`ruff check`, `ruff format --check` over 9 files, `ty check`,
65/65 tests). Not yet deployed — the live verification curls require a running
instance.

- **`compression.conf`** (new) — `AddOutputFilterByType DEFLATE` over the text
  types, wrapped in `<IfModule mod_deflate.c>`.
- **`Dockerfile`** — `a2enmod rewrite headers expires filter deflate`; added
  `COPY compression.conf /etc/apache2/conf-enabled/compression.conf`.
- **`conf-seed/local.protected.php`** — added `$conf['gzip_output'] = 0;` so
  Apache stays the sole compressor and the web Configuration Manager can't flip
  it on (which would double-encode).
- **`skills/corkboard/script/corkboard.py`** — added the `gzip` import,
  `Accept-Encoding: gzip` on the request, and `_gunzip_if_needed()` applied to
  **both** the success and `HTTPError` bodies (mod_deflate compresses error
  responses too). Stays dependency-free.
- **`README.md`** — added a Features bullet and a `compression.conf` row in
  "What's here".

### Notes / deviations

- The `rpc_call` snippet is shown as a function body (not a dedented excerpt)
  so its indentation is `ruff format`-clean — ruff formats Python in `.md`; see
  the ruff/Markdown note in `AGENTS.md`.
- **Not done here (needs a live instance):** build the image and run the four
  verification curls (Brotli clients fall back to `gzip`; CSS gzipped exactly
  once; JSON-RPC gzipped; pages gzipped), then deploy.
- **Optional, deferred:** the `entrypoint.sh` JSON-RPC self-test `curl` carries
  no `Accept-Encoding`, so it already stays readable; adding `--compressed` is
  left for a future tidy-up.
