import pytest

import profiles


def test_known_profiles_exist():
    assert profiles.get_profile("PUMP_12V")["min_off_ms"] == 0
    assert profiles.get_profile("SOCKET_220V")["min_off_ms"] == 180000


def test_unknown_profile_rejected():
    with pytest.raises(ValueError):
        profiles.get_profile("SOCKET_110V")


def test_get_profile_returns_a_copy():
    # A caller mutating its profile must not poison the module table.
    p = profiles.get_profile("SOCKET_220V")
    p["min_off_ms"] = 1
    assert profiles.get_profile("SOCKET_220V")["min_off_ms"] == 180000


def test_both_profiles_have_identical_key_sets():
    # A key present in one profile and missing from the other is how a
    # KeyError reaches the field at 2am instead of the bench.
    assert set(profiles.PUMP_12V) == set(profiles.SOCKET_220V)


def test_mains_profile_requires_watchdog():
    # A hung controller holds the contactor closed; the WDT is the only
    # thing that stops a 2200W motor (spec 7).
    with pytest.raises(ValueError):
        profiles.validate(profiles.get_profile("SOCKET_220V"), wdt_enabled=False)


def test_mains_profile_accepts_watchdog_enabled():
    profiles.validate(profiles.get_profile("SOCKET_220V"), wdt_enabled=True)


def test_demo_profile_tolerates_watchdog_disabled():
    profiles.validate(profiles.get_profile("PUMP_12V"), wdt_enabled=False)


def test_mains_burst_cooldown_not_shorter_than_min_off():
    # Layer 1 conflict bursts sit ABOVE the min-off guard, so a short
    # cooldown would restart an AC motor ~30 times in 15 minutes through
    # the one door min-off cannot close (spec 5.2).
    p = profiles.get_profile("SOCKET_220V")
    assert p["burst_cooldown_ms"] >= p["min_off_ms"]


def test_urgent_holdoff_shorter_than_normal():
    p = profiles.get_profile("SOCKET_220V")
    assert 0 < p["boot_holdoff_urgent_ms"] < p["boot_holdoff_ms"]


def test_reset_loop_holdoff_is_the_longest():
    p = profiles.get_profile("SOCKET_220V")
    assert p["boot_loop_holdoff_ms"] > p["boot_holdoff_ms"]


def test_service_due_compares_ops_against_the_profile_threshold():
    # The threshold is 60% of the contactor's rated AC3 electrical life
    # (spec §5.8) — the point is to replace it BEFORE the contacts weld.
    p = profiles.get_profile("SOCKET_220V")
    assert profiles.service_due(p, 59999) is False
    assert profiles.service_due(p, 60000) is True
    assert profiles.service_due(p, 250000) is True


def test_service_due_is_never_true_when_the_counter_is_disabled():
    # PUMP_12V has contactor_service_ops = 0: no contactor, nothing to wear.
    p = profiles.get_profile("PUMP_12V")
    assert profiles.service_due(p, 10 ** 9) is False


def test_service_due_tolerates_a_missing_count():
    # Defensive only: persist._read currently coerces every failure to 0,
    # so read_contactor_ops never actually returns None today. The guard is
    # here so that if that contract is ever tightened to distinguish
    # "unavailable" from "zero", service_due does not start comparing None.
    p = profiles.get_profile("SOCKET_220V")
    assert profiles.service_due(p, None) is False
