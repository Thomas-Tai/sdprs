# -*- coding: utf-8 -*-
"""Pure reset-loop and boot-hold-off decisions (spec §5.6).

No NVS, no hardware — the counter is read by persist.py and passed in.
Kept pure so the tiering rules are exhaustively testable on the desktop.

The hold-off exists because `_off_since` lives in RAM: a WDT reset loop
(30s timeout) would restart a 2200W motor every 30 seconds, straight past
the min-off guard, because each boot believes the pump has never run.
"""


def is_reset_loop(boot_count, threshold):
    """threshold <= 0 disables detection (12V profile)."""
    return threshold > 0 and boot_count >= threshold


def holdoff_total_ms(profile, urgent, reset_loop):
    """Pick the hold-off duration for this boot.

    Three tiers, most-severe first:
      reset loop -> boot_loop_holdoff_ms  (urgency does NOT shorten it: a
                    node rebooting repeatedly cannot vouch for its sensors)
      urgent     -> boot_holdoff_urgent_ms
      otherwise  -> boot_holdoff_ms

    `urgent` is supplied by the MODE layer, not the profile: in DRAIN a
    high-water reading is a live flood, in COLLECT it means the container
    is full and there is nothing time-critical about starting.
    """
    if reset_loop:
        return profile["boot_loop_holdoff_ms"]
    if urgent:
        return profile["boot_holdoff_urgent_ms"]
    return profile["boot_holdoff_ms"]


def holdoff_remaining_ms(uptime_ms, total_ms):
    """Remaining hold-off, floored at 0. A null uptime means 'just booted'."""
    if total_ms <= 0:
        return 0
    if uptime_ms is None:
        return total_ms
    remaining = total_ms - uptime_ms
    return remaining if remaining > 0 else 0


def is_boot_healthy(uptime_ms, profile):
    """True once this boot has run long enough to disprove a reset loop."""
    window = profile["boot_healthy_ms"]
    if window <= 0:
        return False
    return uptime_ms is not None and uptime_ms >= window


def make_holdoff_tracker(profile, reset_loop):
    """Return a callable (uptime_ms, urgent) -> remaining_ms that LATCHES.

    holdoff_total_ms() is a function of `urgent`, and `urgent` can go from
    True back to False — a flood clears, which is the SUCCESS case, not an
    edge case. Recomputing the remaining time from a total that just grew
    would re-impose a hold-off on an already-running pump. Under
    SOCKET_220V that forced ON->OFF starts the 180s min-off lockout, so
    the pump would refuse to run for three minutes immediately after
    proving it works (spec §5.6, finding A5).

    So: the hold-off may SHORTEN while it is running, but once it reaches
    zero it is done for this boot. `machine.reset()` is what starts a new
    one — which is exactly right, because that is the event it guards.

    Kept here rather than as a closure inside main() so the latch is a pure
    thing that can be tested; main() only supplies the uptime.
    """
    state = {"released": False}

    def remaining(uptime_ms, urgent):
        if state["released"]:
            return 0
        total = holdoff_total_ms(profile, urgent, reset_loop)
        left = holdoff_remaining_ms(uptime_ms, total)
        if left <= 0:
            state["released"] = True
            return 0
        return left

    return remaining
