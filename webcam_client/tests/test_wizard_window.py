# sdprs/webcam_client/tests/test_wizard_window.py
"""The setup window's two pure helpers.

NOTHING here may construct a real tk.Tk(): this suite has to pass on a headless
build machine, and `webcam_client/tests/` has no display guard. Importing
`window` is fine -- it imports tkinter but instantiates nothing at module level.
The window's rendering itself is covered by the bench pass; everything worth
asserting was deliberately pushed into `flow.py` and `connection.py`, which
need no display at all.

`_camera_rows_from_config` migrated here from test_setup_wizard.py when the
transitional façade was deleted.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.gui.wizard.window import _camera_rows_from_config, _strip_frame


def test_camera_rows_from_config_preserves_node_id_and_name():
    cfg = {"cameras": [
        {"device_index": 2, "name": "前門", "node_id": "webcam_a", "enabled": True},
        {"device_index": 5, "name": "後門", "node_id": "webcam_b", "enabled": False},
    ]}
    assert _camera_rows_from_config(cfg) == [
        {"device_index": 2, "name": "前門", "node_id": "webcam_a", "enabled": True},
        {"device_index": 5, "name": "後門", "node_id": "webcam_b", "enabled": False},
    ]


def test_camera_rows_from_config_defaults_a_missing_name_and_enabled_flag():
    rows = _camera_rows_from_config({"cameras": [{"device_index": 0}]})
    assert rows[0]["enabled"] is True
    assert "0" in rows[0]["name"], "a nameless camera still needs something to click"
    assert rows[0]["node_id"] is None


def test_camera_rows_from_an_empty_config_is_empty_not_an_error():
    assert _camera_rows_from_config({}) == []
    assert _camera_rows_from_config({"cameras": []}) == []


def test_strip_frame_removes_the_scanned_ndarray():
    """scan_cameras attaches the frame it grabbed so the thumbnail needs no
    second device open. That frame must not survive into the config: json
    cannot encode an ndarray. save_config now serialises before it truncates,
    so this can no longer destroy the guard's settings -- but relying on that as
    the ONLY defence means every save is one refactor away from failing, and
    the failure lands on the person who cannot fix it."""
    class _FakeNdarray:
        def __repr__(self):
            return "<ndarray>"

    camera = {"device_index": 0, "name": "前門", "node_id": "webcam_a",
              "frame": _FakeNdarray()}
    stripped = _strip_frame(camera)
    assert "frame" not in stripped
    # Everything else survives untouched -- stripping must not quietly drop the
    # node_id, which is what stops a re-save minting a duplicate dashboard tile.
    assert stripped == {"device_index": 0, "name": "前門", "node_id": "webcam_a"}


def test_strip_frame_is_a_copy_and_leaves_the_original_alone():
    camera = {"device_index": 0, "frame": object()}
    _strip_frame(camera)
    assert "frame" in camera, "the row still needs its frame for the thumbnail"
