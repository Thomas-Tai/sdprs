# sdprs/webcam_client/tests/test_wizard_connection.py
"""The setup window's network layer: the 測試連線 probe and the two async
wrappers, plus two bugs the registration path shipped with.

Two things this file exists to defend.

U3 -- the window must never block the Tk thread. `root.update()` pumps the
event loop exactly ONCE and then the synchronous POST owns the thread for the
whole connect timeout: unrepaintable, undraggable, "not responding" painted
over it by Windows. A guard reads that as a crash and pulls the power. So every
network call the window makes has to happen on a worker thread, and the
"runs off the calling thread" assertions below are the only thing that keeps it
there -- a future refactor that inlines the call back onto the caller would
otherwise pass every other test in this file.

U4 -- 測試連線 must prove the address and the password WITHOUT registering
anything. A test that only checked the return value would still pass if someone
reimplemented the probe on top of POST /api/webcam/cameras, which would mint a
node_id (and a dashboard tile) every time a guard pressed a button labelled
"test". `post.assert_not_called()` is the assertion that actually carries the
requirement.

No test here performs real network I/O: httpx is patched at
`webcam_client.gui.wizard.connection.httpx.<method>` throughout.
"""
import ast
import re
import sys
import threading as _t
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client import strings
# Imported as a MODULE on purpose -- never `from ... import test_connection_async`.
# pytest collects any module-level callable named test_* in THIS file, so binding
# the wizard's own test_*-named helper here would turn it into a bogus "test"
# that errors out on a missing `url` fixture. Reaching through `conn.` keeps
# that name out of this module's namespace.
from webcam_client.gui.wizard import connection as conn


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

class _Sink:
    """Records every on_done call, and the thread it arrived on."""

    def __init__(self):
        self.calls = []
        self.threads = []
        self.done = _t.Event()

    def __call__(self, result, error):
        self.threads.append(_t.current_thread())
        self.calls.append((result, error))
        self.done.set()

    @property
    def result(self):
        return self.calls[0][0]

    @property
    def error(self):
        return self.calls[0][1]


def _ping_ok():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"ok": True}
    return resp


def _status(code):
    resp = MagicMock(status_code=code)
    resp.json.return_value = {}
    return resp


# A status code is a 3-digit 1xx-5xx run that is not part of a longer number.
# Same detector test_strings.py uses, for the same reason: `\b` is defined by
# `\w`, and CJK ideographs are `\w`, so a code glued onto Han text slips past a
# word-boundary pattern.
_STATUS_CODE_RE = r"(?<!\d)[1-5]\d\d(?!\d)"
_CJK_RE = r"[一-鿿]"


# --------------------------------------------------------------------------
# U4 -- the probe hits /api/webcam/ping and registers nothing.
# --------------------------------------------------------------------------

def test_probe_connection_calls_the_ping_endpoint_with_the_key_in_the_header():
    with patch("webcam_client.gui.wizard.connection.httpx.get",
               return_value=_ping_ok()) as get:
        ok, err = conn.probe_connection("http://x:8000", "sk-webcam-aaa")
    assert (ok, err) == (True, None)
    url = get.call_args.args[0] if get.call_args.args else get.call_args.kwargs["url"]
    assert url == "http://x:8000/api/webcam/ping", url
    assert get.call_args.kwargs["headers"] == {"X-API-Key": "sk-webcam-aaa"}


def test_probe_connection_never_posts_anything():
    """THE U4 requirement. 測試連線 must be side-effect-free: a probe built on
    the registration endpoint would mint a node_id -- and a duplicate dashboard
    tile -- every time the guard pressed a button labelled "test"."""
    with patch("webcam_client.gui.wizard.connection.httpx.post") as post, \
         patch("webcam_client.gui.wizard.connection.httpx.get",
               return_value=_ping_ok()):
        conn.probe_connection("http://x", "k")
    post.assert_not_called()


def test_test_connection_async_never_posts_on_any_branch():
    """The async wrapper is what the window actually calls, so pin the same
    requirement there -- on the failure branches too, where a "let's just try
    registering instead" fallback would be easiest to sneak in."""
    branches = [
        {"return_value": _ping_ok()},
        {"return_value": _status(401)},
        {"return_value": _status(500)},
        {"side_effect": httpx.ConnectError("refused")},
    ]
    for kwargs in branches:
        sink = _Sink()
        with patch("webcam_client.gui.wizard.connection.httpx.post") as post, \
             patch("webcam_client.gui.wizard.connection.httpx.get", **kwargs):
            conn.test_connection_async("http://x", "k", sink).join(5)
        post.assert_not_called()
        assert len(sink.calls) == 1, kwargs


# --------------------------------------------------------------------------
# U3 -- the probe runs off the Tk thread.
# --------------------------------------------------------------------------

def test_test_connection_async_runs_off_the_calling_thread():
    seen = {}

    def fake_get(url, **kw):
        seen["thread"] = _t.current_thread()
        return _ping_ok()

    sink = _Sink()
    with patch("webcam_client.gui.wizard.connection.httpx.get", side_effect=fake_get):
        conn.test_connection_async("http://x", "k", sink)
        assert sink.done.wait(5), "on_done was never called"
    assert seen["thread"] is not _t.current_thread(), \
        "the probe must not run on the caller thread"
    assert sink.threads[0] is not _t.current_thread(), \
        "on_done must arrive on the worker thread; marshalling back is the window's job"


def test_test_connection_async_calls_back_exactly_once_on_success():
    sink = _Sink()
    with patch("webcam_client.gui.wizard.connection.httpx.get",
               return_value=_ping_ok()):
        conn.test_connection_async("http://x", "k", sink).join(5)
    assert len(sink.calls) == 1, sink.calls
    assert sink.result is True
    assert sink.error is None


# --------------------------------------------------------------------------
# Fault mapping. Every message is asserted against the CONSTANT, never a
# phrase: the copy is operator text and may be rewritten, but it must always be
# THE message for that fault, and it must never carry a code or an exception.
# --------------------------------------------------------------------------

def test_a_rejected_key_is_reported_as_a_wrong_password():
    sink = _Sink()
    with patch("webcam_client.gui.wizard.connection.httpx.get",
               return_value=_status(401)):
        conn.test_connection_async("http://x", "wrong", sink).join(5)
    assert sink.result is False
    assert sink.error == strings.WIZ_KEY_REJECTED
    assert "401" not in sink.error


def test_transport_failures_are_reported_as_cannot_reach_server():
    """The whole httpx failure family, not just ConnectError -- a schemeless
    URL raises UnsupportedProtocol, an unreachable host ConnectTimeout. And the
    exception's own text ("All connection attempts failed") must stay in the
    log: it is English and it blames the server for what is most often a
    mistyped address."""
    for exc in (httpx.UnsupportedProtocol("no scheme"),
                httpx.ConnectTimeout("timed out"),
                httpx.ConnectError("refused"),
                httpx.ReadTimeout("read timed out")):
        sink = _Sink()
        with patch("webcam_client.gui.wizard.connection.httpx.get", side_effect=exc):
            conn.test_connection_async("http://x", "k", sink).join(5)
        assert len(sink.calls) == 1, exc
        assert sink.result is False
        assert sink.error == strings.WIZ_CANNOT_REACH_SERVER, (exc, sink.error)
        assert str(exc) not in sink.error, \
            "the exception repr must go to the log, not the guard"


def test_a_non_http_error_from_httpx_still_produces_a_message():
    """httpx.InvalidURL does NOT inherit from httpx.HTTPError -- it derives
    straight from Exception. A `except httpx.HTTPError` therefore lets it
    escape, and in a console=False exe an escaped exception is swallowed to a
    stderr nobody reads: the button simply appears dead. A guard who pastes an
    address like `http://` reaches exactly this path."""
    sink = _Sink()
    with patch("webcam_client.gui.wizard.connection.httpx.get",
               side_effect=httpx.InvalidURL("no host in url")):
        conn.test_connection_async("http://", "k", sink).join(5)
    assert len(sink.calls) == 1
    assert sink.result is False
    assert sink.error == strings.WIZ_CANNOT_REACH_SERVER


def test_register_cameras_does_not_raise_on_a_non_http_error_either():
    """register_cameras' docstring has always promised it never raises, and
    `except httpx.HTTPError` did not deliver that. `http://` is a live example:
    normalize_server_url rstrips it to `http:`, which is not a URL httpx can
    build a request from."""
    with patch("webcam_client.gui.wizard.connection.httpx.post",
               side_effect=httpx.InvalidURL("no host in url")):
        cams, err = conn.register_cameras("http:", "k", [{"device_index": 0}])
    assert cams is None
    assert err == strings.WIZ_CANNOT_REACH_SERVER


def test_any_other_status_is_reported_as_server_refused_without_the_code():
    sink = _Sink()
    with patch("webcam_client.gui.wizard.connection.httpx.get",
               return_value=_status(500)):
        conn.test_connection_async("http://x", "k", sink).join(5)
    assert sink.result is False
    assert "500" not in sink.error, \
        f"the guard must never be shown a status code: {sink.error!r}"
    assert sink.error == strings.WIZ_SERVER_REFUSED


def test_a_200_that_is_not_the_ping_body_is_not_reported_as_success():
    """The window unlocks section 2 on this result, so "connected" must mean
    "the SDPRS server answered", not "something answered 200". A captive portal
    or a misrouted proxy returns 200 carrying HTML; claiming 連線成功 there
    hands the guard a verified state they have not earned, and the failure
    resurfaces minutes later at 開始 with all their work done."""
    for body in ({"ok": False}, {}, [], "OK", None):
        resp = MagicMock(status_code=200)
        resp.json.return_value = body
        sink = _Sink()
        with patch("webcam_client.gui.wizard.connection.httpx.get", return_value=resp):
            conn.test_connection_async("http://x", "k", sink).join(5)
        assert sink.result is False, body
        assert sink.error == strings.WIZ_BAD_RESPONSE, body

    resp = MagicMock(status_code=200)
    resp.json.side_effect = ValueError("not json")
    sink = _Sink()
    with patch("webcam_client.gui.wizard.connection.httpx.get", return_value=resp):
        conn.test_connection_async("http://x", "k", sink).join(5)
    assert sink.result is False
    assert sink.error == strings.WIZ_BAD_RESPONSE


def test_the_ping_path_reuses_the_registration_faults_wording():
    """One fault, one wording. A second spelling of "wrong password" would be
    invisible in review -- each is only reachable down its own branch -- and
    would teach the guard that the two buttons fail for different reasons."""
    assert conn.probe_connection.__doc__  # the function exists and is documented
    with patch("webcam_client.gui.wizard.connection.httpx.get",
               return_value=_status(401)):
        _, probe_err = conn.probe_connection("http://x", "k")
    with patch("webcam_client.gui.wizard.connection.httpx.post",
               return_value=_status(401)):
        _, register_err = conn.register_cameras("http://x", "k", [{"device_index": 0}])
    assert probe_err == register_err == strings.WIZ_KEY_REJECTED


# --------------------------------------------------------------------------
# on_done fires exactly once on EVERY path, including when the worker raises.
# A worker that dies silently leaves the window stuck on 正在測試連線，請稍候…
# forever, with its buttons disabled -- indistinguishable from the freeze this
# whole change exists to remove.
# --------------------------------------------------------------------------

def test_test_connection_async_calls_back_once_even_when_the_worker_raises():
    with patch("webcam_client.gui.wizard.connection.probe_connection",
               side_effect=RuntimeError("boom")):
        sink = _Sink()
        conn.test_connection_async("http://x", "k", sink).join(5)
    assert len(sink.calls) == 1, f"on_done must fire exactly once: {sink.calls!r}"
    assert sink.result is False
    assert sink.error, "a silent failure leaves the window waiting forever"
    assert "boom" not in sink.error


def test_register_cameras_async_calls_back_once_even_when_the_worker_raises():
    with patch("webcam_client.gui.wizard.connection.register_cameras",
               side_effect=RuntimeError("boom")):
        sink = _Sink()
        conn.register_cameras_async("http://x", "k", [{"device_index": 0}], sink).join(5)
    assert len(sink.calls) == 1, f"on_done must fire exactly once: {sink.calls!r}"
    assert sink.result is None
    assert sink.error
    assert "boom" not in sink.error


# --------------------------------------------------------------------------
# register_cameras_async -- same answer as the sync call, off the Tk thread.
# --------------------------------------------------------------------------

def test_register_cameras_async_runs_off_the_calling_thread():
    seen = {}

    def fake_post(url, **kw):
        seen["thread"] = _t.current_thread()
        resp = MagicMock(status_code=201)
        resp.json.return_value = [{"node_id": "webcam_aaa"}]
        return resp

    sink = _Sink()
    with patch("webcam_client.gui.wizard.connection.httpx.post", side_effect=fake_post):
        conn.register_cameras_async("http://x", "k", [{"device_index": 0}], sink)
        assert sink.done.wait(5), "on_done was never called"
    assert seen["thread"] is not _t.current_thread(), \
        "the registration POST must not run on the caller thread"
    assert sink.threads[0] is not _t.current_thread()


def test_register_cameras_async_hands_back_what_the_sync_version_returns():
    selected = [{"device_index": 0, "name": "Cam"}]
    resp = MagicMock(status_code=201)
    resp.json.return_value = [{"node_id": "webcam_aaa"}]
    with patch("webcam_client.gui.wizard.connection.httpx.post", return_value=resp):
        sync_cams, sync_err = conn.register_cameras("http://x", "k", selected)
    sink = _Sink()
    with patch("webcam_client.gui.wizard.connection.httpx.post", return_value=resp):
        conn.register_cameras_async("http://x", "k", selected, sink).join(5)
    assert (sink.result, sink.error) == (sync_cams, sync_err)
    assert sink.error is None and sink.result[0]["node_id"] == "webcam_aaa"


def test_register_cameras_async_hands_back_the_sync_failure_message():
    selected = [{"device_index": 0}]
    with patch("webcam_client.gui.wizard.connection.httpx.post",
               side_effect=httpx.ConnectError("refused")):
        sync_cams, sync_err = conn.register_cameras("http://x", "k", selected)
    sink = _Sink()
    with patch("webcam_client.gui.wizard.connection.httpx.post",
               side_effect=httpx.ConnectError("refused")):
        conn.register_cameras_async("http://x", "k", selected, sink).join(5)
    assert (sink.result, sink.error) == (sync_cams, sync_err)
    assert sink.error == strings.WIZ_CANNOT_REACH_SERVER


# --------------------------------------------------------------------------
# BUG 1 -- register_cameras could raise, despite its docstring promising it
# never does. `r.get("node_id")` sat OUTSIDE the try, which guarded only
# resp.json(). A 201 carrying a JSON OBJECT instead of a list iterates to
# string keys, so `r` is a truthy str and `r.get` raises AttributeError --
# straight out into the Tk callback, where a console=False exe swallows it to
# a stderr nobody reads and the button just appears dead. That is the exact
# failure the (None, message) contract exists to prevent.
# --------------------------------------------------------------------------

def test_a_201_carrying_a_json_object_is_reported_not_raised():
    resp = MagicMock(status_code=201)
    resp.json.return_value = {"node_id": "webcam_aaa"}  # object, not a list
    with patch("webcam_client.gui.wizard.connection.httpx.post", return_value=resp):
        cams, err = conn.register_cameras("http://x", "k", [{"device_index": 0}])
    assert cams is None
    assert err == strings.WIZ_BAD_RESPONSE


def test_a_201_whose_entries_are_not_objects_is_reported_not_raised():
    """Same shape of fault one level down: a list of strings (or numbers, or
    nulls) also reaches `r.get` on a non-dict."""
    for body in (["webcam_aaa"], [1], [None], [["webcam_aaa"]]):
        resp = MagicMock(status_code=201)
        resp.json.return_value = body
        with patch("webcam_client.gui.wizard.connection.httpx.post", return_value=resp):
            cams, err = conn.register_cameras("http://x", "k", [{"device_index": 0}])
        assert cams is None, body
        assert err == strings.WIZ_BAD_RESPONSE, body


# --------------------------------------------------------------------------
# BUG 2 -- silent under-registration. The zip loop never checked that the
# server returned as many entries as were posted. Fewer entries -> next(reg,
# None) yields None -> the trailing cameras keep no node_id -> and err is still
# None, so the wizard SAVES a config containing cameras that were never
# registered. No warning to the guard, nothing in the log, and the missing
# cameras only surface as tiles that never appear on the dashboard.
# --------------------------------------------------------------------------

def test_fewer_results_than_cameras_posted_is_reported_not_silently_saved():
    selected = [{"device_index": 0, "name": "A"}, {"device_index": 1, "name": "B"}]
    resp = MagicMock(status_code=201)
    resp.json.return_value = [{"node_id": "webcam_aaa"}]  # one short
    with patch("webcam_client.gui.wizard.connection.httpx.post", return_value=resp):
        cams, err = conn.register_cameras("http://x", "k", selected)
    assert err == strings.WIZ_BAD_RESPONSE, \
        "under-registration must be reported, not saved"
    assert cams is None, \
        "returning cameras alongside err=None is what let an unregistered camera reach the config"


def test_more_results_than_cameras_posted_is_reported_too():
    """The mismatch is the signal, in either direction: a body that does not
    correspond one-to-one with what was posted cannot be zipped by position,
    and guessing which extra entry belongs to which camera is how the wrong
    node_id gets written."""
    resp = MagicMock(status_code=201)
    resp.json.return_value = [{"node_id": "webcam_aaa"}, {"node_id": "webcam_bbb"}]
    with patch("webcam_client.gui.wizard.connection.httpx.post", return_value=resp):
        cams, err = conn.register_cameras("http://x", "k", [{"device_index": 0}])
    assert cams is None
    assert err == strings.WIZ_BAD_RESPONSE


def test_an_entry_with_no_node_id_is_reported_not_silently_saved():
    """The count can match while the payload is still useless: `r.get("node_id")`
    on a dict with no node_id returns None, the camera is saved without one,
    and err is None. Same silent under-registration, one field lower."""
    for body in ([{}], [{"node_id": None}], [{"node_id": ""}], [{"id": "webcam_aaa"}]):
        resp = MagicMock(status_code=201)
        resp.json.return_value = body
        with patch("webcam_client.gui.wizard.connection.httpx.post", return_value=resp):
            cams, err = conn.register_cameras("http://x", "k", [{"device_index": 0}])
        assert cams is None, body
        assert err == strings.WIZ_BAD_RESPONSE, body


def test_the_matching_case_still_attaches_every_node_id():
    """The guard rails above must not have made the happy path stricter than
    the server: existing cameras are NOT posted, so the count compared is the
    count of NEW cameras, not of `selected`."""
    selected = [
        {"device_index": 0, "node_id": "webcam_a", "name": "A"},  # existing, not posted
        {"device_index": 1, "name": "B"},                          # new
        {"device_index": 2, "name": "C"},                          # new
    ]
    resp = MagicMock(status_code=201)
    resp.json.return_value = [{"node_id": "webcam_b"}, {"node_id": "webcam_c"}]
    with patch("webcam_client.gui.wizard.connection.httpx.post", return_value=resp) as post:
        cams, err = conn.register_cameras("http://x", "k", selected)
    assert post.call_args.kwargs["json"]["cameras"] == [
        {"device_index": 1, "name": "B"}, {"device_index": 2, "name": "C"}]
    assert err is None
    assert [c["node_id"] for c in cams] == ["webcam_a", "webcam_b", "webcam_c"]


# --------------------------------------------------------------------------
# The copy rule, enforced on this module rather than on strings.py.
# --------------------------------------------------------------------------

def _every_message_this_module_can_return():
    """Drive every failure branch of both sync entry points and collect what
    the guard would be shown."""
    messages = []
    for exc in (httpx.ConnectError("All connection attempts failed"),
                httpx.UnsupportedProtocol("Request URL is missing an 'http://'"),
                httpx.InvalidURL("no host")):
        with patch("webcam_client.gui.wizard.connection.httpx.get", side_effect=exc):
            messages.append(conn.probe_connection("http://x", "k")[1])
        with patch("webcam_client.gui.wizard.connection.httpx.post", side_effect=exc):
            messages.append(conn.register_cameras("http://x", "k", [{"device_index": 0}])[1])
    for code in (401, 403, 404, 500, 502):
        with patch("webcam_client.gui.wizard.connection.httpx.get",
                   return_value=_status(code)):
            messages.append(conn.probe_connection("http://x", "k")[1])
        with patch("webcam_client.gui.wizard.connection.httpx.post",
                   return_value=_status(code)):
            messages.append(conn.register_cameras("http://x", "k", [{"device_index": 0}])[1])
    bad = MagicMock(status_code=200)
    bad.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
    with patch("webcam_client.gui.wizard.connection.httpx.get", return_value=bad):
        messages.append(conn.probe_connection("http://x", "k")[1])
    bad201 = MagicMock(status_code=201)
    bad201.json.return_value = {"node_id": "webcam_aaa"}
    with patch("webcam_client.gui.wizard.connection.httpx.post", return_value=bad201):
        messages.append(conn.register_cameras("http://x", "k", [{"device_index": 0}])[1])
    return [m for m in messages if m]


def test_no_message_this_module_returns_carries_a_status_code_or_an_exception():
    messages = _every_message_this_module_can_return()
    assert len(messages) >= 15, "the sweep stopped covering branches"
    banned = ("Error", "error", "Exception", "Traceback", "None", "null",
              "HTTP", "http", "API", "timeout", "socket")
    for msg in messages:
        assert not re.search(_STATUS_CODE_RE, msg), \
            f"a status code reached the guard: {msg!r}"
        for word in banned:
            assert word not in msg, f"developer text {word!r} reached the guard: {msg!r}"


def test_every_message_this_module_returns_is_a_strings_constant():
    """Never built by concatenation, never inlined here. A message assembled in
    this module is invisible to test_strings.py's scan, which is how the setup
    window shipped a raw status code long after every other surface was clean."""
    known = {v for n, v in vars(strings).items()
             if n.isupper() and isinstance(v, str)}
    for msg in _every_message_this_module_can_return():
        assert msg in known, f"not a strings.* constant: {msg!r}"


def test_this_module_inlines_no_operator_copy():
    """The source-level half of the same rule: no 繁中 string literal outside a
    docstring. Concatenation and f-strings both start with a literal, so
    banning the literal bans the assembly. Docstrings and comments are exempt --
    they are for the technician and naming a label there is how these
    docstrings stay readable."""
    tree = ast.parse(Path(conn.__file__).read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and body:
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            assert not re.search(_CJK_RE, node.value), (
                f"operator copy inlined at line {node.lineno}: {node.value!r} -- "
                "every guard-visible message must be a strings.* constant")
