# sdprs/webcam_client/tests/test_push_engine.py
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.push_engine import PushEngine


def test_push_engine_init():
    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480],
              "jpeg_quality": 40, "target_fps": 8, "motion_threshold": 25}
    engine = PushEngine(config, "https://example.com", "sk-test")
    assert engine._node_id == "webcam_01"
    assert engine._streaming is False


def test_set_streaming_flag():
    config = {"node_id": "webcam_01", "device_index": 0}
    engine = PushEngine(config, "https://example.com", "sk-test")
    with patch.object(engine, "_start_encoder"):
        engine.set_streaming(True)
        assert engine._streaming is True
    with patch.object(engine, "_stop_encoder"):
        engine.set_streaming(False)
        assert engine._streaming is False


def test_push_snapshot_uses_webcam_endpoint_and_raises(monkeypatch):
    # C1 client-side guard: normal-mode frames go to /api/webcam/.../snapshot
    # (never /api/edge), and a 4xx must surface via raise_for_status(), not be
    # swallowed. This is the regression that made the whole feature fail silently.
    import numpy as np
    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    engine = PushEngine(config, "https://example.com", "sk-test")
    mock_resp = MagicMock()
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    engine._client = mock_client
    engine._push_snapshot(np.zeros((480, 640, 3), dtype=np.uint8))
    posted_url = mock_client.post.call_args[0][0]
    assert "/api/webcam/webcam_01/snapshot" in posted_url
    assert "/api/edge/" not in posted_url
    mock_resp.raise_for_status.assert_called_once()


def test_push_snapshot_swallows_http_error():
    # C1 swallow guard: raise_for_status() is CALLED (previous test) AND, when it
    # RAISES (e.g. 401/500), _push_snapshot must swallow it — log a WARNING and
    # return — so a bad status can never propagate out of the push loop.
    import numpy as np
    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    engine = PushEngine(config, "https://example.com", "sk-test")
    request = httpx.Request("POST", "https://example.com/api/webcam/webcam_01/snapshot")
    response = httpx.Response(500, request=request)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=request, response=response
    )
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    engine._client = mock_client
    # Must NOT propagate — no exception escapes.
    engine._push_snapshot(np.zeros((480, 640, 3), dtype=np.uint8))
    mock_resp.raise_for_status.assert_called_once()


def test_set_paused_flag():
    # set_paused toggles the internal Event; default is un-paused.
    config = {"node_id": "webcam_01", "device_index": 0}
    engine = PushEngine(config, "https://example.com", "sk-test")
    assert engine._paused.is_set() is False
    engine.set_paused(True)
    assert engine._paused.is_set() is True
    engine.set_paused(False)
    assert engine._paused.is_set() is False


def test_paused_run_loop_uploads_nothing():
    # The tray "暫停推送" pause must be a REAL no-op-upload: with set_paused(True)
    # the run loop still reads frames (keeps motion state fresh) but calls neither
    # _push_snapshot nor _upload_segments. Regression for the dead pause Event that
    # let snapshots keep uploading while the operator thought pushing was stopped.
    import numpy as np
    config = {"node_id": "webcam_01", "device_index": 0,
              "resolution": [640, 480], "target_fps": 8}
    engine = PushEngine(config, "https://example.com", "sk-test")
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    def read_once():
        # One frame, then stop so run() returns after a single iteration.
        engine._stop_event.set()
        return True, frame

    fake_cap = MagicMock()
    fake_cap.read.side_effect = lambda: read_once()

    engine.set_paused(True)
    with patch("webcam_client.push_engine.open_camera", return_value=fake_cap), \
         patch.object(engine, "_push_snapshot") as mock_push, \
         patch.object(engine, "_upload_segments") as mock_upload:
        engine.run()

    assert fake_cap.read.called          # still reads frames while paused
    assert not mock_push.called          # ...but uploads NOTHING
    assert not mock_upload.called


def test_unpaused_run_loop_pushes_snapshot():
    # Same harness, NOT paused: proves the "not called" above is caused by the
    # pause, not by the test rig — an identical single-frame iteration DOES push.
    import numpy as np
    config = {"node_id": "webcam_01", "device_index": 0,
              "resolution": [640, 480], "target_fps": 8}
    engine = PushEngine(config, "https://example.com", "sk-test")
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    def read_once():
        engine._stop_event.set()
        return True, frame

    fake_cap = MagicMock()
    fake_cap.read.side_effect = lambda: read_once()

    with patch("webcam_client.push_engine.open_camera", return_value=fake_cap), \
         patch.object(engine, "_push_snapshot") as mock_push:
        engine.run()

    assert mock_push.called               # first frame (motion=1.0) pushes


def test_streaming_path_resizes_before_write():
    # Regression for the un-resized streaming write: a camera that ignores the
    # requested resolution delivers mis-sized frames. The streaming path MUST
    # resize to self._resolution before handing bytes to the encoder, otherwise
    # ffmpeg (started with -s WxH) reads misaligned frames -> garbled HLS.
    import numpy as np
    config = {"node_id": "webcam_01", "device_index": 0,
              "resolution": [640, 480], "target_fps": 8}
    engine = PushEngine(config, "https://example.com", "sk-test")

    mock_encoder = MagicMock()
    mock_encoder.write_frame.return_value = True

    # Frame delivered at the WRONG resolution (1280x720, camera ignored request).
    big_frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    def read_once():
        # Deliver exactly one frame, then stop the loop so run() returns.
        engine._stop_event.set()
        return True, big_frame

    fake_cap = MagicMock()
    fake_cap.read.side_effect = lambda: read_once()

    with patch("webcam_client.push_engine.open_camera", return_value=fake_cap):
        engine._streaming = True
        engine._encoder = mock_encoder
        engine.run()

    assert mock_encoder.write_frame.called
    written = mock_encoder.write_frame.call_args[0][0]
    expected_len = len(np.zeros((480, 640, 3), dtype=np.uint8).tobytes())
    assert len(written) == expected_len          # resized to self._resolution
    assert len(written) != len(big_frame.tobytes())  # NOT the raw mis-sized bytes
