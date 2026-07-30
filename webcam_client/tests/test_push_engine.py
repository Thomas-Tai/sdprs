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


def test_a_camera_coming_back_does_not_claim_the_upload_works(monkeypatch):
    """The guard re-seats a USB cable while the SERVER is also down.

    PushEngine watches two independent things -- is there a picture, and do
    snapshots reach the server -- but reported both through ONE slot, so
    whichever spoke last erased the other. The sequence on a real site:

      1. frames fine, uploads failing   -> NO_SERVER reported. Correct.
      2. guard pulls the USB cable      -> sustained bad reads. The read path
         `continue`s before _push_snapshot, so the uplink verdict is frozen.
      3. guard plugs the cable back in  -> the recovery arm fired Fault.NONE,
         the hub dropped this source, health went RUNNING, and because recovery
         toasts are deliberately NOT debounced the guard was toasted 監控中 at
         once -- while NOTHING was uploading. About a second later the next
         failed push re-reported NO_SERVER, which ALSO reset the hub's
         _state_since, so the real fault had to sit through another full 30s
         debounce before it could be announced.

    Clearing the camera domain must never speak for the uplink domain.
    """
    import numpy as np
    from webcam_client import push_engine as pe
    from webcam_client.status import Fault

    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    seen = []
    engine = PushEngine(config, "https://example.com", "sk-test", on_fault=seen.append)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # The server is down for the whole test: every push raises.
    request = httpx.Request("POST", "https://example.com/api/webcam/webcam_01/snapshot")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error", request=request, response=httpx.Response(500, request=request)
    )
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp

    calls = {"n": 0}

    def reads():
        calls["n"] += 1
        if calls["n"] == 1:
            return True, frame                       # step 1: push, and it fails
        if calls["n"] <= 1 + pe.BAD_READ_LIMIT:
            return False, None                       # step 2: cable pulled
        engine._stop_event.set()
        return True, frame                           # step 3: cable back in

    fake_cap = MagicMock()
    fake_cap.read.side_effect = lambda: reads()

    monkeypatch.setattr(pe.time, "sleep", lambda *_a, **_k: None)
    with patch("webcam_client.push_engine.open_camera", return_value=fake_cap), \
         patch("webcam_client.push_engine.httpx.Client", return_value=mock_client):
        engine.run()

    assert Fault.NONE not in seen, (
        f"the camera came back but the uplink is still down -- reporting NONE "
        f"toasts the guard 監控中 while nothing uploads; got {seen}")
    assert seen == [Fault.NO_SERVER], (
        f"one honest verdict: the uplink fault outranks the camera fault and "
        f"survives the camera's recovery; got {seen}")


def test_a_successful_upload_does_not_clear_a_dead_camera():
    """The other direction of the same split, pinned structurally.

    Not reachable through run() today -- the bad-read arm `continue`s before
    _push_snapshot, so no upload succeeds while this camera has no picture. It is
    pinned anyway because the INDEPENDENCE is the design, not a side effect of
    that control flow: any future change that pushes a cached or black frame
    while the camera is down would otherwise silently start reporting 監控中 for
    a camera the guard can see is dark.
    """
    import numpy as np
    from webcam_client.status import Fault

    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    seen = []
    engine = PushEngine(config, "https://example.com", "sk-test", on_fault=seen.append)

    mock_client = MagicMock()
    mock_client.post.return_value = MagicMock()          # a clean 200
    engine._client = mock_client

    engine._report_camera(Fault.CAMERA_DOWN)
    assert seen == [Fault.CAMERA_DOWN]

    engine._push_snapshot(np.zeros((480, 640, 3), dtype=np.uint8))

    assert seen == [Fault.CAMERA_DOWN], (
        f"the upload works, but this camera still has no picture -- a successful "
        f"push must not clear the camera domain; got {seen}")
    assert engine._uplink_fault is Fault.NONE, "the uplink domain DID clear"


def test_the_uplink_domain_outranks_the_camera_domain():
    """_publish() merges the two domains with the hub's own _PRECEDENCE, and the
    merge is only correct if an uplink fault genuinely outranks a camera fault.
    That premise is asserted here rather than left in a comment: reordering
    _PRECEDENCE would otherwise invert the merge with the whole suite still green,
    and the guard would be told to check a USB cable during a server outage.

    Both operand orders are checked on purpose. In the sibling control channel a
    `_worse` reduced to `lambda a, b: b` -- ignoring the ordering entirely --
    survived the ENTIRE suite, because every test happened to put the worse fault
    last.
    """
    from webcam_client.push_engine import _worse
    from webcam_client.status import Fault, _PRECEDENCE

    assert _PRECEDENCE.index(Fault.NO_SERVER) < _PRECEDENCE.index(Fault.CAMERA_DOWN)
    assert _PRECEDENCE.index(Fault.BAD_KEY) < _PRECEDENCE.index(Fault.CAMERA_DOWN)

    for uplink in (Fault.NO_SERVER, Fault.BAD_KEY):
        assert _worse(Fault.CAMERA_DOWN, uplink) is uplink
        assert _worse(uplink, Fault.CAMERA_DOWN) is uplink

    # NONE is not in _PRECEDENCE at all, so it must rank last in both orders.
    assert _worse(Fault.CAMERA_DOWN, Fault.NONE) is Fault.CAMERA_DOWN
    assert _worse(Fault.NONE, Fault.CAMERA_DOWN) is Fault.CAMERA_DOWN
    assert _worse(Fault.NONE, Fault.NONE) is Fault.NONE


def test_shutdown_stops_the_encoder_under_the_stream_lock():
    """set_streaming() is called from the ControlChannel thread (app_controller
    dispatches stream_start / stream_stop from there) and mutates _streaming /
    _encoder under _stream_lock. run()'s teardown used to stop the encoder with
    NO lock held, so a stream_start arriving during shutdown could interleave:
    free an encoder mid-construction, or install one after the teardown had
    passed, leaving an orphaned ffmpeg on the guard's PC until reboot.
    """
    import numpy as np

    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    engine = PushEngine(config, "https://example.com", "sk-test")
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    held = []

    def probe():
        # _stream_lock is a plain threading.Lock, which is NOT reentrant, so a
        # SUCCESSFUL acquire from inside the call proves the caller did not hold
        # it. This is the same structural technique test_status.py uses to prove
        # the hub notifies under its own lock.
        got = engine._stream_lock.acquire(blocking=False)
        if got:
            engine._stream_lock.release()
        held.append(not got)

    def read_once():
        engine._stop_event.set()
        return True, frame

    fake_cap = MagicMock()
    fake_cap.read.side_effect = lambda: read_once()

    with patch("webcam_client.push_engine.open_camera", return_value=fake_cap), \
         patch.object(engine, "_push_snapshot"), \
         patch.object(engine, "_stop_encoder", side_effect=probe):
        engine.run()

    assert held, "the teardown never stopped the encoder at all"
    assert all(held), "the encoder was torn down without holding _stream_lock"
    assert engine._streaming is False, "shutdown must leave _streaming False"


def test_set_streaming_refuses_to_start_an_encoder_after_stop():
    """A stream_start that lands after stop() would start an ffmpeg nobody is
    left to stop -- run()'s teardown has already passed -- so the child process
    outlives the engine and keeps running on the guard's PC until reboot.

    Unrequested extension of the locking fix above: the lock closes the race, but
    the orphan is reachable without any race at all once the engine has stopped.
    """
    config = {"node_id": "webcam_01", "device_index": 0}
    engine = PushEngine(config, "https://example.com", "sk-test")

    engine.stop()
    with patch.object(engine, "_start_encoder") as mock_start:
        engine.set_streaming(True)

    assert not mock_start.called, "no encoder may be started once the engine stops"
    assert engine._streaming is False


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
