"""Local-capture hold helper + server 'hold' command handler wiring."""
import edge_glass_main as m


def test_capture_hold_true_during_cooldown():
    held, reason = m.compute_local_capture_hold(now=100.0, cooldown_until=130.0, has_pending=False)
    assert held is True and reason == "event_capture"


def test_capture_hold_true_when_pending_events():
    held, reason = m.compute_local_capture_hold(now=200.0, cooldown_until=0.0, has_pending=True)
    assert held is True and reason == "event_capture"


def test_capture_hold_false_when_idle():
    held, reason = m.compute_local_capture_hold(now=200.0, cooldown_until=130.0, has_pending=False)
    assert held is False and reason is None


class _FakeClient:
    def __init__(self):
        self.calls = []

    def set_server_hold(self, hold, reason=None):
        self.calls.append((hold, reason))


def test_hold_handler_sets_server_hold():
    client = _FakeClient()
    handler = m.make_hold_handler(client)
    handler({"hold": True, "reason": "active_alert"})
    handler({"hold": False})
    assert client.calls == [(True, "active_alert"), (False, None)]


def test_hold_handler_survives_bad_payload():
    client = _FakeClient()
    handler = m.make_hold_handler(client)
    handler({})  # missing keys -> treated as no-hold, must not raise
    assert client.calls == [(False, None)]
