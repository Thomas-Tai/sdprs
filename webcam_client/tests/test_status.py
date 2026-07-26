# webcam_client/tests/test_status.py
"""StatusHub is the single source of truth for app health. It is deliberately
pure -- no Tk, no pystray, injected clock -- so all of this is unit-testable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.status import (Fault, Health, StatusHub, CONTROL_SOURCE,
                                  NOTIFY_DEBOUNCE_SECONDS)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, secs):
        self.now += secs


def make(**kw):
    changes, notifies = [], []
    clock = FakeClock()
    hub = StatusHub(on_change=changes.append, on_notify=notifies.append,
                    clock=clock, **kw)
    return hub, changes, notifies, clock


def test_starts_in_starting_state():
    hub, _, _, _ = make()
    assert hub.state is Health.STARTING


def test_first_healthy_report_becomes_running():
    hub, changes, _, _ = make()
    hub.report("cam1", Fault.NONE)
    assert hub.state is Health.RUNNING
    assert changes == [Health.RUNNING]


def test_fault_changes_state_immediately():
    """The tray light must never lag -- on_change is not debounced."""
    hub, changes, _, _ = make()
    hub.report("cam1", Fault.NONE)
    hub.report("cam1", Fault.NO_SERVER)
    assert hub.state is Health.NO_SERVER
    assert changes == [Health.RUNNING, Health.NO_SERVER]


def test_on_change_fires_only_on_transition():
    hub, changes, _, _ = make()
    hub.report("cam1", Fault.NO_SERVER)
    hub.report("cam1", Fault.NO_SERVER)
    hub.report("cam1", Fault.NO_SERVER)
    assert changes == [Health.NO_SERVER], "repeat reports must not re-fire"


def test_precedence_bad_key_beats_no_server():
    """BAD_KEY is the more actionable instruction, so it outranks NO_SERVER."""
    hub, _, _, _ = make()
    hub.report("cam1", Fault.NO_SERVER)
    hub.report(CONTROL_SOURCE, Fault.BAD_KEY)
    assert hub.state is Health.BAD_KEY


def test_precedence_no_server_beats_camera_down():
    hub, _, _, _ = make()
    hub.report("cam1", Fault.CAMERA_DOWN)
    hub.report("cam2", Fault.NO_SERVER)
    assert hub.state is Health.NO_SERVER


def test_paused_outranks_every_fault():
    """Uploads are intentionally stopped while paused, so no fault can be
    occurring -- showing a red 'no server' light then would be a lie."""
    hub, _, _, _ = make()
    hub.report("cam1", Fault.BAD_KEY)
    hub.set_paused(True)
    assert hub.state is Health.PAUSED
    hub.set_paused(False)
    assert hub.state is Health.BAD_KEY, "unpausing must reveal the real state again"


def test_recovery_when_last_faulty_source_clears():
    hub, _, _, _ = make()
    hub.report("cam1", Fault.NO_SERVER)
    hub.report("cam2", Fault.NO_SERVER)
    hub.report("cam1", Fault.NONE)
    assert hub.state is Health.NO_SERVER, "cam2 still failing"
    hub.report("cam2", Fault.NONE)
    assert hub.state is Health.RUNNING


def test_degradation_is_debounced():
    """A 2-second network blip must not toast the guard."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NONE)
    notifies.clear()
    hub.report("cam1", Fault.NO_SERVER)
    hub.tick()
    assert notifies == [], "must not notify immediately"
    clock.advance(NOTIFY_DEBOUNCE_SECONDS - 1)
    hub.tick()
    assert notifies == [], "still inside the debounce window"
    clock.advance(2)
    hub.tick()
    assert notifies == [Health.NO_SERVER]


def test_blip_shorter_than_debounce_never_notifies():
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NONE)
    notifies.clear()
    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(5)
    hub.tick()
    hub.report("cam1", Fault.NONE)   # recovered before the window elapsed
    clock.advance(NOTIFY_DEBOUNCE_SECONDS)
    hub.tick()
    assert Health.NO_SERVER not in notifies, "a transient blip must never toast"


def test_recovery_notifies_immediately_without_debounce():
    """The guard should learn at once that the problem cleared."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    notifies.clear()
    hub.report("cam1", Fault.NONE)
    assert notifies == [Health.RUNNING], "recovery must not wait for a tick"


def test_tick_does_not_renotify_a_stable_state():
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    notifies.clear()
    for _ in range(5):
        clock.advance(60)
        hub.tick()
    assert notifies == [], "a stable fault must be announced once, not forever"


def test_clear_all_resets_to_starting_and_forgets_sources():
    """stop_engines() clears reported faults so a settings edit never leaves a
    red light owned by no live worker."""
    hub, _, _, _ = make()
    hub.report("cam1", Fault.BAD_KEY)
    hub.clear_all()
    assert hub.state is Health.STARTING
    assert hub.faulty_sources() == []


def test_clear_all_does_not_toast_starting():
    """stop_engines() runs on every settings edit. Toasting 啟動中 each time is
    exactly the notification fatigue the debounce exists to prevent."""
    hub, _, notifies, _ = make()
    hub.report("cam1", Fault.BAD_KEY)
    notifies.clear()
    hub.clear_all()
    assert notifies == [], "returning to STARTING must be silent"


def test_faulty_sources_lists_only_failing_ones():
    hub, _, _, _ = make()
    hub.report("cam1", Fault.NONE)
    hub.report("cam2", Fault.CAMERA_DOWN)
    assert hub.faulty_sources() == ["cam2"]


def test_callbacks_are_optional():
    hub = StatusHub()
    hub.report("cam1", Fault.NO_SERVER)   # must not raise
    hub.tick()
