# sdprs/webcam_client/status.py
"""Shared vocabulary for the webcam client's health/fault reporting, plus
StatusHub -- the single source of truth for the app's health.

`StatusHub` aggregates raw signals (HTTP failures, camera reads, etc.) from
every worker into one `Health` state and dispatches notifications about it.

THREADING: workers call report() from their own threads; the main thread calls
tick(). Both callbacks fire INSIDE the lock, together with the mutation that
caused them. Two workers with crossing transitions (one recovering as another
starts failing) would otherwise be able to deliver notifications in the opposite
order to the real state sequence and latch the UI to the wrong state
permanently. The price of that guarantee: a callback MUST only enqueue -- never
re-enter this hub, never touch Tk or pystray.

`Health` and `Fault` are the vocabulary that `webcam_client.strings` keys its
operator-facing text on (by `.value`, precisely to avoid importing this module
and creating a circular import) -- this module must not import `strings`.
"""

import threading
import time
from enum import Enum
from typing import Callable, Optional


class Fault(Enum):
    NONE = "none"
    NO_SERVER = "no_server"
    BAD_KEY = "bad_key"
    CAMERA_DOWN = "camera_down"


class Health(Enum):
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    NO_SERVER = "no_server"
    BAD_KEY = "bad_key"
    CAMERA_DOWN = "camera_down"


CONTROL_SOURCE = "__control__"

# A fault must be PRESENT this long before it is announced. A failing uplink
# reports at ~1Hz and transient blips are routine; toasting every one of them
# trains the operator to ignore all notifications.
#
# Present, not "held without interruption": the same window doubles as the
# measure of a recovery, so an episode of trouble ends only after the app has
# been healthy for this long. See _track_dwell_locked -- a flapping uplink used
# to reset a consecutive-time clock forever and was never announced at all.
NOTIFY_DEBOUNCE_SECONDS = 30.0

# Worst first. PAUSED is handled separately (it outranks everything). BAD_KEY
# beats NO_SERVER because it is the more actionable instruction to the guard;
# CAMERA_DOWN is last because other cameras may still be working.
_PRECEDENCE = (Fault.BAD_KEY, Fault.NO_SERVER, Fault.CAMERA_DOWN)

_FAULT_TO_HEALTH = {
    Fault.BAD_KEY: Health.BAD_KEY,
    Fault.NO_SERVER: Health.NO_SERVER,
    Fault.CAMERA_DOWN: Health.CAMERA_DOWN,
}

_HEALTHY = (Health.RUNNING, Health.PAUSED, Health.STARTING)

_RANK = {fault: i for i, fault in enumerate(_PRECEDENCE)}
_RANK_NONE = len(_PRECEDENCE)          # Fault.NONE is not in _PRECEDENCE


def worse_fault(a: Fault, b: Fault) -> Fault:
    """Return whichever of two faults the operator should hear about.

    Ranks by position in _PRECEDENCE, worst first; Fault.NONE is not in
    _PRECEDENCE and therefore always ranks last. Ties return `a`.

    Promoted here from control_channel.py and push_engine.py, which each
    arrived at this rule separately -- control_channel needs it to fold a
    whole poll cycle's per-node verdicts down to one, push_engine needs it to
    merge its camera-fault and uplink-fault domains -- and ended up carrying a
    byte-identical private copy apiece. Two copies of a rule that decides what
    a guard is told is exactly the drift risk the single _PRECEDENCE tuple
    above exists to prevent: a private copy could go stale the moment
    _PRECEDENCE changed, and the two modules would then silently disagree
    about which of two live problems matters more to the guard. One function,
    imported read-only by both, makes that impossible.
    """
    return a if _RANK.get(a, _RANK_NONE) <= _RANK.get(b, _RANK_NONE) else b


class StatusHub:
    def __init__(self, *,
                 on_change: Optional[Callable[[Health], None]] = None,
                 on_notify: Optional[Callable[[Health], None]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self._on_change = on_change or (lambda state: None)
        self._on_notify = on_notify or (lambda state: None)
        self._clock = clock
        self._lock = threading.Lock()
        self._faults = {}            # source -> Fault (NONE entries dropped)
        self._paused = False
        self._state = Health.STARTING
        self._seen_any_report = False
        self._state_since = clock()
        self._notified = Health.STARTING
        # The announcement clock CANNOT be _state_since. _recompute_locked()
        # resets that on every transition, so an uplink that flaps -- down 5s,
        # up 5s, down 5s, faster than the window -- reset it forever and the
        # guard was never told a thing, however many hours the site had been
        # unusable. A flapping uplink is one of the commonest site conditions
        # there is, and it produced total silence (finding #7).
        #
        # So the window is served by how long the fault has ACTUALLY been
        # present in this episode, summed across the gaps.
        #
        # Be precise about what that does and does not weaken. It is strictly
        # EASIER to satisfy than "30 CONSECUTIVE seconds" -- deliberately, since
        # consecutive-30 is the rule that produced the silence: a flap whose
        # longest unbroken stretch is 5s reaches 30s cumulative and is announced,
        # where consecutive-30 never fires at all. What it is strictly harder
        # than is the obvious wrong fix, "30 seconds have elapsed since the
        # trouble started", which counts the healthy gaps and would toast after
        # two seconds of real downtime spread across a 31-second span.
        #
        # The standing rule -- never announce a blip shorter than
        # NOTIFY_DEBOUNCE_SECONDS -- survives on its own terms and needs no
        # comparison to consecutive-30: a toast cannot fire until the fault has
        # been PRESENT for the full window, so a fault present for less than
        # that can never be announced, however its downtime is distributed.
        #
        # _degraded_state is the fault-state the tally belongs to (it is always
        # identical to _state while _state is unhealthy, because entering a
        # fault state always claims it). _degraded_dwell is the downtime banked
        # in EARLIER stretches only -- the stretch running right now is
        # _clock() - _state_since, and tick() adds the two.
        self._degraded_state = None
        self._degraded_dwell = 0.0
        # When the current unbroken run of healthy states began; None while a
        # fault is live. STARTING counts, so the app boots inside a healthy run.
        # A healthy run that lasts a whole window ENDS the episode and zeroes the
        # tally -- without that the tally becomes a lifetime total, and a 25s
        # outage this morning would make a 25s outage this afternoon (a blip,
        # which must stay silent) toast five seconds in.
        self._healthy_since = clock()

    # --- public API -------------------------------------------------------

    @property
    def state(self) -> Health:
        with self._lock:
            return self._state

    def faulty_sources(self) -> list:
        with self._lock:
            return sorted(self._faults)

    def report(self, source: str, fault: Fault) -> None:
        with self._lock:
            self._seen_any_report = True
            if fault is Fault.NONE:
                self._faults.pop(source, None)
            else:
                self._faults[source] = fault
            self._recompute_locked()

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused
            self._recompute_locked()

    def clear_all(self) -> None:
        """Engines are gone -> every reported fault is stale. Without this a
        settings edit or a quit can leave a red light that no live worker owns."""
        with self._lock:
            self._faults.clear()
            self._seen_any_report = False
            self._recompute_locked()

    def tick(self) -> None:
        """Called from the main dispatch loop. Announces a degraded state once
        the fault behind it has been PRESENT for the debounce window.

        "Present for" is cumulative, not consecutive: a fault that comes and
        goes faster than the window used to reset the clock on every transition
        and was therefore never announced at all (finding #7). The sum can only
        reach the window after the fault has genuinely been there that long, so
        a blip still cannot toast. See _track_dwell_locked.
        """
        with self._lock:
            if self._state in _HEALTHY or self._state is self._notified:
                return
            dwell = self._degraded_dwell + (self._clock() - self._state_since)
            if dwell >= NOTIFY_DEBOUNCE_SECONDS:
                self._notified = self._state
                self._on_notify(self._state)

    # --- internals (call with the lock held) ------------------------------

    def _compute_locked(self) -> Health:
        if self._paused:
            return Health.PAUSED
        for fault in _PRECEDENCE:
            if fault in self._faults.values():
                return _FAULT_TO_HEALTH[fault]
        return Health.RUNNING if self._seen_any_report else Health.STARTING

    def _track_dwell_locked(self, new, now) -> None:
        """Carry the cumulative-downtime tally across one state transition.

        Called with the OLD state still in self._state, before the assignment.

        What the guard gets out of it: a server that flaps is announced after 30
        seconds of real downtime instead of never (finding #7), and none of the
        three shapes of blip that must stay silent becomes audible --

          * a short outage that clears: the tally stops growing the moment the
            fault goes, and tick() returns early while the state is healthy;
          * the same fault returning after a full window of good uploads: a new
            episode, tally zeroed;
          * a DIFFERENT fault that has only just begun: a different episode,
            tally zeroed. Without that last rule a freshly-begun CAMERA_DOWN
            would inherit the seconds a server outage had banked and toast at
            once, which was a review finding in its own right.

        Deliberately conservative where it cannot be sure: a fault that
        alternates with a DIFFERENT fault (rather than with health) faster than
        the window still zeroes the tally each time and so is still not
        announced. Announcing it would mean pooling unlike faults into one
        window, which is exactly what re-opens the "new fault bypasses the
        debounce" hole. The tray light still moves and the log still has both
        faults; this only concerns the toast.
        """
        if self._degraded_state is not None and self._state is self._degraded_state:
            # Bank the stretch that is ending. Only time spent WITH the fault
            # counts -- the healthy gaps of a flap contribute nothing, which is
            # what makes the window a downtime measure rather than a wall-clock
            # one. max() because an injected clock is not guaranteed monotonic.
            self._degraded_dwell += max(0.0, now - self._state_since)
        if new in _HEALTHY:
            if self._healthy_since is None:
                self._healthy_since = now
            return
        recovered_for_a_full_window = (
            self._healthy_since is not None
            and now - self._healthy_since >= NOTIFY_DEBOUNCE_SECONDS)
        if new is not self._degraded_state or recovered_for_a_full_window:
            self._degraded_state = new
            self._degraded_dwell = 0.0
        self._healthy_since = None

    def _recompute_locked(self) -> None:
        new = self._compute_locked()
        if new is self._state:
            return
        now = self._clock()
        self._track_dwell_locked(new, now)
        self._state = new
        self._state_since = now
        self._on_change(new)
        # PAUSED is in _HEALTHY (uploads are intentionally stopped, so no fault
        # can be "occurring"), but it is NOT a recovery: it is the guard's own
        # click. Toasting 已暫停上傳 back at them is unsolicited, and when a
        # fault was live at the moment they paused it reads as "problem solved"
        # while the problem is still there.
        #
        # Returning here rather than merely suppressing the toast deliberately
        # leaves _notified untouched, which is what makes the resume side
        # correct too: a fault that SURVIVES the pause is still the last thing
        # _notified holds, so tick() will not re-announce it on resume; a fault
        # that CLEARED during the pause leaves _notified on that fault, so the
        # RUNNING transition on resume is still seen as a recovery and toasts.
        if new is Health.PAUSED:
            return
        # Recovery is NOT debounced: the operator should learn at once that the
        # problem cleared. Degradation waits for tick() so a blip stays silent.
        #
        # STARTING is excluded from the recovery toast on purpose: clear_all()
        # (called by stop_engines on every settings edit) drops the state back to
        # STARTING, and toasting "啟動中" every time the operator opens settings
        # is exactly the notification-fatigue this debounce exists to avoid.
        if new in _HEALTHY:
            recovered = self._notified not in _HEALTHY
            self._notified = new
            if recovered and new is not Health.STARTING:
                self._on_notify(new)
