# OWASP CRS WAF via mod_security (detect → enforce)

**Date:** 2026-07-25
**Status:** Proposed — Phase 1 code-complete; pending deploy + soak (Phase 2
gated on Phase-1 FP data)

## Goal

Add an application-layer WAF — **mod_security v2 + the OWASP Core Rule Set
(CRS) 4** — to the Apache that already fronts the wiki, rolled out in **two
explicit phases**:

1. **Phase 1 — Detect.** `SecRuleEngine DetectionOnly`: every CRS rule runs and
   **logs**, nothing is blocked. The output of this phase is an inventory of
   which rules fire on which endpoints — above all on the `corkboard` agent's
   JSON-RPC writes and human page saves — so we can write a DokuWiki-specific
   exclusion set.
2. **Phase 2 — Enforce.** `SecRuleEngine On`: with exclusions in place, CRS
   blocks (403). Inbound anomaly threshold starts high and is lowered toward the
   default as confidence grows.

CRS is installed as **CRS 4, pinned from upstream** (not the Debian package,
which ships CRS 3.x). Detection data flows to the **Grafana store Fly already
ships our logs to** — no new persistence to build. No DokuWiki or skill change.

This is the WAF the Apache-hardening RFC deferred (its Decision #7); the plan
recorded there (DetectionOnly first, Debian package) is revised here — see
*Why CRS 4 from upstream, not the Debian package*.

## Motivation

The Apache-hardening RFC hardened the *server and transport* layers: fingerprint,
headers, Slowloris, a DoS heuristic, real client IP. It deliberately did **not**
inspect request *contents*. This RFC adds that: a ruleset that looks at the
actual bytes of a request — parameters, headers, JSON bodies — and flags the
attack patterns (XSS, SQLi, RCE, LFI, protocol anomalies) that directory `Deny`
and `mod_evasive` never see.

The reason it was deferred, and the reason the two-phase shape is
non-negotiable, is the same: **this wiki's primary write traffic is exactly what
a WAF false-positives on.** The `corkboard` agent edits pages over JSON-RPC
(`core.putPage`, compare-and-swap writes) with arbitrary content — wiki markup,
fenced code blocks, shell snippets, regex — and humans save the same via the web
UI. CRS will flag that text as XSS / RCE / SQLi. Shipping `SecRuleEngine On` on
day one would block legitimate edits — including the agent's — until the
exclusion set is right. So Phase 1 collects the data, Phase 2 acts on it.

## Background

### CRS 4 vs the Debian package

A 2025-10 Debian Trixie writeup found **CRS 4 not yet packaged** in Debian — the
`modsecurity-crs` apt package ships **CRS 3.x**. CRS 4's headline improvement is
*fewer false positives* and stronger rules; for an agent that writes arbitrary
page content, that is the make-or-break property. So we pin CRS 4 from upstream
into the image at a tagged release, verified against a pinned checksum (the same
pattern the Dockerfile already uses for the DokuWiki tarball). The version we run
is explicit, not "whatever Debian packaged."

### How CRS evaluates a request (anomaly scoring)

CRS 4 defaults to **anomaly scoring mode**: each matching rule adds to a per-request
score, and a request is blocked only when the cumulative **inbound anomaly score**
meets the threshold (default **5** — i.e. one CRITICAL match). Key implications:

- A request can hit several rules; the score is cumulative, so the *block* decision
  is separated from any single rule.
- **Paranoia Level (PL)** selects how many rules are active. **PL1 is the default**
  ("most core rules… you should face FPs rarely"). We use PL1.
- CRS's own deployment guidance for a fresh install is **"start high and
  decrease"**: begin Phase 2 with an elevated threshold (e.g. 7–10, so a single
  critical doesn't block) and lower toward 5 as the exclusion set matures.

### Include order (load-bearing)

The CRS docs are explicit that files load in this order:

1. `modsecurity.conf` (the engine: `SecRuleEngine`, `SecRequestBodyAccess`, audit),
2. `crs-setup.conf` (PL, default actions, allowed methods/content-types),
3. `rules/*.conf` (the CRS rules themselves).

Debian loads `modsecurity.conf` via `mods-enabled/security2.conf`
(`IncludeOptional /etc/modsecurity/*.conf`), and `mods-enabled` is included before
`conf-enabled` in `apache2.conf` — so placing our CRS `Include` directives in a
`conf-enabled/` file guarantees the engine config loads first. Getting this order
wrong means CRS silently runs with wrong defaults.

### Two things must be on for body inspection

- **`SecRequestBodyAccess On`** (default in `modsecurity.conf-recommended`). Without
  it, mod_security never sees POST bodies — which is the whole point here, since
  page content arrives as a JSON-RPC POST body. Phase 1 is useless without it.
- **`SecRequestBodyLimit` and `SecRequestBodyNoFilesLimit`.** Two distinct ceilings,
  and the smaller one is the trap. `SecRequestBodyLimit` (default ~13 MB) caps the
  whole body; `SecRequestBodyNoFilesLimit` caps the *non-file* portion (form
  fields + JSON) and defaults to only **1 MB**. The corkboard agent uploads media
  via JSON-RPC `core.saveMedia`, base64-encoding the file **inside the JSON body**
  (not a multipart file part), so the entire payload is "no files data" and a
  1.0 MB file (~1.3 MB base64) is rejected at the 1 MB default — even in
  DetectionOnly (size limits are protocol-level, not rule matches, so they fire
  regardless of engine mode). All agent traffic is JSON-RPC with zero multipart
  parts, so the files/no-files split carries no information for this workload;
  `modsecurity.conf` sets `SecRequestBodyNoFilesLimit` to track
  `SecRequestBodyLimit`. This was the first real Phase-1 finding (a blocker, not
  a Phase-2 tuning item).

### Where the detection data lands (no new persistence)

mod_security emits its findings through **Apache's error log**, and in this image
the error log → stderr → Fly → **Grafana** (already durable for "all our logs").
So a Phase-1 soak needs **zero** new persistence plumbing: the rule-id / `Msg:` /
URI / severity lines (and usually the matched snippet in `[data …]`) show up in
Grafana automatically. We set CRS's default action to **`log,noauditlog`** for
Phase 1 — lean, all signal in Grafana, and it keeps page-content bodies out of the
shared log store. The verbose `SecAuditLog` (full request/response bodies) is an
*escalation* path, documented but off by default — see Decision #5.

### What the existing stack gives us for free

- **`mod_remoteip` is already loaded** (Apache-hardening RFC) → CRS keys on the
  **real client IP**, not Fly's proxy.
- **`mod_deflate`** compresses *responses*; request bodies are uncompressed JSON,
  so no decompression step is needed for inspection.
- **`mod_xsendfile`** + `SecResponseBodyAccess Off` (below) mean served media is
  never inspected — correct, media is binary.
- **Prefork MPM** (mod_php forces it): mod_security v2 is C and per-process; the
  per-request regex cost is real but cheap relative to PHP execution for this
  workload.

### There is no DokuWiki CRS exclusion plugin

CRS 4 moved application-specific false-positive exclusions out of core into
**plugins** (e.g. `wordpress-rule-exclusions-plugin`). **There is no DokuWiki
plugin.** So the exclusion set for Phase 2 is hand-written against this wiki's
real traffic — another reason Phase 1's data matters.

## The two phases

### Phase 1 — Detect (`SecRuleEngine DetectionOnly`)

- PL1, anomaly-scoring defaults, `SecRequestBodyAccess On`,
  `SecResponseBodyAccess Off` (see Decision #7).
- Default action `log,noauditlog` → every rule match writes to the **error log →
  Grafana**, nothing is blocked.
- **Soak window:** ~1–2 weeks of real traffic (agent gardening runs + human use).
- **Output:** an inventory — grouped by rule id and URI — of what fires where.
  The expected hot spots: `core.putPage` / `do=save` bodies (wiki markup, code
  blocks → XSS/RCE rules), and any rule that mis-flags normal browsing.

### Phase 2 — Enforce (`SecRuleEngine On`)

- Write `crs-exclusions.conf` from Phase 1's inventory (narrowly scoped per
  endpoint — see Decision #6).
- Flip `SecRuleEngine On`.
- Start with an elevated **inbound anomaly threshold** (e.g. `tx.inbound_anomaly_score_threshold = 8`),
  lower toward the default `5` as confidence grows (CRS's "start high and
  decrease").
- **Gate criteria to leave Phase 1:** (a) the soak window elapsed; (b) every
  *recurring* FP has a scoped exclusion; (c) a full agent gardening pass
  (`listPages`, `putPage`, `getMedia`, batch edits) and a human save produce
  **zero** residual FPs after exclusions; (d) no unexplained high-severity events
  remain open.

## Decisions

1. **CRS 4, pinned from upstream, checksum-verified** — not the Debian package
   (CRS 3.x). Pinned `ARG CRS_VERSION` + a pinned SHA-256, verified the same way
   the DokuWiki tarball already is. See *Why CRS 4 from upstream*.
2. **mod_security v2 via `libapache2-mod-security2` + `a2enmod security2`.** The
   Apache module (not libmodsecurity3/nginx); matches the `php:*-apache` image.
3. **Phase 1 `DetectionOnly`, Phase 2 `On`.** Non-negotiable given the FP profile
   — see *The false-positive risk*.
4. **PL1, anomaly-scoring defaults.** The documented balanced starting point.
   Threshold is the Phase-2 tuning knob ("start high and decrease"), not PL.
5. **Phase 1 logs to the error log → Grafana (`log,noauditlog`); audit log off by
   default.** All detection signal is already durable in Grafana; the verbose
   `SecAuditLog` (full bodies) is an *escalation*: enable `SecAuditEngine
   RelevantOnly` + point `SecAuditLog` at the persistent volume
   (`/dokuwiki-persistent/modsec/`, with rotation) only for rules the error-log
   line can't disambiguate. Kept off by default because it captures page-content
   bodies (sensitive) and is chatty.
6. **Inspect everything in Phase 1 (incl. the agent's JSON-RPC endpoint); scope
   exclusions narrowly in Phase 2.** The agent writes content that **humans then
   view** — a stored-XSS vector — so the API is *not* blanket-exempt. Phase 1
   collects FP data on it; Phase 2 writes per-endpoint content-rule exclusions
   (e.g. relax the 941xxx body inspection on `/lib/exe/jsonrpc.php` and `do=save`),
   not a global ruleset weakening. See *Security considerations*.
7. **`SecResponseBodyAccess Off` for Phase 1.** Response inspection checks
   outbound leakage; on DokuWiki it would scan rendered HTML (noisy FPs) and
   carries a documented **RFDoS** risk. Inbound is where the value is.
8. **Hand-write the DokuWiki exclusion set.** No CRS plugin exists for DokuWiki
   (Background). The exclusions live in one reviewed `crs-exclusions.conf`, loaded
   *before* the CRS rules.

## Technical details

### `Dockerfile`

```dockerfile
    apt-get install -y --no-install-recommends \
        libpng-dev libjpeg62-turbo-dev libfreetype6-dev \
        libzip-dev libicu-dev \
        libapache2-mod-xsendfile \
        libapache2-mod-evasive \
        libapache2-mod-security2 \
        curl wget ca-certificates; \
```

```dockerfile
# mod_security engine + OWASP CRS 4 WAF (rfcs/2026-07-25_owasp-crs-waf.md).
# a2enmod security2 loads the module; its conf includes /etc/modsecurity/*.conf.
RUN a2enmod security2

# CRS 4 from upstream (the Debian modsecurity-crs package ships CRS 3.x).
# Pinned + SHA-256-verified, the same pattern as the DokuWiki tarball above.
ARG CRS_VERSION=v4.19.0
ARG CRS_SHA256=<recompute with: curl -sL <url> | sha256sum>
RUN set -eux; \
    rm -rf /etc/modsecurity/crs; \
    wget -qO /tmp/crs.tgz "https://github.com/coreruleset/coreruleset/archive/refs/tags/${CRS_VERSION}.tar.gz"; \
    echo "${CRS_SHA256}  /tmp/crs.tgz" | sha256sum -c -; \
    mkdir -p /etc/modsecurity/crs; \
    tar -xzf /tmp/crs.tgz -C /etc/modsecurity/crs --strip-components=1; \
    rm /tmp/crs.tgz
```

```dockerfile
# mod_security engine config (Phase 1: DetectionOnly) + CRS setup + the Include
# glue. See rfcs/2026-07-25_owasp-crs-waf.md.
COPY modsecurity.conf /etc/modsecurity/modsecurity.conf
COPY crs-setup.conf /etc/modsecurity/crs/crs-setup.conf
COPY modsecurity-crs.conf /etc/apache2/conf-enabled/modsecurity-crs.conf
```

(When bumping `CRS_VERSION`, recompute `CRS_SHA256` — same workflow as
`DOKUWIKI_SHA256`. GPG-verifying the release signature is the CRS-blessed
alternative if you prefer it over the checksum.)

### `modsecurity.conf` (new — engine, Phase 1)

A trimmed `modsecurity.conf-recommended` with our Phase-1 choices:

```apache
# mod_security engine config. Phase 1 = DetectionOnly (log, never block).
# Flip SecRuleEngine to On for Phase 2. See rfcs/2026-07-25_owasp-crs-waf.md.

SecRuleEngine DetectionOnly

# Inspect request bodies — REQUIRED: page content arrives as a JSON-RPC POST
# body, so without this Phase 1 collects nothing useful.
SecRequestBodyAccess On
# Don't inspect responses: rendered-HTML FPs + the documented RFDoS risk.
# Inbound is where the value is.
SecResponseBodyAccess Off

# Overall body ceiling; oversize is rejected before inspection. ~13 MiB.
SecRequestBodyLimit 13107200
# Cap on the NON-file portion of the body (form fields + JSON). mod_security's
# default is only 1 MiB, and the agent's core.saveMedia uploads base64-encode the
# file INSIDE the JSON body (no multipart file parts), so the whole payload is
# "no files data" and was rejected at 1.0 MiB files -- even in DetectionOnly.
# Set it to track SecRequestBodyLimit; all agent traffic is JSON-RPC (zero
# multipart parts), so the files/no-files split is meaningless here. Bump together.
SecRequestBodyNoFilesLimit 13107200

# Audit log OFF by default: detection signal goes to the error log -> Fly ->
# Grafana (already durable). Escalation path in the RFC (Decision #5).
SecAuditEngine Off
SecAuditLogRelevantStatus "^(?:5|4(?!04))"
# If audit is ever turned on, RelevantOnly + the persistent volume + rotation.

# Default to the JSON body processor for application/json (the agent's RPC),
# so CRS rules inspect the structured payload.
SecRule REQUEST_HEADERS:Content-Type "application/json" \
    "id:200001,phase:1,pass,nolog,ctl:requestBodyProcessor=JSON"
```

### `crs-setup.conf` (new — CRS tuning, Phase 1)

A copy of upstream `crs-setup.conf.example` with the Phase-1 edits. The defaults
already cover DokuWiki (POST is an allowed method; `application/json` and
`multipart/form-data` are allowed content-types), so the only changes are the
logging action and (optionally) declaring PL1 explicitly:

```apache
# Phase 1: log to the ERROR LOG only (-> Grafana), no audit log. Lean + durable.
# (Upstream default is log,auditlog; we drop auditlog to keep page-content bodies
# out of the shared log store and to avoid the chatty audit file.)
SecDefaultAction "phase:1,log,noauditlog,pass"
SecDefaultAction "phase:2,log,noauditlog,pass"

# Paranoia level 1 (the default; stated explicitly).
SecAction "id:900000,phase:1,pass,nolog,t:none,\
  setvar:tx.blocking_paranoia_level=1"

# Allowed methods / content-types: defaults already include POST + JSON +
# multipart, which is all DokuWiki and the agent use. No change needed here.
```

(Phase 2 adds `setvar:tx.inbound_anomaly_score_threshold=8` here, then lowers it.)

### `modsecurity-crs.conf` (new — the Include glue)

Loads CRS in the required order, guarded so Apache still starts if the module
isn't loaded:

```apache
# Include OWASP CRS 4 in the required order:
#   1. modsecurity.conf (loaded by security2.conf via /etc/modsecurity/*.conf)
#   2. crs-setup.conf  (below)
#   3. rules/*.conf    (below)
# mods-enabled/ loads before conf-enabled/ in apache2.conf, so the engine config
# is in place by the time these run. See rfcs/2026-07-25_owasp-crs-waf.md.
<IfModule security2_module>
    IncludeOptional /etc/modsecurity/crs/crs-setup.conf
    IncludeOptional /etc/modsecurity/crs/rules/*.conf
</IfModule>
```

### Phase 2: `crs-exclusions.conf` (skeleton — filled from Phase 1 data)

Loaded *before* the CRS rules. Empty in Phase 1; Phase 2 fills it from the Grafana
inventory. The expected shape (placeholder IDs — the real ones come from Phase 1):

```apache
# DokuWiki / corkboard-agent exclusions, written from Phase-1 detection data.
# Narrowly scoped per endpoint: relax ONLY the content-inspection rules that
# false-positive on wiki markup / code, only on the write endpoints. Do NOT
# weaken the ruleset globally. See rfcs/2026-07-25_owasp-crs-waf.md.

# Example shape (replace IDs after Phase 1):
# <LocationMatch "^/lib/exe/jsonrpc\.php">
#     SecRuleRemoveById 941100 941160 932130   # XSS / RCE on agent page writes
# </LocationMatch>
```

### No `conf-seed` or skill change

All WAF config is Apache-side and image-baked. DokuWiki and `corkboard.py` are
untouched — the agent sends normal JSON-RPC; whether a request is logged or
blocked is invisible to it except as an HTTP status (a Phase-2 403 would surface
as an RPC error, which is exactly the signal to tune against).

## The false-positive risk (the crux)

This is why the two-phase shape exists. Concretely, expect CRS to fire on:

- **`core.putPage` / `do=save` bodies** — wiki markup and fenced code blocks
  contain `<script>`-like fragments, `system(`/`eval(`-like shell, and `union
  select`-like text. The **941xxx (XSS)**, **932xxx (RCE)**, and **942xxx (SQLi)**
  rule chains will match benign content. This is the bulk of Phase-1 noise.
- **`core.getMedia` / upload bodies** — large base64 / multipart payloads; size,
  not pattern, is the risk (the body limit).
- **Normal browsing** — the occasional `?id=` / search query that looks injection-y.

The key reframing: DokuWiki **already escapes/sanitizes content on render** (it's
a wiki; it has its own XSS mitigations). So a lot of what CRS flags as "XSS" is
content DokuWiki would have rendered safely anyway. CRS here is **defense-in-depth
on top of the application's own protections**, not a replacement for them — which
is also why narrowly-scoped content exclusions on the write endpoints (Decision
#6) are an acceptable trade and not a security hole.

Phase 1's job is to turn "expect FPs" into a concrete, reviewed list. Phase 2 acts
only on that list.

## Security considerations

- **Stored XSS via the agent is a real vector** — the agent writes pages humans
  view. This is *why* the API isn't blanket-exempted (Decision #6): even
  authenticated, programmatic writes of rendered content should be inspected,
  with content-rule *exclusions* scoped to confirmed FPs rather than a wholesale
  pass. DokuWiki's own escaping is the primary defense; CRS is the backstop.
- **WAF ≠ access control.** CRS inspects content; it does not replace DokuWiki's
  ACL (`@ALL 0`, `@api`/`@admin` gate on the API) or the directory `Deny`.
- **Request-body limit vs uploads.** Two limits, and the smaller one (`SecRequestBodyNoFilesLimit`, default 1 MiB) was the actual blocker: the agent's JSON-RPC `core.saveMedia` base64-encodes the file *inside the JSON body*, so the whole payload counts as "no files data" and a 1.0 MiB file was rejected at the 1 MiB default — even in DetectionOnly. Fixed in Phase 1 by raising it to track `SecRequestBodyLimit` (13 MiB); revisit if larger uploads are ever needed. (The overall `SecRequestBodyLimit` was never the constraint in practice.)
- **Audit log captures bodies.** Kept off by default (Decision #5); if enabled,
  it includes page content and possibly auth headers — keep it on the private
  volume, not Grafana, and rotate it.
- **Bypass surface.** `SecResponseBodyAccess Off` and any Phase-2 exclusions each
  open a narrow bypass; both are deliberate, scoped, and documented trade-offs,
  not blanket weakenings.

## Migration / rollout

### Phase 1 — Detect

- **Build-time only.** One apt package, a pinned+verified CRS download, four conf
  files, one `a2enmod`. No data migration; takes effect on next deploy.
- **Verify after deploy** (inside the container + via Grafana):

  ```bash
  # 1. Module + engine loaded, config valid:
  docker exec <app> apache2ctl -M | grep security2          # expect: security2_module
  docker exec <app> apache2ctl -t                            # expect: Syntax OK

  # 2. DetectionOnly is ACTIVE: a classic attack payload is SERVED (200) but LOGGED.
  curl -s -o /dev/null -w '%{http_code}\n' 'https://<app>/?id=1%27%20OR%20%271%27=%271'
  #   expect: 200   (NOT 403 — we're in DetectionOnly)
  #   then in Grafana: a ModSecurity entry with id 942xxx (SQLi), the URI, severity.

  # 3. Normal traffic is unaffected — run a full agent gardening pass and a
  #    human save; confirm 200s and that the only Grafana entries are expected
  #    content-rule FPs on the write endpoints.

  # 4. Request-body inspection is actually on: PUT a page whose body contains a
  #    benign <script>-ish snippet via the agent; confirm a 941xxx entry appears.
  ```

- **Cold-start impact:** non-trivial but bounded — mod_security loads at Apache
  start and runs per request. Watch Phase-1 cold start (~7 s baseline) for
  regression; CRS regex cost is dwarfed by PHP but is the one new per-request cost.

### Phase 2 — Enforce

```bash
  # 1. After writing crs-exclusions.conf from the Phase-1 inventory and flipping
  #    SecRuleEngine -> On in modsecurity.conf, redeploy.

  # 2. The same attack payload is now BLOCKED:
  curl -s -o /dev/null -w '%{http_code}\n' 'https://<app>/?id=1%27%20OR%20%271%27=%271'
  #   expect: 403

  # 3. Legit traffic still passes (the exclusion set works): full agent pass +
  #    human save return 200 with no new Grafana blocks.

  # 4. Lower tx.inbound_anomaly_score_threshold toward 5 over the following days
  #    as confidence grows; watch Grafana for new blocks to tune.
```

- **Rollback:** Phase 2 → flip `SecRuleEngine` back to `DetectionOnly` and
  redeploy (one-line, image-baked). Phase 1 → remove the four confs / the
  `a2enmod`; the wiki is unchanged either way (CRS in DetectionOnly alters
  nothing about how the wiki behaves).

## Alternatives considered

- **Debian `modsecurity-crs` apt package (CRS 3.x).** Simplest, but ships the
  older ruleset with *more* FPs — the worst property for this workload — and lags
  upstream. Revisit only if the package ships CRS 4 by implementation time.
  Rejected for now (Decision #1).
- **Ship `On` immediately with pre-written exclusions.** Rejected: no off-the-shelf
  DokuWiki exclusion set exists, so we'd be guessing — high risk of blocking the
  agent or human edits. Phase 1 collects the ground truth first.
- **Blanket-exempt the agent's JSON-RPC endpoint.** Rejected (Decision #6): the
  agent writes rendered content → stored-XSS vector. Narrow content-rule
  exclusions only.
- **Self-contained mode (deny on first match).** Anomaly scoring gives richer
  logs and a tunable threshold; keep the default.
- **Sampling percentage to ease in.** An alternative to "start threshold high";
  means some requests bypass CRS entirely. Less attractive than a high threshold,
  which inspects everything but tolerates a few matches before blocking.
- **An edge/managed WAF (Cloudflare, Fly).** Fly offers no managed WAF; an
  external edge WAF is a bigger architecture change and wouldn't see the
  authenticated JSON-RPC bodies the same way. mod_security at the origin is the
  fit here.

## Implementation checklist

### Phase 1 — Detect

- [x] `Dockerfile`: add `libapache2-mod-security2` to apt; `ARG CRS_VERSION` +
      pinned `CRS_SHA256`; download+verify+extract CRS 4 to
      `/etc/modsecurity/crs`; `a2enmod security2`; `COPY` the three confs.
- [x] `modsecurity.conf`: `SecRuleEngine DetectionOnly`, `SecRequestBodyAccess On`,
      `SecResponseBodyAccess Off`, audit off, JSON body processor.
- [x] `crs-setup.conf`: default action `log,noauditlog`, PL1 (explicit).
- [x] `modsecurity-crs.conf`: the Include glue, `IfModule`-guarded.
- [ ] Deploy, run the Phase-1 verification curls; confirm detection entries land
      in Grafana and normal traffic is unaffected.
- [ ] Soak ~1–2 weeks; build the rule-id × URI FP inventory in Grafana.

**Phase 1 — implemented (code):** CRS pinned at **`v4.28.0`**
(SHA-256 `d8acc96f25ad07c8e3a595a23c797324f6d77e59ddf9e26e90dd95ebd2e676ce`),
verified the same way as the DokuWiki tarball. Four confs shipped:
`modsecurity.conf`, `crs-setup.conf`, `modsecurity-crs.conf`, and
`crs-exclusions.conf`. The exclusions file (RFC Decision #8) was created in
Phase 1 (not deferred to Phase 2) to hold a favicon healthcheck exclusion:
Fly's `fly.toml` healthcheck GETs the static `.ico` every 10s, which tripped
minor PL1 protocol rules and -- with CRS's default `tx.reporting_level=4` --
flooded the log with rule 980170 per-request summaries; a phase:1
`ctl:ruleEngine=Off` for `/favicon.ico$` silences it. **Placement
gotcha:** `crs-exclusions.conf` lives in `/etc/modsecurity/crs/` (alongside
`crs-setup.conf`), NOT `/etc/modsecurity/` root — Debian `security2.conf`
runs `IncludeOptional /etc/modsecurity/*.conf`, so a root-level file would be
double-loaded (glob + our explicit include) → duplicate rule id → Apache
fails to start. The `crs/` subdir is outside the non-recursive glob. Phase-2
content-rule FP
exclusions get added to the same file. **Body-limit fix (Phase 1):** the agent's
`core.saveMedia` uploads base64-encode the file *inside the JSON body* (not
multipart), so the whole payload counts as mod_security's "no files data" and
tripped `SecRequestBodyNoFilesLimit`'s 1 MiB default at 1.0 MiB files — *even in
DetectionOnly*, since size limits are protocol-level rejections, not rule
matches. `modsecurity.conf` now sets it to track `SecRequestBodyLimit` (13 MiB);
all agent traffic is JSON-RPC with zero multipart file parts, so the
files/no-files split carries no information here. (README 'Features' + 'What's
here' rows and the checklist→notes conversion are the last Phase-2 item, per the
convention in `AGENTS.md`.)

### Phase 2 — Enforce (gated on Phase-1 data)

- [ ] Write `crs-exclusions.conf` from the inventory (narrow, per-endpoint).
- [ ] `modsecurity.conf`: `SecRuleEngine On`.
- [ ] `crs-setup.conf`: `tx.inbound_anomaly_score_threshold = 8` (then lower).
- [ ] Deploy, run the Phase-2 verification curls (attack → 403, legit → 200);
      lower the threshold toward 5 over the following days.
- [ ] On moving to Implemented: update `README.md` (Features + "What's here"
      rows) and replace this checklist with implementation notes (per the RFC
      convention in `AGENTS.md`).
