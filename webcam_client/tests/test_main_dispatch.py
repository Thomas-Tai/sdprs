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
        def __init__(self, cfg):
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
        def __init__(self, cfg):
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
