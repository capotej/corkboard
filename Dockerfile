# DokuWiki on Fly.io
# DokuWiki is a flat-file wiki (no database), so all we need is PHP + Apache
# and a persistent volume mounted for data/, conf/ and installed plugins/templates.

FROM php:8.2-apache

# DokuWiki release to install
ARG DOKUWIKI_VERSION=2025-05-14b
ARG DOKUWIKI_URL=https://download.dokuwiki.org/src/dokuwiki/dokuwiki-${DOKUWIKI_VERSION}.tgz

# PHP extensions DokuWiki relies on (gd for image resizing, intl for better
# Unicode handling, zip for archive uploads, mbstring for multibyte strings).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        libpng-dev libjpeg62-turbo-dev libfreetype6-dev \
        libzip-dev libicu-dev \
        curl wget ca-certificates; \
    docker-php-ext-configure gd --with-freetype --with-jpeg; \
    docker-php-ext-install -j"$(nproc)" gd zip intl opcache; \
    apt-get clean; rm -rf /var/lib/apt/lists/*

# Enable Apache rewrite module (DokuWiki nice URLs / .htaccess support).
RUN a2enmod rewrite headers expires

# Download and extract DokuWiki into the webroot.
RUN set -eux; \
    rm -rf /var/www/html/*; \
    wget -qO /tmp/dokuwiki.tgz "${DOKUWIKI_URL}"; \
    # The archive extracts to a single top-level dir (dokuwiki/); strip it.
    tar -xzf /tmp/dokuwiki.tgz -C /var/www/html --strip-components=1; \
    rm /tmp/dokuwiki.tgz

# Locked-down config templates. These are NOT placed in conf/ at build time —
# the image's conf/ stays pristine so DokuWiki's web installer still works.
# entrypoint.sh writes them into the volume's conf/ only when
# DOKU_ADMIN_PASSWORD is set (i.e. the closed-wiki default is opt-in via secret).
COPY conf-seed/ /usr/local/share/dokuwiki-seed/

# Defense-in-depth: block direct HTTP access to data/conf/bin/inc regardless
# of .htaccess / AllowOverride behaviour (protects users.auth.php, etc.).
COPY apache-deny-sensitive.conf /etc/apache2/conf-enabled/dokuwiki-security.conf

# OPcache + preload (build-time only, zero per-boot cost). Sizes opcache and
# pre-compiles DokuWiki's core library at Apache startup so the first request
# after a Fly auto-start is served fast. See the files for details.
COPY dokuwiki-opcache.ini /usr/local/etc/php/conf.d/dokuwiki-opcache.ini
COPY preload.php /usr/local/share/dokuwiki/preload.php

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
