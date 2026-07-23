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
        curl wget ca-certificates; \
    docker-php-ext-configure gd --with-freetype --with-jpeg; \
    docker-php-ext-install -j"$(nproc)" gd zip intl; \
    apt-get clean; rm -rf /var/lib/apt/lists/*

# Enable Apache rewrite module (DokuWiki nice URLs / .htaccess support).
RUN a2enmod rewrite headers expires

# Download and extract DokuWiki into the webroot.
RUN set -eux; \
    rm -rf /var/www/html/*; \
    wget -qO /tmp/dokuwiki.tgz "${DOKUWIKI_URL}"; \
    # Always verify the download against the pinned checksum; fails on mismatch.
    echo "${DOKUWIKI_SHA256}  /tmp/dokuwiki.tgz" | sha256sum -c -; \
    # The archive extracts to a single top-level dir (dokuwiki/); strip it.
    tar -xzf /tmp/dokuwiki.tgz -C /var/www/html --strip-components=1; \
    rm /tmp/dokuwiki.tgz

# Corkboard RPC plugin: server-side RPC methods for the agent (today:
# wanted/orphans/media-orphans in a single call). Ships as a bundled plugin in
# lib/plugins/corkboard/ (the entrypoint refreshes bundled plugins each boot).
COPY corkboard-plugin/ /var/www/html/lib/plugins/corkboard/

# Locked-down config templates. The image's conf/ stays pristine at build
# time; entrypoint.sh always writes these into the volume's conf/ (the wiki
# ships closed by default). DOKU_ADMIN_PASSWORD (a Fly secret) is required —
# the entrypoint fails fast if it's missing.
COPY conf-seed/ /usr/local/share/dokuwiki-seed/

# Defense-in-depth: block direct HTTP access to data/conf/bin/inc regardless
# of .htaccess / AllowOverride behaviour (protects users.auth.php, etc.).
COPY apache-deny-sensitive.conf /etc/apache2/conf-enabled/dokuwiki-security.conf

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
