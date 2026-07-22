#!/usr/bin/env bash
# entrypoint.sh — wire DokuWiki up to the persistent Fly volume.
#
# DokuWiki stores everything on disk (it has no database), so the directories
# that hold user content and configuration must survive container restarts and
# redeployments. On Fly.io we mount a single volume at /dokuwiki-persistent and
# relocate these directories there:
#
#   data/            pages, media, attic, meta, cache, locks, ...
#   conf/            local config, ACLs, users (+ release-default conf files)
#   lib/plugins/     installed plugins
#   lib/tpl/         installed templates
#
# On first boot we seed the volume from the stock directories shipped in the
# image, then symlink the stock locations to the volume so DokuWiki keeps
# working unchanged. On every subsequent boot the symlinks just point at the
# already-populated volume.
#
# conf/ gets one extra step (see the loop below): its RELEASE-DEFAULT files
# (dokuwiki.php, the *.conf files, license.php, …) are refreshed from the image
# EVERY boot so they track the running DokuWiki version, while its
# user-managed files (local.php, acl.auth.php, users.auth.php, …) persist
# untouched. Persisting the whole conf/ dir without that refresh froze the
# defaults at the first-boot release and broke upgrades — Mort added
# $conf['syntax'] to conf/dokuwiki.php, but a volume seeded from the prior
# release kept the old dokuwiki.php (no 'syntax' key), so the parser read null
# and threw TypeError: ModeRegistry::__construct(): Argument #1 ($syntax) …
# null given. (There are NO per-file symlinks in conf/ — the whole dir is
# symlinked, like the others.)
set -euo pipefail

WEBROOT="/var/www/html"
PERSIST="/dokuwiki-persistent"

# Directories (relative to WEBROOT) that must persist across deploys.
PERSIST_DIRS="data conf lib/plugins lib/tpl"

mkdir -p "${PERSIST}"
chown www-data:www-data "${PERSIST}"

# Defense: the Dockerfile assumes PHP 8.5 ships OPcache statically compiled
# (so it no longer runs `docker-php-ext-install opcache`). If that assumption
# is ever wrong, OPcache is silently absent and cold starts get slower — surface
# it loudly here instead of letting it degrade quietly. Non-fatal: the wiki
# works without opcache (just slower cold starts). ~40ms, cold starts only.
if ! php -r 'exit(extension_loaded("Zend OPcache") ? 0 : 1);' 2>/dev/null; then
  echo "[entrypoint] WARNING: Zend OPcache is NOT loaded."
  echo "[entrypoint]   Cold starts will be slower. The Dockerfile assumes PHP 8.5"
  echo "[entrypoint]   statically compiles OPcache (see dokuwiki-opcache.ini); if this"
  echo "[entrypoint]   base image doesn't, re-add the opcache install step."
fi

for rel in ${PERSIST_DIRS}; do
  src="${WEBROOT}/${rel}"
  dst="${PERSIST}/${rel}"

  # If the webroot already has a symlink here (from a previous boot), it
  # already points at the volume — nothing to do. (On Fly every machine start
  # is a fresh container: the image ships these as real dirs, so we recreate
  # the symlinks each boot — cheap, just rm + ln per dir.)
  if [ -L "${src}" ]; then
    continue
  fi

  # Seed the volume from the image's stock copy the first time only.
  if [ ! -e "${dst}" ]; then
    mkdir -p "$(dirname "${dst}")"
    cp -a "${src}" "${dst}"
    chown -R www-data:www-data "${dst}"
  fi

  # conf/ special case: it mixes release-shipped defaults (dokuwiki.php, the
  # *.conf files, license.php, …) that MUST track the running version, with
  # user-managed files (local.php, acl.auth.php, users.auth.php, …) that must
  # persist. Refresh the release defaults from the image BEFORE symlinking,
  # skipping user-managed files. This is what keeps upgrades safe without any
  # per-file symlinks: conf/dokuwiki.php always matches the running version,
  # and user edits are never clobbered.
  if [ "${rel}" = "conf" ]; then
    for imgfile in "${src}"/*; do
      [ -e "${imgfile}" ] || continue
      name="$(basename "${imgfile}")"
      case "${name}" in
        local.php|local.protected.php|acl.auth.php|users.auth.php|plugins.local.php) ;;  # user-managed: never overwrite
        *) cp -a "${imgfile}" "${dst}/${name}" ;;
      esac
    done
  fi

  # lib/plugins and lib/tpl ship bundled entries WITH the release (config,
  # authldap, usermanager, ... / the doku template, ...) AND also hold entries
  # the user installs via the Extension Manager, which must persist. Refresh the
  # BUNDLED entries from the image each boot so they track the running DokuWiki
  # version — mirroring the conf/ release-default refresh above — while entries
  # on the volume that are NOT in the image (user-installed) are left alone. We
  # replace each bundled entry wholesale (rm + cp) rather than merge, so files an
  # upstream release deleted are removed here too. This writes only to the volume
  # (not overlayfs), so there's no copy-up cost like the old webroot chown, and
  # it runs only on a true cold start, never on suspend/resume.
  if [ "${rel}" = "lib/plugins" ] || [ "${rel}" = "lib/tpl" ]; then
    for imgentry in "${src}"/*; do
      [ -e "${imgentry}" ] || continue
      name="$(basename "${imgentry}")"
      rm -rf "${dst}/${name}"
      cp -a "${imgentry}" "${dst}/${name}"
      chown -R www-data:www-data "${dst}/${name}"
    done
  fi

  # Replace the stock directory with a symlink into the volume.
  rm -rf "${src}"
  ln -s "${dst}" "${src}"
done

# NOTE: the old `chown -R www-data:www-data "${WEBROOT}"` is deliberately gone.
# The Dockerfile already chowns /var/www/html to www-data at build time, so on a
# fresh container the webroot is already correctly owned. Re-chowning it every
# boot forced overlayfs to copy up ~5000 files — the single biggest cold-start
# cost (~20s on a shared-cpu-1x). The symlink targets on the volume are already
# www-data-owned, so nothing here needs re-chowning.

# --- Optional closed-wiki defaults + admin bootstrap --------------------
# Two modes:
#
#   DOKU_ADMIN_PASSWORD set  ->  closed wiki: write the lockdown config
#                                (local.php / local.protected.php / acl.auth.php)
#                                into the volume once, create the admin account
#                                (and the agent account if DOKU_AGENT_PASSWORD is
#                                also set) from secrets, and remove install.php.
#
#   DOKU_ADMIN_PASSWORD unset->  stock DokuWiki: keep the web installer so you
#                                can run first-run setup yourself and pick an
#                                ACL policy. (The installer refuses to run once
#                                conf/local.php exists, which is why the lockdown
#                                files are only written in the secret path.)
if [ -n "${DOKU_ADMIN_PASSWORD:-}" ]; then
  # local.protected.php is image-managed lockdown config (it can't be edited
  # via the web UI anyway), so ALWAYS sync it from the image. This is how
  # upgrades reliably apply to an existing volume — e.g. when remote=1 is added.
  cp "/usr/local/share/dokuwiki-seed/local.protected.php" "${PERSIST}/conf/local.protected.php"
  chown www-data:www-data "${PERSIST}/conf/local.protected.php"
  chmod 0640 "${PERSIST}/conf/local.protected.php"

  # The rest is user-editable (title, ACL, upload mime types, plugin
  # enable/disable) — seed once, never clobber edits.
  for f in local.php acl.auth.php mime.local.conf plugins.local.php; do
    dst="${PERSIST}/conf/${f}"
    if [ ! -e "${dst}" ]; then
      cp "/usr/local/share/dokuwiki-seed/${f}" "${dst}"
      chown www-data:www-data "${dst}"
      chmod 0640 "${dst}"
    fi
  done

  # Create accounts (idempotent — won't clobber existing users, so later
  # password changes survive redeploys). bootstrap-user.php creates the admin
  # (always, since DOKU_ADMIN_PASSWORD is set here) and the agent when
  # DOKU_AGENT_PASSWORD is set. On a normal Fly resume every expected account
  # already exists, so we skip spawning PHP entirely — one fewer PHP CLI
  # cold-start per boot. If any expected account is missing we fall back to
  # the idempotent bootstrap (which chowns its own output).
  USERS_FILE="${PERSIST}/conf/users.auth.php"
  need_bootstrap=0
  user_present() { [ -f "${USERS_FILE}" ] && grep -q "^${1}:" "${USERS_FILE}"; }
  ADMIN_USER="${DOKU_ADMIN_USER:-admin}"
  AGENT_USER="${DOKU_AGENT_USER:-agent}"
  user_present "${ADMIN_USER}" || need_bootstrap=1
  if [ -n "${DOKU_AGENT_PASSWORD:-}" ]; then
    user_present "${AGENT_USER}" || need_bootstrap=1
  fi
  if [ "${need_bootstrap}" = "1" ]; then
    php /usr/local/bin/bootstrap-user.php
  else
    echo "[entrypoint] admin/agent accounts already present — skipping bootstrap-user.php."
  fi

  # Every file touched above is chowned at the point it's written, so the old
  # blanket `chown -R ${PERSIST}` is gone — it recursed the whole volume (pages,
  # media, attic, cache) on every boot and grew without bound.

  # The installer is no longer needed (and can't run with our config present).
  rm -f "${WEBROOT}/install.php"
  if [ "${need_bootstrap}" = "1" ]; then
    echo "[entrypoint] closed-wiki defaults applied; users bootstrapped (admin always; agent if DOKU_AGENT_PASSWORD set)."
  else
    echo "[entrypoint] closed-wiki defaults applied; existing users left untouched."
  fi

  # Non-blocking JSON-RPC self-test: wait for Apache, then prove the agent can
  # authenticate against the API. The result is printed to stdout (visible via
  # `fly logs`). Backgrounded so it never blocks or breaks startup.
  if [ -n "${DOKU_AGENT_PASSWORD:-}" ]; then
    (
      set +e
      agent="${DOKU_AGENT_USER:-agent}"
      for _ in $(seq 1 30); do
        curl -sf -o /dev/null "http://127.0.0.1/" && break
        sleep 1
      done
      resp=$(curl -s -u "${agent}:${DOKU_AGENT_PASSWORD}" \
        -H 'Content-Type: application/json' \
        -d '{"jsonrpc":"2.0","method":"core.whoAmI","id":1}' \
        http://127.0.0.1/lib/exe/jsonrpc.php 2>/dev/null || echo '(request failed)')
      echo "[entrypoint] JSON-RPC self-test as '${agent}': ${resp}"
    ) &
  fi
else
  echo "[entrypoint] DOKU_ADMIN_PASSWORD not set — using the standard web installer."
  echo "[entrypoint] Open the site to create your superuser, then lock down via Admin > ACL."
  echo "[entrypoint] For the baked closed-wiki default instead, set the secret BEFORE first boot:"
  echo "[entrypoint]   fly secrets set DOKU_ADMIN_PASSWORD='choose-a-password' -a <app>"
fi

exec "$@"
