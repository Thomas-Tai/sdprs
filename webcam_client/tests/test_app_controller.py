# webcam_client/tests/test_app_controller.py
"""AppController owns the worker threads so the MAIN thread can stop them
(freeing cameras), rebuild them from a new config in-process, and fan out
pause/resume. Factories are injected so this is testable without real cameras
or network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.app_controller import AppController


class FakeEngine:
    def __init__(self, cam, server_url, api_key):
        self.cam, self.server_url, self.api_key = cam, server_url, api_key
        self._node_id = cam.get("node_id", "")
        self.started = self.stopped = self.joined = False
        self.paused = None
        self.streaming = None

    def start(self): self.started = True
    def stop(self): self.stopped = True
    def join(self, timeout=None): self.joined = True
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


def test_stop_engines_stops_joins_and_clears():
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    first = list(made["engines"])
    ctrl.stop_engines()
    assert all(e.stopped and e.joined for e in first)
    assert made["controls"][0].stopped
    ctrl.start_engines()
    assert len(made["engines"]) == 4  # fresh engines, old ones not reused


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
