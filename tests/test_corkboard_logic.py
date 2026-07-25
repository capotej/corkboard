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
    check("single replace",
          cb.apply_edits("hello world\nfoo bar", [("hello world", "hello earth")]),
          "hello earth\nfoo bar")

    check("multi sequential",
          cb.apply_edits("a=1\nb=2", [("a=1", "a=2"), ("b=2", "b=3")]),
          "a=2\nb=3")

    # edit 2 anchors text that edit 1 just produced -> sequential, not parallel
    check("sequential dependency",
          cb.apply_edits("x", [("x", "x=1"), ("x=1", "x=2")]),
          "x=2")

    check("no-op (old==new) returns identical",
          cb.apply_edits("abc", [("abc", "abc")]), "abc")

    check_raises("0 matches aborts",
                 lambda: cb.apply_edits("abc", [("zzz", "q")]), "0 matches")
    check_raises(">1 matches aborts",
                 lambda: cb.apply_edits("dup dup", [("dup", "x")]), "2 times", "ambiguous")
    check_raises("empty old aborts",
                 lambda: cb.apply_edits("abc", [("", "x")]), "empty")

    # a later bad edit must not have mutated content from an earlier good one:
    # the whole call raises and the caller simply doesn't save.
    check_raises("second edit bad -> whole call raises",
                 lambda: cb.apply_edits("a\nb", [("a", "A"), ("nope", "X")]), "not found")


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

    check("under exact heading -> heading line idx",
          cb.locate_anchor(lines, "under", "Lessons"), 1)
    check("after unique substring -> that line idx",
          cb.locate_anchor(lines, "after", "old lesson"), 2)
    check("before heading -> heading line idx",
          cb.locate_anchor(lines, "before", "Notes"), 3)

    check_raises("under missing lists candidates",
                 lambda: cb.locate_anchor(lines, "under", "Nope"),
                 "no heading", "Lessons", "Notes")

    ambig = ["== A ==", "x", "== A =="]
    check_raises("under ambiguous",
                 lambda: cb.locate_anchor(ambig, "under", "A"), "appears 2 times")

    check_raises("after missing",
                 lambda: cb.locate_anchor(lines, "after", "zzz"), "not found")

    dups = ["dup here", "dup there"]
    check_raises("after ambiguous",
                 lambda: cb.locate_anchor(dups, "after", "dup"), "matched 2 lines")


# ----------------------------------------------------------------- insert_block
def test_insert_block():
    check("under inserts after heading as first section content",
          cb.insert_block("===== Lessons =====\n- old\nmore", "under", "Lessons", "- new"),
          "===== Lessons =====\n- new\n- old\nmore")

    check("after inserts after matched line",
          cb.insert_block("a\nb\nc", "after", "b", "B2"), "a\nb\nB2\nc")

    check("before inserts before matched line",
          cb.insert_block("a\nb\nc", "before", "b", "B0"), "a\nB0\nb\nc")

    check("multiline text",
          cb.insert_block("h\nx", "after", "h", "y\nz"), "h\ny\nz\nx")

    check("trailing newline not doubled",
          cb.insert_block("h\nx", "after", "h", "- a\n"), "h\n- a\nx")

    check("text with intentional blank line preserved",
          cb.insert_block("h\nx", "after", "h", "- a\n\n"), "h\n- a\n\nx")

    check("insert preserves surrounding content far from anchor",
          cb.insert_block("top\n===== H =====\nmiddle\nbottom", "under", "H", "INS"),
          "top\n===== H =====\nINS\nmiddle\nbottom")


# ------------------------------------------------------------ find_matching_lines
def test_find_matching_lines():
    content = "alpha\nbeta\ngamma\nalphabet"
    check("single substring",
          cb.find_matching_lines(content, "beta", False), [(2, "beta")])
    check("multi substring",
          cb.find_matching_lines(content, "alph", False), [(1, "alpha"), (4, "alphabet")])
    check("regex anchored",
          cb.find_matching_lines(content, "^b", True), [(2, "beta")])
    check("no matches -> []",
          cb.find_matching_lines(content, "zzz", False), [])
    check("substring is case-sensitive by default",
          cb.find_matching_lines(content, "ALPHA", False), [])
    check("substring ignore-case matches",
          cb.find_matching_lines(content, "ALPHA", False, ignore_case=True),
          [(1, "alpha"), (4, "alphabet")])
    check("regex ignore-case",
          cb.find_matching_lines(content, "^A", True, ignore_case=True),
          [(1, "alpha"), (4, "alphabet")])


# --------------------------------------------------------------- _extract_rev
def test_extract_rev():
    # Regression guard for the live bug where the server keyed the page rev as
    # 'revision', not 'version' — _page_rev returned 0 for every existing page
    # and every CAS write conflicted. This DokuWiki build uses 'revision' only.
    check("revision key", cb._extract_rev({"revision": 1784986908}), "1784986908")
    check("version key is NOT read (this build uses 'revision')",
          cb._extract_rev({"version": 123}), "0")
    check("revision read; version ignored",
          cb._extract_rev({"revision": 9, "version": 7}), "9")
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
    cb.rpc = lambda m, p: "A\nX\n"                         # getPage
    cb.rpc_call = lambda m, p: ({"revision": 1}, None)      # getPageInfo (rev)
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
        {"page": "bad:page", "text": "BBB"},   # fails here
        {"page": "p3", "text": "CCC"},
    ]
    out_stop, _, saved_stop = _run_apply(plan, stop=True)
    check("stop-on-first-error: later entry not processed", "p3" not in saved_stop, True)
    check("stop-on-first-error: report still printed",
          "=== apply summary ===" in out_stop, True)

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

    def fake_urlopen(body):
        def _u(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request",
                                         {}, io.BytesIO(body))
        return _u

    os.environ.setdefault("CORKBOARD_URL", "https://example.com")
    os.environ.setdefault("CORKBOARD_USER", "u")
    os.environ.setdefault("CORKBOARD_PASS", "p")
    orig = urllib.request.urlopen
    try:
        # JSON-RPC error nested in an HTTP 400 body -> surface the RPC code/msg
        body = json.dumps({
            "error": {
                "code": 121,
                "message": "The requested page (revision) does not exist",
            },
        }).encode()
        urllib.request.urlopen = fake_urlopen(body)
        res, err = cb.rpc_call("core.getPageInfo", ["ghost:page"])
        check("rpc_call surfaced nested RPC code (not HTTP 400)", err.get("code"), 121)
        check("rpc_call surfaced nested RPC message",
              err.get("message"), "The requested page (revision) does not exist")
        check("rpc_call returns no result on error", res, None)

        # non-JSON 400 body -> fall back to the HTTP code + raw body
        urllib.request.urlopen = fake_urlopen(b"<html>Bad Request</html>")
        res, err = cb.rpc_call("core.savePage", ["x"])
        check("non-JSON body -> HTTP code preserved", err.get("code"), 400)
        check("non-JSON body -> raw body as message",
              err.get("message"), "<html>Bad Request</html>")
    finally:
        urllib.request.urlopen = orig


def main():
    for fn in (test_apply_edits, test_heading_text, test_locate_anchor,
               test_insert_block, test_find_matching_lines, test_extract_rev,
               test_load_edits, test_page_rev_missing_page,
               test_rpc_call_unwraps_jsonrpc_error_in_http_body,
               test_apply_resilience, test_apply_stop_on_first_error):
        fn()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
