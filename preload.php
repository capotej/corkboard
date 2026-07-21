<?php
/**
 * preload.php
 *
 * Compiled into opcache at Apache startup via `opcache.preload` (see
 * dokuwiki-opcache.ini). Speeds up the first request after a Fly auto-start by
 * pre-compiling DokuWiki's core library, so the first page view doesn't pay the
 * PHP compilation cost.
 *
 * Design notes (grounded in https://www.php.net/manual/en/opcache.preloading.php):
 *
 *   - Uses opcache_compile_file(), NOT require/include. It parses + compiles a
 *     file WITHOUT executing it, so DokuWiki's inc/init.php boot logic never
 *     runs here (no side effects, no DokuWiki environment needed). It is also
 *     order-independent: classes that extend each other can be compiled in any
 *     order, unlike include.
 *
 *   - Scope is intentionally limited to image-shipped core: inc/ plus the
 *     top-level entry scripts. Plugins (lib/plugins/) and templates (lib/tpl/)
 *     live on the persistent volume and are user-editable — preloading a broken
 *     plugin would break Apache startup, so they are excluded.
 *
 *   - Every call is best-effort (@): opcache_compile_file() returns false on a
 *     normal failure (unreadable file, etc.) rather than throwing, and a single
 *     skip must never abort startup.
 *
 * Add new preload roots cautiously — anything preloaded is pinned in shared
 * memory until Apache restarts.
 */

$entryScripts = [
    '/var/www/html/doku.php',
    '/var/www/html/index.php',
    '/var/www/html/feed.php',
    '/var/www/html/lib/exe/jsonrpc.php',   // JSON-RPC API entrypoint (our agent)
    '/var/www/html/lib/exe/ajax.php',
    '/var/www/html/lib/exe/detail.php',
    '/var/www/html/lib/exe/mediamanager.php',
];

// Image-shipped PHP, NOT on the volume (safe + immutable). Vendor FIRST so the
// parent classes (SimplePie, IXR, splitbrain/phpcli, …) compile before the
// DokuWiki core files that extend them — avoids "Can't preload unlinked class:
// Unknown parent" warnings during preload and preloads the composer deps too.
$coreRoots = [
    '/var/www/html/vendor',
    '/var/www/html/inc',
];

$files = $entryScripts;

foreach ($coreRoots as $root) {
    if (!is_dir($root)) {
        continue;
    }
    $it = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($root, FilesystemIterator::SKIP_DOTS)
    );
    foreach ($it as $f) {
        if ($f->isFile() && strtolower($f->getExtension()) === 'php') {
            $files[] = $f->getPathname();
        }
    }
}

$files = array_unique($files);

$compiled = 0;
$skipped  = 0;
foreach ($files as $file) {
    if (!is_readable($file) || !@opcache_compile_file($file)) {
        $skipped++;
        continue;
    }
    $compiled++;
}

// Emitted during Apache/mod_php preload startup; visible via `fly logs`.
error_log(sprintf('[preload] compiled %d file(s), skipped %d.', $compiled, $skipped));
