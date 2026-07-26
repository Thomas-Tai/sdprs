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

# A fault must persist this long before it is announced. A failing uplink
# reports at ~1Hz and transient blips are routine; toasting every one of them
# trains the operator to ignore all notifications.
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
        it has been stable for the debounce window."""
        with self._lock:
            if self._state in _HEALTHY or self._state is self._notified:
                return
            if self._clock() - self._state_since >= NOTIFY_DEBOUNCE_SECONDS:
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

    def _recompute_locked(self) -> None:
        new = self._compute_locked()
        if new is self._state:
            return
        self._state = new
        self._state_since = self._clock()
        self._on_change(new)
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
