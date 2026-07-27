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
  corkboard.py sitemap                                  # page tree (one call)
  corkboard.py search "full text"                       # full-text search
  corkboard.py media-upload diagram.png ns diag.png     # upload (binary or text)
  corkboard.py media-get ns:diag.png -o diag.png        # download
  corkboard.py raw core.getMediaInfo '["ns:diag.png"]'  # escape hatch
"""

import argparse
import base64
import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.request


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


def _gunzip_if_needed(raw, headers):
    """Decode a response body, gunzipping when the server used Content-Encoding.

    urllib has no transparent content-encoding handling (unlike requests/curl),
    so we ask for gzip (Accept-Encoding in rpc_call) and decode it here. Applied
    to both 200 and 4xx bodies: mod_deflate compresses error responses too.
    """
    if "gzip" in headers.get("Content-Encoding", "").lower():
        return gzip.decompress(raw)
    return raw


def _strip_html(s):
    """Crude tag/whitespace stripper for web-server error pages (Apache/nginx/PHP
    4xx/5xx HTML). NOT a general HTML parser — just collapses <...> and runs of
    whitespace so a 413/500 body is legible inside the one-line RPC error
    message. Stdlib only."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def rpc_call(method, params=None):
    """Call a JSON-RPC method (positional params). Returns (result, err), where
    err is {code, message} on failure and None on success — JSON-RPC application
    errors AND HTTP/network failures are normalized to the same shape. Does NOT
    exit — callers decide.
    rpc() wraps this and exits on error; reach for rpc_call() directly when a
    specific error is expected and recoverable (e.g. getPageInfo's code 121 on a
    missing page).

    Verified-working core.* set:

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
        headers={
            "Content-Type": "application/json",
            "Authorization": _b64auth(),
            # Ask the server to gzip the response. urllib has no transparent
            # content-encoding handling, so we decode it in _gunzip_if_needed.
            "Accept-Encoding": "gzip",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            obj = json.loads(_gunzip_if_needed(r.read(), r.headers).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # Read the FULL body before parsing. A [:300] cap here would (a) break
        # json.loads on a large JSON-RPC error payload and (b) cut off the
        # diagnostic HTML Apache/nginx/mod_security/PHP return on e.g. 413/500,
        # leaving media-upload failures reading identically no matter which
        # layer rejected them. We only truncate for display (below).
        text = _gunzip_if_needed(e.read(), e.headers).decode("utf-8", "replace")
        code, msg = e.code, text
        try:
            ej = json.loads(text)
            ej = ej.get("error") if isinstance(ej, dict) else None
            if isinstance(ej, dict):
                # DokuWiki returns JSON-RPC application errors as HTTP 400, with
                # the real RPC code/message nested in the body. Surface those so
                # callers can branch on the RPC code (e.g. getPageInfo's 121) and
                # rpc() prints [121] ... rather than a [400] JSON blob.
                code = ej.get("code", e.code)
                msg = ej.get("message", text)
        except (ValueError, TypeError):
            # Not JSON — a web-server/proxy/PHP error page (HTML), e.g. Apache's
            # 413 when a request-body limit is exceeded. Tag it so it isn't
            # mistaken for a JSON-RPC error, keep the HTTP status front and
            # centre, and strip tags so the agent can read which layer said what
            # (the stripped text often names the server: 'nginx', 'Apache/2.4',
            # a custom ErrorDocument, ...).
            snippet = _strip_html(text).strip()[:500]
            msg = f"HTTP {e.code} (not a JSON-RPC response)"
            if snippet:
                msg = f"{msg}: {snippet}"
        return None, {"code": code, "message": msg}
    except urllib.error.URLError as e:
        return None, {"code": "network", "message": str(e)}
    return obj.get("result"), obj.get("error")


def rpc(method, params=None):
    """Call a JSON-RPC method; exit with a clear message on ANY error (HTTP,
    network, or JSON-RPC). Returns the `result` field. Use rpc_call() directly
    when a specific error is expected and recoverable (e.g. getPageInfo's
    code 121 on a missing page)."""
    result, err = rpc_call(method, params)
    if err:
        sys.exit(f"corkboard: {method} failed: [{err.get('code')}] {err.get('message')}")
    return result


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
    return [
        p.get("id") if isinstance(p, dict) else p for p in (rpc("core.listPages", ["", 0]) or [])
    ]


def _local_targets(page):
    out = []
    for lk in rpc("core.getPageLinks", [page]) or []:
        if isinstance(lk, dict) and lk.get("type") == "local" and lk.get("page"):
            out.append(lk["page"])
    return out


def _is_entrypoint(pid):
    # landing pages are legitimately un-linked from content; don't flag as orphans
    return (
        pid.endswith(":start")
        or pid == "start"
        or pid.endswith(":sidebar")
        or pid == "sidebar"
        or pid.endswith(":playground")
        or pid == "playground"
    )


def cmd_links(page):
    for tgt in _local_targets(page):
        print(tgt)


def cmd_backlinks(page):
    for src in rpc("core.getPageBackLinks", [page]) or []:
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
        media = [
            m.get("id") if isinstance(m, dict) else m for m in (rpc("core.listMedia", [ns]) or [])
        ]
        print(f"scanning {len(media)} media files for usage...", file=sys.stderr)
        found = []
        for mid in media:
            if _is_system_media(mid):
                continue
            if not (rpc("core.getMediaUsage", [mid]) or []):
                found.append(mid)
    for mid in sorted(found):
        print(mid)


# ------------------------------------------------------------------- sitemap
def _build_tree(page_list):
    """Build a nested {pages:{}, ns:{}} tree from core.listPages results.
    Each leaf page -> {id, title, full}; intermediate components become namespaces.
    `id` is the (prefix-stripped) id used for nesting/display; `full` is the
    original wiki id, used to decide index/start markers across scoping."""
    root = {"pages": {}, "ns": {}}
    for p in page_list:
        pid = p.get("id") if isinstance(p, dict) else p
        full = p.get("full", pid) if isinstance(p, dict) else p
        title = (p.get("title") if isinstance(p, dict) else "") or ""
        parts = pid.split(":")
        node = root
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                node["pages"][part] = {"id": pid, "title": title, "full": full}
            else:
                node = node["ns"].setdefault(part, {"pages": {}, "ns": {}})
    return root


def _annotate(node):
    """Store recursive descendant page count on every node (node['count'])."""
    node["count"] = len(node["pages"]) + sum(_annotate(c) for c in node["ns"].values())
    return node["count"]


def _page_title(info, name):
    """A display title, or '' if missing / equal to the id, full id, or leaf name.
    With `useheading` off, DokuWiki returns title == the full page id, so compare
    against all of those to avoid echoing the id back as a fake title."""
    t = (info or {}).get("title", "")
    if not t:
        return ""
    if t in (info.get("id", ""), info.get("full", ""), name):
        return ""
    return t


def _is_index_page(name, info):
    """True for a namespace's index/start landing page — one named index/start
    that lives INSIDE a namespace (original full id has a colon). The wiki-root
    `start` (site homepage, no colon) is intentionally not marked."""
    if name not in ("index", "start"):
        return False
    full = (info or {}).get("full") or (info or {}).get("id", "")
    return ":" in full


def _tree_sort_key(entry):
    kind, name, _ = entry
    # float each namespace's index/start landing page to the top of its group
    return (0 if (kind == "page" and name in ("index", "start")) else 1, name)


def _emit_tree(node, lines, prefix, depth, level, system, scoped):
    """Append a box-drawing tree of node's children to `lines`.

    depth None = full tree; depth N collapses anything deeper than N levels.
    The top-level `wiki:` namespace is always collapsed in the whole-wiki view
    (it's DokuWiki's built-in docs); scope in with --ns wiki to expand it."""
    entries = [("ns", n, c) for n, c in node["ns"].items()] + [
        ("page", n, info) for n, info in node["pages"].items()
    ]
    entries.sort(key=_tree_sort_key)
    for idx, (kind, name, data) in enumerate(entries):
        last = idx == len(entries) - 1
        connector = "└── " if last else "├── "
        child_prefix = prefix + ("    " if last else "│   ")
        if kind == "page":
            is_idx = _is_index_page(name, data)
            seg = name + (" *" if is_idx else "")
            t = _page_title(data, name)
            if t:
                seg += f'  "{t}"'
            if system:
                seg += "  [system]"
            lines.append(prefix + connector + seg)
        else:  # namespace
            count = data["count"]
            is_system_ns = system or (name == "wiki" and level == 1)
            collapse_system = name == "wiki" and level == 1 and not scoped
            will_recurse = (depth is None or level < depth) and not collapse_system
            tag = "  [system]" if is_system_ns else ""
            lines.append(f"{prefix}{connector}{name}/ — {count} pages{tag}")
            if will_recurse:
                _emit_tree(data, lines, child_prefix, depth, level + 1, is_system_ns, scoped)


def _to_json(node, system=False):
    """Nested JSON tree: {pages, namespace_pages[], namespaces[]} per node."""
    return {
        "pages": node["count"],
        "namespace_pages": [
            {"id": info["id"], "title": info["title"], "is_index": _is_index_page(name, info)}
            for name, info in sorted(node["pages"].items())
        ],
        "namespaces": [
            {"name": name, **_to_json(child, system=system or name == "wiki")}
            for name, child in sorted(node["ns"].items())
        ],
    }


def cmd_sitemap(ns, depth, as_json):
    """One-call page tree (core.listPages) for orientation + placement.

    Enriched ASCII tree by default: per-namespace page counts, page titles
    (when present), index/start markers, and [system] for the wiki: namespace.
    --depth N collapses deep subtrees into counts; --json emits structured data.
    """
    ns = (ns or "").strip().rstrip(":")
    if depth == 0:
        depth = None
    raw = rpc("core.listPages", [ns, 0]) or []
    if not raw:
        print("(no pages)" if not ns else f"(no pages in {ns}:)")
        return
    # core.listPages returns FULL ids (e.g. reports:2024:q1) even when scoped,
    # so strip the namespace prefix to root the tree AT the scoped namespace.
    # Keep the original full id too (it drives index/start marking under scoping).
    pfx = f"{ns}:" if ns else ""
    pages = []
    for p in raw:
        if isinstance(p, dict):
            pid = p.get("id", "")
            np = dict(p)
            np["full"] = pid
            np["id"] = pid[len(pfx) :] if (pfx and pid.startswith(pfx)) else pid
            pages.append(np)
        else:
            pages.append(
                {"id": p[len(pfx) :] if (pfx and p.startswith(pfx)) else p, "full": p, "title": ""}
            )
    root = _build_tree(pages)
    _annotate(root)
    system = ns == "wiki" or ns.startswith("wiki:")
    if as_json:
        out = {"root": ns, "total_pages": root["count"], "tree": _to_json(root, system=system)}
        print(json.dumps(out, indent=2))
        return
    label = f"({ns}:)" if ns else "(root)"
    lines = [f"{label} — {root['count']} pages"]
    _emit_tree(root, lines, "", depth, 1, system, bool(ns))
    if system or "wiki" in root["ns"]:
        lines.append("(* = namespace index/start page; [system] = DokuWiki built-in pages)")
    else:
        lines.append("(* = namespace index/start page)")
    print("\n".join(lines))


# ------------------------------------------------------------------- editing ops
# Surgical in-place editing: edit / find / insert / apply. The string logic
# (apply_edits, locate_anchor, insert_block, find_matching_lines) is PURE — it
# takes and returns text and touches no RPC — so it is unit-tested with no
# server. The cmd_* wrappers do the getPage/commit; _commit centralizes save +
# CAS + link-health so every writer behaves identically.


def apply_edits(content, edits):
    """Apply [(old, new), ...] SEQUENTIALLY (edit N sees edit N-1's result).
    Each `old` MUST occur exactly once in the content as it stands when that
    edit runs; a 0 or >1 match raises ValueError and changes nothing. This is
    the exact-match contract of the local `edit` tool: never silently clobber.
    Returns the new content (== content when every edit is a no-op)."""
    cur = content
    for i, (old, new) in enumerate(edits, 1):
        if old == "":
            raise ValueError(f"edit {i}: --old is empty")
        n = cur.count(old)
        if n == 0:
            raise ValueError(f"edit {i}: old text not found (0 matches) — refusing to save")
        if n > 1:
            raise ValueError(
                f"edit {i}: old text matches {n} times — anchor is ambiguous; "
                f"include more surrounding text so it is unique. Refusing to save."
            )
        cur = cur.replace(old, new, 1)
    return cur


def _heading_text(line):
    """Strip DokuWiki heading decorators ('== T ==' .. '====== T ======').
    Returns the bare heading title, or None if the line isn't a heading.
    DokuWiki requires the leading and trailing '=' runs to be the SAME length,
    so we match them separately and compare — a naive backreference would
    backtracks the leading run and accept unbalanced ones like '==== T ==='."""
    m = re.match(r"^(={2,6})\s*(.*?)\s*(=+)\s*$", line.strip())
    if not m or len(m.group(1)) != len(m.group(3)):
        return None
    return m.group(2)


def locate_anchor(lines, kind, val):
    """Index (0-based) of the anchor line for an insert.
      kind='under'  -> the heading whose title EQUALS val (insert after it)
      kind='after'  -> the unique line containing val (insert after it)
      kind='before' -> the unique line containing val (insert before it)
    Raises ValueError on 0 or >1 matches so a bad anchor never silently lands."""
    if kind == "under":
        heads = [(i, _heading_text(ln)) for i, ln in enumerate(lines) if _heading_text(ln)]
        hits = [(i, t) for i, t in heads if t == val]
        if not hits:
            avail = (
                "\n".join(f"  L{i + 1}: {t}" for i, t in heads) or "  (no headings on this page)"
            )
            raise ValueError(f"no heading exactly named {val!r}. Headings:\n{avail}")
        if len(hits) > 1:
            raise ValueError(f"heading {val!r} appears {len(hits)} times — ambiguous")
        return hits[0][0]
    hits = [i for i, ln in enumerate(lines) if val in ln]
    what = "anchor"
    if not hits:
        raise ValueError(f"{what} text {val!r} not found on any line")
    if len(hits) > 1:
        raise ValueError(f"{what} text {val!r} matched {len(hits)} lines — need more context")
    return hits[0]


def insert_block(content, kind, val, text):
    """Insert `text` (one or more lines) into content at the anchor.
    kind in {under, after, before}; 'under' inserts right after the heading
    (as the section's first content). A trailing newline in `text` is kept,
    not doubled."""
    lines = content.split("\n")
    idx = locate_anchor(lines, kind, val)
    ins = text.split("\n")
    if ins and ins[-1] == "" and text.endswith("\n"):
        ins = ins[:-1]  # 'a\n'.split -> ['a','']; drop the spurious trailing empty
    if kind == "before":
        new = lines[:idx] + ins + lines[idx:]
    else:  # under / after both insert AFTER the anchor line
        new = lines[: idx + 1] + ins + lines[idx + 1 :]
    return "\n".join(new)


def find_matching_lines(content, pattern, regex, ignore_case=False):
    """[(1-based lineno, line), ...] for lines matching `pattern` (substring,
    or a Python regex when regex=True). ignore_case lowercases both sides for a
    substring match and adds re.IGNORECASE for regex. Empty list = no matches."""
    lines = content.split("\n")
    flags = re.IGNORECASE if ignore_case else 0
    if regex:
        try:
            rx = re.compile(pattern, flags)
        except re.error as e:
            sys.exit(f"corkboard: invalid regex: {e}")
        return [(i + 1, ln) for i, ln in enumerate(lines) if rx.search(ln)]
    if ignore_case:
        pat = pattern.lower()
        return [(i + 1, ln) for i, ln in enumerate(lines) if pat in ln.lower()]
    return [(i + 1, ln) for i, ln in enumerate(lines) if pattern in ln]


def _show_context(content, edits, ctx=2):
    """Preview where each edit's --old lands (first match + line numbers),
    WITHOUT saving. Reports the match count so an ambiguous anchor is visible
    before any write. Display only — does not validate uniqueness."""
    lines = content.split("\n")
    for i, (old, _new) in enumerate(edits, 1):
        count = content.count(old)
        print(f"=== edit {i}: {count} match(es) ===")
        if count == 0:
            print("  (old text not found anywhere on the page)")
            print()
            continue
        pos = content.find(old)
        lineno = content.count("\n", 0, pos) + 1
        start = max(0, lineno - ctx)
        end = min(len(lines), lineno + 1 + ctx)
        for n in range(start, end):
            mark = ">" if (n + 1) == lineno else " "
            print(f"{mark}{n + 1:>5}│{lines[n]}")
        if count > 1:
            print(
                f"  ⚠ ambiguous ({count} matches) — add context to --old "
                "so it's unique before saving"
            )
        print()
    print("(--show-context: nothing saved)")


def _load_edits(path):
    """Load a JSON edits file into [(old, new), ...]. Accepts either a flat list
    of {"old","new"} objects or a single such object."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    out = []
    for e in data:
        if not isinstance(e, dict) or "old" not in e:
            sys.exit(f"corkboard: edit entry missing 'old': {json.dumps(e)[:120]}")
        out.append((e["old"], e.get("new", "")))
    return out


# ---- RPC wrappers shared by the writers (corkboard plugin assumed present) ---
def _extract_rev(info):
    """Pull the page revision out of a core.getPageInfo result, as a string.
    This DokuWiki build serializes it as 'revision'. Returns '0' when missing /
    falsy (a brand-new page compares as 0, so the first save is allowed)."""
    if not isinstance(info, dict):
        return "0"
    return str(info.get("revision") or 0)


def _page_rev(page):
    """Current revision of a page (core.getPageInfo), as a string; '0' for a
    page that doesn't exist yet (so the first CAS save is allowed). getPageInfo
    raises JSON-RPC code 121 ('revision does not exist') on a missing page
    rather than returning a rev — treat that as '0', and let any other error
    surface. This is the CAS base value."""
    info, err = rpc_call("core.getPageInfo", [page])
    if err:
        if err.get("code") == 121:
            return "0"
        sys.exit(f"corkboard: core.getPageInfo failed: [{err.get('code')}] {err.get('message')}")
    return _extract_rev(info)


def _linkhealth(page):
    """Broken outgoing internal links from one page. plugin.corkboard.linkhealth
    is the per-page analog of wanted() — index-backed, one call."""
    return rpc("plugin.corkboard.linkhealth", [page]) or []


def _print_linkhealth(page):
    broken = _linkhealth(page)
    if broken:
        print(f"  ⚠ {len(broken)} broken outgoing link(s): {', '.join(broken)}")
    else:
        print("  ✓ no broken outgoing links")


def _save(page, text, summary, base_rev=None):
    """Save page text. With base_rev, use plugin.corkboard.cas (compare-and-swap):
    writes only if the page's current rev == base_rev, else returns conflict and
    writes nothing. Without base_rev, plain core.savePage (blind overwrite).
    Returns {saved:bool, conflict:bool, current_rev:str|None}."""
    if base_rev is not None:
        return rpc("plugin.corkboard.cas", [page, str(base_rev), text, summary, False])
    ok = rpc("core.savePage", [page, text, summary, False])
    return {"saved": bool(ok), "conflict": False, "current_rev": None}


def _commit(page, content, new_content, summary, check, use_cas):
    """Save new_content (derived from `content`) for page. Handles CAS conflict,
    prints outcome + link-health. Exits non-zero on conflict/failure; never
    writes on conflict."""
    base_rev = _page_rev(page) if use_cas else None
    res = _save(page, new_content, summary, base_rev)
    if res.get("conflict"):
        sys.exit(
            f"corkboard: CONFLICT — {page} was edited concurrently "
            f"(server rev {res.get('current_rev')}, expected {base_rev}). "
            f"NOT saved — re-run to re-fetch and retry."
        )
    if not res.get("saved"):
        sys.exit(f"corkboard: save failed for {page}: {res}")
    rev = res.get("current_rev")
    print(f"saved {page}" + (f" (rev {rev})" if rev else ""))
    if check:
        _print_linkhealth(page)


# ---- command handlers -------------------------------------------------------
def cmd_find(page, pattern, regex, ignore_case=False):
    content = rpc("core.getPage", [page]) or ""
    matches = find_matching_lines(content, pattern, regex, ignore_case)
    if not matches:
        print(f"(no matches for {pattern!r} in {page})")
        return
    for n, ln in matches:
        print(f"{n}:{ln}")


def cmd_edit(page, edits, summary, show_context, check, use_cas):
    content = rpc("core.getPage", [page]) or ""
    if show_context:
        _show_context(content, edits)
        return
    try:
        new_content = apply_edits(content, edits)
    except ValueError as e:
        sys.exit(f"corkboard: edit aborted: {e}")
    if new_content == content:
        print("(no changes — edits are no-ops); not saving")
        return
    _commit(page, content, new_content, summary, check, use_cas)


def cmd_insert(page, kind, val, text, summary, check, use_cas):
    content = rpc("core.getPage", [page]) or ""
    try:
        new_content = insert_block(content, kind, val, text)
    except ValueError as e:
        sys.exit(f"corkboard: insert aborted: {e}")
    if new_content == content:
        print("(insert is a no-op); not saving")
        return
    _commit(page, content, new_content, summary, check, use_cas)


def _entry_text(entry):
    if "text" in entry:
        return entry["text"]
    if "file" in entry:
        with open(entry["file"], encoding="utf-8") as f:
            return f.read()
    return None


def cmd_apply(path, check, use_cas, stop_on_first_error=False):
    """Apply a JSON plan of edits/inserts/replaces across pages. Each entry:
      {"page":.., "sum":.., "edits":[{"old","new"}, ...]}          surgical edit
      {"page":.., "sum":.., "insert":{"under"|"after"|"before":.., "text":..}}
      {"page":.., "sum":.., "text":.. | "file":..}                    full replace
    Per-entry "check"/"cas" booleans override the apply-level defaults. A page
    that errors (incl. RPC failures) is recorded as `failed` and the run
    CONTINUES by default — the per-page report always prints, so partial
    progress is visible (DokuWiki has no transactions: committed entries stay).
    Pass --stop-on-first-error to fail fast. Exits non-zero if any page did not
    apply (ok/noop are not failures)."""
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    if isinstance(plan, dict):
        plan = [plan]
    results = []
    for i, entry in enumerate(plan, 1):
        page = entry.get("page")
        if not page:
            results.append((f"entry#{i}", "skipped", "missing 'page'"))
            continue
        summary = entry.get("sum") or entry.get("summary") or ""
        try:
            content = rpc("core.getPage", [page]) or ""
            if "edits" in entry:
                edits = [(e["old"], e.get("new", "")) for e in entry["edits"]]
                new_content = apply_edits(content, edits)
                op = f"{len(edits)} edit(s)"
            elif "insert" in entry:
                ins = entry["insert"]
                kind = next((k for k in ("under", "after", "before") if k in ins), None)
                if kind is None:
                    raise ValueError("insert needs one of under/after/before")
                new_content = insert_block(content, kind, ins[kind], ins.get("text", ""))
                op = f"insert {kind}"
            else:
                t = _entry_text(entry)
                if t is None:
                    raise ValueError("entry needs 'edits', 'insert', 'text', or 'file'")
                new_content = t
                op = "replace"
            if new_content == content:
                results.append((page, "noop", op + " — no changes"))
                continue
            base_rev = _page_rev(page) if (use_cas and entry.get("cas", True)) else None
            res = _save(page, new_content, summary, base_rev)
            if res.get("conflict"):
                results.append((page, "conflict", f"{op} — server rev {res.get('current_rev')}"))
            elif res.get("saved"):
                msg = op
                if check and entry.get("check", True):
                    broken = _linkhealth(page)
                    msg += f"; ⚠{len(broken)} broken" if broken else "; ✓ links ok"
                results.append((page, "ok", msg))
            else:
                results.append((page, "failed", f"{op} — {res}"))
        except (ValueError, KeyError, FileNotFoundError, OSError, json.JSONDecodeError) as e:
            results.append((page or f"entry#{i}", "failed", f"{type(e).__name__}: {e}"))
        except SystemExit as e:
            # rpc()/sys.exit() failures (network, RPC errors) must not abort the
            # whole batch — record this entry as failed and keep going, then
            # print the full report so partial progress is visible (DokuWiki has
            # no transactions: already-committed earlier entries stay).
            results.append(
                (
                    page or f"entry#{i}",
                    "failed",
                    f"rpc: {str(e.code)[:160] if e.code else 'aborted'}",
                )
            )
        if stop_on_first_error and results and results[-1][1] not in ("ok", "noop"):
            break
    print("=== apply summary ===")
    for pg, status, detail in results:
        print(f"  [{status:>8}] {pg} — {detail}")
    bad = [r for r in results if r[1] not in ("ok", "noop")]
    if bad:
        sys.exit(f"\n{len(bad)} of {len(results)} page(s) not applied")


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
    p.add_argument("page")
    p.add_argument("--file")
    p.add_argument("--text")
    p.add_argument("--sum", default="")
    p.add_argument(
        "--rev",
        help="compare-and-swap: only save if current rev matches (from getPageInfo)",
    )
    p.add_argument(
        "--check",
        dest="check",
        action="store_true",
        default=False,
        help="run a post-write link-health check",
    )

    a = sp.add_parser("append", help="append text to a page via core.appendPage")
    a.add_argument("page")
    a.add_argument("--file")
    a.add_argument("--text")
    a.add_argument("--sum", default="")
    a.add_argument(
        "--check",
        dest="check",
        action="store_true",
        default=False,
        help="run a post-write link-health check",
    )

    d = sp.add_parser(
        "delete",
        help="clear page content (empty savePage — an update; token has no delete perm)",
    )
    d.add_argument("page")
    d.add_argument("--sum", default="cleared")

    lst = sp.add_parser("list", help="list page ids in a namespace (recursive by default)")
    lst.add_argument("ns")
    lst.add_argument(
        "--depth",
        type=int,
        default=0,
        help="0 = recursive (default); N = descend N levels",
    )

    sp.add_parser("all", help="list every page id")

    sp.add_parser("search", help="full-text search via core.searchPages").add_argument("query")
    sp.add_parser("version", help="DokuWiki version")

    mu = sp.add_parser("media-upload", help="upload a file via core.saveMedia (binary or text)")
    mu.add_argument("file")
    mu.add_argument("ns")
    mu.add_argument("name")
    mu.add_argument(
        "--no-overwrite",
        dest="overwrite",
        action="store_false",
        default=True,
        help="fail instead of overwriting an existing media id",
    )

    mg = sp.add_parser("media-get", help="download a media file via core.getMedia")
    mg.add_argument("mediaid")
    mg.add_argument("-o", "--out")

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

    sm = sp.add_parser(
        "sitemap",
        help="page tree in ONE call (core.listPages) — bird's-eye view + placement",
    )
    sm.add_argument("--ns", default="", help="scope to a namespace")
    sm.add_argument(
        "--depth",
        type=int,
        default=0,
        help="0 = full tree (default); N = collapse beyond N levels",
    )
    sm.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="emit nested JSON instead of an ASCII tree",
    )

    f = sp.add_parser("find", help="in-page search with line numbers (grep -n style)")
    f.add_argument("page")
    f.add_argument("pattern")
    f.add_argument("-E", "--regex", action="store_true", help="treat pattern as a Python regex")
    f.add_argument(
        "-i",
        "--ignore-case",
        dest="ignore_case",
        action="store_true",
        help="case-insensitive match",
    )

    e = sp.add_parser(
        "edit",
        help="surgical in-place edit: replace exact --old with --new (asserts a unique match)",
    )
    e.add_argument("page")
    e.add_argument(
        "--old",
        action="append",
        default=[],
        metavar="OLD",
        help="text to find (repeatable; pairs with the next --new)",
    )
    e.add_argument(
        "--new",
        action="append",
        default=[],
        metavar="NEW",
        help="replacement (repeatable; pairs with the preceding --old)",
    )
    e.add_argument(
        "--edits",
        metavar="FILE",
        help="JSON file [{old,new},...] (alternative to --old/--new)",
    )
    e.add_argument("--sum", default="")
    e.add_argument(
        "--show-context",
        action="store_true",
        help="preview each match + line numbers; save nothing",
    )
    e.add_argument(
        "--no-check",
        dest="check",
        action="store_false",
        default=True,
        help="skip the post-write link-health check",
    )
    e.add_argument(
        "--no-cas",
        dest="cas",
        action="store_false",
        default=True,
        help="skip concurrency-safe compare-and-swap (allow a blind overwrite)",
    )

    ins = sp.add_parser(
        "insert",
        help="insert text at an anchor (--under HEADING | --after LINE | --before LINE)",
    )
    ins.add_argument("page")
    g = ins.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--under",
        metavar="HEADING",
        help="insert right after the heading named HEADING",
    )
    g.add_argument("--after", metavar="LINE", help="insert after the unique line containing LINE")
    g.add_argument(
        "--before",
        metavar="LINE",
        help="insert before the unique line containing LINE",
    )
    ins.add_argument("--file")
    ins.add_argument("--text")
    ins.add_argument("--sum", default="")
    ins.add_argument("--no-check", dest="check", action="store_false", default=True)
    ins.add_argument("--no-cas", dest="cas", action="store_false", default=True)

    ap_apply = sp.add_parser(
        "apply",
        help="apply edits/inserts/replaces across pages from a JSON plan",
    )
    ap_apply.add_argument("file")
    ap_apply.add_argument("--no-check", dest="check", action="store_false", default=True)
    ap_apply.add_argument("--no-cas", dest="cas", action="store_false", default=True)
    ap_apply.add_argument(
        "--stop-on-first-error",
        dest="stop_on_first_error",
        action="store_true",
        help="abort after the first failed entry (default: continue and report all)",
    )

    raw = sp.add_parser("raw", help="escape hatch: call any JSON-RPC method")
    raw.add_argument("method")
    raw.add_argument("params", help="JSON array of params", nargs="?", default="[]")

    args = ap.parse_args()
    if args.cmd == "get":
        sys.stdout.write(rpc("core.getPage", [args.page]) or "")
    elif args.cmd == "put":
        text = _read_input(args)
        if args.rev is not None:
            res = _save(args.page, text, args.sum, args.rev)
            if res.get("conflict"):
                sys.exit(
                    f"corkboard: CONFLICT — {args.page} edited concurrently "
                    f"(server rev {res.get('current_rev')}, expected {args.rev}); NOT saved."
                )
            ok = res.get("saved")
        else:
            ok = rpc("core.savePage", [args.page, text, args.sum, False])
        print("ok" if ok else "FAILED")
        if args.check:
            _print_linkhealth(args.page)
    elif args.cmd == "append":
        ok = rpc("core.appendPage", [args.page, _read_input(args), args.sum, False])
        print("ok" if ok else "FAILED")
        if args.check:
            _print_linkhealth(args.page)
    elif args.cmd == "delete":
        ok = rpc("core.savePage", [args.page, "", args.sum, False])
        print("cleared" if ok else "FAILED")
    elif args.cmd == "list":
        for pg in rpc("core.listPages", [args.ns, args.depth]) or []:
            print(pg.get("id") if isinstance(pg, dict) else pg)
    elif args.cmd == "all":
        for pg in rpc("core.listPages", ["", 0]) or []:
            print(pg.get("id") if isinstance(pg, dict) else pg)
    elif args.cmd == "search":
        for hit in rpc("core.searchPages", [args.query]) or []:
            print(f"{hit.get('id')}\t{hit.get('title', '')}")
    elif args.cmd == "version":
        print(rpc("core.getWikiVersion", []))
    elif args.cmd == "media-upload":
        media_upload(args.file, args.ns, args.name, args.overwrite)
    elif args.cmd == "media-get":
        media_get(args.mediaid, args.out)
    elif args.cmd == "media-list":
        for m in rpc("core.listMedia", [args.ns]) or []:
            print(m.get("id") if isinstance(m, dict) else m)
    elif args.cmd == "media-info":
        print(json.dumps(rpc("core.getMediaInfo", [args.mediaid]), indent=2, default=str))
    elif args.cmd == "media-delete":
        try:
            print(rpc("core.deleteMedia", [args.mediaid]))
        except SystemExit as e:
            sys.exit(
                f"{e}\n(tip: the token has no delete permission (403); "
                "remove media via the web Media Manager.)"
            )
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
    elif args.cmd == "sitemap":
        cmd_sitemap(args.ns, args.depth, args.as_json)
    elif args.cmd == "find":
        cmd_find(args.page, args.pattern, args.regex, args.ignore_case)
    elif args.cmd == "edit":
        edits = list(_load_edits(args.edits)) if args.edits else []
        if args.old:
            if len(args.old) != len(args.new):
                sys.exit("corkboard: --old and --new must appear the same number of times")
            edits += list(zip(args.old, args.new, strict=False))
        if not edits:
            sys.exit("corkboard: edit needs at least one --old/--new pair (or --edits FILE)")
        cmd_edit(args.page, edits, args.sum, args.show_context, args.check, args.cas)
    elif args.cmd == "insert":
        if args.under is not None:
            kind, val = "under", args.under
        elif args.after is not None:
            kind, val = "after", args.after
        else:
            kind, val = "before", args.before
        cmd_insert(args.page, kind, val, _read_input(args), args.sum, args.check, args.cas)
    elif args.cmd == "apply":
        cmd_apply(args.file, args.check, args.cas, args.stop_on_first_error)
    elif args.cmd == "raw":
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as e:
            sys.exit(f"corkboard: raw params must be a JSON array: {e}")
        print(json.dumps(rpc(args.method, params), indent=2, default=str))


if __name__ == "__main__":
    main()
