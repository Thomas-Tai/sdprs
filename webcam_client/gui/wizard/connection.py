# sdprs/webcam_client/gui/wizard/connection.py
"""Server URL handling, client identity, and camera registration.

Moved verbatim out of ``gui/setup_wizard.py``: everything here is pure Python
plus one HTTP call — no Tk, no threads — so it can be tested without a display.
"""
import logging
from typing import Optional

import httpx

from ... import strings

logger = logging.getLogger("webcam_client.gui.wizard")


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
    reg = iter(registered)
    result = []
    for c in selected:
        c = dict(c)
        if not c.get("node_id"):
            r = next(reg, None)
            if r:
                c["node_id"] = r.get("node_id")
        result.append(c)
    return result, None


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
