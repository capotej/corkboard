#!/usr/bin/env python3
"""Unit tests for the PURE string logic in corkboard.py.

These deliberately exercise only the RPC-free helpers (apply_edits,
locate_anchor, insert_block, find_matching_lines, _heading_text, _load_edits)
so they run with NO live DokuWiki and NO credentials — the exact-match /
never-clobber contract and anchor placement are validated here; the RPC path
(save, cas, linkhealth) is validated against a real instance on deploy.

Run:  python3 tests/test_corkboard_logic.py
"""

import contextlib
import io
import json
import os
import sys
import tempfile

# Import the skill helper without running it (main() is __main__-guarded, and
# _cfg()/rpc() are only called lazily, so importing touches no network).
SCRIPT = os.path.join(os.path.dirname(__file__), "..", "skills", "corkboard", "script")
sys.path.insert(0, os.path.abspath(SCRIPT))
import corkboard as cb  # noqa: E402

_passed = 0
_failed = 0


def check(name, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL {name}\n  got:  {got!r}\n  want: {want!r}")


def check_raises(name, fn, *substrs):
    """Assert fn() raises ValueError whose message contains each `substrs`."""
    global _passed, _failed
    try:
        fn()
    except ValueError as e:
        msg = str(e)
        missing = [s for s in substrs if s not in msg]
        if missing:
            _failed += 1
            print(f"FAIL {name}\n  raised but message missing {missing}\n  msg: {msg!r}")
            return
        _passed += 1
        return
    except Exception as e:  # wrong exception type
        _failed += 1
        print(f"FAIL {name}\n  raised {type(e).__name__}, expected ValueError")
        return
    _failed += 1
    print(f"FAIL {name}\n  did not raise (expected ValueError)")


# ----------------------------------------------------------------- apply_edits
def test_apply_edits():
    check(
        "single replace",
        cb.apply_edits("hello world\nfoo bar", [("hello world", "hello earth")]),
        "hello earth\nfoo bar",
    )

    check(
        "multi sequential",
        cb.apply_edits("a=1\nb=2", [("a=1", "a=2"), ("b=2", "b=3")]),
        "a=2\nb=3",
    )

    # edit 2 anchors text that edit 1 just produced -> sequential, not parallel
    check("sequential dependency", cb.apply_edits("x", [("x", "x=1"), ("x=1", "x=2")]), "x=2")

    check("no-op (old==new) returns identical", cb.apply_edits("abc", [("abc", "abc")]), "abc")

    check_raises("0 matches aborts", lambda: cb.apply_edits("abc", [("zzz", "q")]), "0 matches")
    check_raises(
        ">1 matches aborts",
        lambda: cb.apply_edits("dup dup", [("dup", "x")]),
        "2 times",
        "ambiguous",
    )
    check_raises("empty old aborts", lambda: cb.apply_edits("abc", [("", "x")]), "empty")

    # a later bad edit must not have mutated content from an earlier good one:
    # the whole call raises and the caller simply doesn't save.
    check_raises(
        "second edit bad -> whole call raises",
        lambda: cb.apply_edits("a\nb", [("a", "A"), ("nope", "X")]),
        "not found",
    )


# --------------------------------------------------------------- _heading_text
def test_heading_text():
    check("h2", cb._heading_text("== T =="), "T")
    check("h3", cb._heading_text("=== T ==="), "T")
    check("h5", cb._heading_text("===== T ====="), "T")
    check("h6 balanced", cb._heading_text("====== T ======"), "T")
    check("plain text is None", cb._heading_text("just text"), None)
    check("mismatched levels is None", cb._heading_text("==== T ==="), None)
    check("leading/trailing spaces stripped", cb._heading_text("  == Spaced ==  "), "Spaced")
    check("multi-word title", cb._heading_text("===multi word heading==="), "multi word heading")
    check("non-heading with equals is None", cb._heading_text("a = b = c"), None)


# --------------------------------------------------------------- locate_anchor
def test_locate_anchor():
    lines = ["Intro", "===== Lessons =====", "- old lesson", "===== Notes =====", "- note"]

    check(
        "under exact heading -> heading line idx", cb.locate_anchor(lines, "under", "Lessons"), 1
    )
    check(
        "after unique substring -> that line idx",
        cb.locate_anchor(lines, "after", "old lesson"),
        2,
    )
    check("before heading -> heading line idx", cb.locate_anchor(lines, "before", "Notes"), 3)

    check_raises(
        "under missing lists candidates",
        lambda: cb.locate_anchor(lines, "under", "Nope"),
        "no heading",
        "Lessons",
        "Notes",
    )

    ambig = ["== A ==", "x", "== A =="]
    check_raises(
        "under ambiguous", lambda: cb.locate_anchor(ambig, "under", "A"), "appears 2 times"
    )

    check_raises("after missing", lambda: cb.locate_anchor(lines, "after", "zzz"), "not found")

    dups = ["dup here", "dup there"]
    check_raises(
        "after ambiguous", lambda: cb.locate_anchor(dups, "after", "dup"), "matched 2 lines"
    )


# ----------------------------------------------------------------- insert_block
def test_insert_block():
    check(
        "under inserts after heading as first section content",
        cb.insert_block("===== Lessons =====\n- old\nmore", "under", "Lessons", "- new"),
        "===== Lessons =====\n- new\n- old\nmore",
    )

    check(
        "after inserts after matched line",
        cb.insert_block("a\nb\nc", "after", "b", "B2"),
        "a\nb\nB2\nc",
    )

    check(
        "before inserts before matched line",
        cb.insert_block("a\nb\nc", "before", "b", "B0"),
        "a\nB0\nb\nc",
    )

    check("multiline text", cb.insert_block("h\nx", "after", "h", "y\nz"), "h\ny\nz\nx")

    check(
        "trailing newline not doubled", cb.insert_block("h\nx", "after", "h", "- a\n"), "h\n- a\nx"
    )

    check(
        "text with intentional blank line preserved",
        cb.insert_block("h\nx", "after", "h", "- a\n\n"),
        "h\n- a\n\nx",
    )

    check(
        "insert preserves surrounding content far from anchor",
        cb.insert_block("top\n===== H =====\nmiddle\nbottom", "under", "H", "INS"),
        "top\n===== H =====\nINS\nmiddle\nbottom",
    )


# ------------------------------------------------------------ find_matching_lines
def test_find_matching_lines():
    content = "alpha\nbeta\ngamma\nalphabet"
    check("single substring", cb.find_matching_lines(content, "beta", False), [(2, "beta")])
    check(
        "multi substring",
        cb.find_matching_lines(content, "alph", False),
        [(1, "alpha"), (4, "alphabet")],
    )
    check("regex anchored", cb.find_matching_lines(content, "^b", True), [(2, "beta")])
    check("no matches -> []", cb.find_matching_lines(content, "zzz", False), [])
    check(
        "substring is case-sensitive by default",
        cb.find_matching_lines(content, "ALPHA", False),
        [],
    )
    check(
        "substring ignore-case matches",
        cb.find_matching_lines(content, "ALPHA", False, ignore_case=True),
        [(1, "alpha"), (4, "alphabet")],
    )
    check(
        "regex ignore-case",
        cb.find_matching_lines(content, "^A", True, ignore_case=True),
        [(1, "alpha"), (4, "alphabet")],
    )


# --------------------------------------------------------------- _extract_rev
def test_extract_rev():
    # Regression guard for the live bug where the server keyed the page rev as
    # 'revision', not 'version' — _page_rev returned 0 for every existing page
    # and every CAS write conflicted. This DokuWiki build uses 'revision' only.
    check("revision key", cb._extract_rev({"revision": 1784986908}), "1784986908")
    check(
        "version key is NOT read (this build uses 'revision')",
        cb._extract_rev({"version": 123}),
        "0",
    )
    check("revision read; version ignored", cb._extract_rev({"revision": 9, "version": 7}), "9")
    check("missing -> 0 (new page)", cb._extract_rev({}), "0")
    check("falsy revision -> 0", cb._extract_rev({"revision": 0}), "0")
    check("string revision coerced", cb._extract_rev({"revision": "1784986908"}), "1784986908")
    check("non-dict (None) -> 0", cb._extract_rev(None), "0")
    check("non-dict (list) -> 0", cb._extract_rev(["x"]), "0")


# ------------------------------------------------------------------- _load_edits
def test_load_edits():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
        json.dump([{"old": "a", "new": "b"}, {"old": "c", "new": "d"}], tf)
        path_list = tf.name
    check("flat list", cb._load_edits(path_list), [("a", "b"), ("c", "d")])
    os.unlink(path_list)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
        json.dump({"old": "x", "new": "y"}, tf)
        path_one = tf.name
    check("single object -> one edit", cb._load_edits(path_one), [("x", "y")])
    os.unlink(path_one)


def test_page_rev_missing_page():
    # Bug #2 guard: getPageInfo raises code 121 on a missing page, which used to
    # crash _page_rev (and thus every CAS write on a new page through
    # edit/insert/apply). code 121 must map to rev "0"; other errors must still
    # surface so they are not silently swallowed.
    orig = cb.rpc_call
    try:
        cb.rpc_call = lambda m, p: (None, {"code": 121, "message": "revision does not exist"})
        check("missing page -> rev 0", cb._page_rev("ghost:page"), "0")

        cb.rpc_call = lambda m, p: ({"revision": 1784986908}, None)
        check("existing page -> its rev", cb._page_rev("real:page"), "1784986908")

        cb.rpc_call = lambda m, p: (None, {"code": 500, "message": "boom"})
        raised = False
        try:
            cb._page_rev("real:page")
        except SystemExit:
            raised = True
        check("non-121 error still exits", raised, True)
    finally:
        cb.rpc_call = orig


def _stub_apply(saved, fail_page):
    """Wire no-op RPC stubs onto corkboard; _save appends to `saved` unless the
    page is `fail_page`, in which case it sys.exits (simulating an RPC failure).
    Returns the original values for restoration."""
    orig = (cb.rpc, cb.rpc_call, cb._save, cb._linkhealth)
    cb.rpc = lambda m, p: "A\nX\n"  # getPage
    cb.rpc_call = lambda m, p: ({"revision": 1}, None)  # getPageInfo (rev)
    cb._linkhealth = lambda page: []

    def fake_save(page, text, summary, base_rev=None):
        if page == fail_page:
            sys.exit("corkboard: simulated RPC failure")
        saved.append(page)
        return {"saved": True, "conflict": False, "current_rev": "2"}

    cb._save = fake_save
    return orig


def _run_apply(plan, stop):
    """Run cmd_apply on a temp plan with stubs; return (stdout, exit_code)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
        json.dump(plan, tf)
        path = tf.name
    saved = []
    orig = _stub_apply(saved, "bad:page")
    buf = io.StringIO()
    exit_code = None
    try:
        with contextlib.redirect_stdout(buf):
            cb.cmd_apply(path, True, True, stop)
    except SystemExit as e:
        exit_code = e.code
    finally:
        cb.rpc, cb.rpc_call, cb._save, cb._linkhealth = orig
        os.unlink(path)
    return buf.getvalue(), exit_code, saved


def test_apply_resilience():
    # Observation guard: an entry whose RPC fails (sys.exit) must NOT abort the
    # batch silently. Entry 1 commits; entry 2 fails; the per-page report still
    # prints so partial progress is visible; exit is non-zero.
    plan = [
        {"page": "ok:page", "edits": [{"old": "A", "new": "B"}]},
        {"page": "bad:page", "edits": [{"old": "X", "new": "Y"}]},
    ]
    out, exit_code, saved = _run_apply(plan, stop=False)
    check("apply printed the report header", "=== apply summary ===" in out, True)
    check("apply recorded the ok entry", "[      ok] ok:page" in out, True)
    check("apply recorded the failed entry", "[  failed] bad:page" in out, True)
    check("apply committed entry 1 before entry 2 failed", saved, ["ok:page"])
    check("apply exited non-zero on partial failure", bool(exit_code), True)


def test_apply_stop_on_first_error():
    # With --stop-on-first-error, processing halts after the first failure and
    # later entries are NOT attempted (but the report still prints). Default
    # (continue) still tries every entry.
    plan = [
        {"page": "p1", "text": "AAA"},
        {"page": "bad:page", "text": "BBB"},  # fails here
        {"page": "p3", "text": "CCC"},
    ]
    out_stop, _, saved_stop = _run_apply(plan, stop=True)
    check("stop-on-first-error: later entry not processed", "p3" not in saved_stop, True)
    check("stop-on-first-error: report still printed", "=== apply summary ===" in out_stop, True)

    out_cont, _, saved_cont = _run_apply(plan, stop=False)
    check("default continue: later entry IS processed", "p3" in saved_cont, True)
    check("default continue: only the failing page unsaved", set(saved_cont), {"p1", "p3"})


def test_rpc_call_unwraps_jsonrpc_error_in_http_body():
    # Root-cause guard for the live bug: DokuWiki returns JSON-RPC application
    # errors (e.g. getPageInfo's code 121) as HTTP 400, with the real RPC
    # code/message nested in the body. rpc_call must surface the nested code so
    # _page_rev's 121 branch fires and rpc() prints [121] ... not [400] <blob>.
    import urllib.error
    import urllib.request

    def fake_urlopen(body, status=400, reason="Bad Request"):
        def _u(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, status, reason, {}, io.BytesIO(body))

        return _u

    os.environ.setdefault("CORKBOARD_URL", "https://example.com")
    os.environ.setdefault("CORKBOARD_USER", "u")
    os.environ.setdefault("CORKBOARD_PASS", "p")
    orig = urllib.request.urlopen
    try:
        # JSON-RPC error nested in an HTTP 400 body -> surface the RPC code/msg
        body = json.dumps(
            {
                "error": {
                    "code": 121,
                    "message": "The requested page (revision) does not exist",
                },
            }
        ).encode()
        urllib.request.urlopen = fake_urlopen(body)
        res, err = cb.rpc_call("core.getPageInfo", ["ghost:page"])
        check("rpc_call surfaced nested RPC code (not HTTP 400)", err.get("code"), 121)
        check(
            "rpc_call surfaced nested RPC message",
            err.get("message"),
            "The requested page (revision) does not exist",
        )
        check("rpc_call returns no result on error", res, None)

        # non-JSON HTTP body (a web-server error page, e.g. Apache/nginx 413
        # when a request-body limit trips) -> HTTP code preserved, message
        # tagged as NOT a JSON-RPC response, tags stripped so the readable body
        # names the layer. (The [:300]-cap-on-raw-HTML this replaced made every
        # 413 read identically no matter which layer rejected it.)
        urllib.request.urlopen = fake_urlopen(b"<html>Bad Request</html>")
        res, err = cb.rpc_call("core.savePage", ["x"])
        check("non-JSON body -> HTTP code preserved", err.get("code"), 400)
        msg = err.get("message")
        check(
            "non-JSON body -> flagged not-a-JSON-RPC-response",
            "not a JSON-RPC response" in msg,
            True,
        )
        check("non-JSON body -> HTTP status in message", "400" in msg, True)
        check("non-JSON body -> tags stripped (readable body present)", "Bad Request" in msg, True)
        check("non-JSON body -> no raw <html> tag in message", "<html>" not in msg, True)

        # 413 from a server that names itself (nginx) -> the readable body must
        # surface the layer, so the agent can tell who rejected the upload.
        nginx = b"<html><head><title>413</title></head><body><center>nginx</center></body></html>"
        urllib.request.urlopen = fake_urlopen(nginx, 413, "Request Entity Too Large")
        res, err = cb.rpc_call("core.saveMedia", ["reports:x.png", "b64", True])
        check("413 body -> HTTP code preserved", err.get("code"), 413)
        check("413 body -> names the rejecting layer (nginx)", "nginx" in err.get("message"), True)
        check(
            "413 body -> tagged not-a-JSON-RPC-response",
            "not a JSON-RPC response" in err.get("message"),
            True,
        )
    finally:
        urllib.request.urlopen = orig


def _rules(findings):
    """Sorted rule ids from a findings list — compact way to assert which rules
    fired (and that no others did)."""
    return sorted({f.rule for f in findings})


def test_lint_dw001_mixed_table_headers():
    # Most common production breakage (14 across 11 pages in one scan): a header
    # row that starts with ^ but mixes | separators renders the whole table as
    # literal text.
    check(
        "mixed header detected",
        _rules(cb.lint_wikitext("^ Col1 | Col2 |")),
        ["DW001"],
    )
    check(
        "mixed header fix replaces | with ^",
        cb.lint_wikitext("^ Col1 | Col2 |")[0].suggestion,
        "^ Col1 ^ Col2 ^",
    )
    check("all-^ header is clean", _rules(cb.lint_wikitext("^ Col1 ^ Col2 ^")), [])
    check("all-| data row is clean (not a header)", _rules(cb.lint_wikitext("| a | b |")), [])


def test_lint_dw002_indented_table_rows():
    # A ^/| row with leading whitespace trips the 2-space code-block parser; the
    # whole table renders as a gray code box.
    f = cb.lint_wikitext("  ^ H ^\n  | d |")
    check("indented header flagged", _rules(f), ["DW002"])
    check(
        "indented row reports the indent depth",
        f[0].message,
        "Indented table row (2-space) renders as code, not a table",
    )
    check("indented row fix strips leading ws", f[0].suggestion, "^ H ^")
    check("column-0 table is clean", _rules(cb.lint_wikitext("^ H ^\n| d |")), [])


def test_lint_dw003_list_continuation():
    # The Markdown habit: a continuation indented to align with item text renders
    # as a code block while the item's first line renders fine. Report only.
    f = cb.lint_wikitext("  - **Item.** desc\n    continues here")
    check("4-space continuation after list item flagged", _rules(f), ["DW003"])
    check("DW003 is report-only (no suggestion)", f[0].suggestion, None)
    check("same-line item is clean", _rules(cb.lint_wikitext("  - item all on one line")), [])
    check(
        "non-indented line after item is clean",
        _rules(cb.lint_wikitext("  - item\nplain text")),
        [],
    )
    # multi-line continuation: the second continuation is flagged too (list ctx persists)
    check(
        "multi-line continuation both flagged",
        [x.line for x in cb.lint_wikitext("  - a\n    c1\n    c2") if x.rule == "DW003"],
        [2, 3],
    )


def test_lint_dw004_markdown_headings():
    check("# H1 -> 6 equals", cb.lint_wikitext("# Foo")[0].suggestion, "====== Foo ======")
    check("## H2 -> 5 equals", cb.lint_wikitext("## Foo")[0].suggestion, "===== Foo =====")
    check(
        "###### H6 clamps to 2 equals (no DokuWiki H6)",
        cb.lint_wikitext("###### Foo")[0].suggestion,
        "== Foo ==",
    )
    check("ATX closing # stripped", cb.lint_wikitext("# Foo #")[0].suggestion, "====== Foo ======")
    check("no-space-after-# is not a heading", _rules(cb.lint_wikitext("#tag")), [])
    check("DokuWiki = heading is clean", _rules(cb.lint_wikitext("====== Foo ======")), [])


def test_lint_dw005_markdown_links():
    f = cb.lint_wikitext("see [text](https://x.org) here")
    check("markdown link detected", _rules(f), ["DW005"])
    check("markdown link fix swaps args", f[0].suggestion, "see [[https://x.org|text]] here")
    check("DokuWiki [[url|text]] is clean", _rules(cb.lint_wikitext("[[https://x.org|text]]")), [])
    check("DokuWiki [[page]] is clean", _rules(cb.lint_wikitext("[[a:b]]")), [])


def test_lint_dw006_markdown_fences():
    f = cb.lint_wikitext("```python\ncode\n```")
    check("both fence lines flagged", [x.line for x in f if x.rule == "DW006"], [1, 3])
    check(
        "opening fence -> <code lang>",
        [x for x in f if x.line == 1][0].suggestion,
        "<code python>",
    )
    check("closing fence -> </code>", [x for x in f if x.line == 3][0].suggestion, "</code>")
    check("bare fence -> <code>", cb.lint_wikitext("```")[0].suggestion, "<code>")
    check("DokuWiki <code> is clean", _rules(cb.lint_wikitext("<code>\nx\n</code>")), [])


def test_lint_dw007_namespace_relative_links():
    # [[start]] on a page in projects: resolves to projects:start, not root.
    f = cb.lint_wikitext("[[start]]", "projects")
    check("bare token flagged in a namespace", _rules(f), ["DW007"])
    check("bare token fix prepends colon", f[0].suggestion, "[[:start]]")
    check("absolute [[:start]] is clean", _rules(cb.lint_wikitext("[[:start]]", "projects")), [])
    check(
        "multi-segment [[a:b]] is clean (root-relative)",
        _rules(cb.lint_wikitext("[[a:b]]", "projects")),
        [],
    )
    check(
        "external [[https://x]] is clean",
        _rules(cb.lint_wikitext("[[https://x|y]]", "projects")),
        [],
    )
    check("interwiki [[wp>Foo]] is clean", _rules(cb.lint_wikitext("[[wp>Foo]]", "projects")), [])
    check(
        "same-page anchor [[#sec]] is clean", _rules(cb.lint_wikitext("[[#sec]]", "projects")), []
    )
    check(
        "bare token in ROOT namespace is clean (no ambiguity)",
        _rules(cb.lint_wikitext("[[start]]", "")),
        [],
    )


def test_lint_dw008_links_in_headings():
    f = cb.lint_wikitext("====== See [[x]] ======")
    check("link in heading flagged", _rules(f), ["DW008"])
    check("DW008 is report-only", f[0].suggestion, None)
    check("plain heading is clean", _rules(cb.lint_wikitext("====== Title ======")), [])


def test_lint_dw009_mediawiki_tags():
    check("<gallery> flagged", _rules(cb.lint_wikitext("<gallery>")), ["DW009"])
    check("</figure> flagged", _rules(cb.lint_wikitext("</figure>")), ["DW009"])
    check(
        "<figcaption class=x> flagged (with attrs)",
        _rules(cb.lint_wikitext("<figcaption class=x>")),
        ["DW009"],
    )
    check("DokuWiki <code> is clean", _rules(cb.lint_wikitext("<code>")), [])


def test_lint_code_block_exclusion():
    # The core correctness property: <code>/<file> and fence interiors are
    # literal, so every rule must skip them. A dirty line that triggers 3 rules
    # in the open triggers NONE inside a block.
    dirty = "^ a | b |\n  | c |\n# h"  # DW001, DW002, DW004 in the open
    check(
        "dirty lines flagged in the open",
        _rules(cb.lint_wikitext(dirty)),
        ["DW001", "DW002", "DW004"],
    )
    blocked = "<code>\n" + dirty + "\n</code>"
    check("same lines clean inside <code>", _rules(cb.lint_wikitext(blocked)), [])
    fenced = "```\n" + dirty + "\n```"
    check(
        "interior clean inside a fence (only the 2 fences flagged)",
        _rules(cb.lint_wikitext(fenced)),
        ["DW006"],
    )
    # a fence nested inside <code> is literal -> not converted, doesn't toggle
    check(
        "fence inside <code> is clean",
        _rules(cb.lint_wikitext("<code>\n```\n# h\n```\n</code>")),
        [],
    )


def test_lint_findings_sorted():
    f = cb.lint_wikitext("# h\n^ a | b\n[t](u)")
    check(
        "findings sorted by line then rule",
        [(x.line, x.rule) for x in f],
        [(1, "DW004"), (2, "DW001"), (3, "DW005")],
    )


def test_lint_namespace_helper():
    check("projects:foo -> projects", cb._namespace_of("projects:foo"), "projects")
    check("a:b:c -> a:b", cb._namespace_of("a:b:c"), "a:b")
    check("start -> '' (root)", cb._namespace_of("start"), "")
    check("'' -> ''", cb._namespace_of(""), "")


def test_lint_fix():
    # Each auto-fixable rule applied in place.
    check("DW001 fix", cb.lint_fix("^ A | B")[0], "^ A ^ B")
    check("DW002 fix", cb.lint_fix("  ^ A ^")[0], "^ A ^")
    check("DW004 fix", cb.lint_fix("# H")[0], "====== H ======")
    check("DW005 fix", cb.lint_fix("[t](u)")[0], "[[u|t]]")
    check("DW006 fix", cb.lint_fix("```python\nx\n```"), ("<code python>\nx\n</code>", 2))
    check("DW007 fix (needs namespace)", cb.lint_fix("[[start]]", "projects")[0], "[[:start]]")
    # cascade: markdown link [t](bare) -> [[bare|t]] -> DW007 absolutizes -> [[:bare|t]]
    check("DW005->DW007 cascade", cb.lint_fix("[t](start)", "projects")[0], "[[:start|t]]")
    # report-only rules (DW003/8/9) are NOT touched by --fix
    fixed, n = cb.lint_fix("<gallery>")
    check("DW009 not auto-fixed", fixed, "<gallery>")
    check("DW009 not counted as fixed", n, 0)
    # clean content is a no-op
    clean = "====== T ======\n^ A ^ B ^\n| 1 |"
    check("clean content unchanged", cb.lint_fix(clean), (clean, 0))


# --- move command (plugin.corkboard.move) ------------------------------------
def _stub_move(result):
    """Wire a no-op RPC stub for cmd_move; returns originals for restoration."""
    orig = (cb.rpc, cb._linkhealth)
    cb.rpc = lambda m, p: result
    cb._linkhealth = lambda page: []
    return orig


def _run_move(result, **flags):
    """Run cmd_move with stubs; return (stdout, exit_code_or_None)."""
    defaults = dict(kind="page", ns=False, rewrite=True, autoskip=False, check=True)
    defaults.update(flags)
    buf = io.StringIO()
    exit_code = None
    orig = _stub_move(result)
    try:
        with contextlib.redirect_stdout(buf):
            cb.cmd_move("old:page", "new:page", **defaults)
    except SystemExit as e:
        exit_code = e.code
    finally:
        cb.rpc, cb._linkhealth = orig
    return buf.getvalue(), exit_code


def test_build_move_opts_defaults():
    check(
        "move opts default to page, rewrite on",
        cb.build_move_opts("page", False, True, False),
        {"kind": "page", "ns": False, "rewrite": True, "autoskip": False},
    )


def test_build_move_opts_media_ns():
    check(
        "move opts reflect media + namespace",
        cb.build_move_opts("media", True, True, False),
        {"kind": "media", "ns": True, "rewrite": True, "autoskip": False},
    )


def test_build_move_opts_no_rewrite_autoskip():
    check(
        "move opts reflect --no-rewrite + --autoskip",
        cb.build_move_opts("page", False, False, True),
        {"kind": "page", "ns": False, "rewrite": False, "autoskip": True},
    )


def test_move_success_prints_and_checks():
    out, exit_code = _run_move(
        {"moved": True, "src": "old:page", "dst": "new:page", "kind": "page", "steps": 2}
    )
    check("move success prints moved line", "moved old:page -> new:page" in out, True)
    check("move success reports steps", "2 step" in out, True)
    check("move success runs link-health on dst", "no broken outgoing links" in out, True)
    check("move success exits cleanly", exit_code is None, True)


def test_move_failure_exits_with_reason():
    out, exit_code = _run_move(
        {"moved": False, "src": "old:page", "dst": "new:page", "kind": "page", "reason": "exists"}
    )
    check("move failure exits non-zero", bool(exit_code), True)
    check("move failure names the reason", "exists" in str(exit_code), True)


def test_move_in_progress_hint():
    out, exit_code = _run_move(
        {"moved": False, "src": "a", "dst": "b", "kind": "page", "reason": "in_progress"}
    )
    check("move in_progress surfaces the retry hint", "retry" in str(exit_code), True)


def main():
    for fn in (
        test_apply_edits,
        test_heading_text,
        test_locate_anchor,
        test_insert_block,
        test_find_matching_lines,
        test_extract_rev,
        test_load_edits,
        test_page_rev_missing_page,
        test_rpc_call_unwraps_jsonrpc_error_in_http_body,
        test_apply_resilience,
        test_apply_stop_on_first_error,
        test_lint_dw001_mixed_table_headers,
        test_lint_dw002_indented_table_rows,
        test_lint_dw003_list_continuation,
        test_lint_dw004_markdown_headings,
        test_lint_dw005_markdown_links,
        test_lint_dw006_markdown_fences,
        test_lint_dw007_namespace_relative_links,
        test_lint_dw008_links_in_headings,
        test_lint_dw009_mediawiki_tags,
        test_lint_code_block_exclusion,
        test_lint_findings_sorted,
        test_lint_namespace_helper,
        test_lint_fix,
        test_build_move_opts_defaults,
        test_build_move_opts_media_ns,
        test_build_move_opts_no_rewrite_autoskip,
        test_move_success_prints_and_checks,
        test_move_failure_exits_with_reason,
        test_move_in_progress_hint,
    ):
        fn()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
