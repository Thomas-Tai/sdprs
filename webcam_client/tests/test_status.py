# webcam_client/tests/test_status.py
"""StatusHub is the single source of truth for app health. It is deliberately
pure -- no Tk, no pystray, injected clock -- so all of this is unit-testable."""
import sys
import threading
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


# --------------------------------------------------------------------------
# Ledger row 4: CAMERA_DOWN is the only fault->health entry never asserted on
# its own. Every other mapping is pinned by a precedence test that happens to
# exercise it; CAMERA_DOWN is only ever seen LOSING a precedence contest, so
# _FAULT_TO_HEALTH[Fault.CAMERA_DOWN] could be wired to any state at all and
# the suite would stay green. push_engine now reports it from two places, so
# pin the mapping in isolation.
# --------------------------------------------------------------------------

def test_camera_down_alone_maps_to_the_camera_down_health():
    hub, changes, _, _ = make()
    hub.report("cam1", Fault.CAMERA_DOWN)
    assert hub.state is Health.CAMERA_DOWN
    assert changes == [Health.CAMERA_DOWN]
    assert hub.faulty_sources() == ["cam1"]
    hub.report("cam1", Fault.NONE)
    assert hub.state is Health.RUNNING


# --------------------------------------------------------------------------
# m-7 (linked to ledger row 3): pausing during a live fault must not toast.
#
# PAUSED lives in _HEALTHY, so entering it used to count as a "recovery" and
# fire the immediate recovery toast -- 已暫停上傳, unsolicited, in response to
# the guard's OWN click, and reading as "problem solved" while the problem is
# still there. STARTING is already excluded for the same family of reason.
#
# The minimal fix is to leave _notified untouched when entering PAUSED, which
# also gets the resume side right for free -- hence the three tests below,
# which pin BOTH directions.
# --------------------------------------------------------------------------

def test_pausing_during_a_live_fault_does_not_toast_a_recovery():
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    assert notifies == [Health.NO_SERVER], "precondition: the fault was announced"
    notifies.clear()

    hub.set_paused(True)

    assert hub.state is Health.PAUSED
    assert notifies == [], (
        "pausing is the guard's own click, not a recovery -- toasting 已暫停上傳 "
        "here reads as 'problem solved' while the server is still unreachable")


def test_resuming_with_the_fault_still_present_does_not_re_announce_it():
    """The other half of the same fix: the guard was already told about this
    fault before they paused. Pausing and resuming must not make the app repeat
    itself 30 seconds later as though the outage were new."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    notifies.clear()

    hub.set_paused(True)
    hub.set_paused(False)
    assert hub.state is Health.NO_SERVER, "the fault never went away"

    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    assert notifies == [], "an already-announced fault must not be re-announced"


def test_a_fault_that_cleared_during_the_pause_still_toasts_its_recovery():
    """...and the fix must not swallow a REAL recovery. The guard paused while
    the server was down, the server came back while paused; on resume they must
    still learn that uploads are working again."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    notifies.clear()

    hub.set_paused(True)
    hub.report("cam1", Fault.NONE)       # the server came back while paused
    assert hub.state is Health.PAUSED, "PAUSED still outranks everything"
    notifies.clear()

    hub.set_paused(False)
    assert hub.state is Health.RUNNING
    assert notifies == [Health.RUNNING], "a real recovery must still be announced"


# --------------------------------------------------------------------------
# Ledger row A: the branch's central design claim, under real threads.
# --------------------------------------------------------------------------

def test_final_notification_matches_final_state_under_concurrent_reports():
    """This is the ONE guarantee "notify inside the lock" exists to provide.

    on_change fires INSIDE the hub's lock, together with the mutation that
    caused it. Move it outside (the obvious "don't call user code under a
    lock" refactor) and two workers with crossing transitions -- one
    recovering as another starts failing -- can deliver their notifications in
    the opposite order to the real state sequence, latching the tray to a
    state that has already passed, permanently. Until now that was protected
    by code inspection alone: grepping the suite for threading returned zero
    matches.

    Deterministic by construction, not by timing: every state change appends to
    `seen` while the lock is held, so seen[-1] is ALWAYS the current state --
    reports that change nothing append nothing and leave the invariant intact.
    A barrier (not a sleep) is what creates the contention.

    D-2: the seen[-1] check alone did NOT catch its own named regression. A
    probe that moved the notify outside the lock reordered 63 notifications
    mid-run and this test still passed 30/30, because seen[-1] inspects only
    the FINAL notification -- taken after every worker has finished and there
    is no contention left to get wrong. The structural check in `watcher` is
    what actually holds the line: it asserts the lock is held at the moment
    on_change runs, which is the property the ordering guarantee rests on.
    """
    seen = []
    checked = []

    def watcher(state):
        # threading.Lock is NOT reentrant, so a successful acquire from inside
        # on_change proves the hub was NOT holding it -- i.e. the notification
        # and the mutation that caused it are no longer atomic.
        if hub._lock.acquire(blocking=False):
            hub._lock.release()      # never leave it held: that deadlocks the run
            raise AssertionError(
                "on_change fired OUTSIDE the hub lock -- two crossing "
                "transitions can now be delivered in the opposite order to the "
                "real state sequence, latching the tray to a state that has "
                "already passed")
        checked.append(state)
        seen.append(state)

    hub = StatusHub(on_change=watcher)

    faults = (Fault.NONE, Fault.NO_SERVER, Fault.BAD_KEY, Fault.CAMERA_DOWN)
    n_threads, n_iterations = 8, 300
    start = threading.Barrier(n_threads)
    errors = []

    def worker(i):
        try:
            start.wait()
            for j in range(n_iterations):
                hub.report(f"cam{i}", faults[(i + j) % len(faults)])
        except BaseException as exc:      # a barrier/lock failure must not hide
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "a worker deadlocked in report()"
    assert not errors, f"a worker raised: {errors!r}"
    assert seen, "no state change was ever notified"
    assert len(checked) > 100, (
        f"the lock-held check only ran {len(checked)} times -- too few "
        f"transitions to have exercised the contention this test exists for")
    assert seen[-1] is hub.state, (
        f"the last notification said {seen[-1]} but the hub settled on "
        f"{hub.state} -- the tray would be latched to a state that has passed")
