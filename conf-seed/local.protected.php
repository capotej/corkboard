<?php
/**
 * local.protected.php — locked-down defaults.
 *
 * This file is loaded AFTER local.php and CANNOT be changed from the web
 * Configuration Manager. Edit or remove this file on disk to alter these.
 *
 * These two settings are mandatory because we bypass the web installer:
 *   - useacl=1    : without it acl.auth.php is ignored and the wiki is open
 *   - superuser   : defaults to '!!not set!!' in DokuWiki, so we set @admin
 */
$conf['useacl']         = 1;
$conf['superuser']      = '@admin';

// Closed wiki: no self-registration, no self-service password reset.
// (Accounts are created by an admin via User Manager, or bootstrapped on first boot.)
$conf['disableactions'] = 'register,resendpwd';

// Sitemap (do=index) leak: DokuWiki builds the namespace tree from the on-disk
// data/pages/ directories, so anonymous visitors — who have NO read permission
// (@ALL 0) — still see every top-level namespace (ml, personal, projects, …)
// as expandable folders, revealing the wiki's structure even though the pages
// inside are ACL-blocked. sneaky_index makes the sitemap respect read ACL on
// namespaces: with @ALL 0 anonymous users see an empty sitemap, while logged-in
// users (@user 8) see the full tree. Locked here so it can't be relaxed from
// the web Configuration Manager.
$conf['sneaky_index'] = 1;

// JSON-RPC API (lib/exe/jsonrpc.php). Enabled here so it can't be toggled off
// from the web UI. Access is restricted to the @api group (the bootstrapped
// 'agent' user) plus @admin. ACL still applies per-method after auth.
$conf['remote']     = 1;
$conf['remoteuser'] = '@api,@admin';

// Don't phone home. DokuWiki's update check periodically fetches
// update.dokuwiki.org (and reports the running version). We ship a pinned
// release and disable popularity reporting via plugins.local.php; turning
// this off too keeps the wiki from making outbound calls it doesn't need.
$conf['updatecheck'] = 0;

// Apache (mod_deflate) is the sole on-the-wire compressor; DokuWiki must not
// also gzip its own output (it would double-encode pages and CSS/JS). The
// default is already 0; locked here so the web Configuration Manager can't flip
// it on. See rfcs/2026-07-25_http-compression.md.
$conf['gzip_output'] = 0;

// Offload media delivery to Apache's mod_xsendfile. 2 = the X-Sendfile header
// (Apache); the module (libapache2-mod-xsendfile) and xsendfile.conf must ship
// with the image — if they don't, this leaks the internal path and breaks
// downloads, so it's locked here alongside the module. See
// rfcs/2026-07-25_x-sendfile-media-delivery.md.
$conf['xsendfile'] = 2;

// Clean URLs: DokuWiki emits path-style links (/wiki/syntax, not
// /doku.php?id=wiki:syntax), and mod_rewrite (rewrite.conf) routes them back.
// userewrite=1 = webserver rewrite (clean URLs via mod_rewrite); useslash=1 =
// '/' between namespaces in URLs. Both REQUIRED together, and both must match
// the rewrite rules — locked here so the web Configuration Manager can't flip
// them off (which would 404 every clean link the wiki emits).
// Old ugly URLs (/doku.php?id=…) still resolve (doku.php is a real file).
// See rfcs/2026-07-25_clean-urls-mod-rewrite.md.
$conf['userewrite'] = 1;
$conf['useslash']   = 1;

// Emit absolute (https://host/…) URLs + rel=canonical pointing at the clean
// form, so the now-two URL shapes (clean + legacy ugly) don't read as duplicate
// content to crawlers, and links in feeds/exports are stable. baseurl is
// auto-detected from the request; no manual setting behind Fly. Separable from
// the rewrite itself — can be dropped without affecting routing.
// See rfcs/2026-07-25_clean-urls-mod-rewrite.md.
$conf['canonical']  = 1;
