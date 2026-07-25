# sdprs/webcam_client/tests/test_setup_wizard.py
"""Setup wizard connection handling.

The wizard's Start button ran the registration POST under a bare
`except httpx.ConnectError`. Real failures raise OTHER httpx errors
(UnsupportedProtocol for a schemeless URL, ConnectTimeout for an unreachable
host), which escaped as an unhandled Tk-callback exception -> swallowed to
stderr -> in a console=False exe the button "did nothing". These pin the fix:
normalize a schemeless URL, and turn EVERY failure into a (None, message) so
the GUI can always show feedback.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.gui.setup_wizard import normalize_server_url, register_cameras


def test_normalize_prepends_http_when_scheme_missing():
    # THE trigger: user pastes host:port with no scheme.
    assert normalize_server_url("localhost:8000") == "http://localhost:8000"
    assert normalize_server_url(" 192.168.1.50:8000/ ") == "http://192.168.1.50:8000"
    assert normalize_server_url("myapp.zeabur.app") == "http://myapp.zeabur.app"


def test_normalize_keeps_explicit_scheme_and_strips_trailing_slash():
    assert normalize_server_url("https://app.zeabur.app/") == "https://app.zeabur.app"
    assert normalize_server_url("http://localhost:8000") == "http://localhost:8000"


def test_register_cameras_returns_message_not_raises_on_schemeless_or_transport_error():
    # The exact bug: a non-ConnectError httpx error must NOT escape.
    for exc in (httpx.UnsupportedProtocol("no scheme"),
                httpx.ConnectTimeout("timed out"),
                httpx.ConnectError("refused")):
        with patch("webcam_client.gui.setup_wizard.httpx.post", side_effect=exc):
            cams, err = register_cameras("http://x", "k", [{"device_index": 0}])
        assert cams is None
        assert err and "無法連線" in err, (exc, err)


def test_register_cameras_maps_401_and_other_status():
    with patch("webcam_client.gui.setup_wizard.httpx.post",
               return_value=MagicMock(status_code=401)):
        cams, err = register_cameras("http://x", "k", [{"device_index": 0}])
    assert cams is None and "API Key" in err

    with patch("webcam_client.gui.setup_wizard.httpx.post",
               return_value=MagicMock(status_code=500)):
        cams, err = register_cameras("http://x", "k", [{"device_index": 0}])
    assert cams is None and "500" in err


def test_register_cameras_success_attaches_node_ids():
    resp = MagicMock(status_code=201)
    resp.json.return_value = [{"node_id": "webcam_aaa"}]
    with patch("webcam_client.gui.setup_wizard.httpx.post", return_value=resp):
        cams, err = register_cameras("http://x", "k", [{"device_index": 0, "name": "Cam"}])
    assert err is None
    assert cams[0]["node_id"] == "webcam_aaa"


def test_register_cameras_non_json_201_is_reported_not_raised():
    resp = MagicMock(status_code=201)
    resp.json.side_effect = ValueError("not json")
    with patch("webcam_client.gui.setup_wizard.httpx.post", return_value=resp):
        cams, err = register_cameras("http://x", "k", [{"device_index": 0}])
    assert cams is None and err


def test_register_skips_post_when_all_cameras_already_registered():
    # Editing settings must NOT re-register already-known cameras (that minted a
    # fresh node_id each edit -> duplicate dashboard tiles).
    selected = [{"device_index": 0, "node_id": "webcam_a", "name": "A"}]
    with patch("webcam_client.gui.setup_wizard.httpx.post") as post:
        cams, err = register_cameras("http://x", "k", selected)
    post.assert_not_called()
    assert err is None
    assert cams[0]["node_id"] == "webcam_a"


def test_register_posts_only_new_cameras_and_preserves_existing_ids():
    selected = [
        {"device_index": 0, "node_id": "webcam_a", "name": "A"},  # existing
        {"device_index": 1, "name": "B"},                          # new
    ]
    resp = MagicMock(status_code=201)
    resp.json.return_value = [{"node_id": "webcam_new"}]
    with patch("webcam_client.gui.setup_wizard.httpx.post", return_value=resp) as post:
        cams, err = register_cameras("http://x", "k", selected)
    body = post.call_args.kwargs["json"]
    assert body["cameras"] == [{"device_index": 1, "name": "B"}]  # only the new one
    assert err is None
    assert cams[0]["node_id"] == "webcam_a"    # preserved
    assert cams[1]["node_id"] == "webcam_new"  # assigned to the new one


def test_scan_cameras_async_runs_off_the_calling_thread(monkeypatch):
    import threading as _t
    from webcam_client.gui import setup_wizard as sw
    seen = {}

    def fake_scan(max_index=10):
        seen["thread"] = _t.current_thread()
        return [{"device_index": 0, "width": 640, "height": 480}]

    monkeypatch.setattr(sw, "scan_cameras", fake_scan)
    done = _t.Event()
    result = {}

    def on_done(cams):
        result["cams"] = cams
        done.set()

    sw._scan_cameras_async(on_done)
    assert done.wait(5), "on_done was never called"
    assert seen["thread"] is not _t.current_thread(), "scan must not run on the caller thread"
    assert result["cams"][0]["device_index"] == 0


def test_load_thumbnail_async_runs_off_the_calling_thread(monkeypatch):
    import threading as _t
    from webcam_client.gui import setup_wizard as sw
    seen = {}

    def fake_grab(device_index):
        seen["thread"] = _t.current_thread()
        seen["device_index"] = device_index
        return "FRAME"

    def fake_make_thumbnail(frame):
        assert frame == "FRAME"
        return "THUMB"

    monkeypatch.setattr(sw, "grab_preview_frame", fake_grab)
    monkeypatch.setattr(sw, "make_thumbnail", fake_make_thumbnail)
    done = _t.Event()
    result = {}

    def on_ready(thumb):
        result["thumb"] = thumb
        done.set()

    sw._load_thumbnail_async(3, on_ready)
    assert done.wait(5), "on_ready was never called"
    assert seen["thread"] is not _t.current_thread(), "thumbnail grab must not run on the caller thread"
    assert seen["device_index"] == 3
    assert result["thumb"] == "THUMB"


def test_camera_rows_from_config_preserves_node_id_and_name():
    from webcam_client.gui.setup_wizard import _camera_rows_from_config
    cfg = {"cameras": [
        {"device_index": 2, "name": "前門", "node_id": "webcam_a", "enabled": True},
        {"device_index": 5, "name": "後門", "node_id": "webcam_b", "enabled": False},
    ]}
    assert _camera_rows_from_config(cfg) == [
        {"device_index": 2, "name": "前門", "node_id": "webcam_a", "enabled": True},
        {"device_index": 5, "name": "後門", "node_id": "webcam_b", "enabled": False},
    ]
