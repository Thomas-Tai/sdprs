# webcam_client/tests/test_main_dispatch.py
"""The tray runs in a daemon thread; its callbacks must only ENQUEUE, so the
GUI opens on the MAIN thread. _handle_request is that main-thread servicer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class FakeController:
    def __init__(self, config):
        self._config = config
        self.calls = []
        self.applied = None

    @property
    def config(self):
        return self._config

    def stop_engines(self): self.calls.append("stop_engines")
    def start_engines(self): self.calls.append("start_engines")
    def apply(self, cfg): self.calls.append("apply"); self.applied = cfg
    def shutdown(self): self.calls.append("shutdown")


def test_open_settings_applies_new_config_on_save(monkeypatch):
    import webcam_client.main as m
    saved = {}
    monkeypatch.setattr(m, "save_config", lambda c: saved.update(c))
    ctrl = FakeController({"server_url": "old"})
    # hub=None: this path never fails, so it never reaches the hub-dependent
    # branch -- the omission is explicit rather than a silent default (NIT).
    keep = m._handle_request("OPEN_SETTINGS", ctrl,
                             lambda cfg: {"server_url": "new"}, hub=None)
    assert keep is True
    assert ctrl.calls == ["stop_engines", "apply"]
    assert ctrl.applied == {"server_url": "new"}
    assert saved == {"server_url": "new"}


def test_open_settings_resumes_old_config_on_cancel(monkeypatch):
    import webcam_client.main as m
    monkeypatch.setattr(m, "save_config", lambda c: None)
    ctrl = FakeController({"server_url": "old"})
    keep = m._handle_request("OPEN_SETTINGS", ctrl, lambda cfg: None, hub=None)
    assert keep is True
    assert ctrl.calls == ["stop_engines", "start_engines"]
    assert ctrl.applied is None


def test_quit_shuts_down_and_stops_loop():
    import webcam_client.main as m
    ctrl = FakeController({})
    # hub=None: QUIT never touches the hub, so there is nothing to omit by
    # accident here -- explicit anyway, per the contract change above (NIT).
    keep = m._handle_request("QUIT", ctrl, lambda cfg: None, hub=None)
    assert keep is False
    assert ctrl.calls == ["shutdown"]


# --------------------------------------------------------------------------
# NIT: controller.shutdown() ran twice on the tray-quit path.
#
# _handle_request's QUIT branch already calls controller.shutdown() (proved
# above) and returns False; main()'s trailing controller.shutdown(), after the
# dispatch loop, then repeated it unconditionally. shutdown() -> stop_engines()
# -> hub.clear_all(), which pushes a stray "HEALTH" token onto a queue nobody
# will ever drain again -- harmless today, but two calls for one quit.
# --------------------------------------------------------------------------

def test_quit_from_the_tray_shuts_the_controller_down_exactly_once(monkeypatch):
    import webcam_client.main as m

    class CountingCtrl(_FakeCtrl):
        def __init__(self, cfg, **kw):
            super().__init__(cfg, **kw)
            self.shutdown_calls = 0

        def shutdown(self):
            self.shutdown_calls += 1

    made = {}

    def ctrl_factory(cfg, **kw):
        ctrl = CountingCtrl(cfg, **kw)
        made["ctrl"] = ctrl
        return ctrl

    class FakeTray:
        icon = None

        def __init__(self, **kw):
            self._kw = kw

        def start(self):
            self._kw["on_quit"]()   # the guard picking 離開 from the tray menu

        def set_health(self, state):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(m, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(m, "_acquire_single_instance", lambda: True)
    monkeypatch.setattr(m, "is_first_run", lambda: False)
    monkeypatch.setattr(m, "add_secret", lambda s: None)
    monkeypatch.setattr(m, "_close_splash", lambda: None)
    monkeypatch.setattr(m, "load_config", lambda: {
        "server_url": "http://x", "api_key": "k",
        "cameras": [{"device_index": 0, "enabled": True, "node_id": "n1"}]})
    monkeypatch.setattr(m, "AppController", ctrl_factory)
    monkeypatch.setattr(m, "TrayApp", FakeTray)
    monkeypatch.setattr(m, "_running", True)

    m.main()

    assert made["ctrl"].shutdown_calls == 1, (
        f"shutdown() ran {made['ctrl'].shutdown_calls} times on an ordinary "
        "tray-quit -- main()'s trailing call must not repeat what "
        "_handle_request's QUIT branch already did")


def test_open_settings_recovers_from_settings_fn_exception():
    """Finding B: recovery must not stack a duplicate engine set on top of a
    partially-started one. If the failure happened mid-apply() (config
    already swapped, engines partially started -- see AppController Finding
    A), calling start_engines() again would append a SECOND set on top of the
    partial one. apply(controller.config) instead stops first (cleaning any
    partial set, releasing cameras) then rebuilds fresh from the current
    config -- no duplication. The loop must still stay alive."""
    import webcam_client.main as m

    def raising_fn(cfg):
        raise RuntimeError("boom: settings window blew up")

    ctrl = FakeController({"server_url": "old"})
    # hub=None: FakeController.apply() never raises, so the recovery succeeds
    # on the first attempt and never reaches the hub-dependent branch -- the
    # omission is explicit, not a silent default (NIT).
    keep = m._handle_request("OPEN_SETTINGS", ctrl, raising_fn, hub=None)
    assert keep is True
    assert ctrl.calls == ["stop_engines", "apply"]
    assert ctrl.applied == {"server_url": "old"}


def test_tray_open_settings_callback_only_enqueues():
    import queue
    q = queue.Queue()
    on_open = lambda: q.put("OPEN_SETTINGS")
    on_open()
    assert q.get_nowait() == "OPEN_SETTINGS"


# --------------------------------------------------------------------------
# NIT: TrayApp.stop() was dead code -- main() never called it -- so the
# SIGINT/SIGTERM path never NIM_DELETEd the pystray icon. The process exits,
# but Windows is never told the icon is gone, so it sits in the notification
# area (looking perfectly live) until the guard happens to hover over it.
# _signal_handler only flips the _running flag (it must stay fast and
# reentrant-safe), so main()'s own shutdown sequence is the one place left
# that can still call tray.stop().
# --------------------------------------------------------------------------

def test_signal_shutdown_still_stops_the_tray_icon(monkeypatch):
    import webcam_client.main as m

    stopped = []

    class FakeTray:
        icon = None

        def __init__(self, **kw):
            pass

        def start(self):
            pass

        def set_health(self, state):
            pass

        def stop(self):
            stopped.append(True)

    _boot(monkeypatch, m, FakeTray)
    # Simulate the signal having already fired before the loop's first check --
    # exactly what happens when SIGINT/SIGTERM arrives during startup or while
    # the loop is blocked on q.get().
    monkeypatch.setattr(m, "_running", False)

    m.main()

    assert stopped, (
        "TrayApp.stop() must run on every shutdown path, including "
        "SIGINT/SIGTERM -- otherwise the icon is left in the notification "
        "area after the process is already gone")


def test_close_splash_is_safe_without_pyi_splash():
    """pyi_splash only exists inside a frozen build that declared a Splash. In
    dev, and in any build without one, importing it raises -- _close_splash must
    swallow that rather than killing startup."""
    import webcam_client.main as m
    m._close_splash()
    m._close_splash()  # idempotent


def test_close_splash_swallows_a_failing_close(monkeypatch):
    """A splash that errors on close must not take the app down with it."""
    import types
    import webcam_client.main as m

    fake = types.ModuleType("pyi_splash")

    def boom():
        raise RuntimeError("splash already gone")

    fake.close = boom
    monkeypatch.setitem(sys.modules, "pyi_splash", fake)
    monkeypatch.setattr(m, "_splash_closed", False)
    m._close_splash()  # must not raise


def test_open_settings_registers_rotated_key_with_redactor(monkeypatch, tmp_path):
    """FIX 2: load_config() and the first-run wizard both call add_secret()
    after they change the api_key; the OPEN_SETTINGS path (tray -> settings)
    is the THIRD path that changes it, and it did not. After an operator
    rotates the key this way, the redactor must scrub the NEW key for the
    rest of the process lifetime, not just the original one from process
    start. Assert the actual redaction behaviour, not merely that
    add_secret() was called -- a call-count assertion would pass even if it
    were called with a stale or wrong value."""
    import logging
    import webcam_client.logging_setup as ls

    monkeypatch.setattr(ls, "get_config_dir", lambda: tmp_path)
    ls.reset_for_tests()
    handler = ls.setup_logging()
    try:
        import webcam_client.main as m
        monkeypatch.setattr(m, "save_config", lambda c: None)

        ctrl = FakeController({"server_url": "old", "api_key": "OLDKEY111"})
        new_key = "ROTATEDKEY999"
        # hub=None: the save succeeds, so this never reaches the hub-dependent
        # branch -- explicit, not a silent default (NIT).
        keep = m._handle_request(
            "OPEN_SETTINGS", ctrl,
            lambda cfg: {"server_url": "old", "api_key": new_key}, hub=None)
        assert keep is True

        logging.getLogger("webcam_client.test").warning(
            f"auth failed for key {new_key}")
        handler.flush()
        body = (tmp_path / "logs" / ls.LOG_FILENAME).read_text(encoding="utf-8")
        assert new_key not in body, "ROTATED API KEY LEAKED INTO THE LOG FILE"
        assert ls.REDACTED in body
    finally:
        ls.reset_for_tests()


def test_setup_logging_runs_before_single_instance_check(monkeypatch):
    """FIX 3: three log statements sit in the window between the two calls --
    single_instance.py's "Global mutex unavailable, using session-local" INFO
    (the COMMON path on a non-admin account) and "mutex creation failed,
    allowing launch" WARNING, plus main.py's "another instance is already
    running" INFO. With no handler attached yet, the INFO records vanish and
    the WARNING reaches only sys.stderr, which is None in a console=False
    build. setup_logging() must run FIRST so those lines land on disk.

    The existing test_tray_starts_before_engines monkeypatches BOTH functions
    to no-ops, which is exactly why a wrong order previously survived review
    undetected -- this test pins the actual call ORDER instead."""
    import tkinter.messagebox as messagebox
    import webcam_client.main as m

    order = []
    monkeypatch.setattr(m, "setup_logging", lambda *a, **k: order.append("logging"))
    monkeypatch.setattr(m, "_acquire_single_instance", lambda: (
        order.append("single_instance"), False)[1])  # refuse -> main() returns early
    # main() pops a real modal messagebox on the refused-instance path; stub it
    # so the test does not block waiting for a click.
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)

    m.main()
    assert order == ["logging", "single_instance"], f"got {order}"


def test_main_proceeds_when_setup_logging_raises(monkeypatch):
    """FIX 4: setup_logging() does an unguarded mkdir() + file open. A
    domain-joined PC with %APPDATA% folder-redirected to a network share that
    is offline at logon (routine in AD environments) makes mkdir() raise
    OSError. Before this fix that propagated out of main() with nowhere to
    show it (console=False) -- the exe would show a splash for ~17s then
    vanish, on EVERY launch, forever. A diagnostic aid must never become a
    single point of startup failure: main() must swallow the failure and
    keep going without a file log."""
    import webcam_client.main as m

    def raising_setup_logging(*a, **k):
        raise OSError("network share offline")

    order = []

    class FakeCtrl:
        # **kw absorbs status_hub= (main() now hands the controller the hub so
        # its workers can report faults).
        def __init__(self, cfg, **kw):
            self._config = cfg

        @property
        def config(self):
            return self._config

        def start_engines(self):
            order.append("engines")

        def shutdown(self):
            pass

        pause_all = resume_all = lambda self: None

    class FakeTray:
        def __init__(self, **kw):
            pass

        def start(self):
            order.append("tray")

        def set_health(self, state):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(m, "setup_logging", raising_setup_logging)
    monkeypatch.setattr(m, "_acquire_single_instance", lambda: True)
    monkeypatch.setattr(m, "AppController", FakeCtrl)
    monkeypatch.setattr(m, "TrayApp", FakeTray)
    monkeypatch.setattr(m, "load_config", lambda: {
        "server_url": "http://x", "api_key": "k",
        "cameras": [{"device_index": 0, "enabled": True, "node_id": "n"}]})
    monkeypatch.setattr(m, "is_first_run", lambda: False)
    monkeypatch.setattr(m, "add_secret", lambda s: None)
    monkeypatch.setattr(m, "_running", False)  # exit the dispatch loop at once

    m.main()  # must not raise
    assert order == ["tray", "engines"], f"main() did not proceed past setup_logging: {order}"


def test_no_cameras_configured_still_closes_splash(monkeypatch):
    """FIX 5: the other two early-exit paths (refused single-instance, wizard
    cancelled) both call _close_splash(); the "no cameras configured" return
    did not, leaving it inconsistent with its siblings. Tidiness/consistency
    (Opus rates it Minor -- the splash is not always_on_top and the parent
    bootloader tears it down when the child exits regardless), but cheap to
    fix while in the file."""
    import webcam_client.main as m

    closed = []
    monkeypatch.setattr(m, "_close_splash", lambda: closed.append(True))
    monkeypatch.setattr(m, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(m, "_acquire_single_instance", lambda: True)
    monkeypatch.setattr(m, "load_config", lambda: {
        "server_url": "http://x", "api_key": "k", "cameras": []})
    monkeypatch.setattr(m, "is_first_run", lambda: False)
    monkeypatch.setattr(m, "add_secret", lambda s: None)

    m.main()
    assert closed, "_close_splash() must be called on the no-cameras exit path"


def test_tray_starts_before_engines(monkeypatch):
    """S6: opening cameras takes 0.5-2s each; the tray icon is the only sign of
    life, so it must exist BEFORE engines start, not after."""
    import webcam_client.main as m

    order = []

    class FakeCtrl:
        # **kw absorbs status_hub= (see the note in the test above).
        def __init__(self, cfg, **kw):
            self._config = cfg

        @property
        def config(self):
            return self._config

        def start_engines(self):
            order.append("engines")

        def shutdown(self):
            pass

        pause_all = resume_all = lambda self: None

    class FakeTray:
        def __init__(self, **kw):
            pass

        def start(self):
            order.append("tray")

        def set_health(self, state):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(m, "AppController", FakeCtrl)
    monkeypatch.setattr(m, "TrayApp", FakeTray)
    monkeypatch.setattr(m, "load_config", lambda: {
        "server_url": "http://x", "api_key": "k",
        "cameras": [{"device_index": 0, "enabled": True, "node_id": "n"}]})
    monkeypatch.setattr(m, "is_first_run", lambda: False)
    monkeypatch.setattr(m, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(m, "add_secret", lambda s: None)
    monkeypatch.setattr(m, "_acquire_single_instance", lambda: True)
    monkeypatch.setattr(m, "_running", False)  # exit the dispatch loop at once

    m.main()
    assert order == ["tray", "engines"], f"got {order}"


# --------------------------------------------------------------------------
# Task 6: StatusHub wired into the dispatch loop
# --------------------------------------------------------------------------

class _FakeCtrl:
    """AppController stand-in for the tests below that run main() end-to-end."""

    def __init__(self, cfg, **kw):
        self._config = cfg
        self.status_hub = kw.get("status_hub")
        self.applied = []
        self.on_start_engines = None

    @property
    def config(self):
        return self._config

    def start_engines(self):
        if self.on_start_engines is not None:
            self.on_start_engines()

    def stop_engines(self): pass
    def apply(self, cfg): self.applied.append(cfg)
    def shutdown(self): pass
    def pause_all(self): pass
    def resume_all(self): pass

    def running_node_ids(self):
        # Nothing is really running behind this fake, which is also what a
        # failed rebuild leaves behind -- the state _report_rebuild_failure has
        # to describe honestly.
        return []


def _boot(monkeypatch, m, tray_cls, cameras=None, on_start_engines=None):
    """Stub everything main() touches before its dispatch loop, so a test can
    drive the loop itself. Returns a dict that gains "ctrl" once main() builds
    the controller."""
    made = {}
    monkeypatch.setattr(m, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(m, "_acquire_single_instance", lambda: True)
    monkeypatch.setattr(m, "is_first_run", lambda: False)
    monkeypatch.setattr(m, "add_secret", lambda s: None)
    monkeypatch.setattr(m, "_close_splash", lambda: None)
    monkeypatch.setattr(m, "load_config", lambda: {
        "server_url": "http://x", "api_key": "k",
        "cameras": cameras if cameras is not None else [
            {"device_index": 0, "enabled": True,
             "node_id": "n1", "name": "前門攝影機"}]})

    def ctrl_factory(cfg, **kw):
        ctrl = _FakeCtrl(cfg, **kw)
        ctrl.on_start_engines = on_start_engines
        made["ctrl"] = ctrl
        return ctrl

    monkeypatch.setattr(m, "AppController", ctrl_factory)
    monkeypatch.setattr(m, "TrayApp", tray_cls)
    # The loop reads this module global every iteration; set it through
    # monkeypatch so a test may flip it to False and still have it restored.
    monkeypatch.setattr(m, "_running", True)
    return made


def test_health_message_repaints_the_tray(monkeypatch):
    """Workers only enqueue; the tray is repainted on the MAIN thread."""
    import webcam_client.main as m
    from webcam_client.status import StatusHub, Fault, Health

    hub = StatusHub()
    painted = []

    class FakeTray:
        def set_health(self, state):
            painted.append(state)

    hub.report("n1", Fault.BAD_KEY)
    m._handle_health(hub, FakeTray())
    assert painted == [Health.BAD_KEY]


def test_notify_message_toasts_the_state_it_was_handed(monkeypatch):
    """I-2: the toast announces the state carried ON THE TOKEN, not hub.state.

    (This test previously drove _handle_notify(hub, icon) and asserted it
    re-read hub.state -- i.e. it pinned the defective behaviour, which is why
    the defect survived review. The state on the token is now the contract.)"""
    import webcam_client.main as m
    from webcam_client.status import Health

    sent = []
    monkeypatch.setattr(m, "notify_state",
                        lambda icon, state, camera_names=None: sent.append(state) or True)
    m._handle_notify(object(), Health.NO_SERVER)
    assert sent == [Health.NO_SERVER]


def test_split_token_handles_both_token_shapes():
    import webcam_client.main as m
    from webcam_client.status import Health

    assert m._split_token("QUIT") == ("QUIT", None)
    assert m._split_token("HEALTH") == ("HEALTH", None)
    assert m._split_token(("NOTIFY", Health.BAD_KEY)) == ("NOTIFY", Health.BAD_KEY)


# --------------------------------------------------------------------------
# I-2: a matured notification must survive the trip through the queue.
#
# tick() decides WHICH state matured past the 30s debounce and latches
# _notified to it at FIRE time, but the token used to be the bare string
# "NOTIFY" -- so _handle_notify toasted whatever hub.state happened to be at
# DRAIN time. The window is wide in practice: the status window and the
# settings wizard both BLOCK this loop, so every transition that happens while
# one is open accumulates and drains at once when it closes.
#
# The three cases below are the reviewer's probe, driving the real StatusHub
# and the real _handle_notify with a fake clock.
# --------------------------------------------------------------------------

class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, secs):
        self.now += secs


def _matured_no_server(monkeypatch):
    """Build a hub wired exactly as main() wires it, drive NO_SERVER past the
    debounce and let tick() fire. Returns (hub, q, clock, toasts, drain)."""
    import queue
    import webcam_client.main as m
    from webcam_client.status import (StatusHub, Fault, CONTROL_SOURCE,
                                      NOTIFY_DEBOUNCE_SECONDS)

    toasts = []
    monkeypatch.setattr(m, "notify_state",
                        lambda icon, state, camera_names=None: toasts.append(state) or True)
    clock = _FakeClock()
    q = queue.Queue()
    hub = StatusHub(on_change=lambda state: q.put("HEALTH"),
                    on_notify=lambda state: q.put(("NOTIFY", state)),
                    clock=clock)

    def drain():
        """What the dispatch loop does once the blocking window closes."""
        while not q.empty():
            name, payload = m._split_token(q.get_nowait())
            if name == "NOTIFY":
                m._handle_notify(None, payload)

    hub.report(CONTROL_SOURCE, Fault.NO_SERVER)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()                      # NO_SERVER matured and fired
    return hub, q, clock, toasts, drain


def test_a_matured_notification_is_not_lost_when_recovery_beats_the_drain(monkeypatch):
    """Case A. The server was down for 30+ seconds and came back while the
    status window was open. Re-reading hub.state told the guard 監控中 TWICE
    and never mentioned the outage -- the notification was simply lost.

    Timing updated for human decision 5 (2026-07-30): the recovery is no longer
    announced on the transition, it waits for a full window of health. The
    guarantee THIS test exists for is untouched and is what the first assertion
    still pins -- the matured NO_SERVER survives a recovery that beats the drain,
    and the guard does not get a bare 監控中 in its place. The recovery arriving
    later is asserted too, so "not lost" cannot be satisfied by never announcing
    it at all."""
    from webcam_client.status import (Fault, Health, CONTROL_SOURCE,
                                     NOTIFY_DEBOUNCE_SECONDS)

    hub, q, clock, toasts, drain = _matured_no_server(monkeypatch)
    hub.report(CONTROL_SOURCE, Fault.NONE)      # recovery, before the drain
    drain()

    assert toasts == [Health.NO_SERVER], (
        "the outage must still be announced even though the recovery overtook "
        f"the drain -- and must not be replaced by a bare 監控中: {toasts}")
    assert hub.state is Health.RUNNING, "the tray light is not debounced"

    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    drain()
    assert toasts == [Health.NO_SERVER, Health.RUNNING], (
        f"...and once the uploads have held for a full window, the recovery "
        f"must be announced too: {toasts}")


def test_a_new_fault_before_the_drain_does_not_bypass_the_debounce(monkeypatch):
    """Case B. A DIFFERENT fault arrived before the drain. Re-reading hub.state
    toasted CAMERA_DOWN 0 seconds after it began -- the exact "toast for a blip
    shorter than the debounce" this design exists to prevent -- and then again
    30s later, because _notified had been latched to the OLD state."""
    from webcam_client.status import Fault, Health, CONTROL_SOURCE, NOTIFY_DEBOUNCE_SECONDS

    hub, q, clock, toasts, drain = _matured_no_server(monkeypatch)
    # Report the camera fault FIRST, while NO_SERVER still outranks it, so
    # clearing the server fault steps straight from NO_SERVER to CAMERA_DOWN
    # without passing through RUNNING -- one degradation, no recovery in it.
    hub.report("n1", Fault.CAMERA_DOWN)
    assert hub.state is Health.NO_SERVER, "precondition: NO_SERVER still outranks"
    hub.report(CONTROL_SOURCE, Fault.NONE)      # now CAMERA_DOWN, 0s old
    assert hub.state is Health.CAMERA_DOWN
    drain()

    assert toasts == [Health.NO_SERVER], (
        f"CAMERA_DOWN started 0s ago and must not be toasted yet: {toasts}")

    hub.tick()                                   # still inside its own debounce
    drain()
    assert toasts == [Health.NO_SERVER], "still inside CAMERA_DOWN's debounce"

    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    drain()
    assert toasts == [Health.NO_SERVER, Health.CAMERA_DOWN], \
        f"CAMERA_DOWN must be announced exactly once, on its own clock: {toasts}"


def test_a_matured_notification_survives_an_uneventful_drain(monkeypatch):
    """Case C, the control: nothing changes between fire and drain, so the
    behaviour is unchanged. Guards against a fix that breaks the common path."""
    from webcam_client.status import Health, NOTIFY_DEBOUNCE_SECONDS

    hub, q, clock, toasts, drain = _matured_no_server(monkeypatch)
    drain()
    assert toasts == [Health.NO_SERVER]

    for _ in range(3):                           # ordinary idle ticks after it
        clock.advance(NOTIFY_DEBOUNCE_SECONDS)
        hub.tick()
        drain()
    assert toasts == [Health.NO_SERVER], \
        f"a stable fault must be announced once, not forever: {toasts}"


def test_camera_display_names_maps_node_ids_to_names():
    """hub.faulty_sources() speaks in node_ids; the guard speaks in the camera
    names they typed in the settings window."""
    import webcam_client.main as m

    cams = [{"node_id": "n1", "name": "前門攝影機", "device_index": 0},
            {"node_id": "n2", "device_index": 3}]
    assert m._camera_display_names(["n1"], cams) == ["前門攝影機"]
    # A camera saved without a name falls back to the same default the wizard
    # shows the operator, so the two windows agree on what it is called.
    assert m._camera_display_names(["n2"], cams) == ["Webcam 3"]
    assert m._camera_display_names(["n1", "n2"], cams) == ["前門攝影機", "Webcam 3"]


def test_control_source_never_reaches_the_operator():
    """hub.faulty_sources() returns raw KEYS -- node_ids plus the literal
    "__control__" -- while build_status_lines takes DISPLAY names and joins
    them into a Chinese sentence. Passing the keys straight through would put
    "__control__" in front of a security guard.

    The control channel is not a camera, so it is dropped here. Nothing is lost:
    control_channel.py only ever reports NO_SERVER or BAD_KEY, both of which
    outrank CAMERA_DOWN in the hub's precedence, and CAMERA_DOWN's text is the
    only one that interpolates camera names at all."""
    import webcam_client.main as m
    from webcam_client.status import CONTROL_SOURCE, Health
    from webcam_client.gui.status_window import build_status_lines

    cams = [{"node_id": "n1", "name": "前門攝影機", "device_index": 0}]
    names = m._camera_display_names([CONTROL_SOURCE, "n1"], cams)
    assert CONTROL_SOURCE not in names
    assert names == ["前門攝影機"]

    for state in Health:
        joined = " ".join(build_status_lines(state, 1, names))
        assert "__control__" not in joined
        assert "_" not in joined, f"an internal token leaked: {joined!r}"

    # ...and when the control channel is the ONLY thing failing, the guard gets
    # a clean sentence rather than an empty subject.
    alone = m._camera_display_names([CONTROL_SOURCE], cams)
    assert alone == []
    for state in Health:
        joined = " ".join(build_status_lines(state, 1, alone))
        assert "__control__" not in joined and "_" not in joined


def test_a_fault_source_with_no_camera_is_never_shown_raw():
    """node_ids are server-side identifiers. If a fault arrives for a camera
    that is no longer in the config (a settings edit racing an in-flight
    worker), drop it -- never print the id at a guard."""
    import webcam_client.main as m

    names = m._camera_display_names(
        ["8f3c9a12-not-in-config"],
        [{"node_id": "n1", "name": "前門攝影機", "device_index": 0}])
    assert names == []


# --------------------------------------------------------------------------
# F-1: the camera-down toast never said WHICH camera.
#
# notify_state() built the toast from describe(state) with no context, so
# camera_down's "{camera_names}目前沒有畫面。" fell back to strings.py's generic
# "攝影機" default. On a multi-camera site the guard learned SOME camera was
# down without learning which -- the one fact that decides what they
# physically go and check. The status window already resolved this correctly
# via main._camera_display_names(); the toast needed the same names threaded
# through to notify_state().
# --------------------------------------------------------------------------

class _NameClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, secs):
        self.now += secs


def test_camera_down_toast_names_the_specific_camera_that_is_down(monkeypatch):
    """Drives the REAL dispatch loop end to end -- real StatusHub, real
    notify_state(), real strings.describe() -- so this proves the actual toast
    TEXT names the camera, not merely that some list of names reached a mock.

    Camera names are captured at NOTIFY-drain time (hub.faulty_sources(),
    called from the dispatch loop, never from inside StatusHub's on_notify
    callback -- that callback fires while the hub still holds its own
    non-reentrant lock, and faulty_sources() takes that same lock, so calling
    it from inside the callback would deadlock the thread that is supposed to
    announce the fault). This is deliberately NOT the same treatment `state`
    gets: state rides the token because tick() commits to it once, at fire
    time, and nothing can ever recover which state matured after the fact.
    Camera names have no such one-shot commitment in status.py (which this
    lane does not own) -- hub.faulty_sources() stays queryable at any time --
    so reading it fresh at drain time answers the more useful question for a
    toast that can land seconds (or, behind a blocked status window, minutes)
    after the fault matured: which camera needs the guard's attention RIGHT
    NOW, not which one the debounce math happened to be timing 30s earlier."""
    import webcam_client.main as m
    from webcam_client.status import Fault, NOTIFY_DEBOUNCE_SECONDS

    clock = _NameClock()
    made = {}
    real_hub_cls = m.StatusHub

    def hub_factory(**kw):
        made["hub"] = real_hub_cls(clock=clock, **kw)
        return made["hub"]

    class FakeIcon:
        def __init__(self):
            self.calls = []

        def notify(self, message, title=None):
            self.calls.append((title, message))

    fake_icon = FakeIcon()
    tray_kw = {}

    class FakeTray:
        icon = fake_icon

        def __init__(self, **kw):
            tray_kw.update(kw)

        def start(self):
            pass

        def set_health(self, state):
            pass

        def stop(self):
            pass

    cams = [{"node_id": "n1", "name": "前門攝影機", "device_index": 0, "enabled": True},
            {"node_id": "n2", "name": "後門攝影機", "device_index": 1, "enabled": True}]

    def a_camera_fails():
        # Only the BACK door camera goes dark; the front door is fine.
        made["hub"].report("n2", Fault.CAMERA_DOWN)
        clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
        made["hub"].tick()          # matures the toast synchronously, no real sleep
        tray_kw["on_quit"]()        # let the loop drain HEALTH, NOTIFY, then QUIT

    _boot(monkeypatch, m, FakeTray, cameras=cams, on_start_engines=a_camera_fails)
    monkeypatch.setattr(m, "StatusHub", hub_factory)

    m.main()

    assert fake_icon.calls, "no toast reached the icon"
    _, message = fake_icon.calls[-1]
    assert "後門攝影機" in message, (
        f"the toast must name the camera that is actually down: {message!r}")
    assert "前門攝影機" not in message, "must not blame the camera that is fine"
    assert "n2" not in message and "__control__" not in message, (
        f"an internal identifier leaked into operator-facing text: {message!r}")


def test_enabled_cameras_are_read_from_the_live_config():
    """The count in the window must come from the CURRENT config, not from the
    list captured at startup: after a settings edit the startup list is stale
    and the window would tell the guard the wrong number of cameras."""
    import webcam_client.main as m

    cfg = {"cameras": [{"node_id": "a", "enabled": True},
                       {"node_id": "b", "enabled": False},
                       {"node_id": "c"}]}
    assert [c["node_id"] for c in m.enabled_cameras(cfg)] == ["a", "c"]
    assert m.enabled_cameras({}) == []
    assert m.enabled_cameras(None) == []


def test_main_and_the_controller_share_one_enabled_cameras_filter():
    """m-1: this filter existed twice -- one copy decided which cameras RUN,
    the other decided what the guard is TOLD. They agreed, but nothing made
    them, so any divergence would make the status window lie about how many
    cameras are being watched. Identity, not equality: two functions that
    merely happen to behave the same today is the bug."""
    import webcam_client.main as m
    from webcam_client import app_controller

    assert m.enabled_cameras is app_controller.enabled_cameras
    assert not hasattr(m, "_enabled_cameras"), \
        "main must not keep a private second copy of the filter"

    cfg = {"cameras": [{"node_id": "a", "enabled": True},
                       {"node_id": "b", "enabled": False}]}
    ctrl = app_controller.AppController(cfg, engine_factory=lambda *a, **k: None,
                                        control_factory=lambda *a, **k: None)
    assert ctrl._enabled_cameras() == app_controller.enabled_cameras(cfg)


def test_tray_open_status_menu_reaches_the_status_window(monkeypatch):
    """Task 4 gave TrayApp.on_open_status a no-op default and NO test exercised
    the menu/double-click path. Had main() forgotten to pass a real callback,
    the status window would have been unreachable -- no error, no log line, a
    silently dead feature. This drives the whole path: the tray callback (fired
    by pystray on ITS OWN daemon thread) may only enqueue, and the MAIN loop is
    what opens the window."""
    import webcam_client.main as m

    opened = []
    monkeypatch.setattr(m, "open_status_window",
                        lambda state, **kw: opened.append((state, kw)))

    class FakeTray:
        icon = None

        def __init__(self, **kw):
            self._kw = kw

        def start(self):
            # the guard clicking 監控狀態 in the tray menu, then 離開
            self._kw["on_open_status"]()
            self._kw["on_quit"]()

        def set_health(self, state):
            pass

        def stop(self):
            pass

    _boot(monkeypatch, m, FakeTray)
    m.main()

    assert opened, "the tray's 監控狀態 callback never reached open_status_window"
    _, kw = opened[0]
    assert kw["camera_count"] == 1
    assert kw["faulty_names"] == []
    for name in ("on_open_logs", "on_reconnect", "on_settings"):
        assert callable(kw[name]), f"the {name} button is wired to nothing"


def test_status_window_reconnect_button_rebuilds_engines(monkeypatch):
    """重新連線 must be the same in-process rebuild the OPEN_SETTINGS error path
    uses -- not a restart the guard has to perform by hand."""
    import webcam_client.main as m

    captured = {}
    monkeypatch.setattr(m, "open_status_window",
                        lambda state, **kw: captured.update(kw))

    class FakeTray:
        icon = None

        def __init__(self, **kw):
            self._kw = kw

        def start(self):
            self._kw["on_open_status"]()
            self._kw["on_quit"]()

        def set_health(self, state):
            pass

        def stop(self):
            pass

    made = _boot(monkeypatch, m, FakeTray)
    m.main()
    captured["on_reconnect"]()
    assert made["ctrl"].applied == [made["ctrl"].config]


# --------------------------------------------------------------------------
# NIT: hub=None defaults on _handle_request() and _rebuild_engines() let a
# future call site silently drop the STARTING-forever backstop -- no error,
# no log line, just a tray that can now sit on 啟動中 forever after a failed
# rebuild, exactly like before this whole phase existed. The sole production
# call site (main()'s dispatch loop, and main()'s two start_engines() guards)
# always passes a real hub; only tests legitimately have no hub to give. So
# the default is removed -- hub becomes REQUIRED -- and every test call site
# that does not care about the hub-dependent branch now says so explicitly
# (hub=None), rather than getting there by omission.
# --------------------------------------------------------------------------

def test_rebuild_engines_requires_hub_explicitly():
    """A call site that forgets hub must fail loudly at once, not silently
    keep the STARTING-forever backstop switched off."""
    import pytest
    import webcam_client.main as m

    class Ctrl:
        config = {"cameras": []}

        def apply(self, cfg):
            pass

    with pytest.raises(TypeError):
        m._rebuild_engines(Ctrl())


def test_handle_request_requires_hub_explicitly():
    import pytest
    import webcam_client.main as m

    ctrl = FakeController({})

    with pytest.raises(TypeError):
        m._handle_request("QUIT", ctrl, lambda cfg: None)


def test_a_failed_rebuild_never_leaves_the_light_claiming_startup():
    """apply() is stop_engines() + start_engines(), and stop_engines() ends in
    hub.clear_all() -- which resets the hub to STARTING. If start_engines()
    then raises, nothing is left alive to report a fault (the control channel
    is built LAST, so it never gets built at all): the hub stays on STARTING
    for the rest of the process. Grey tray, "啟動中 / 正在連線並開啟攝影機，
    請稍候。" forever, and no toast either, because tick() never announces a
    healthy state. The guard reaches that dead end by pressing 重新連線 -- the
    button they press to FIX things.

    With an empty camera list this is the ONE case where NO_SERVER is the honest
    report (see _report_rebuild_failure): nothing is running and there is no
    camera to blame."""
    import webcam_client.main as m
    from webcam_client.status import StatusHub, Health

    hub = StatusHub()
    attempts = []

    class ExplodingCtrl:
        config = {"cameras": []}

        def apply(self, cfg):
            attempts.append(cfg)
            hub.clear_all()          # stop_engines() got this far...
            raise RuntimeError("camera 0 will not open")

        def running_node_ids(self):
            return []

    assert m._rebuild_engines(ExplodingCtrl(), hub) is False
    assert len(attempts) == 2, "a failed rebuild must be retried once"
    assert hub.state is not Health.STARTING, \
        "the tray would sit grey on 啟動中 forever, with no toast to correct it"


# --------------------------------------------------------------------------
# Finding #18: a PARTIALLY failed rebuild used to latch a permanent, false
# 「無法連線到伺服器」 that nothing alive could ever clear.
#
# start_engines() publishes each engine the moment it starts (deliberately, so a
# later failure cannot leak an open camera) and builds the ControlChannel LAST.
# So when camera 2 refuses to start, camera 1 is alive and uploading and there is
# no control channel at all. Writing CONTROL_SOURCE/NO_SERVER then put a fault on
# a key NOTHING alive owns -- no control channel exists to report Fault.NONE for
# it -- and NO_SERVER outranks CAMERA_DOWN, so the guard read 「無法連線到伺服器 /
# 請檢查電腦後方的網路線」 forever while camera 1's snapshots landed on the server
# perfectly well. Only another clear_all() (a settings save, or pressing 重新連線
# again) could shift it.
#
# The backstop now reports what is actually true, per camera: the cameras left
# with NO worker have no picture, and CAMERA_DOWN is the fault whose text says so
# and whose action -- re-seat the USB cable, then press 重新連線 -- is what makes
# the fix take. It is also the LOWEST fault in _PRECEDENCE, so a stale one masks
# nothing, which is the whole difference from the NO_SERVER it replaces.
# --------------------------------------------------------------------------

_TWO_CAMS = [
    {"node_id": "n1", "name": "前門攝影機", "device_index": 0, "enabled": True},
    {"node_id": "n2", "name": "後門攝影機", "device_index": 1, "enabled": True},
]


def test_a_partially_failed_rebuild_names_only_the_cameras_with_no_worker():
    import webcam_client.main as m
    from webcam_client.status import StatusHub, Fault, Health, CONTROL_SOURCE

    hub = StatusHub()

    class HalfBuiltCtrl:
        config = {"cameras": list(_TWO_CAMS)}

        def apply(self, cfg):
            hub.clear_all()               # stop_engines() got this far...
            hub.report("n1", Fault.NONE)  # ...camera 1 came up and is uploading
            raise RuntimeError("camera 2 will not start")

        def running_node_ids(self):
            return ["n1"]

    ctrl = HalfBuiltCtrl()
    assert m._rebuild_engines(ctrl, hub) is False

    assert CONTROL_SOURCE not in hub.faulty_sources(), (
        "the backstop wrote a fault under a key no live worker owns: NO_SERVER "
        "outranks every camera fault, so the guard is told the network is down "
        "while camera 1 uploads fine, and nothing alive can ever clear it")
    assert hub.faulty_sources() == ["n2"], (
        f"only the camera with no worker has no picture: {hub.faulty_sources()}")
    assert hub.state is Health.CAMERA_DOWN

    # ...and the guard is told WHICH camera, by the name they typed in settings.
    names = m._camera_display_names(hub.faulty_sources(),
                                    m.enabled_cameras(ctrl.config))
    assert names == ["後門攝影機"]


def test_a_working_camera_keeps_uploading_after_a_failed_rebuild():
    """The other half of the same judgement: the engine that IS working is left
    alone. Stopping it would make the old NO_SERVER message true, at the price of
    taking a camera that was recording perfectly well off the air."""
    import webcam_client.main as m
    from webcam_client.status import StatusHub, Fault, Health

    hub = StatusHub()
    stopped = []

    class HalfBuiltCtrl:
        config = {"cameras": list(_TWO_CAMS)}

        def apply(self, cfg):
            hub.clear_all()
            hub.report("n1", Fault.NONE)
            raise RuntimeError("camera 2 will not start")

        def stop_engines(self):
            stopped.append(True)

        def running_node_ids(self):
            return ["n1"]

    m._rebuild_engines(HalfBuiltCtrl(), hub)

    assert stopped == [], "the surviving camera must not be taken off the air"
    # n1 goes on reporting, and its healthy reports are not drowned out.
    hub.report("n1", Fault.NONE)
    assert hub.state is Health.CAMERA_DOWN, "n2 still has no worker"
    assert "n1" not in hub.faulty_sources()


def test_a_total_rebuild_failure_names_every_camera():
    """Nothing came up at all: every enabled camera is without a picture, so
    every one of them is named. Honest, and still not a network message."""
    import webcam_client.main as m
    from webcam_client.status import StatusHub, Health

    hub = StatusHub()

    class DeadCtrl:
        config = {"cameras": list(_TWO_CAMS)}

        def apply(self, cfg):
            hub.clear_all()
            raise RuntimeError("nothing will start")

        def running_node_ids(self):
            return []

    assert m._rebuild_engines(DeadCtrl(), hub) is False
    assert hub.faulty_sources() == ["n1", "n2"]
    assert hub.state is Health.CAMERA_DOWN
    assert m._camera_display_names(hub.faulty_sources(),
                                   m.enabled_cameras(DeadCtrl.config)) == \
        ["前門攝影機", "後門攝影機"]


def test_a_rebuild_that_lost_only_the_command_channel_invents_no_fault(caplog):
    """The one failure mode where every enabled camera DOES have a worker: the
    engines all started and the ControlChannel was what raised (it is built
    last). Every camera has a picture and is uploading, so any fault at all
    would be a lie -- CAMERA_DOWN about cameras that are fine, NO_SERVER about an
    uplink that is carrying snapshots. The engines report the truth by
    themselves; the missing command channel (no live view) has no word in the
    frozen Fault vocabulary, so it goes to the technician's log instead."""
    import logging
    import webcam_client.main as m
    from webcam_client.status import StatusHub, Fault, Health

    hub = StatusHub()

    class NoChannelCtrl:
        config = {"cameras": list(_TWO_CAMS)}

        def apply(self, cfg):
            hub.clear_all()
            hub.report("n1", Fault.NONE)
            hub.report("n2", Fault.NONE)
            raise RuntimeError("control channel would not start")

        def running_node_ids(self):
            return ["n1", "n2"]

    with caplog.at_level(logging.WARNING, logger="webcam_client.main"):
        assert m._rebuild_engines(NoChannelCtrl(), hub) is False

    assert hub.faulty_sources() == [], (
        f"both cameras are uploading; any fault here is a lie: "
        f"{hub.faulty_sources()}")
    assert hub.state is Health.RUNNING
    assert [r for r in caplog.records if r.levelno >= logging.WARNING], \
        "a rebuild failure that cannot be shown to the guard must still be logged"


def test_the_failed_rebuild_backstop_never_raises():
    """_rebuild_engines() documents that it never raises, and three call sites
    depend on it -- including startup, where an escaping exception leaves main()
    and the traceback goes to a sys.stderr that is None in the windowed build."""
    import webcam_client.main as m
    from webcam_client.status import StatusHub

    hub = StatusHub()

    class BrokenCtrl:
        config = {"cameras": list(_TWO_CAMS)}

        def apply(self, cfg):
            raise RuntimeError("no")

        def running_node_ids(self):
            raise RuntimeError("and the backstop's own lookup is broken too")

    assert m._rebuild_engines(BrokenCtrl(), hub) is False


def test_a_successful_rebuild_leaves_the_hub_alone():
    """The backstop above must not invent a fault on the happy path."""
    import webcam_client.main as m
    from webcam_client.status import StatusHub, Health

    hub = StatusHub()

    class Ctrl:
        config = {"cameras": []}

        def apply(self, cfg): pass

    assert m._rebuild_engines(Ctrl(), hub) is True
    assert hub.state is Health.STARTING
    assert hub.faulty_sources() == []


def test_a_second_attempt_recovers_without_reporting_a_fault():
    import webcam_client.main as m
    from webcam_client.status import StatusHub, Health

    hub = StatusHub()
    calls = []

    class FlakyCtrl:
        config = {"cameras": []}

        def apply(self, cfg):
            calls.append(cfg)
            if len(calls) == 1:
                hub.clear_all()
                raise RuntimeError("device busy")

    assert m._rebuild_engines(FlakyCtrl(), hub) is True
    assert hub.state is Health.STARTING and hub.faulty_sources() == []


def test_the_reconnect_button_cannot_strand_the_guard(monkeypatch):
    """End to end through the callback main() actually hands the window: a bare
    controller.apply() there is swallowed by the window's _guarded() wrapper,
    so the failure is invisible AND the light lies. Drive the real callback."""
    import webcam_client.main as m
    from webcam_client.status import Health

    captured = {}
    monkeypatch.setattr(m, "open_status_window",
                        lambda state, **kw: captured.update(kw))
    made_hub = {}
    real_hub_cls = m.StatusHub

    def hub_factory(**kw):
        made_hub["hub"] = real_hub_cls(**kw)
        return made_hub["hub"]

    monkeypatch.setattr(m, "StatusHub", hub_factory)

    class FakeTray:
        icon = None

        def __init__(self, **kw):
            self._kw = kw

        def start(self):
            self._kw["on_open_status"]()
            self._kw["on_quit"]()

        def set_health(self, state):
            pass

        def stop(self):
            pass

    made = _boot(monkeypatch, m, FakeTray)
    m.main()

    def exploding_apply(cfg):
        made_hub["hub"].clear_all()      # stop_engines() already ran
        raise RuntimeError("camera 0 will not open")

    made["ctrl"].apply = exploding_apply
    captured["on_reconnect"]()           # must not raise
    assert made_hub["hub"].state is not Health.STARTING


def test_a_failed_startup_never_leaves_the_light_claiming_startup(monkeypatch):
    """m-3: startup's start_engines() was the one call site with no backstop.

    Both rebuild paths already report CONTROL_SOURCE/NO_SERVER when they cannot
    get engines running, so the light stops claiming the app is still starting.
    Unguarded at startup, an exception leaves main() entirely: the traceback
    goes to a sys.stderr that is None in the windowed build, so the guard sees
    the tray icon appear and then vanish, with no log line and no dialog."""
    import webcam_client.main as m
    from webcam_client.status import Health

    made_hub = {}
    real_hub_cls = m.StatusHub

    def hub_factory(**kw):
        made_hub["hub"] = real_hub_cls(**kw)
        return made_hub["hub"]

    class FakeTray:
        icon = None

        def __init__(self, **kw): pass
        def start(self): pass
        def set_health(self, state): pass
        def stop(self): pass

    class ExplodingCtrl:
        def __init__(self, cfg, **kw):
            self._config = cfg

        @property
        def config(self):
            return self._config

        def start_engines(self):
            raise RuntimeError("camera 0 will not open")

        def apply(self, cfg):
            raise RuntimeError("camera 0 still will not open")

        def running_node_ids(self): return []
        def stop_engines(self): pass
        def shutdown(self): pass
        def pause_all(self): pass
        def resume_all(self): pass

    _boot(monkeypatch, m, FakeTray)
    monkeypatch.setattr(m, "StatusHub", hub_factory)
    monkeypatch.setattr(m, "AppController", ExplodingCtrl)
    monkeypatch.setattr(m, "_running", False)   # skip the dispatch loop

    m.main()      # must not raise: the exception must never leave main()

    assert made_hub["hub"].state is not Health.STARTING, \
        "the tray would sit grey on 啟動中 forever, with no toast to correct it"


def test_a_successful_startup_does_not_invent_a_fault(monkeypatch):
    """The m-3 backstop must not fire on the happy path."""
    import webcam_client.main as m
    from webcam_client.status import Health

    made_hub = {}
    real_hub_cls = m.StatusHub

    def hub_factory(**kw):
        made_hub["hub"] = real_hub_cls(**kw)
        return made_hub["hub"]

    class FakeTray:
        icon = None

        def __init__(self, **kw): pass
        def start(self): pass
        def set_health(self, state): pass
        def stop(self): pass

    _boot(monkeypatch, m, FakeTray)
    monkeypatch.setattr(m, "StatusHub", hub_factory)
    monkeypatch.setattr(m, "_running", False)

    m.main()
    assert made_hub["hub"].faulty_sources() == []
    assert made_hub["hub"].state is Health.STARTING


def test_open_settings_recovery_failure_also_reports_to_the_hub():
    """The OPEN_SETTINGS error path strands the hub the same way (it too ends
    in stop_engines() -> clear_all()), so it shares the same helper."""
    import webcam_client.main as m
    from webcam_client.status import StatusHub, Health

    hub = StatusHub()

    class ExplodingCtrl:
        config = {"server_url": "old"}

        def stop_engines(self): hub.clear_all()
        def apply(self, cfg): raise RuntimeError("still broken")
        def running_node_ids(self): return []

    def raising_fn(cfg):
        raise RuntimeError("boom: settings window blew up")

    keep = m._handle_request("OPEN_SETTINGS", ExplodingCtrl(), raising_fn, hub)
    assert keep is True, "the dispatch loop must survive"
    assert hub.state is not Health.STARTING


def test_a_worker_fault_repaints_the_tray_from_the_main_loop(monkeypatch):
    """End to end: the hub's on_change fires INSIDE the hub's lock on the
    WORKER's thread, so it may only enqueue -- the repaint has to happen on the
    dispatch loop. Drops the fault after startup's first paint so the assertion
    cannot be satisfied by that initial set_health() alone."""
    import webcam_client.main as m
    from webcam_client.status import Fault, Health

    made = {}
    real_hub_cls = m.StatusHub

    def hub_factory(**kw):
        made["hub"] = real_hub_cls(**kw)
        return made["hub"]

    monkeypatch.setattr(m, "StatusHub", hub_factory)
    painted = []
    tray_kw = {}

    class FakeTray:
        icon = None

        def __init__(self, **kw):
            tray_kw.update(kw)

        def start(self):
            pass

        def set_health(self, state):
            painted.append(state)

        def stop(self):
            pass

    def a_camera_fails():
        # start_engines() runs AFTER the tray's first paint, which is exactly
        # where a real worker would first report.
        made["hub"].report("n1", Fault.CAMERA_DOWN)
        tray_kw["on_quit"]()

    _boot(monkeypatch, m, FakeTray, on_start_engines=a_camera_fails)
    m.main()

    assert painted == [Health.STARTING, Health.CAMERA_DOWN], \
        f"the loop did not repaint the tray from the hub: {painted}"


def test_the_idle_loop_ticks_the_hub(monkeypatch):
    """hub.tick() is what promotes a sustained fault into a toast, and NOTHING
    else in the dispatch loop calls it -- there is no timer thread. If the
    queue.Empty branch drops it, every notification in the app silently stops
    working.

    #19: the previous version asserted only that tick() had been called at least
    once, and it ended the loop from inside tick() ITSELF. Two holes followed.
    Deleting hub.tick() from the Empty branch made this test HANG rather than
    fail, because nothing was then left to stop the loop -- and a hung run is not
    a red test. And a tick HOISTED out of the loop (moved above the `while`) still
    satisfied it, so the one thing the name promises -- that the LOOP ticks, every
    idle pass -- was never pinned at all.

    So: two ticks are required, and the loop is ended from a separate thread, so
    a missing tick fails an assertion instead of hanging."""
    import threading
    import webcam_client.main as m
    from webcam_client.status import Health

    ticks = []
    ticked_twice = threading.Event()

    class FakeHub:
        def __init__(self, **kw):
            self.state = Health.STARTING

        def faulty_sources(self):
            return []

        def report(self, source, fault): pass
        def set_paused(self, paused): pass
        def clear_all(self): pass

        def tick(self):
            ticks.append(True)
            if len(ticks) >= 2:
                ticked_twice.set()

    painted = []

    class FakeTray:
        icon = None

        def __init__(self, **kw): pass
        def start(self): pass
        def set_health(self, state): painted.append(state)
        def stop(self): pass

    def stopper():
        # Ends the loop from OUTSIDE it. The ceiling is generous (this needs two
        # 1-second idle waits) and only ever reached when the loop is not
        # ticking, which is the case that must fail loudly rather than hang.
        ticked_twice.wait(timeout=15)
        m._running = False          # restored by _boot's monkeypatch.setattr

    monkeypatch.setattr(m, "StatusHub", FakeHub)
    _boot(monkeypatch, m, FakeTray)
    stop_thread = threading.Thread(target=stopper)
    stop_thread.start()
    m.main()
    stop_thread.join(timeout=5)

    assert len(ticks) >= 2, (
        f"the dispatch loop must tick the hub on every idle pass, not once "
        f"before the loop begins: {len(ticks)} tick(s)")
    assert painted == [Health.STARTING], (
        f"nothing was ever enqueued, so those ticks can only have come from the "
        f"queue.Empty branch rather than from a drained token: {painted}")


def test_hub_callbacks_only_enqueue(monkeypatch):
    """The hub invokes on_change/on_notify while HOLDING its non-reentrant lock,
    from worker threads. Anything more than a q.put() there re-enters the hub or
    touches Tk/pystray off the main thread -- the exact bug class this design
    exists to prevent.

    #19: this used to assert only that the callbacks enqueued the RIGHT TOKEN, so
    a callback that ALSO called tray.set_health(...) -- the precise violation the
    sentence above names -- sailed through. It now proves ONLY. Every route out of
    a callback is tripwired: the hub (re-entry), the tray and the two window/toast
    entry points (Tk off the main thread), and the queue records ALL of its own
    calls rather than just puts, so a second queue interaction shows up too.

    The tripwires are proved live before they are trusted: main()'s own startup
    trips several of them, and that is asserted as a precondition. Residual, and
    stated rather than papered over -- this cannot prove the absence of an
    arbitrary side effect (a file write, a socket). It pins the three classes the
    design names."""
    import queue
    import webcam_client.main as m
    from webcam_client.status import Health

    captured = {}
    trips = []
    q_calls = []

    class FakeHub:
        def __init__(self, **kw):
            captured.update(kw)
            self._state = Health.STARTING

        @property
        def state(self):
            trips.append("hub.state")
            return self._state

        def faulty_sources(self):
            trips.append("hub.faulty_sources")
            return []

        def report(self, source, fault): trips.append("hub.report")
        def set_paused(self, paused): trips.append("hub.set_paused")
        def clear_all(self): trips.append("hub.clear_all")

        def tick(self):
            trips.append("hub.tick")
            m._running = False

    class FakeTray:
        icon = None

        def __init__(self, **kw): pass
        def start(self): trips.append("tray.start")
        def set_health(self, state): trips.append("tray.set_health")
        def stop(self): trips.append("tray.stop")

    class RecordingQueue(queue.Queue):
        """Records every call, not only put(): a callback that DRAINS the queue,
        or puts twice, is as much a violation as one that touches Tk."""

        def put(self, item, *a, **kw):
            q_calls.append(("put", item))
            super().put(item, *a, **kw)

        def get(self, *a, **kw):
            q_calls.append(("get", None))
            return super().get(*a, **kw)

        def get_nowait(self, *a, **kw):
            q_calls.append(("get_nowait", None))
            return super().get_nowait(*a, **kw)

    monkeypatch.setattr(m.queue, "Queue", RecordingQueue)
    monkeypatch.setattr(m, "StatusHub", FakeHub)
    monkeypatch.setattr(m, "notify_state",
                        lambda icon, state, camera_names=None: trips.append("notify_state"))
    monkeypatch.setattr(m, "open_status_window",
                        lambda state, **kw: trips.append("open_status_window"))
    monkeypatch.setattr(m, "run_setup_wizard",
                        lambda cfg, **kw: trips.append("run_setup_wizard"))
    _boot(monkeypatch, m, FakeTray)
    m.main()

    assert callable(captured.get("on_change")) and callable(captured.get("on_notify"))
    # The instruments must be live, or "trips == []" below proves nothing.
    assert "tray.set_health" in trips and "hub.state" in trips, \
        f"the tripwires never fired during main(), so they cannot be trusted: {trips}"
    assert any(kind == "get" for kind, _ in q_calls), \
        "the queue recorder never saw the loop's own get()"

    for name, token in (("on_change", "HEALTH"),
                        ("on_notify", ("NOTIFY", Health.BAD_KEY))):
        del trips[:]
        del q_calls[:]
        captured[name](Health.BAD_KEY)
        assert q_calls == [("put", token)], (
            f"{name} must make exactly ONE queue call, a put of {token!r}: "
            f"{q_calls}")
        assert trips == [], (
            f"{name} did more than enqueue -- it reached {trips}. That runs on a "
            f"WORKER thread inside the hub's own non-reentrant lock: a hub call "
            f"deadlocks or reads state the hub is midway through changing, and a "
            f"tray/Tk call paints from the wrong thread.")
    # I-2: on_notify still ONLY enqueues -- the load-bearing invariant is
    # untouched -- but its token CARRIES the state, because the state that
    # matured cannot be recovered at drain time (see _handle_notify).


# --------------------------------------------------------------------------
# The status window no longer freezes the dispatch loop.
#
# It used to call root.mainloop() and render hub.state BY VALUE, so the one
# screen built to tell the guard the truth was the only screen structurally
# guaranteed to lie: open it at 09:00, the switch dies at 09:01, and at 09:35
# it still reads 監控中 with the tray light still green and no toast fired,
# because tick() could not run either. _status_pump is the loop's work, done
# from the window's own Tk timer on the same thread.
# --------------------------------------------------------------------------

from webcam_client.status import Health          # module level: _pump_ctx defaults to it


class _PumpHub:
    """Minimal hub for the pump: records ticks, lets a test set the state."""

    def __init__(self, state=None):
        self.state = state if state is not None else Health.STARTING
        self.ticks = 0

    def faulty_sources(self):
        return []

    def tick(self):
        self.ticks += 1


class _PumpTray:
    def __init__(self):
        self.icon = object()
        self.painted = []

    def set_health(self, state):
        self.painted.append(state)


def _pump_ctx(state=Health.STARTING):
    import queue as _q
    hub = _PumpHub(state)
    tray = _PumpTray()
    controller = FakeController({"cameras": [
        {"device_index": 0, "name": "前門", "enabled": True, "node_id": "n1"}]})
    return hub, tray, controller, _q.Queue()


def test_the_status_window_repaints_the_tray_while_it_is_open(monkeypatch):
    """A HEALTH token that arrived while the window was open used to sit in the
    queue until the guard closed it -- so the tray light stayed green for as
    long as they kept the window up looking at why it was green."""
    import webcam_client.main as m
    hub, tray, controller, q = _pump_ctx()

    hub.state = Health.NO_SERVER
    q.put("HEALTH")
    result = m._status_pump(hub, tray, controller, q)

    assert tray.painted == [Health.NO_SERVER], \
        f"the tray must repaint from the hub while the window is open: {tray.painted}"
    assert result["close"] is False


def test_the_status_window_lets_a_matured_toast_fire(monkeypatch):
    """hub.tick() is what promotes a sustained fault into a toast, and it only
    ran in the loop's queue.Empty branch -- which the window's mainloop made
    unreachable. Half an hour of outage produced no notification at all."""
    import webcam_client.main as m
    hub, tray, controller, q = _pump_ctx()

    m._status_pump(hub, tray, controller, q)
    m._status_pump(hub, tray, controller, q)

    assert hub.ticks == 2, \
        f"the hub must keep maturing notifications while the window is open: {hub.ticks}"


def test_the_status_window_toasts_the_state_that_matured(monkeypatch):
    """NOTIFY carries its state for the reason _handle_notify documents, and
    the pump must honour that rather than re-reading hub.state."""
    import webcam_client.main as m
    hub, tray, controller, q = _pump_ctx()
    toasted = []
    monkeypatch.setattr(m, "notify_state",
                        lambda icon, state, camera_names=None: toasted.append(state))

    hub.state = Health.RUNNING                  # recovery already happened
    q.put(("NOTIFY", Health.NO_SERVER))         # the outage matured earlier
    m._status_pump(hub, tray, controller, q)

    assert toasted == [Health.NO_SERVER], \
        f"the toast must announce what matured, not what is true now: {toasted}"


def test_quitting_from_the_tray_closes_the_window_and_still_quits():
    """THE unquittable-app bug. TrayApp._quit() stops the pystray icon and then
    enqueues QUIT -- but the queue could not be drained behind the window's
    mainloop. The icon vanished, the process kept uploading, ALREADY_RUNNING
    pointed the guard at an icon that no longer existed, and only Task Manager
    could end it.

    The pump must NOT shut down itself: there is one implementation of QUIT,
    in _handle_request. It closes the window and puts the token back."""
    import webcam_client.main as m
    hub, tray, controller, q = _pump_ctx()

    q.put("QUIT")
    result = m._status_pump(hub, tray, controller, q)

    assert result["close"] is True, "QUIT must close the status window"
    assert q.get_nowait() == "QUIT", \
        "the token must go back on the queue so the main loop performs the shutdown"


def test_opening_settings_from_the_tray_closes_the_status_window():
    """Same contract as QUIT: the wizard needs this window gone (it re-opens
    the cameras), and _handle_request already owns that sequence."""
    import webcam_client.main as m
    hub, tray, controller, q = _pump_ctx()

    q.put("OPEN_SETTINGS")
    result = m._status_pump(hub, tray, controller, q)

    assert result["close"] is True
    assert q.get_nowait() == "OPEN_SETTINGS"


def test_the_pump_hands_back_the_current_state_not_the_open_time_snapshot():
    """The window renders from this, so it is the half of the fix that stops
    the text going stale."""
    import webcam_client.main as m
    hub, tray, controller, q = _pump_ctx(Health.RUNNING)

    hub.state = Health.CAMERA_DOWN
    result = m._status_pump(hub, tray, controller, q)

    assert result["state"] is Health.CAMERA_DOWN
    assert result["camera_count"] == 1


def test_a_redundant_open_status_does_not_reopen_the_window():
    """The window is already open; double-clicking the tray again must be a
    no-op, not a second Tk root on top of the first."""
    import webcam_client.main as m
    hub, tray, controller, q = _pump_ctx()

    q.put("OPEN_STATUS")
    result = m._status_pump(hub, tray, controller, q)

    assert result["close"] is False
    assert q.empty()


def test_the_pump_yields_instead_of_starving_the_window():
    """A fault flapping faster than one pump can drain would otherwise hold the
    Tk thread indefinitely and freeze the very window this keeps live. The
    remainder waits 250ms; it is not dropped."""
    import webcam_client.main as m
    hub, tray, controller, q = _pump_ctx()

    for _ in range(m._PUMP_DRAIN_LIMIT + 10):
        q.put("HEALTH")
    m._status_pump(hub, tray, controller, q)

    assert q.qsize() == 10, \
        f"the pump must bound its drain and leave the rest queued: {q.qsize()}"


def test_the_status_window_gets_a_pump_when_there_is_a_tray(monkeypatch):
    """Wiring check: the guarantees above are worth nothing if the window is
    still opened without a pump."""
    import webcam_client.main as m
    hub, tray, controller, q = _pump_ctx()
    seen = {}

    def fake_open(state, **kw):
        seen.update(kw)

    monkeypatch.setattr(m, "open_status_window", fake_open)
    m._handle_open_status(hub, controller, q, tray)
    assert callable(seen.get("pump")), "the window must be handed a live pump"

    seen.clear()
    m._handle_open_status(hub, controller, q, None)
    assert seen.get("pump") is None, \
        "with no tray there is nothing to repaint; fall back to the snapshot"
