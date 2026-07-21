<?php
/**
 * plugins.local.php — disable bundled plugins we don't ship by default.
 *
 * DokuWiki's Extension Manager (Admin → Extension Manager) also writes to this
 * file; on first boot we seed it from the image with the lockdown defaults
 * below, then never clobber it — so re-enabling a plugin via the manager (or
 * here) survives redeploys. To re-enable a plugin, change its 0 to a 1.
 *
 *   1 = enabled, 0 = disabled.
 *
 * Note: authplain (the active default auth backend) is intentionally left
 * enabled here. Disabling it would break all logins.
 */

// Popularity Feedback Plugin — the admin tool that gathers anonymous stats and
// lets you submit them to dokuwiki.org. Off by default for a closed wiki.
$plugins['popularity'] = 0;

// Alternative auth backends we don't use (authplain is the active default).
// Disabling them shrinks the attack surface and stops the login path from
// probing each backend in turn.
$plugins['authpdo']  = 0;  // authPDO  — authenticate against a PDO database
$plugins['authldap'] = 0;  // authLDAP — authenticate against an LDAP directory
$plugins['authad']   = 0;  // authAD   — authenticate against Active Directory
