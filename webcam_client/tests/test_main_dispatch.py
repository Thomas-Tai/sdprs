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
