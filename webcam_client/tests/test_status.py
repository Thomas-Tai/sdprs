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


def test_worse_fault_ranks_by_precedence_position():
    """worse_fault() is the promoted helper control_channel.py and
    push_engine.py both import read-only -- they used to each carry a
    byte-identical private copy, which is exactly the drift risk the single
    _PRECEDENCE tuple exists to prevent (a private copy could go stale the
    moment _PRECEDENCE changed, and the two modules would silently disagree
    about which of two live faults matters more to the guard).

    Expectations here are derived from _PRECEDENCE's OWN order via
    `.index()`, not from hardcoded fault names, so a future reordering of
    _PRECEDENCE moves this test's expectations with it instead of leaving
    them to silently go stale.
    """
    from webcam_client.status import worse_fault, _PRECEDENCE

    all_faults = (Fault.NONE,) + _PRECEDENCE

    def rank(fault):
        # Mirrors _PRECEDENCE-relative ranking, not a hardcoded value: NONE is
        # not in _PRECEDENCE at all, so it must sort after every real fault.
        return _PRECEDENCE.index(fault) if fault in _PRECEDENCE else len(_PRECEDENCE)

    # Every ordered pair, both operand orders -- 16 pairs for today's 4 faults.
    for a in all_faults:
        for b in all_faults:
            expected = a if rank(a) <= rank(b) else b
            assert worse_fault(a, b) is expected, (
                f"worse_fault({a}, {b}) should be {expected} per _PRECEDENCE's "
                f"own order, got {worse_fault(a, b)}")

    # Fault.NONE ranks LAST against every real fault, in both operand orders --
    # pinned explicitly, not just as a side effect of the loop above.
    for fault in _PRECEDENCE:
        assert worse_fault(fault, Fault.NONE) is fault
        assert worse_fault(Fault.NONE, fault) is fault
    assert worse_fault(Fault.NONE, Fault.NONE) is Fault.NONE

    # Ties return the first argument.
    for fault in all_faults:
        assert worse_fault(fault, fault) is fault


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


def test_recovery_is_announced_after_a_full_window_of_health():
    """REVISED by human decision 5 (2026-07-30). This test previously asserted
    the opposite -- "recovery must not wait for a tick" -- and it was right for
    an outage that ENDS. It was wrong for one that FLAPS: once a fault had banked
    its 30 seconds, every up-swing toasted 監控中 and every down-swing re-toasted
    the fault, measured at 8 toasts across 4 five-second flaps. A five-second
    recovery in the middle of a flap is not a recovery, so announcing one was
    itself a small untruth as well as the notification fatigue the debounce
    exists to prevent.

    The guarantee is now symmetric with degradation: announced once the app has
    been healthy for the window. Revised rather than deleted, because the thing
    it protects -- a real recovery must eventually reach the guard -- still
    holds, and only its timing changed."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    assert notifies == [Health.NO_SERVER], "precondition: the fault was announced"
    notifies.clear()

    hub.report("cam1", Fault.NONE)
    assert hub.state is Health.RUNNING, "the tray light is NOT debounced"
    hub.tick()
    assert notifies == [], (
        "uploads have worked for zero seconds; announcing a recovery now is what "
        "made a flapping server toast on every up-swing")

    clock.advance(NOTIFY_DEBOUNCE_SECONDS - 1)
    hub.tick()
    assert notifies == [], "still inside the recovery window"

    clock.advance(2)
    hub.tick()
    assert notifies == [Health.RUNNING], "a real recovery must still be announced"

    clock.advance(NOTIFY_DEBOUNCE_SECONDS * 3)
    hub.tick()
    assert notifies == [Health.RUNNING], "...exactly once, not on every tick"


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
# the suite would stay green. push_engine's camera domain now reports it from
# THREE sites (push_engine.py: the camera failing to open, the sustained-bad-
# read counter, and the crashed-worker except arm), so pin the mapping in
# isolation.
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
    still learn that uploads are working again.

    A pause is real evidence of health here, not a gap in it, so it counts toward
    the recovery window human decision 5 introduced. The control channel keeps
    polling throughout a pause -- pausing only stops the push engines uploading --
    so a CONTROL_SOURCE fault clearing mid-pause means the server really was
    answering for those seconds. The clock therefore advances DURING the pause
    below, and the recovery is announced on the first tick after resume rather
    than waiting out a second window.

    This can never announce a recovery that has not happened: the announcement
    only fires while the state IS RUNNING, and if the fault were still present
    _compute_locked would return the fault on resume, never RUNNING."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    notifies.clear()

    hub.set_paused(True)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)   # a real pause takes real time
    hub.report("cam1", Fault.NONE)       # the server came back while paused
    assert hub.state is Health.PAUSED, "PAUSED still outranks everything"
    hub.tick()
    assert notifies == [], "PAUSED is the guard's own click, never an announcement"
    notifies.clear()

    hub.set_paused(False)
    assert hub.state is Health.RUNNING
    hub.tick()
    assert notifies == [Health.RUNNING], "a real recovery must still be announced"


# --------------------------------------------------------------------------
# Finding #7: a FLAPPING fault could never be announced at all.
#
# tick() used to measure the debounce from _state_since, and _recompute_locked()
# resets _state_since on EVERY state change. An uplink that flaps -- down 5s, up
# 5s, down 5s, faster than the 30s window -- therefore reset the announcement
# clock forever, and the guard was never told anything, no matter how many hours
# the site had been effectively unusable. A flapping uplink is one of the
# commonest real site conditions there is, and it produced total silence.
#
# The window is now measured against how long the fault has ACTUALLY been
# present during this episode, not against how long the state has held without
# interruption. That is strictly EASIER to satisfy than "30 CONSECUTIVE
# seconds" -- on purpose, because consecutive-30 IS the rule that produced the
# silence: test_a_flapping_fault_is_eventually_announced below has a longest
# unbroken stretch of 5 seconds, so consecutive-30 would never fire. What it is
# strictly harder than is the obvious wrong fix, "30 seconds since the trouble
# started" -- see test_a_second_blip_a_window_later_still_does_not_announce.
#
# The standing rule (never announce a blip shorter than the window) holds on its
# own terms: a toast cannot fire until the fault has been PRESENT for the full
# 30s, so a fault present for less can never be announced.
#
# The four tests below pin both directions -- the flap is announced, and none of
# the three shapes of blip that must stay silent become audible.
# --------------------------------------------------------------------------

def test_a_flapping_fault_is_eventually_announced():
    """Six 5-second outages with 5 seconds of working uploads between them: 30
    seconds of real downtime, no single stretch longer than 5. The guard has to
    be told; before this the clock reset 12 times and nothing ever fired."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NONE)
    notifies.clear()

    for cycle in range(1, 7):
        hub.report("cam1", Fault.NO_SERVER)
        clock.advance(5)
        hub.tick()
        if cycle < 6:
            assert notifies == [], (
                f"cycle {cycle}: only {5 * cycle}s of real downtime so far, so "
                f"the fault has not been present for a full window yet")
            hub.report("cam1", Fault.NONE)          # ...and back up again
            clock.advance(5)
            hub.tick()
            assert notifies == [], (
                f"cycle {cycle}: uploads are working at this instant")

    assert notifies == [Health.NO_SERVER], (
        "60 seconds into a server that flaps every 5 seconds the guard has "
        "still been told nothing: every transition reset the announcement clock")


def test_a_second_blip_a_window_later_still_does_not_announce():
    """The trap the obvious fix falls into, and the reason the clock counts
    downtime rather than the span it happened in.

    An implementation that merely remembered WHEN the trouble started ("first
    degraded 31s ago, and it is degraded right now") toasts here -- after two
    seconds of actual downtime, which is exactly the blip the rule forbids."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NONE)
    notifies.clear()

    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(1)
    hub.report("cam1", Fault.NONE)               # a 1s blip
    clock.advance(29)                            # 29s of perfectly good uploads
    hub.report("cam1", Fault.NO_SERVER)          # a second 1s blip, 30s later
    clock.advance(1)
    hub.tick()

    assert notifies == [], (
        f"two seconds of downtime inside 31 seconds is a blip, not an outage: "
        f"{notifies}")


def test_a_full_window_of_recovery_forgets_the_earlier_downtime():
    """An episode has to END, or the accumulator becomes a lifetime total: a 25s
    outage this morning would bank 25 seconds of credit, and a 25s outage this
    afternoon -- a blip, which must stay silent -- would toast 5 seconds in."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NONE)
    notifies.clear()

    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(25)                            # just inside the window
    hub.tick()
    assert notifies == [], "precondition: 25s must not have announced"
    hub.report("cam1", Fault.NONE)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)   # a FULL window of good uploads
    hub.tick()

    hub.report("cam1", Fault.NO_SERVER)          # a genuinely new outage
    clock.advance(25)
    hub.tick()
    assert notifies == [], (
        f"the second outage must serve its own full window instead of "
        f"inheriting credit from one that had already cleared: {notifies}")

    clock.advance(6)
    hub.tick()
    assert notifies == [Health.NO_SERVER], "...and must still be announced"


def test_a_different_fault_starts_its_own_window():
    """Cumulative downtime is tracked PER FAULT. A freshly-begun CAMERA_DOWN
    must not inherit the 29 seconds an unrelated server outage had banked --
    that is the "a new fault bypasses the debounce" bug, which was a review
    finding of its own."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NONE)
    notifies.clear()

    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(29)
    hub.tick()
    assert notifies == [], "precondition: NO_SERVER is still inside its window"

    hub.report("cam2", Fault.CAMERA_DOWN)        # NO_SERVER still outranks it
    hub.report("cam1", Fault.NONE)               # now CAMERA_DOWN, 0s old
    assert hub.state is Health.CAMERA_DOWN
    clock.advance(2)
    hub.tick()
    assert notifies == [], (
        f"CAMERA_DOWN began 2 seconds ago and must serve its own window: "
        f"{notifies}")

    clock.advance(NOTIFY_DEBOUNCE_SECONDS)
    hub.tick()
    assert notifies == [Health.CAMERA_DOWN]


def test_a_sustained_flap_is_announced_once_not_on_every_swing():
    """Human decision 5 (2026-07-30), and the other half of finding #7's fix.

    Making a flap announceable at all created the opposite failure. Once the
    fault had banked its window, EVERY down-swing re-announced it and every
    up-swing announced 監控中: measured at 8 further toasts across 4 five-second
    flaps, alternating, roughly one every five seconds. That is precisely what
    NOTIFY_DEBOUNCE_SECONDS' own comment says trains the operator to ignore all
    notifications -- so the flap fix would have traded silence for noise.

    Per episode the guard now hears the fault once, and 監控中 once the uploads
    have actually held for the window. The tray light still tracks every swing;
    only the toasts are quiet."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NONE)
    notifies.clear()

    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    assert notifies == [Health.NO_SERVER], "precondition: the outage was announced"

    for _ in range(4):
        hub.report("cam1", Fault.NONE)       # up for 5s -- not a recovery
        clock.advance(5)
        hub.tick()
        hub.report("cam1", Fault.NO_SERVER)  # down again -- already announced
        clock.advance(5)
        hub.tick()

    assert notifies == [Health.NO_SERVER], (
        f"a flapping server must be announced once, not on every swing: {notifies}")

    # ...and when it genuinely settles, the guard IS still told.
    hub.report("cam1", Fault.NONE)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    assert notifies == [Health.NO_SERVER, Health.RUNNING], (
        f"a settled recovery must still be announced, exactly once: {notifies}")


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
