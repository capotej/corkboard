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
