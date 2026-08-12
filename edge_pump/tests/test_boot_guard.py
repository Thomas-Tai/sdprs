import boot_guard
import profiles


def mains():
    return profiles.get_profile("SOCKET_220V")


def demo():
    return profiles.get_profile("PUMP_12V")


def test_reset_loop_detected_at_threshold():
    assert boot_guard.is_reset_loop(3, 3) is True
    assert boot_guard.is_reset_loop(2, 3) is False


def test_reset_loop_detection_disabled_by_zero_threshold():
    assert boot_guard.is_reset_loop(99, 0) is False


def test_normal_boot_uses_the_full_holdoff():
    assert boot_guard.holdoff_total_ms(mains(), urgent=False, reset_loop=False) == 60000


def test_urgent_boot_is_shortened():
    # Water is rising after a brownout reset. A flat 60s refusal to pump is
    # a worse failure than the short-cycling the hold-off prevents.
    assert boot_guard.holdoff_total_ms(mains(), urgent=True, reset_loop=False) == 10000


def test_reset_loop_overrides_urgency():
    # A node rebooting every 30s has not earned the benefit of the doubt on
    # its own sensor readings.
    assert boot_guard.holdoff_total_ms(mains(), urgent=True, reset_loop=True) == 300000


def test_demo_profile_has_no_holdoff():
    assert boot_guard.holdoff_total_ms(demo(), urgent=False, reset_loop=False) == 0


def test_remaining_counts_down():
    assert boot_guard.holdoff_remaining_ms(0, 60000) == 60000
    assert boot_guard.holdoff_remaining_ms(20000, 60000) == 40000
    assert boot_guard.holdoff_remaining_ms(60000, 60000) == 0
    assert boot_guard.holdoff_remaining_ms(99000, 60000) == 0


def test_remaining_is_zero_when_disabled():
    assert boot_guard.holdoff_remaining_ms(0, 0) == 0


def test_null_uptime_means_full_holdoff():
    assert boot_guard.holdoff_remaining_ms(None, 60000) == 60000


def test_boot_healthy_after_the_window():
    assert boot_guard.is_boot_healthy(300000, mains()) is True
    assert boot_guard.is_boot_healthy(299999, mains()) is False


def test_boot_healthy_is_false_when_disabled():
    # boot_healthy_ms == 0 means the counter is unused; never clear it.
    assert boot_guard.is_boot_healthy(999999, demo()) is False


# ---- The tracker: the hold-off must never come BACK ----

def test_tracker_counts_down_and_then_stays_released():
    t = boot_guard.make_holdoff_tracker(mains(), reset_loop=False)
    assert t(0, urgent=False) == 60000
    assert t(20000, urgent=False) == 40000
    assert t(60000, urgent=False) == 0
    assert t(60001, urgent=False) == 0


def test_tracker_does_not_reengage_when_urgency_ends():
    """THE regression this tracker exists for.

    Boot into a flood: high_water is True, so the hold-off shortens to 10s
    and the pump starts. Thirty seconds later the pump has done its job and
    high_water clears — which makes `urgent` False, which makes
    holdoff_total_ms() return to 60000. Recomputing remaining time from
    that would give 60000-40000 = 20000 and force a RUNNING pump off, and
    under SOCKET_220V that ON->OFF transition starts a 180s min-off
    lockout. A basement that pumped for 30 seconds would then refuse to
    pump for another three minutes, in the exact scenario the urgent
    shortening was added for (spec §5.6, finding A5).

    Once released, released stays."""
    t = boot_guard.make_holdoff_tracker(mains(), reset_loop=False)
    assert t(0, urgent=True) == 10000        # flood: shortened
    assert t(10000, urgent=True) == 0        # released, pump may start
    assert t(40000, urgent=False) == 0       # water gone — must NOT come back
    assert t(41000, urgent=False) == 0


def test_tracker_still_shortens_when_urgency_appears_mid_holdoff():
    # The shortening direction must keep working: water rises during a
    # normal 60s hold-off and the response must not wait the full minute.
    t = boot_guard.make_holdoff_tracker(mains(), reset_loop=False)
    assert t(0, urgent=False) == 60000
    assert t(15000, urgent=True) == 0        # 15s > the 10s urgent total


def test_tracker_reset_loop_ignores_urgency():
    t = boot_guard.make_holdoff_tracker(mains(), reset_loop=True)
    assert t(0, urgent=True) == 300000
    assert t(60000, urgent=True) == 240000


def test_tracker_is_inert_on_the_demo_profile():
    t = boot_guard.make_holdoff_tracker(demo(), reset_loop=False)
    assert t(0, urgent=False) == 0
