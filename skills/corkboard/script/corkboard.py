#!/usr/bin/env python3
"""corkboard - generic DokuWiki helper (Python 3 stdlib only).

Talks to a DokuWiki's built-in Remote API (ApiCore) over JSON-RPC at
<url>/lib/exe/jsonrpc.php with HTTP Basic auth. One transport, one auth method,
for everything (pages AND media). Uses the `core.*` methods throughout.

MEDIA UPLOAD:
  core.saveMedia(media, base64, overwrite) base64-DECODES the content, so it works
  for binary AND text and can overwrite (round-trips a real PNG byte-for-byte).

PERMISSIONS:
  The token has READ + UPDATE but NOT DELETE. So: pages can be read/written/
  appended and emptied (empty savePage clears content — that's an update, not a
  true delete); media can be uploaded/overwritten/listed/read. But
  core.deleteMedia returns 403 — clean stray media via the web Media Manager.

Config is via environment variables (never hardcode secrets):

  CORKBOARD_URL    base URL, no trailing slash        (required)
  CORKBOARD_USER   username                            (required)
  CORKBOARD_PASS   password                            (required)

Examples:
  export CORKBOARD_URL=https://wiki.example.com CORKBOARD_USER=me CORKBOARD_PASS=secret
  corkboard.py get ns:page                              # print raw wikitext
  corkboard.py put ns:page --file body.txt --sum m      # create/replace a page
  echo "more" | corkboard.py append ns:page             # append from stdin
  corkboard.py delete ns:page                           # clear page content (update)
  corkboard.py list ns                                  # pages (recursive)
  corkboard.py all                                      # every page
  corkboard.py search "full text"                       # full-text search
  corkboard.py media-upload diagram.png ns diag.png     # upload (binary or text)
  corkboard.py media-get ns:diag.png -o diag.png        # download
  corkboard.py raw core.getMediaInfo '["ns:diag.png"]'  # escape hatch
"""
import argparse, base64, json, os, sys
import urllib.error, urllib.request


def _cfg():
    url = os.environ.get("CORKBOARD_URL", "").rstrip("/")
    user = os.environ.get("CORKBOARD_USER", "")
    pw = os.environ.get("CORKBOARD_PASS", "")
    if not url or not user or not pw:
        sys.exit("corkboard: set CORKBOARD_URL, CORKBOARD_USER, CORKBOARD_PASS")
    return url, user, pw


def _b64auth():
    _, user, pw = _cfg()
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def rpc(method, params=None):
    """Call a core.* JSON-RPC method (positional params).
    Returns the `result` field, or exits on error. Verified-working core.* set:

      core.getPage(page[,rev])                  -> str
      core.savePage(page, text, summary, minor) -> bool   (empty text clears page)
      core.appendPage(page, text, summary, minor)-> bool
      core.listPages(namespace, depth, hash)    -> [{id,...}]   (depth 0 = recursive)
      core.searchPages(query)                   -> [{id,title,...}]
      core.getWikiVersion()                     -> str
      core.saveMedia(media, base64, overwrite)  -> bool   (round-trips binary)
      core.getMedia(media[,rev])                -> str    (base64 of file contents)
      core.listMedia(namespace, pattern, depth, hash) -> [{id,...}]
      core.getMediaInfo(media,...)              -> {size,lastModified,isimage,...}
      core.deleteMedia(media)                   -> 403 (no delete permission)
    """
    url = _cfg()[0]
    payload = {"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}
    req = urllib.request.Request(
        f"{url}/lib/exe/jsonrpc.php",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": _b64auth()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        sys.exit(f"corkboard: HTTP {e.code} on {method}: {e.read().decode('utf-8','replace')[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"corkboard: network error on {method}: {e}")
    if obj.get("error"):
        e = obj["error"]
        sys.exit(f"corkboard: {method} failed: [{e.get('code')}] {e.get('message')}")
    return obj.get("result")


# ------------------------------------------------------------------- media ops
def _mediaid(ns, name):
    return f"{ns}:{name}" if ns else name


def media_upload(file, ns, name, overwrite=True):
    if not os.path.exists(file):
        sys.exit(f"corkboard: file not found: {file}")
    with open(file, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    mediaid = _mediaid(ns, name)
    ok = rpc("core.saveMedia", [mediaid, b64, bool(overwrite)])
    print(f"{'uploaded' if ok else 'FAILED'} {mediaid}")


def media_get(mediaid, out=None):
    b64 = rpc("core.getMedia", [mediaid])
    if not isinstance(b64, str):
        sys.exit(f"corkboard: core.getMedia returned no data for {mediaid}")
    data = base64.b64decode(b64)
    if out:
        with open(out, "wb") as f:
            f.write(data)
        print(f"wrote {out} ({len(data)} bytes)")
    else:
        sys.stdout.buffer.write(data)


# --------------------------------------------------------------- gardening ops
def _all_page_ids():
    return [p.get("id") if isinstance(p, dict) else p
            for p in (rpc("core.listPages", ["", 0]) or [])]


def _local_targets(page):
    out = []
    for lk in (rpc("core.getPageLinks", [page]) or []):
        if isinstance(lk, dict) and lk.get("type") == "local" and lk.get("page"):
            out.append(lk["page"])
    return out


def _is_entrypoint(pid):
    # landing pages are legitimately un-linked from content; don't flag as orphans
    return (pid.endswith(":start") or pid == "start"
            or pid.endswith(":sidebar") or pid == "sidebar"
            or pid.endswith(":playground") or pid == "playground")


def cmd_links(page):
    for tgt in _local_targets(page):
        print(tgt)


def cmd_backlinks(page):
    for src in (rpc("core.getPageBackLinks", [page]) or []):
        print(src)


def cmd_wanted():
    """Broken internal links: targets linked-to but not existing as pages."""
    # Fast path: the Corkboard RPC plugin computes this server-side in one call.
    try:
        result = rpc("plugin.corkboard.wanted", [])
    except SystemExit:
        result = None
    if result is None:
        # Fallback: client-side walk (N getPageLinks calls) when the plugin is absent.
        pages = _all_page_ids()
        existing = set(pages)
        result = {}
        print(f"scanning {len(pages)} pages for broken links...", file=sys.stderr)
        for src in pages:
            for tgt in _local_targets(src):
                if tgt not in existing:
                    result.setdefault(tgt, []).append(src)
    if not result:
        print("(no broken internal links)")
        return
    for tgt in sorted(result):
        print(tgt)
        for src in sorted(set(result[tgt])):
            print(f"  <- {src}")


def cmd_orphans():
    """Existing pages with no inbound links (entry points excluded)."""
    # Fast path: the Corkboard RPC plugin computes this server-side in one call.
    try:
        pids = rpc("plugin.corkboard.orphans", [])
    except SystemExit:
        pids = None
    if pids is None:
        # Fallback: client-side walk (N getPageBackLinks calls) when the plugin is absent.
        pages = _all_page_ids()
        print(f"scanning {len(pages)} pages for orphans...", file=sys.stderr)
        pids = []
        for pid in pages:
            if _is_entrypoint(pid):
                continue
            if not (rpc("core.getPageBackLinks", [pid]) or []):
                pids.append(pid)
    for pid in sorted(p for p in pids if not _is_entrypoint(p)):
        print(pid)


def _is_system_media(mid):
    # the wiki: namespace holds DokuWiki's shipped docs/logos; template assets are
    # not linked from pages, so they'd be false-positive orphans
    return mid.startswith("wiki:")


def cmd_media_orphans(ns):
    """Media files in a namespace not referenced from any page."""
    # Fast path: the Corkboard RPC plugin computes this server-side in one call.
    try:
        found = rpc("plugin.corkboard.mediaorphans", [ns])
    except SystemExit:
        found = None
    if found is None:
        # Fallback: client-side walk (N getMediaUsage calls) when the plugin is absent.
        media = [m.get("id") if isinstance(m, dict) else m
                 for m in (rpc("core.listMedia", [ns]) or [])]
        print(f"scanning {len(media)} media files for usage...", file=sys.stderr)
        found = []
        for mid in media:
            if _is_system_media(mid):
                continue
            if not (rpc("core.getMediaUsage", [mid]) or []):
                found.append(mid)
    for mid in sorted(found):
        print(mid)


# ------------------------------------------------------------------- subcommands
def _read_input(args):
    if args.file:
        return open(args.file, encoding="utf-8").read()
    if args.text is not None:
        return args.text
    return sys.stdin.read()


def main():
    ap = argparse.ArgumentParser(prog="corkboard", description="DokuWiki (Corkboard) helper")
    sp = ap.add_subparsers(dest="cmd", required=True)

    sp.add_parser("get", help="print raw wikitext of a page").add_argument("page")

    p = sp.add_parser("put", help="write a page (create/replace) via core.savePage")
    p.add_argument("page"); p.add_argument("--file"); p.add_argument("--text"); p.add_argument("--sum", default="")

    a = sp.add_parser("append", help="append text to a page via core.appendPage")
    a.add_argument("page"); a.add_argument("--file"); a.add_argument("--text"); a.add_argument("--sum", default="")

    d = sp.add_parser("delete", help="clear page content (empty savePage — an update; token has no delete perm)")
    d.add_argument("page"); d.add_argument("--sum", default="cleared")

    l = sp.add_parser("list", help="list page ids in a namespace (recursive by default)")
    l.add_argument("ns"); l.add_argument("--depth", type=int, default=0, help="0 = recursive (default); N = descend N levels")

    sp.add_parser("all", help="list every page id")

    sp.add_parser("search", help="full-text search via core.searchPages").add_argument("query")
    sp.add_parser("version", help="DokuWiki version")

    mu = sp.add_parser("media-upload", help="upload a file via core.saveMedia (binary or text)")
    mu.add_argument("file"); mu.add_argument("ns"); mu.add_argument("name")
    mu.add_argument("--no-overwrite", dest="overwrite", action="store_false", default=True,
                    help="fail instead of overwriting an existing media id")

    mg = sp.add_parser("media-get", help="download a media file via core.getMedia")
    mg.add_argument("mediaid"); mg.add_argument("-o", "--out")

    ml = sp.add_parser("media-list", help="list media ids in a namespace")
    ml.add_argument("ns")

    mi = sp.add_parser("media-info", help="info for one media file")
    mi.add_argument("mediaid")

    md = sp.add_parser("media-delete", help="delete a media file (403 — no delete perm)")
    md.add_argument("mediaid")

    sp.add_parser("wanted", help="broken internal links (linked-to, not existing)")
    sp.add_parser("orphans", help="pages with no inbound links")
    mo = sp.add_parser("media-orphans", help="unreferenced media in a namespace")
    mo.add_argument("ns")
    lk = sp.add_parser("links", help="outgoing internal links from a page")
    lk.add_argument("page")
    bl = sp.add_parser("backlinks", help="pages linking TO a page")
    bl.add_argument("page")

    raw = sp.add_parser("raw", help="escape hatch: call any JSON-RPC method")
    raw.add_argument("method"); raw.add_argument("params", help="JSON array of params", nargs="?", default="[]")

    args = ap.parse_args()
    if args.cmd == "get":
        sys.stdout.write(rpc("core.getPage", [args.page]) or "")
    elif args.cmd == "put":
        ok = rpc("core.savePage", [args.page, _read_input(args), args.sum, False])
        print("ok" if ok else "FAILED")
    elif args.cmd == "append":
        ok = rpc("core.appendPage", [args.page, _read_input(args), args.sum, False])
        print("ok" if ok else "FAILED")
    elif args.cmd == "delete":
        ok = rpc("core.savePage", [args.page, "", args.sum, False])
        print("cleared" if ok else "FAILED")
    elif args.cmd == "list":
        for pg in (rpc("core.listPages", [args.ns, args.depth]) or []):
            print(pg.get("id") if isinstance(pg, dict) else pg)
    elif args.cmd == "all":
        for pg in (rpc("core.listPages", ["", 0]) or []):
            print(pg.get("id") if isinstance(pg, dict) else pg)
    elif args.cmd == "search":
        for hit in (rpc("core.searchPages", [args.query]) or []):
            print(f"{hit.get('id')}\t{hit.get('title', '')}")
    elif args.cmd == "version":
        print(rpc("core.getWikiVersion", []))
    elif args.cmd == "media-upload":
        media_upload(args.file, args.ns, args.name, args.overwrite)
    elif args.cmd == "media-get":
        media_get(args.mediaid, args.out)
    elif args.cmd == "media-list":
        for m in (rpc("core.listMedia", [args.ns]) or []):
            print(m.get("id") if isinstance(m, dict) else m)
    elif args.cmd == "media-info":
        print(json.dumps(rpc("core.getMediaInfo", [args.mediaid]), indent=2, default=str))
    elif args.cmd == "media-delete":
        try:
            print(rpc("core.deleteMedia", [args.mediaid]))
        except SystemExit as e:
            sys.exit(f"{e}\n(tip: the token has no delete permission (403); "
                     "remove media via the web Media Manager.)")
    elif args.cmd == "wanted":
        cmd_wanted()
    elif args.cmd == "orphans":
        cmd_orphans()
    elif args.cmd == "media-orphans":
        cmd_media_orphans(args.ns)
    elif args.cmd == "links":
        cmd_links(args.page)
    elif args.cmd == "backlinks":
        cmd_backlinks(args.page)
    elif args.cmd == "raw":
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as e:
            sys.exit(f"corkboard: raw params must be a JSON array: {e}")
        print(json.dumps(rpc(args.method, params), indent=2, default=str))


if __name__ == "__main__":
    main()
