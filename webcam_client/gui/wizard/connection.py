# sdprs/webcam_client/gui/wizard/connection.py
"""Server URL handling, client identity, connection test, and camera registration.

Moved out of ``gui/setup_wizard.py``: everything here is pure Python plus HTTP
— no Tk — so it can be tested without a display.

Two rules hold for every function below.

**Nothing here ever raises at its caller.** Each returns ``(value, None)`` or
``(None, message)``. The callers run inside Tk callbacks, and in a windowed
(``console=False``) PyInstaller exe an unhandled exception is swallowed to a
stderr nobody reads — so a raise does not surface as an error, it surfaces as a
button that does nothing.

**Nothing here builds operator copy.** Every message is a ``strings.*``
constant handed back untouched: no concatenation, no f-string, no inline 繁中.
Status codes and exception reprs go to ``logger`` for the technician —
``test_strings.py`` scans ``strings.py`` for leaks, and a message assembled
here would be invisible to that scan, which is exactly how the setup window
kept shipping a raw status code long after every other surface was clean.

The two ``*_async`` wrappers exist for §7.2 U3: the window used to call
``register_cameras`` straight from the button handler after a single
``root.update()``, so the Tk thread blocked for the whole connect timeout and
Windows painted "not responding" over the window. A guard reads that as a
crash. Both wrappers put the network on a daemon thread and call ``on_done``
FROM that thread; marshalling back onto Tk with ``root.after`` is the window's
job, not this module's.
"""
import logging
import threading
from typing import Optional

import httpx

from ... import strings

logger = logging.getLogger("webcam_client.gui.wizard")

# One timeout for every call this module makes. A guard watching a spinner has
# no way to tell "still trying" from "hung", so the wait must be short enough
# that the answer always arrives while they are still looking at the window.
_TIMEOUT = 10.0


def normalize_server_url(raw: str) -> str:
    """Make a user-typed server URL usable before it is joined with a path.

    - Default to ``http://`` when the scheme is omitted. A schemeless URL made
      ``httpx`` raise ``UnsupportedProtocol``, which is NOT an ``httpx.ConnectError``
      and so escaped ``on_start`` as an unhandled Tk-callback exception — in a
      windowed (console=False) exe that is swallowed and the button "does nothing".
    - Strip a trailing slash so ``f"{url}/api/..."`` never becomes ``//api/...``,
      which the server routes as 404 (``push_engine`` rstrips; the wizard did not).
    """
    url = (raw or "").strip()
    if url and "://" not in url:
        url = "http://" + url
    return url.rstrip("/")


def probe_connection(server_url: str, api_key: str):
    """Ask the server whether this 連線位址 + 連線密碼 pair works. Registers nothing.

    Backs the 測試連線 button (spec §7.2 U4). The guard can prove the two fields
    are right BEFORE choosing a single camera; the old window only found out at
    開始, after all the work was done, and then threw it away behind a modal.

    ``GET /api/webcam/ping`` is guaranteed side-effect-free — no table write, no
    queue, no buffer. Every other key-authenticated route was disqualified for a
    side effect (registering cameras, writing the snapshot buffer, or dequeuing
    the command the client itself is waiting for), which is why that route had
    to exist at all. Do NOT reimplement this on top of the registration
    endpoint: that would mint a node_id, and a duplicate dashboard tile, every
    time the guard pressed a button labelled "test".

    Returns ``(True, None)`` or ``(False, message)``. Never raises.

    Deliberately named ``probe_``, not ``test_``: pytest collects any
    module-level callable named ``test_*`` in a test module, so a test file that
    imported a ``test_connection`` helper by name would collect it as a test
    case and error on a missing ``server_url`` fixture.
    """
    try:
        resp = httpx.get(
            f"{server_url}/api/webcam/ping",
            headers={"X-API-Key": api_key},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        logger.warning("connection test transport failure: %s", e, exc_info=True)
        return False, strings.WIZ_CANNOT_REACH_SERVER
    except Exception as e:
        # httpx.InvalidURL does NOT inherit from httpx.HTTPError — it derives
        # straight from Exception — so `except httpx.HTTPError` alone lets it
        # escape, and an escaped exception here is an invisible dead button.
        # A guard who pastes a half-finished address reaches this path, and
        # "check the address, then call the administrator" is the right
        # instruction for every way it can happen.
        logger.warning("connection test could not issue the request: %s", e, exc_info=True)
        return False, strings.WIZ_CANNOT_REACH_SERVER
    if resp.status_code == 401:
        # Both "no key" and "unknown key" answer 401; the guard's action is the
        # same either way, so they share WIZ_KEY_REJECTED with the save path.
        logger.warning("connection test rejected the key (401)")
        return False, strings.WIZ_KEY_REJECTED
    if resp.status_code != 200:
        logger.warning("connection test unexpected status %s", resp.status_code)
        return False, strings.WIZ_SERVER_REFUSED
    # A 200 is not proof the SDPRS server answered. A captive portal or a
    # misrouted proxy returns 200 carrying HTML, and this result is what
    # unlocks section 2 — claiming 連線成功 there hands the guard a verified
    # state they have not earned, and the real failure resurfaces at 開始 with
    # all their work already done.
    try:
        body = resp.json()
    except Exception as e:
        logger.warning("connection test body was not JSON: %s", e, exc_info=True)
        return False, strings.WIZ_BAD_RESPONSE
    if not isinstance(body, dict) or not body.get("ok"):
        logger.warning("connection test body was not the expected ping payload")
        return False, strings.WIZ_BAD_RESPONSE
    return True, None


def register_cameras(server_url: str, api_key: str, selected: list):
    """POST only cameras that lack a node_id; keep already-registered ones as-is.

    Editing settings must be idempotent: re-registering every camera on each
    edit minted a fresh node_id per camera, so the dashboard grew a duplicate
    webcam tile every time settings were opened. Cameras that already carry a
    node_id are left untouched; only new ones are sent.

    Returns ``(cameras, None)`` on success or ``(None, message)`` on ANY failure.
    Never raises: the caller runs inside a Tk callback where an unhandled
    exception is swallowed in a windowed exe and the Start button appears dead.
    """
    new_cams = [dict(c) for c in selected if not c.get("node_id")]
    if not new_cams:
        return [dict(c) for c in selected], None
    try:
        resp = httpx.post(
            f"{server_url}/api/webcam/cameras",
            json={"cameras": new_cams},
            headers={"X-API-Key": api_key},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        # The exception goes to the LOG, never to the guard: its text is English
        # ("All connection attempts failed", "[Errno 11001] getaddrinfo failed")
        # and it blames the server for what is most often a mistyped address.
        logger.warning("camera registration transport failure: %s", e, exc_info=True)
        return None, strings.WIZ_CANNOT_REACH_SERVER
    except Exception as e:
        # Same hole as bug 1 above, one storey up: "never raises" was not true.
        # httpx.InvalidURL derives from Exception, NOT from httpx.HTTPError, so
        # an address like `http://` (which normalize_server_url rstrips to
        # `http:`) escaped this handler entirely — and an escape here is a dead
        # 開始 button in a windowed exe, not an error message. Mirrors
        # probe_connection; see the note there for why this maps to
        # WIZ_CANNOT_REACH_SERVER.
        logger.warning("camera registration could not issue the request: %s", e,
                       exc_info=True)
        return None, strings.WIZ_CANNOT_REACH_SERVER
    if resp.status_code == 401:
        logger.warning("camera registration rejected the key (401)")
        return None, strings.WIZ_KEY_REJECTED
    if resp.status_code != 201:
        # Same rule as everywhere else: the technician gets the code, the guard
        # gets the action. This line used to hand the guard "伺服器回應：500".
        logger.warning("camera registration unexpected status %s", resp.status_code)
        return None, strings.WIZ_SERVER_REFUSED
    try:
        registered = resp.json()
    except Exception as e:
        logger.warning("camera registration body was not JSON: %s", e, exc_info=True)
        return None, strings.WIZ_BAD_RESPONSE
    # Validate the WHOLE body before touching it. Two bugs lived in the zip
    # loop that used to follow, and both were invisible from the outside:
    #
    # 1. `r.get("node_id")` sat outside the try above, which guards only
    #    resp.json(). A 201 carrying a JSON OBJECT instead of a list iterates
    #    to string keys, so `r` was a truthy str and `r.get` raised
    #    AttributeError straight out into the Tk callback — the one thing this
    #    function's (None, message) contract exists to prevent.
    # 2. Nothing checked that the server returned as many entries as were
    #    posted. One entry short and `next(reg, None)` yielded None, the
    #    trailing cameras kept no node_id, and err was STILL None — so the
    #    wizard saved a config containing cameras that were never registered.
    #    Nothing told the guard; the cameras just never appeared on the
    #    dashboard.
    #
    # The results are zipped to the posted cameras BY POSITION, so a body that
    # does not correspond one-to-one is not merely incomplete, it is
    # unzippable: guessing which entry belongs to which camera is how the wrong
    # node_id gets written. Any mismatch — either direction, a non-dict entry,
    # or an entry with no usable node_id — is a 2xx whose body could not be
    # understood, which is what WIZ_BAD_RESPONSE says and who it escalates to.
    if (not isinstance(registered, list)
            or len(registered) != len(new_cams)
            or not all(isinstance(r, dict) and r.get("node_id") for r in registered)):
        logger.warning(
            "camera registration body did not match the %d camera(s) posted: %r",
            len(new_cams), registered)
        return None, strings.WIZ_BAD_RESPONSE
    reg = iter(registered)
    result = []
    for c in selected:
        c = dict(c)
        if not c.get("node_id"):
            c["node_id"] = next(reg)["node_id"]
        result.append(c)
    return result, None


def _run_off_the_tk_thread(work, on_done, failed_result):
    """Run ``work()`` on a daemon thread and hand its ``(result, error)`` to
    ``on_done`` — from the worker thread.

    ``on_done`` is called EXACTLY ONCE on every path, including when ``work``
    raises. A worker that dies silently is worse than a slow one: the window is
    left on 正在測試連線，請稍候… forever with its buttons disabled, which is
    indistinguishable from the freeze this whole change exists to remove.

    The call sits OUTSIDE the try on purpose. Inside it, an exception raised by
    ``on_done`` itself (a Tk callback bug, say) would be caught here and
    ``on_done`` invoked a second time with a failure it never actually had.

    ``daemon=True``: the guard closing the window must not be blocked by a
    thread still waiting out a 10 s connect timeout — the process has to be
    able to exit while it is in flight.
    """
    def runner():
        try:
            result, error = work()
        except Exception as e:
            # Last resort. Everything below already returns rather than raises,
            # so reaching here means something upstream of the response did —
            # a malformed 連線位址, a non-encodable character pasted into the
            # 連線密碼 box. Those are address/password faults whose fix, and
            # whose escalation, WIZ_CANNOT_REACH_SERVER already states.
            logger.exception("wizard network worker failed: %s", e)
            result, error = failed_result, strings.WIZ_CANNOT_REACH_SERVER
        on_done(result, error)

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t


def test_connection_async(server_url: str, api_key: str, on_done):
    """Run :func:`probe_connection` off the Tk thread; ``on_done(ok, error)``.

    ``on_done`` arrives on the WORKER thread — the window marshals it back with
    ``root.after``. Returns the ``Thread`` so tests can join deterministically;
    the window ignores it.
    """
    return _run_off_the_tk_thread(
        lambda: probe_connection(server_url, api_key), on_done, failed_result=False)


def register_cameras_async(server_url: str, api_key: str, selected: list, on_done):
    """Run :func:`register_cameras` off the Tk thread; ``on_done(cameras, error)``.

    ``on_done`` gets exactly what the synchronous call returns, on the WORKER
    thread. Returns the ``Thread``; see :func:`test_connection_async`.
    """
    return _run_off_the_tk_thread(
        lambda: register_cameras(server_url, api_key, selected), on_done,
        failed_result=None)


def _client_identity_changed(existing_config: Optional[dict], server_url: str,
                             api_key: str) -> bool:
    """True when the entered server_url or api_key differs from the config the
    cameras were last registered under.

    A camera's node_id is owned by the webcam CLIENT whose key registered it.
    If the operator changes the API Key (or Server URL) in the settings window,
    the key now authenticates as a different client, and reusing the old
    node_ids makes the server reject every snapshot/command with
    403 "Camera not owned by this client". When the identity changes the old
    node_ids must be dropped so the cameras re-register under the new client.
    """
    old = existing_config or {}
    return (api_key != old.get("api_key", "")
            or server_url != normalize_server_url(old.get("server_url", "")))


def _build_cameras_for_registration(selected_rows: list, identity_changed: bool) -> list:
    """Build the camera dicts to hand register_cameras. Preserve a row's node_id
    ONLY when the client identity is unchanged (idempotent — no duplicate tile);
    drop it when the identity changed so the camera re-registers under, and is
    owned by, the new client. A row that never had a node_id never gains one."""
    cameras = []
    for r in selected_rows:
        cam = {"device_index": r["device_index"],
               "name": r["name"],
               "resolution": [640, 480], "jpeg_quality": 40, "target_fps": 8}
        if r.get("node_id") and not identity_changed:
            cam["node_id"] = r["node_id"]
        cameras.append(cam)
    return cameras
