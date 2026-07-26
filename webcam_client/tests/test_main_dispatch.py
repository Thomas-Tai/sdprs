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
    keep = m._handle_request("OPEN_SETTINGS", ctrl, lambda cfg: {"server_url": "new"})
    assert keep is True
    assert ctrl.calls == ["stop_engines", "apply"]
    assert ctrl.applied == {"server_url": "new"}
    assert saved == {"server_url": "new"}


def test_open_settings_resumes_old_config_on_cancel(monkeypatch):
    import webcam_client.main as m
    monkeypatch.setattr(m, "save_config", lambda c: None)
    ctrl = FakeController({"server_url": "old"})
    keep = m._handle_request("OPEN_SETTINGS", ctrl, lambda cfg: None)
    assert keep is True
    assert ctrl.calls == ["stop_engines", "start_engines"]
    assert ctrl.applied is None


def test_quit_shuts_down_and_stops_loop():
    import webcam_client.main as m
    ctrl = FakeController({})
    keep = m._handle_request("QUIT", ctrl, lambda cfg: None)
    assert keep is False
    assert ctrl.calls == ["shutdown"]


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
    keep = m._handle_request("OPEN_SETTINGS", ctrl, raising_fn)
    assert keep is True
    assert ctrl.calls == ["stop_engines", "apply"]
    assert ctrl.applied == {"server_url": "old"}


def test_tray_open_settings_callback_only_enqueues():
    import queue
    q = queue.Queue()
    on_open = lambda: q.put("OPEN_SETTINGS")
    on_open()
    assert q.get_nowait() == "OPEN_SETTINGS"


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
        keep = m._handle_request(
            "OPEN_SETTINGS", ctrl,
            lambda cfg: {"server_url": "old", "api_key": new_key})
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


def test_notify_message_toasts_current_state(monkeypatch):
    import webcam_client.main as m
    from webcam_client.status import StatusHub, Fault, Health

    sent = []
    monkeypatch.setattr(m, "notify_state",
                        lambda icon, state: sent.append(state) or True)
    hub = StatusHub()
    hub.report("n1", Fault.NO_SERVER)
    m._handle_notify(hub, object())
    assert sent == [Health.NO_SERVER]


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


def test_enabled_cameras_are_read_from_the_live_config():
    """The count in the window must come from the CURRENT config, not from the
    list captured at startup: after a settings edit the startup list is stale
    and the window would tell the guard the wrong number of cameras."""
    import webcam_client.main as m

    cfg = {"cameras": [{"node_id": "a", "enabled": True},
                       {"node_id": "b", "enabled": False},
                       {"node_id": "c"}]}
    assert [c["node_id"] for c in m._enabled_cameras(cfg)] == ["a", "c"]
    assert m._enabled_cameras({}) == []


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

    made = _boot(monkeypatch, m, FakeTray)
    m.main()
    captured["on_reconnect"]()
    assert made["ctrl"].applied == [made["ctrl"].config]


def test_a_failed_rebuild_never_leaves_the_light_claiming_startup():
    """apply() is stop_engines() + start_engines(), and stop_engines() ends in
    hub.clear_all() -- which resets the hub to STARTING. If start_engines()
    then raises, nothing is left alive to report a fault (the control channel
    is built LAST, so it never gets built at all): the hub stays on STARTING
    for the rest of the process. Grey tray, "啟動中 / 正在連線並開啟攝影機，
    請稍候。" forever, and no toast either, because tick() never announces a
    healthy state. The guard reaches that dead end by pressing 重新連線 -- the
    button they press to FIX things."""
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

    assert m._rebuild_engines(ExplodingCtrl(), hub) is False
    assert len(attempts) == 2, "a failed rebuild must be retried once"
    assert hub.state is not Health.STARTING, \
        "the tray would sit grey on 啟動中 forever, with no toast to correct it"


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

    made = _boot(monkeypatch, m, FakeTray)
    m.main()

    def exploding_apply(cfg):
        made_hub["hub"].clear_all()      # stop_engines() already ran
        raise RuntimeError("camera 0 will not open")

    made["ctrl"].apply = exploding_apply
    captured["on_reconnect"]()           # must not raise
    assert made_hub["hub"].state is not Health.STARTING


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
    else calls it -- there is no timer thread. If the queue.Empty branch drops
    it, every notification in the app silently stops working."""
    import webcam_client.main as m
    from webcam_client.status import Health

    ticks = []

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
            m._running = False      # restored by _boot's monkeypatch.setattr

    class FakeTray:
        icon = None

        def __init__(self, **kw): pass
        def start(self): pass
        def set_health(self, state): pass

    monkeypatch.setattr(m, "StatusHub", FakeHub)
    _boot(monkeypatch, m, FakeTray)
    m.main()
    assert ticks, "the idle dispatch loop never called hub.tick()"


def test_hub_callbacks_only_enqueue(monkeypatch):
    """The hub invokes on_change/on_notify while HOLDING its lock, from worker
    threads. Anything more than a q.put() there re-enters the hub or touches Tk
    off the main thread -- the exact bug class this design exists to prevent."""
    import queue
    import webcam_client.main as m
    from webcam_client.status import Health

    captured = {}

    class FakeHub:
        def __init__(self, **kw):
            captured.update(kw)
            self.state = Health.STARTING

        def faulty_sources(self): return []
        def report(self, source, fault): pass
        def set_paused(self, paused): pass
        def clear_all(self): pass

        def tick(self):
            m._running = False

    class FakeTray:
        icon = None

        def __init__(self, **kw): pass
        def start(self): pass
        def set_health(self, state): pass

    puts = []

    class RecordingQueue(queue.Queue):
        def put(self, item, *a, **kw):
            puts.append(item)
            super().put(item, *a, **kw)

    monkeypatch.setattr(m.queue, "Queue", RecordingQueue)
    monkeypatch.setattr(m, "StatusHub", FakeHub)
    _boot(monkeypatch, m, FakeTray)
    m.main()

    assert callable(captured.get("on_change")) and callable(captured.get("on_notify"))
    captured["on_change"](Health.BAD_KEY)
    assert puts[-1] == "HEALTH", "on_change must ONLY enqueue"
    captured["on_notify"](Health.BAD_KEY)
    assert puts[-1] == "NOTIFY", "on_notify must ONLY enqueue"
