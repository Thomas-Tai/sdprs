# webcam_client/tests/test_app_controller.py
"""AppController owns the worker threads so the MAIN thread can stop them
(freeing cameras), rebuild them from a new config in-process, and fan out
pause/resume. Factories are injected so this is testable without real cameras
or network."""
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.app_controller import AppController


class FakeEngine:
    def __init__(self, cam, server_url, api_key):
        self.cam, self.server_url, self.api_key = cam, server_url, api_key
        self._node_id = cam.get("node_id", "")
        self.started = self.stopped = self.joined = False
        self.paused = None
        self.streaming = None
        self._raise_on_stop = cam.get("_raise_on_stop", False)
        self._alive_after_join = cam.get("_alive_after_join", False)
        self._raise_on_start = cam.get("_raise_on_start", False)

    def start(self):
        if self._raise_on_start:
            raise RuntimeError("boom: start() failed")
        self.started = True

    def stop(self):
        self.stopped = True
        if self._raise_on_stop:
            raise RuntimeError("boom: stop() failed")

    def join(self, timeout=None): self.joined = True
    def is_alive(self): return self._alive_after_join
    def set_paused(self, v): self.paused = v
    def set_streaming(self, v): self.streaming = v


class FakeControl:
    def __init__(self, server_url, api_key, node_ids, on_command):
        self.node_ids, self.on_command = node_ids, on_command
        self.started = self.stopped = False

    def start(self): self.started = True
    def stop(self): self.stopped = True


def _controller(config):
    made = {"engines": [], "controls": []}
    def ef(cam, s, k):
        e = FakeEngine(cam, s, k); made["engines"].append(e); return e
    def cf(s, k, ids, cb):
        c = FakeControl(s, k, ids, cb); made["controls"].append(c); return c
    return AppController(config, engine_factory=ef, control_factory=cf), made


CONFIG = {
    "server_url": "http://x", "api_key": "k", "motion_threshold": 30,
    "cameras": [
        {"device_index": 0, "node_id": "webcam_a", "enabled": True},
        {"device_index": 1, "node_id": "webcam_b", "enabled": True},
    ],
}


def test_start_engines_builds_one_per_enabled_camera_and_starts_them():
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    assert len(made["engines"]) == 2
    assert all(e.started for e in made["engines"])
    assert made["engines"][0].cam["motion_threshold"] == 30
    assert made["controls"][0].node_ids == ["webcam_a", "webcam_b"]
    assert made["controls"][0].started


def test_start_engines_tracks_each_engine_immediately_on_mid_loop_failure():
    """Finding A regression: engines were previously batched into a local list
    and only added to self._engines via a single extend() AFTER the whole
    build loop finished. If the 2nd of N cameras raised while
    building/starting, the 1st engine -- already holding an open camera --
    was never tracked, so stop_engines() couldn't clean it up: an orphaned,
    leaked camera handle. Each engine must be appended to self._engines the
    moment it is started, so a mid-loop failure still leaves already-started
    engines cleanable."""
    cfg = {
        "server_url": "http://x", "api_key": "k",
        "cameras": [
            {"device_index": 0, "node_id": "a", "enabled": True},
            {"device_index": 1, "node_id": "b", "enabled": True, "_raise_on_start": True},
            {"device_index": 2, "node_id": "c", "enabled": True},
        ],
    }
    ctrl, made = _controller(cfg)

    with pytest.raises(RuntimeError):
        ctrl.start_engines()

    # only the first engine was fully built+started before the 2nd raised
    assert len(made["engines"]) == 2
    first = made["engines"][0]
    assert first.started

    # it must already be tracked -- not orphaned -- so stop_engines can free it
    assert ctrl._engines == [first]

    ctrl.stop_engines()  # must be able to clean up the already-started engine
    assert first.stopped and first.joined
    assert ctrl._engines == []


def test_stop_engines_stops_joins_and_clears():
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    first = list(made["engines"])
    ctrl.stop_engines()
    assert all(e.stopped and e.joined for e in first)
    assert made["controls"][0].stopped
    ctrl.start_engines()
    assert len(made["engines"]) == 4  # fresh engines, old ones not reused


def test_stop_engines_continues_after_one_engine_stop_raises():
    """A raising e.stop() must not abort cleanup of the remaining engines,
    and _engines must still end up cleared (finally-semantics)."""
    cfg = {
        "server_url": "http://x", "api_key": "k",
        "cameras": [
            {"device_index": 0, "node_id": "a", "enabled": True, "_raise_on_stop": True},
            {"device_index": 1, "node_id": "b", "enabled": True},
        ],
    }
    ctrl, made = _controller(cfg)
    ctrl.start_engines()
    first = list(made["engines"])

    ctrl.stop_engines()  # must not raise despite first.stop() blowing up

    assert first[0].stopped  # attempted, even though it raised
    assert first[1].stopped and first[1].joined
    assert first[0].joined  # join loop still runs for the raising engine too
    assert made["controls"][0].stopped
    assert ctrl._engines == []

    ctrl.start_engines()
    assert len(made["engines"]) == 4  # fresh engines only, none leaked


def test_stop_engines_logs_warning_when_engine_still_alive_after_join(caplog):
    """join(timeout=5) result must not be discarded: an engine still alive
    after join is a stuck thread == a camera that never got released, and
    must be surfaced via a warning naming the engine."""
    cfg = {
        "server_url": "http://x", "api_key": "k",
        "cameras": [
            {"device_index": 0, "node_id": "stuck_cam", "enabled": True,
             "_alive_after_join": True},
        ],
    }
    ctrl, made = _controller(cfg)
    ctrl.start_engines()

    with caplog.at_level(logging.WARNING, logger="webcam_client.app_controller"):
        ctrl.stop_engines()  # must not crash

    assert made["engines"][0].joined
    assert ctrl._engines == []
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "expected a warning log when engine is still alive after join"
    assert any("stuck_cam" in r.getMessage() for r in warnings)


def test_apply_stops_old_then_starts_new_and_updates_config():
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    old = list(made["engines"])
    new_cfg = {"server_url": "http://y", "api_key": "k2",
               "cameras": [{"device_index": 0, "node_id": "webcam_c", "enabled": True}]}
    ctrl.apply(new_cfg)
    assert all(e.stopped for e in old)
    assert ctrl.config["server_url"] == "http://y"
    assert made["engines"][-1].server_url == "http://y"
    assert len(made["engines"]) == 3  # 2 old + 1 new built


def test_pause_and_resume_fan_out_to_current_engines():
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    ctrl.pause_all()
    assert all(e.paused is True for e in made["engines"])
    ctrl.resume_all()
    assert all(e.paused is False for e in made["engines"])


def test_pause_state_persists_across_apply():
    """Finding 1: if the operator paused pushes before opening settings, the
    NEW engines built by apply() must come up already paused -- otherwise the
    tray still shows amber/paused while uploads have silently resumed."""
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    ctrl.pause_all()
    new_cfg = {"server_url": "http://y", "api_key": "k2",
               "cameras": [{"device_index": 0, "node_id": "webcam_c", "enabled": True}]}
    ctrl.apply(new_cfg)
    new_engines = made["engines"][2:]  # engines built by this apply() only
    assert new_engines, "apply should have built new engines"
    assert all(e.paused is True for e in new_engines)


def test_resume_state_persists_across_apply():
    """Mirror of the above: if the operator resumed before opening settings,
    the new engines must come up unpaused."""
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    ctrl.pause_all()
    ctrl.resume_all()
    new_cfg = {"server_url": "http://y", "api_key": "k2",
               "cameras": [{"device_index": 0, "node_id": "webcam_c", "enabled": True}]}
    ctrl.apply(new_cfg)
    new_engines = made["engines"][2:]
    assert new_engines, "apply should have built new engines"
    assert all(e.paused is False for e in new_engines)


def test_disabled_cameras_are_skipped():
    cfg = {"server_url": "http://x", "api_key": "k", "cameras": [
        {"device_index": 0, "node_id": "a", "enabled": True},
        {"device_index": 1, "node_id": "b", "enabled": False},
    ]}
    ctrl, made = _controller(cfg)
    ctrl.start_engines()
    assert len(made["engines"]) == 1
    assert made["controls"][0].node_ids == ["a"]


def test_on_command_routes_stream_toggles_to_matching_engine():
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    cb = made["controls"][0].on_command
    cb("webcam_b", "stream_start", None)
    assert made["engines"][1].streaming is True
    cb("webcam_b", "stream_stop", None)
    assert made["engines"][1].streaming is False
