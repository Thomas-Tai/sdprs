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

    with patch("webcam_client.push_engine.open_camera", return_value=fake_cap), \
         patch.object(engine, "_push_snapshot"), \
         patch.object(engine, "_upload_segments"):
        engine._streaming = True
        engine._encoder = mock_encoder
        engine.run()

    assert mock_encoder.write_frame.called
    written = mock_encoder.write_frame.call_args[0][0]
    expected_len = len(np.zeros((480, 640, 3), dtype=np.uint8).tobytes())
    assert len(written) == expected_len          # resized to self._resolution
    assert len(written) != len(big_frame.tobytes())  # NOT the raw mis-sized bytes


def test_streaming_still_pushes_snapshot():
    # ROBUSTNESS (grey-tile fix): entering live-view/streaming must NOT stop the
    # 1Hz snapshot the dashboard tile lives on. The old run loop was
    # `if streaming: feed encoder  else: push snapshot`, so starting live view
    # silenced snapshots and the tile went stale -> grey. A streaming iteration
    # must now do BOTH: feed the encoder AND push a snapshot.
    import numpy as np
    config = {"node_id": "webcam_01", "device_index": 0,
              "resolution": [640, 480], "target_fps": 8}
    engine = PushEngine(config, "https://example.com", "sk-test")

    mock_encoder = MagicMock()
    mock_encoder.write_frame.return_value = True
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    def read_once():
        engine._stop_event.set()
        return True, frame

    fake_cap = MagicMock()
    fake_cap.read.side_effect = lambda: read_once()

    with patch("webcam_client.push_engine.open_camera", return_value=fake_cap), \
         patch.object(engine, "_push_snapshot") as mock_push, \
         patch.object(engine, "_upload_segments"):
        engine._streaming = True
        engine._encoder = mock_encoder
        engine.run()

    assert mock_push.called, "snapshot must still be pushed while streaming"
    assert mock_encoder.write_frame.called, "encoder must still be fed while streaming"


def test_push_engine_reports_camera_down_when_camera_wont_open():
    """Review finding 2: nothing constructed a PushEngine and verified on_fault
    fires for CAMERA_DOWN. Mirrors the ControlChannel on_fault tests -- the
    classification table's first row (`cap is None` -> CAMERA_DOWN)."""
    from webcam_client.status import Fault

    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    seen = []
    engine = PushEngine(config, "https://example.com", "sk-test", on_fault=seen.append)

    with patch("webcam_client.push_engine.open_camera", return_value=None):
        engine.run()

    assert seen == [Fault.CAMERA_DOWN]


def test_push_engine_reports_none_on_successful_snapshot():
    """Mirrors test_control_channel_reports_none_on_clean_poll: a clean push
    must report Fault.NONE (line 191) so a prior fault clears."""
    import numpy as np
    from webcam_client.status import Fault

    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    seen = []
    engine = PushEngine(config, "https://example.com", "sk-test", on_fault=seen.append)
    mock_resp = MagicMock()
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    engine._client = mock_client

    engine._push_snapshot(np.zeros((480, 640, 3), dtype=np.uint8))

    assert seen == [Fault.NONE]


def test_push_engine_dedups_repeated_snapshot_faults():
    """Mirrors test_control_channel_dedups_repeated_faults: a failing uplink
    runs at ~1Hz; repeated identical faults must report to the hub once, not
    every cycle."""
    import numpy as np
    from webcam_client.status import Fault

    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    seen = []
    engine = PushEngine(config, "https://example.com", "sk-test", on_fault=seen.append)

    request = httpx.Request("POST", "https://example.com/api/webcam/webcam_01/snapshot")
    response = httpx.Response(500, request=request)
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=request, response=response
    )
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    engine._client = mock_client

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    for _ in range(5):
        engine._push_snapshot(frame)

    assert seen == [Fault.NO_SERVER], "repeat identical faults must report once"


def test_push_engine_reports_camera_down_after_sustained_read_failures(monkeypatch):
    """I-1 scenario A: the guard pulls the USB cable while the app is running.

    cap.read() then returns (False, None) forever. The old loop slept 100ms and
    `continue`d without ever reaching _push_snapshot, so the engine's LAST report
    (Fault.NONE) stood for the life of the process: tray green, status window
    saying the camera is fine, camera dead for hours. Sustained read failure must
    reach the hub as CAMERA_DOWN.
    """
    from webcam_client import push_engine as pe
    from webcam_client.status import Fault

    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    seen = []
    engine = PushEngine(config, "https://example.com", "sk-test", on_fault=seen.append)

    calls = {"n": 0}

    def failing_read():
        calls["n"] += 1
        # Run a few iterations past the threshold to prove the dedup holds.
        if calls["n"] > pe.BAD_READ_LIMIT + 5:
            engine._stop_event.set()
        return False, None

    fake_cap = MagicMock()
    fake_cap.read.side_effect = lambda: failing_read()

    monkeypatch.setattr(pe.time, "sleep", lambda *_a, **_k: None)
    with patch("webcam_client.push_engine.open_camera", return_value=fake_cap):
        engine.run()

    assert seen == [Fault.CAMERA_DOWN], "a camera that stopped delivering frames must be reported once"
    fake_cap.release.assert_called_once()


def test_push_engine_clears_camera_down_on_first_good_read(monkeypatch):
    """Recovery half of I-1: the guard plugs the cable back in. The first good
    read after a reported CAMERA_DOWN must clear it, or the tray stays red on a
    camera that is working again."""
    import numpy as np
    from webcam_client import push_engine as pe
    from webcam_client.status import Fault

    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    seen = []
    engine = PushEngine(config, "https://example.com", "sk-test", on_fault=seen.append)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    calls = {"n": 0}

    def reads():
        calls["n"] += 1
        if calls["n"] <= pe.BAD_READ_LIMIT:
            return False, None
        engine._stop_event.set()
        return True, frame

    fake_cap = MagicMock()
    fake_cap.read.side_effect = lambda: reads()

    monkeypatch.setattr(pe.time, "sleep", lambda *_a, **_k: None)
    # _push_snapshot is patched out so the NONE below can only come from the
    # read-recovery path, not from a successful upload.
    with patch("webcam_client.push_engine.open_camera", return_value=fake_cap), \
         patch.object(engine, "_push_snapshot"):
        engine.run()

    assert seen == [Fault.CAMERA_DOWN, Fault.NONE]


def test_push_engine_ignores_a_brief_read_hiccup(monkeypatch):
    """The threshold exists so a USB re-enumeration or a single dropped frame
    does not flap the tray. Below the threshold: report NOTHING."""
    import numpy as np
    from webcam_client import push_engine as pe

    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    seen = []
    engine = PushEngine(config, "https://example.com", "sk-test", on_fault=seen.append)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    calls = {"n": 0}

    def reads():
        calls["n"] += 1
        if calls["n"] <= pe.BAD_READ_LIMIT - 1:
            return False, None
        engine._stop_event.set()
        return True, frame

    fake_cap = MagicMock()
    fake_cap.read.side_effect = lambda: reads()

    monkeypatch.setattr(pe.time, "sleep", lambda *_a, **_k: None)
    with patch("webcam_client.push_engine.open_camera", return_value=fake_cap), \
         patch.object(engine, "_push_snapshot"):
        engine.run()

    assert seen == [], "a sub-threshold read hiccup must not touch the hub"


def test_push_engine_reports_camera_down_when_run_loop_crashes(caplog):
    """I-1 scenario B: any exception in the loop body (cv2.error out of
    compute_motion / cv2.resize, an OpenCV teardown error) used to propagate out
    of run(). threading.excepthook writes to sys.stderr, which is None in the
    console=False onefile build -- so a crashed engine left NO evidence at all
    and NO hub report. It must log with a traceback and tell the operator that
    camera has no picture."""
    import logging
    import numpy as np
    from webcam_client.status import Fault

    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    seen = []
    engine = PushEngine(config, "https://example.com", "sk-test", on_fault=seen.append)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    def read_once():
        engine._stop_event.set()
        return True, frame

    fake_cap = MagicMock()
    fake_cap.read.side_effect = lambda: read_once()

    with caplog.at_level(logging.ERROR, logger="webcam_client.push_engine"), \
         patch("webcam_client.push_engine.open_camera", return_value=fake_cap), \
         patch("webcam_client.push_engine.compute_motion",
               side_effect=RuntimeError("cv2 exploded")):
        engine.run()          # must NOT propagate

    assert seen == [Fault.CAMERA_DOWN]
    assert any(r.exc_info for r in caplog.records), "the traceback must reach the log file"
    fake_cap.release.assert_called_once()


def test_bad_read_threshold_is_about_three_seconds():
    """The threshold is documented in wall-clock terms to the operator-facing
    reviewer; pin the two constants it is derived from so a later tweak to the
    sleep cannot silently turn '3 seconds' into 30."""
    from webcam_client import push_engine as pe

    delay = pe.BAD_READ_LIMIT * pe.BAD_READ_SLEEP
    assert 2.0 <= delay <= 5.0, f"read-failure grace period is {delay}s"


def test_classify_maps_401_and_403_to_bad_key():
    """These are the two the guard can act on: 'call the administrator'."""
    from webcam_client.push_engine import _classify
    from webcam_client.status import Fault

    for code in (401, 403):
        exc = Exception("rejected")
        exc.response = type("R", (), {"status_code": code})()
        assert _classify(exc) is Fault.BAD_KEY, f"{code} must be BAD_KEY"


def test_classify_maps_transport_failure_to_no_server():
    from webcam_client.push_engine import _classify
    from webcam_client.status import Fault
    import httpx

    assert _classify(httpx.ConnectError("refused")) is Fault.NO_SERVER


def test_classify_maps_5xx_to_no_server():
    """A 500 is not a key problem -- telling the guard to call the admin about
    their password would send them down the wrong path."""
    from webcam_client.push_engine import _classify
    from webcam_client.status import Fault

    exc = Exception("boom")
    exc.response = type("R", (), {"status_code": 500})()
    assert _classify(exc) is Fault.NO_SERVER
