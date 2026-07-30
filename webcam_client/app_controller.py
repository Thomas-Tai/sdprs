# webcam_client/app_controller.py
import logging
import threading
from typing import Callable, List, Optional

from .status import CONTROL_SOURCE

logger = logging.getLogger("webcam_client.app_controller")


class _NoOpStatusHub:
    """Stand-in used when the caller doesn't wire a real StatusHub, so worker
    wiring (report/set_paused/clear_all) can be called unconditionally without
    a None-check at every call site."""

    def report(self, source: str, fault) -> None:
        pass

    def set_paused(self, paused: bool) -> None:
        pass

    def clear_all(self) -> None:
        pass


class _FaultGate:
    """One worker's on_fault callback, with an off switch the controller owns.

    Finding #5: stop_engines() gives an engine 5 seconds to die and then merely
    LOGS that it is still alive, and it never joins the control channel at all
    (stop() only sets an event, and the channel's long-poll runs up to 5s). Both
    workers therefore outlive the teardown routinely, and both keep their
    callback -- so a report can land AFTER the finally: block ran
    hub.clear_all(), re-inserting a fault the controller has already declared
    stale, for a camera the guard may have just deleted in the settings window.
    main.py's _camera_display_names() cannot map that node_id to any configured
    camera, so it drops it and logs, and what the guard is left with is a red
    tray light beside a status window whose detail line names NO camera at all.

    A stale CONTROL_SOURCE report is worse: NO_SERVER outranks CAMERA_DOWN, so it
    hides every real camera fault behind 「無法連線到伺服器 / 請檢查網路線」 while
    the pictures are landing on the server perfectly well.

    The worker calls this from ITS OWN thread while the main thread is inside
    stop_engines(), so the switch must be thread-safe. threading.Event is exactly
    that, and it brings no lock of its own for a callback to deadlock on.
    """

    def __init__(self, hub, source: str):
        self._hub = hub
        self.source = source
        self._live = threading.Event()
        self._live.set()

    def disarm(self) -> None:
        self._live.clear()

    def __call__(self, fault) -> None:
        if not self._live.is_set():
            # INFO, not DEBUG: the root logger sits at INFO, so a DEBUG line here
            # would be discarded and a worker that would not die would leave no
            # trace whatsoever. stop_engines() logs the stuck thread by name at
            # WARNING; this names what it went on to say, so the technician can
            # tie the two together. The volume is safe -- both workers dedup
            # locally and only call on_fault when their verdict CHANGES.
            logger.info("Ignoring fault %s from a torn-down worker (%s)",
                        getattr(fault, "value", fault), self.source)
            return
        self._hub.report(self.source, fault)


def enabled_cameras(config: Optional[dict]) -> List[dict]:
    """The cameras currently in play, read from a LIVE config dict.

    THE single definition, on purpose. This filter used to exist twice -- here,
    deciding which cameras actually RUN, and again in main.py, deciding what the
    status window TELLS the guard. The two agreed, but nothing made them: any
    divergence (a new "disabled" spelling, a different default for a missing
    `enabled` key) would make the window quietly lie about how many cameras are
    being watched, which is precisely the class of untruth this phase exists to
    remove. main.py imports this rather than keeping its own copy.

    Tolerates None/{} so an error path with no config to hand still gets a list.
    """
    return [c for c in (config or {}).get("cameras", []) if c.get("enabled", True)]


def _default_engine_factory(cam: dict, server_url: str, api_key: str,
                            on_fault: Optional[Callable] = None):
    from .push_engine import PushEngine
    return PushEngine(cam, server_url, api_key, on_fault=on_fault)


def _default_control_factory(server_url: str, api_key: str, node_ids: list,
                             on_command: Callable, on_fault: Optional[Callable] = None):
    from .control_channel import ControlChannel
    return ControlChannel(server_url, api_key, node_ids, on_command, on_fault=on_fault)


class AppController:
    """Owns the running PushEngines + ControlChannel. Lets the MAIN thread stop
    them (releasing cameras), rebuild them from a new config in-process, and fan
    out pause/resume. Factories are injectable for unit testing without real
    cameras or network."""

    def __init__(self, config: dict, *, engine_factory: Optional[Callable] = None,
                 control_factory: Optional[Callable] = None, status_hub=None):
        self._config = dict(config)
        self._engine_factory = engine_factory or _default_engine_factory
        self._control_factory = control_factory or _default_control_factory
        self._hub = status_hub if status_hub is not None else _NoOpStatusHub()
        self._engines: List = []
        # Parallel to _engines and mutated in the SAME critical sections: one
        # gate per engine, in the same order. Never append to or clear one
        # without the other, or stop_engines() will fail to silence a worker it
        # has torn down (Finding #5).
        self._gates: List[_FaultGate] = []
        self._control = None
        self._control_gate: Optional[_FaultGate] = None
        self._paused = False
        # pause_all/resume_all run on the pystray daemon thread; start/stop/apply
        # run on the main thread. Guards _engines mutations + snapshots so the
        # two threads never observe/mutate the list mid-iteration.
        self._lock = threading.Lock()

    @property
    def config(self) -> dict:
        return self._config

    def _enabled_cameras(self) -> List[dict]:
        return enabled_cameras(self._config)

    def running_node_ids(self) -> List[str]:
        """The node_ids that currently have a tracked engine.

        Tracked, deliberately NOT is_alive(): an engine whose camera will not
        open reports CAMERA_DOWN from inside its own run() before the thread
        exits, so the hub already knows about that camera and a second synthetic
        report would only be noise. What this exists to identify is the camera
        with NO worker at all -- the one nothing alive will ever report for.
        main.py's failed-rebuild backstop needs exactly that set (Finding #18).
        """
        with self._lock:
            return [g.source for g in self._gates if g.source]

    def start_engines(self) -> None:
        server_url = self._config.get("server_url", "")
        api_key = self._config.get("api_key", "")
        motion = self._config.get("motion_threshold", 25)
        for cam in self._enabled_cameras():
            cam = dict(cam)
            cam["motion_threshold"] = motion
            node_id = cam.get("node_id", "")
            # A gate rather than a bare closure, so stop_engines() can silence
            # this worker even when it outlives its join (Finding #5).
            gate = _FaultGate(self._hub, node_id)
            engine = self._engine_factory(cam, server_url, api_key, on_fault=gate)
            engine.start()
            with self._lock:
                # Re-assert the remembered pause state on freshly built engines
                # -- otherwise a settings save silently un-pauses uploads while
                # the tray still shows amber/"resume" (Finding 1) -- and READ it
                # in the same critical section that publishes the engine
                # (Finding #8).
                #
                # pause_all() runs on the pystray daemon thread and writes
                # _paused BEFORE it snapshots _engines, so exactly one of the two
                # always delivers a pause: either this read sees the write, or
                # that snapshot contains this engine. Reading _paused OUTSIDE
                # this lock lost the pause that landed in between, leaving
                # uploads running while the tray menu still offered
                # 「恢復上傳」 -- the tray lying about whether the site uploads.
                #
                # set_paused() is called with the lock held. Safe: it only sets a
                # threading.Event and cannot re-enter this controller. A worker
                # method that DID call back in here would deadlock on this
                # non-reentrant lock -- which is the reason nothing heavier than
                # this belongs inside the block.
                #
                # Publishing the engine here also keeps the Finding A fix: it is
                # tracked the moment it is started, so a later camera failing in
                # this loop cannot orphan an already-open camera. It is published
                # BEFORE set_paused() for the same reason -- a set_paused() that
                # raised would otherwise leave a started, untracked engine
                # holding an open camera, which is the leak that fix exists to
                # close. No other thread can see the half-configured engine: this
                # is all one critical section.
                self._engines.append(engine)
                self._gates.append(gate)
                engine.set_paused(self._paused)
        node_ids = [c["node_id"] for c in self._enabled_cameras() if c.get("node_id")]
        self._control_gate = _FaultGate(self._hub, CONTROL_SOURCE)
        self._control = self._control_factory(
            server_url, api_key, node_ids, self._on_command,
            on_fault=self._control_gate)
        self._control.start()

    def stop_engines(self) -> None:
        with self._lock:
            engines = list(self._engines)
            gates = list(self._gates)
        control_gate, self._control_gate = self._control_gate, None
        # Disarm BEFORE anything is asked to stop, not after the clear_all() in
        # the finally: block. Every report made from here on is about to be wiped
        # by that clear_all() anyway, so dropping them costs the guard nothing --
        # whereas disarming afterwards would leave a window between the clear and
        # the disarm in which a report lands and then OUTLIVES the clear, which is
        # Finding #5's latched red light by a narrower door.
        for gate in gates:
            gate.disarm()
        if control_gate is not None:
            control_gate.disarm()
        try:
            if self._control is not None:
                try:
                    self._control.stop()
                except Exception:
                    logger.exception("Error stopping control channel")
                self._control = None
            for e in engines:
                try:
                    e.stop()
                except Exception:
                    logger.exception("Error stopping engine %s",
                                     getattr(e, "_node_id", e))
            for e in engines:
                join = getattr(e, "join", None)
                if callable(join):
                    try:
                        join(timeout=5)
                    except Exception:
                        logger.exception("Error joining engine %s",
                                         getattr(e, "_node_id", e))
                is_alive = getattr(e, "is_alive", None)
                if callable(is_alive) and is_alive():
                    logger.warning(
                        "Engine %s did not stop within timeout; "
                        "camera device may still be open",
                        getattr(e, "_node_id", e))
        finally:
            with self._lock:
                self._engines = []
                self._gates = []
            # Engines are gone -> every reported fault is stale. Without this a
            # settings edit or a quit can leave a red light no live worker owns.
            # Safe to call the hub with no lock held, and it must stay that way:
            # clear_all() runs the hub's on_change inside the HUB's lock, and
            # nesting that under this one is how a deadlock gets built.
            self._hub.clear_all()

    def apply(self, new_config: dict) -> None:
        self.stop_engines()
        self._config = dict(new_config)
        self.start_engines()

    # Both of these run on the pystray DAEMON thread while start_engines() may be
    # running on the main thread.
    #
    # The order below is load-bearing and must not be rearranged: _paused is
    # written BEFORE the _engines snapshot is taken. That is what guarantees a
    # pause reaches every engine, including one being built right now (Finding
    # #8). The snapshot here and the read-and-append in start_engines() are
    # mutually exclusive critical sections, so for any engine E exactly one of
    # two things is true: E was already in the snapshot, or E's read of _paused
    # happened after this write and saw it. Move the write BELOW the snapshot and
    # neither holds -- the pause is lost, uploads keep running, and the tray goes
    # on offering 「恢復上傳」 for a site that never paused. Pinned by
    # test_pause_all_writes_the_flag_before_it_snapshots_the_engines.
    #
    # The fan-out itself stays OUTSIDE the lock: the hub takes its own lock and
    # runs its on_change callback inside it, and nesting that under this lock is
    # how a deadlock gets built.
    def pause_all(self) -> None:
        self._paused = True
        self._hub.set_paused(True)
        with self._lock:
            engines = list(self._engines)
        for e in engines:
            e.set_paused(True)

    def resume_all(self) -> None:
        self._paused = False
        self._hub.set_paused(False)
        with self._lock:
            engines = list(self._engines)
        for e in engines:
            e.set_paused(False)

    def shutdown(self) -> None:
        self.stop_engines()

    def _on_command(self, node_id: str, command: str,
                    params: Optional[dict] = None) -> None:
        # _on_command runs on the ControlChannel thread -- snapshot under the
        # lock first (Finding C) so it never iterates _engines concurrently
        # with a start/stop/apply mutation on the main thread.
        with self._lock:
            engines = list(self._engines)
        for e in engines:
            if getattr(e, "_node_id", None) == node_id:
                if command == "stream_start":
                    e.set_streaming(True)
                elif command == "stream_stop":
                    e.set_streaming(False)
                break
