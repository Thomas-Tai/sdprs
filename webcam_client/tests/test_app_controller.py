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
    # Both factories accept -- and ignore -- the on_fault kwarg AppController now
    # always passes. These fakes predate fault reporting (Task 3) and none of the
    # pre-existing tests below care about it; only the new hub-forwarding tests
    # further down build fakes that capture it.
    def ef(cam, s, k, on_fault=None):
        e = FakeEngine(cam, s, k); made["engines"].append(e); return e
    def cf(s, k, ids, cb, on_fault=None):
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


def test_controller_forwards_worker_faults_to_the_hub():
    from webcam_client.app_controller import AppController
    from webcam_client.status import StatusHub, Fault, Health

    hub = StatusHub()
    captured = {}

    class FakeEngine:
        def __init__(self, cam, url, key, on_fault=None):
            captured["on_fault"] = on_fault
            self._node_id = cam.get("node_id")

        def start(self): pass
        def set_paused(self, v): pass
        def stop(self): pass

    ctrl = AppController(
        {"cameras": [{"device_index": 0, "node_id": "n1", "enabled": True}]},
        engine_factory=lambda cam, url, key, on_fault=None: FakeEngine(
            cam, url, key, on_fault),
        control_factory=lambda url, key, ids, cb, on_fault=None: type(
            "C", (), {"start": lambda s: None, "stop": lambda s: None})(),
        status_hub=hub,
    )
    ctrl.start_engines()
    captured["on_fault"](Fault.BAD_KEY)
    assert hub.state is Health.BAD_KEY


def test_stop_engines_clears_stale_faults_from_the_hub():
    """A red light must never outlive the worker that reported it."""
    from webcam_client.app_controller import AppController
    from webcam_client.status import StatusHub, Fault, Health

    hub = StatusHub()
    hub.report("n1", Fault.BAD_KEY)
    ctrl = AppController(
        {"cameras": []},
        engine_factory=lambda *a, **k: None,
        control_factory=lambda *a, **k: type(
            "C", (), {"start": lambda s: None, "stop": lambda s: None})(),
        status_hub=hub,
    )
    ctrl.stop_engines()
    assert hub.state is Health.STARTING
    assert hub.faulty_sources() == []


# --------------------------------------------------------------------------
# Finding #5: a zombie worker must not be able to resurrect a red light for a
# camera the guard has just deleted.
#
# stop_engines() gives each engine 5 seconds to die and then merely LOGS that it
# is still alive. A stuck engine keeps its on_fault closure and keeps calling it
# -- AFTER the finally: block ran hub.clear_all() -- so a fault re-appears for a
# camera that may no longer be in the config at all. main.py's
# _camera_display_names() then cannot map that node_id to any configured camera,
# drops it and logs; the guard is left with a red tray light and a status window
# whose detail line names NO camera. The report has to become inert the moment
# the controller has torn the worker down.
# --------------------------------------------------------------------------

class _RecordingHub:
    """StatusHub stand-in that remembers WHO reported and WHEN, so a test can
    assert about reports that arrive during the teardown as well as after it."""

    def __init__(self):
        self.reports = []
        self.cleared = 0

    def report(self, source, fault):
        self.reports.append((source, fault))

    def set_paused(self, paused):
        pass

    def clear_all(self):
        self.cleared += 1


def _hub_controller(cfg, hub, *, alive_after_join=False, stop_reports=None):
    """Controller whose engine/control fakes capture their on_fault callbacks."""
    made = {"engines": [], "controls": [], "engine_faults": [], "control_faults": []}

    class Engine:
        def __init__(self, cam, url, key, on_fault=None):
            self._node_id = cam.get("node_id", "")
            self._on_fault = on_fault
            made["engine_faults"].append(on_fault)
            self.paused = None

        def start(self): pass
        def set_paused(self, v): self.paused = v

        def stop(self):
            if stop_reports is not None:
                # A real engine reports from ITS OWN thread and can do so at any
                # moment during the teardown; this is that report, made
                # deterministic.
                self._on_fault(stop_reports)

        def join(self, timeout=None): pass
        def is_alive(self): return alive_after_join

    class Control:
        def __init__(self, url, key, ids, cb, on_fault=None):
            made["control_faults"].append(on_fault)

        def start(self): pass
        def stop(self): pass

    def ef(cam, u, k, on_fault=None):
        e = Engine(cam, u, k, on_fault); made["engines"].append(e); return e

    def cf(u, k, ids, cb, on_fault=None):
        c = Control(u, k, ids, cb, on_fault); made["controls"].append(c); return c

    ctrl = AppController(cfg, engine_factory=ef, control_factory=cf, status_hub=hub)
    return ctrl, made


ONE_CAM = {"server_url": "http://x", "api_key": "k",
           "cameras": [{"device_index": 0, "node_id": "webcam_a", "enabled": True}]}


def test_a_stuck_engine_cannot_report_after_it_has_been_torn_down():
    """The engine that outlived join(timeout=5) is still running with its
    closure intact. Its next report must go nowhere: it is an opinion about a
    camera the controller no longer owns, and the hub has already been cleared."""
    from webcam_client.status import StatusHub, Fault, Health

    hub = StatusHub()
    ctrl, made = _hub_controller(ONE_CAM, hub, alive_after_join=True)
    ctrl.start_engines()
    on_fault = made["engine_faults"][0]
    on_fault(Fault.NONE)                     # while alive: reports normally
    assert hub.state is Health.RUNNING

    ctrl.stop_engines()
    on_fault(Fault.CAMERA_DOWN)              # the zombie, six seconds later

    assert hub.faulty_sources() == [], (
        "a torn-down engine re-inserted a fault after clear_all(): the guard "
        "gets a red light for a camera that may no longer be in the config, and "
        "a status window naming no camera at all")
    assert hub.state is Health.STARTING


def test_a_torn_down_control_channel_cannot_report_either():
    """The control channel is stopped but NEVER joined -- stop() only sets an
    event and its long-poll runs up to 5 seconds -- so the same zombie report is
    easier to hit here than on an engine. It is also worse: CONTROL_SOURCE's
    NO_SERVER outranks CAMERA_DOWN, so a stale one masks every real camera fault
    behind 「無法連線到伺服器」 while the pictures are landing fine."""
    from webcam_client.status import StatusHub, Fault, Health

    hub = StatusHub()
    ctrl, made = _hub_controller(ONE_CAM, hub)
    ctrl.start_engines()
    on_fault = made["control_faults"][0]

    ctrl.stop_engines()
    on_fault(Fault.NO_SERVER)

    assert hub.faulty_sources() == [], (
        "a stopped control channel latched NO_SERVER onto a hub that no live "
        "worker owns")
    assert hub.state is Health.STARTING


def test_no_fault_survives_the_teardown_it_arrived_during():
    """The disarm has to come BEFORE the stop, not after clear_all().

    Disarming afterwards leaves a window -- between clear_all() and the disarm --
    in which an in-flight report lands and then outlives the clear, which is the
    same latched red light by a narrower door. Every report in the teardown is
    about to be wiped by clear_all() anyway, so dropping them costs nothing."""
    from webcam_client.status import Fault

    hub = _RecordingHub()
    ctrl, made = _hub_controller(ONE_CAM, hub, stop_reports=Fault.CAMERA_DOWN)
    ctrl.start_engines()
    hub.reports.clear()

    ctrl.stop_engines()

    assert hub.cleared == 1, "precondition: stop_engines still clears the hub"
    assert hub.reports == [], (
        f"a report reached the hub during the teardown: {hub.reports}")


def test_running_node_ids_lists_only_cameras_with_a_tracked_engine():
    """main.py's failed-rebuild backstop needs to know which cameras actually
    have a worker, so it can name the ones that do NOT instead of blaming the
    network for all of them (finding #18)."""
    hub = _RecordingHub()
    cfg = {"server_url": "http://x", "api_key": "k", "cameras": [
        {"device_index": 0, "node_id": "a", "enabled": True},
        {"device_index": 1, "node_id": "b", "enabled": True},
        {"device_index": 2, "node_id": "c", "enabled": False},
    ]}
    ctrl, made = _hub_controller(cfg, hub)
    assert ctrl.running_node_ids() == [], "nothing is running before start"
    ctrl.start_engines()
    assert ctrl.running_node_ids() == ["a", "b"], "the disabled camera has no engine"
    ctrl.stop_engines()
    assert ctrl.running_node_ids() == []


def test_running_node_ids_reports_what_a_mid_loop_failure_left_running():
    """The case the backstop exists for: camera 2 refused to start, so camera 1
    is alive and uploading and camera 2 has no worker at all."""
    cfg = {
        "server_url": "http://x", "api_key": "k",
        "cameras": [
            {"device_index": 0, "node_id": "a", "enabled": True},
            {"device_index": 1, "node_id": "b", "enabled": True,
             "_raise_on_start": True},
            {"device_index": 2, "node_id": "c", "enabled": True},
        ],
    }
    ctrl, made = _controller(cfg)
    with pytest.raises(RuntimeError):
        ctrl.start_engines()
    assert ctrl.running_node_ids() == ["a"]


# --------------------------------------------------------------------------
# Finding #8: the lost pause.
#
# start_engines() runs on the MAIN thread; pause_all()/resume_all() run on the
# pystray daemon thread. self._paused was READ at the set_paused() call and the
# engine APPENDED afterwards, under a lock the read was not part of. A pause
# landing in between wrote _paused = True, snapshotted an _engines list this
# engine was not in yet, and fanned out to nobody -- so the engine came up
# UPLOADING while the tray menu offered 「恢復上傳」. The tray then lies about
# whether the site is uploading, which is the exact untruth this phase exists to
# remove. This is a re-run of the ledger's Finding 1: the re-assertion was added,
# the synchronisation behind it never was.
# --------------------------------------------------------------------------

def test_a_pause_landing_mid_rebuild_is_not_lost():
    import threading

    entered_set_paused = threading.Event()
    tray_finished = threading.Event()
    hooked = []

    class Engine:
        def __init__(self, cam, url, key, on_fault=None):
            self._node_id = cam.get("node_id", "")
            self.paused = None

        def start(self): pass
        def stop(self): pass
        def join(self, timeout=None): pass
        def is_alive(self): return False

        def set_paused(self, v):
            self.paused = v
            if hooked:
                return
            hooked.append(True)
            entered_set_paused.set()
            # Hand the tray thread its window. With the bug nothing holds the
            # controller lock, so pause_all() runs straight through and this
            # returns at once. With the fix pause_all() is blocked on the lock,
            # this simply times out, and the fan-out reaches the engine after
            # the append.
            tray_finished.wait(timeout=0.5)

    ctrl = AppController(
        ONE_CAM,
        engine_factory=lambda cam, u, k, on_fault=None: Engine(cam, u, k),
        control_factory=lambda *a, **kw: type(
            "C", (), {"start": lambda s: None, "stop": lambda s: None})())

    def tray_thread():
        entered_set_paused.wait(timeout=5)
        ctrl.pause_all()                     # the guard picks 暫停上傳
        tray_finished.set()

    t = threading.Thread(target=tray_thread)
    t.start()
    ctrl.start_engines()
    t.join(timeout=5)

    assert not t.is_alive(), "the tray thread deadlocked in pause_all()"
    assert ctrl._paused is True, "precondition: the pause was recorded"
    engine = ctrl._engines[0]
    assert engine.paused is True, (
        "a pause that landed during the rebuild was lost: this engine is "
        "uploading while the tray menu offers 「恢復上傳」")


def test_the_pause_state_is_read_atomically_with_the_append():
    """The mechanism behind the test above, pinned directly so a refactor cannot
    quietly reopen the window while the outcome test still happens to pass.

    threading.Lock is NOT reentrant, so a successful acquire from inside
    set_paused() proves the controller was NOT holding it -- i.e. the read of
    _paused and the append of the engine are not atomic with respect to the
    snapshot pause_all() takes."""
    held = []

    class Engine:
        def __init__(self, cam, url, key, on_fault=None):
            self._node_id = cam.get("node_id", "")

        def start(self): pass

        def set_paused(self, v):
            if ctrl._lock.acquire(blocking=False):
                ctrl._lock.release()     # never leave it held: that deadlocks
                held.append(False)
            else:
                held.append(True)

    ctrl = AppController(
        ONE_CAM,
        engine_factory=lambda cam, u, k, on_fault=None: Engine(cam, u, k),
        control_factory=lambda *a, **kw: type(
            "C", (), {"start": lambda s: None, "stop": lambda s: None})())
    ctrl.start_engines()

    assert held == [True], (
        "the remembered pause state was read outside the lock it is appended "
        "under, so a pause arriving from the tray thread can be lost")


def test_pause_all_writes_the_flag_before_it_snapshots_the_engines():
    """The other half of the fix, and the reason pause_all() itself needs no
    lock change: the flag is written BEFORE the snapshot is taken.

    That ordering is what makes the pause reach every engine. The two critical
    sections (this snapshot, and start_engines()' read-and-append) are mutually
    exclusive, so either the snapshot already contains the new engine, or the
    engine's read of _paused happens after this write and sees it. Move the write
    BELOW the snapshot and neither holds -- finding #8 comes straight back with
    the lock still in place, which is why this ordering is pinned rather than
    left as a comment.

    Green before and after the fix: a regression guard, not a red test."""
    ctrl = AppController({"cameras": []}, engine_factory=lambda *a, **k: None,
                         control_factory=lambda *a, **k: None)
    seen = []
    real_lock = ctrl._lock

    class WatchingLock:
        """Records _paused as it stands at the moment the lock is taken."""

        def __enter__(self):
            real_lock.acquire()
            seen.append(ctrl._paused)
            return self

        def __exit__(self, *exc):
            real_lock.release()
            return False

        def acquire(self, *a, **kw): return real_lock.acquire(*a, **kw)
        def release(self): return real_lock.release()

    ctrl._lock = WatchingLock()
    ctrl.pause_all()
    assert seen and seen[0] is True, (
        "pause_all() snapshotted _engines while _paused was still False: an "
        "engine appended just after the snapshot would read False and come up "
        "uploading while the tray offers 「恢復上傳」")


def test_pause_and_resume_reach_the_hub():
    from webcam_client.app_controller import AppController
    from webcam_client.status import StatusHub, Health

    hub = StatusHub()
    ctrl = AppController({"cameras": []}, engine_factory=lambda *a, **k: None,
                         control_factory=lambda *a, **k: None, status_hub=hub)
    ctrl.pause_all()
    assert hub.state is Health.PAUSED
    ctrl.resume_all()
    assert hub.state is not Health.PAUSED
