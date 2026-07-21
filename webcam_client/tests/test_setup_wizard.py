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
