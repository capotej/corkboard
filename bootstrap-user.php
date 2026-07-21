<?php
/**
 * bootstrap-user.php
 *
 * Creates the initial DokuWiki user accounts from environment variables.
 * Run by entrypoint.sh inside the locked-down (DOKU_ADMIN_PASSWORD) branch.
 *
 *   Admin  : DOKU_ADMIN_USER (admin) / DOKU_ADMIN_PASSWORD (required to reach
 *            this code) / DOKU_ADMIN_NAME (Administrator) / DOKU_ADMIN_EMAIL
 *            groups: admin,user        -> superuser via @admin
 *
 *   Agent  : DOKU_AGENT_USER (agent) / DOKU_AGENT_PASSWORD (optional) /
 *            DOKU_AGENT_NAME (API Agent) / DOKU_AGENT_EMAIL
 *            groups: user,api          -> read/write pages (@user ACL) AND
 *                                          JSON-RPC API access via the @api group
 *
 * Passwords are hashed with PHP's native bcrypt ($2y$), DokuWiki's default
 * passcrypt. Existing users are never overwritten, so password changes made
 * later (via User Manager) survive redeploys.
 */

$usersFile = getenv('DOKU_USERS_FILE') ?: '/var/www/html/conf/users.auth.php';

$accounts = [
    [
        'pass_var'     => 'DOKU_ADMIN_PASSWORD',
        'user_var'     => 'DOKU_ADMIN_USER', 'user_default' => 'admin',
        'name_var'     => 'DOKU_ADMIN_NAME', 'name_default' => 'Administrator',
        'mail_var'     => 'DOKU_ADMIN_EMAIL',
        'groups'       => 'admin,user',
    ],
    [
        'pass_var'     => 'DOKU_AGENT_PASSWORD',
        'user_var'     => 'DOKU_AGENT_USER', 'user_default' => 'agent',
        'name_var'     => 'DOKU_AGENT_NAME', 'name_default' => 'API Agent',
        'mail_var'     => 'DOKU_AGENT_EMAIL',
        'groups'       => 'user,api',
    ],
];

// Read once; reused for existence checks across all accounts.
$existing = is_file($usersFile)
    ? file($usersFile, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES)
    : [];

/** Return true if $user already has a record. */
function user_exists(array $lines, $user)
{
    foreach ($lines as $line) {
        if ($line === '' || $line[0] === '#') {
            continue;
        }
        $parts = explode(':', $line);
        if (isset($parts[0]) && $parts[0] === $user) {
            return true;
        }
    }
    return false;
}

$changed = false;

foreach ($accounts as $acct) {
    $user = getenv($acct['user_var']) ?: $acct['user_default'];
    $pass = getenv($acct['pass_var']) ?: '';

    if ($pass === '') {
        fwrite(STDERR, "bootstrap-user: {$acct['pass_var']} is empty; skipping '{$user}'.\n");
        continue;
    }

    if (user_exists($existing, $user)) {
        fwrite(STDERR, "bootstrap-user: user '{$user}' already exists; left untouched.\n");
        continue;
    }

    $name  = getenv($acct['name_var']) ?: $acct['name_default'];
    $mail  = getenv($acct['mail_var']) ?: ($user . '@localhost');
    $hash  = password_hash($pass, PASSWORD_BCRYPT);
    $record = implode(':', [$user, $hash, $name, $mail, $acct['groups']]);

    // HTTP access to conf/ is denied by Apache, so the file is never served.
    if (!is_file($usersFile)) {
        file_put_contents($usersFile, "# users.auth.php\n# created by bootstrap-user.php\n");
    }
    file_put_contents($usersFile, $record . "\n", FILE_APPEND);
    $existing[] = $record; // keep in-memory view consistent for later accounts

    echo "bootstrap-user: created user '{$user}' (groups: {$acct['groups']}).\n";
    $changed = true;
}

if ($changed) {
    @chmod($usersFile, 0640);
    @chown($usersFile, 'www-data'); // entrypoint runs as root
    @chgrp($usersFile, 'www-data');
}
