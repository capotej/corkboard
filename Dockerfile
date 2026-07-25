# DokuWiki on Fly.io
# DokuWiki is a flat-file wiki (no database), so all we need is PHP + Apache
# and a persistent volume mounted for data/, conf/ and installed plugins/templates.

FROM php:8.5.8-apache

# DokuWiki release to install
ARG DOKUWIKI_VERSION=2026-07-14a
ARG DOKUWIKI_URL=https://download.dokuwiki.org/src/dokuwiki/dokuwiki-${DOKUWIKI_VERSION}.tgz
# SHA-256 of the .tgz, pinned to DOKUWIKI_VERSION. The download is ALWAYS
# verified against this - a mismatch fails the build. When you bump
# DOKUWIKI_VERSION, also update this hash (recompute with
# `curl -sL <DOKUWIKI_URL> | sha256sum`), or override at build time with
# --build-arg DOKUWIKI_SHA256=<sha>.
ARG DOKUWIKI_SHA256=88a4a37bba7353b883610bbb738c30472af9d4254bd7064495a106f2e8086de3

# PHP extensions DokuWiki relies on (gd for image resizing, intl for better
# Unicode handling, zip for archive uploads, mbstring for multibyte strings).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libpng-dev libjpeg62-turbo-dev libfreetype6-dev \
        libzip-dev libicu-dev \
        libapache2-mod-xsendfile \
        libapache2-mod-evasive \
        libapache2-mod-security2 \
        curl wget ca-certificates; \
    docker-php-ext-configure gd --with-freetype --with-jpeg; \
    docker-php-ext-install -j"$(nproc)" gd zip intl; \
    apt-get clean; rm -rf /var/lib/apt/lists/*

# Enable Apache modules. rewrite = clean URLs (rfcs/2026-07-25_clean-urls-mod-rewrite.md); headers/expires = cache headers;
# filter+deflate = on-the-wire gzip (rfcs/2026-07-25_http-compression.md);
# xsendfile = offload media delivery (rfcs/2026-07-25_x-sendfile-media-delivery.md);
# remoteip = recover real client IP from Fly's proxy (mod_evasive + logs need it);
# reqtimeout = Slowloris caps; evasive = rough DoS rate-limit;
# security2 = mod_security v2 WAF engine (loads /etc/modsecurity/*.conf).
# (rfcs/2026-07-25_apache-hardening.md, rfcs/2026-07-25_owasp-crs-waf.md)
RUN a2enmod rewrite headers expires filter deflate xsendfile remoteip reqtimeout evasive security2

# Disable modules we don't use (shrink attack surface). a2dismod on an
# already-disabled module is a harmless no-op (exit 0). cgi: PHP runs as a module,
# never CGI; autoindex/status/info/userdir: not needed (disabling autoindex also
# makes the Options -Indexes rule belt-and-suspenders). -f: Debian flags
# autoindex as "essential", so a2dismod aborts it non-interactively without -f.
# (rfcs/2026-07-25_apache-hardening.md)
RUN a2dismod -f autoindex status info cgi userdir

# Download and extract DokuWiki into the webroot.
RUN set -eux; \
    rm -rf /var/www/html/*; \
    wget -qO /tmp/dokuwiki.tgz "${DOKUWIKI_URL}"; \
    # Always verify the download against the pinned checksum; fails on mismatch.
    echo "${DOKUWIKI_SHA256}  /tmp/dokuwiki.tgz" | sha256sum -c -; \
    # The archive extracts to a single top-level dir (dokuwiki/); strip it.
    tar -xzf /tmp/dokuwiki.tgz -C /var/www/html --strip-components=1; \
    rm /tmp/dokuwiki.tgz

# OWASP Core Rule Set (CRS) 4 -- the WAF ruleset for the mod_security engine
# enabled above. The Debian `modsecurity-crs` apt package ships CRS 3.x (more
# false positives, the worst property for an agent that writes arbitrary page
# content), so we pin CRS 4 from upstream instead. Pinned + SHA-256-verified,
# the same pattern as the DokuWiki tarball above. Extracts to a single
# top-level `coreruleset-<ver>/` dir, hence --strip-components=1.
# Phase 1 runs DetectionOnly; see rfcs/2026-07-25_owasp-crs-waf.md.
ARG CRS_VERSION=v4.28.0
# SHA-256 of the CRS .tar.gz, pinned to CRS_VERSION -- verified on every build.
# When you bump CRS_VERSION, recompute this (`curl -sL <url> | sha256sum`) or
# override at build time with --build-arg CRS_SHA256=<sha>.
ARG CRS_SHA256=d8acc96f25ad07c8e3a595a23c797324f6d77e59ddf9e26e90dd95ebd2e676ce
RUN set -eux; \
    rm -rf /etc/modsecurity/crs; \
    wget -qO /tmp/crs.tgz "https://github.com/coreruleset/coreruleset/archive/refs/tags/${CRS_VERSION}.tar.gz"; \
    echo "${CRS_SHA256}  /tmp/crs.tgz" | sha256sum -c -; \
    mkdir -p /etc/modsecurity/crs; \
    tar -xzf /tmp/crs.tgz -C /etc/modsecurity/crs --strip-components=1; \
    rm /tmp/crs.tgz

# Corkboard RPC plugin: server-side RPC methods for the agent (today:
# wanted/orphans/media-orphans in a single call). Ships as a bundled plugin in
# lib/plugins/corkboard/ (the entrypoint refreshes bundled plugins each boot).
COPY corkboard-plugin/ /var/www/html/lib/plugins/corkboard/

# Locked-down config templates. The image's conf/ stays pristine at build
# time; entrypoint.sh always writes these into the volume's conf/ (the wiki
# ships closed by default). CORKBOARD_ADMIN_PASS and CORKBOARD_AGENT_PASS (Fly
# secrets) are both required — the entrypoint fails fast if either is missing.
COPY conf-seed/ /usr/local/share/dokuwiki-seed/

# Defense-in-depth: block direct HTTP access to data/conf/bin/inc/vendor
# regardless of .htaccess / AllowOverride behaviour (protects users.auth.php,
# etc.), plus Options -Indexes -ExecCGI. (rfcs/2026-07-25_apache-hardening.md)
COPY apache-deny-sensitive.conf /etc/apache2/conf-enabled/dokuwiki-security.conf

# Apache server-level hardening: server fingerprint/TRACE, security headers,
# Slowloris caps. (rfcs/2026-07-25_apache-hardening.md)
COPY apache-hardening.conf /etc/apache2/conf-enabled/apache-hardening.conf

# Recover the real client IP from Fly's proxy; rough DoS rate-limit.
# (rfcs/2026-07-25_apache-hardening.md)
COPY remoteip.conf /etc/apache2/conf-enabled/remoteip.conf
COPY evasive.conf /etc/apache2/conf-enabled/evasive.conf

# On-the-wire gzip compression of text responses (mod_deflate).
COPY compression.conf /etc/apache2/conf-enabled/compression.conf

# Offload media delivery from PHP to Apache (mod_xsendfile).
COPY xsendfile.conf /etc/apache2/conf-enabled/xsendfile.conf

# Clean URLs (mod_rewrite) — DokuWiki's canonical rewrite rules in server config
# (not .htaccess). Pairs with userewrite=2 + useslash=1 in local.protected.php.
# (rfcs/2026-07-25_clean-urls-mod-rewrite.md)
COPY rewrite.conf /etc/apache2/conf-enabled/rewrite.conf

# mod_security + OWASP CRS 4 WAF (rfcs/2026-07-25_owasp-crs-waf.md). The engine
# conf (modsecurity.conf) is picked up by security2.conf's
# `IncludeOptional /etc/modsecurity/*.conf`; crs-setup.conf + the rules are pulled
# in by conf-enabled/modsecurity-crs.conf (mods-enabled/ loads before
# conf-enabled/ in apache2.conf, so the engine config is in place first).
COPY modsecurity.conf /etc/modsecurity/modsecurity.conf
COPY crs-setup.conf /etc/modsecurity/crs/crs-setup.conf
COPY crs-exclusions.conf /etc/modsecurity/crs/crs-exclusions.conf
COPY modsecurity-crs.conf /etc/apache2/conf-enabled/modsecurity-crs.conf

# OPcache tuning (build-time only, zero per-boot cost). Sizes opcache for fast
# cold starts; preload is intentionally disabled in the ini (it broke runtime
# constants under Mort). See dokuwiki-opcache.ini for the rationale.
COPY dokuwiki-opcache.ini /usr/local/etc/php/conf.d/dokuwiki-opcache.ini

# Creates the initial admin account from Fly secrets on first boot.
COPY bootstrap-user.php /usr/local/bin/bootstrap-user.php

# Apache in this image runs as www-data (uid 33). Give it ownership of the
# webroot so it can write to data/ and conf/. The entrypoint handles making
# data/conf/plugins persistent on the mounted volume at runtime.
RUN chown -R www-data:www-data /var/www/html

# Copy our entrypoint that wires up the persistent volume.
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/bootstrap-user.php

EXPOSE 80

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["apache2-foreground"]
