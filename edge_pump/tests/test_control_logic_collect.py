from control_logic import (decide_collect, initial_state, DEFAULT_CONFIG,
                           CONTAINER_FULL, COLLECT_RAIN_ON, SOURCE_DRY)


def cfg(**over):
    c = dict(DEFAULT_CONFIG)
    c.update(over)
    return c


def timing(**over):
    t = {"pump_on_elapsed_ms": None, "rain_wet_elapsed_ms": None,
         "level_low_elapsed_ms": None, "burst_phase_elapsed_ms": None,
         "conflict_elapsed_ms": None, "rest_elapsed_ms": None,
         "min_off_elapsed_ms": None}
    t.update(over)
    return t


def readings(level=None, float_dry=None, high_water=None, raining=None):
    return {"level_pct": level, "float_dry": float_dry,
            "high_water": high_water, "raining": raining}


# ---- The inversion: high_water STOPS the pump here ----

def test_container_full_stops_the_pump():
    st = dict(initial_state(), pump_state="ON")
    d = decide_collect(readings(high_water=True, float_dry=False),
                       timing(), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == CONTAINER_FULL
    assert d["flags"]["container_full"] is True


def test_container_full_prevents_starting():
    d = decide_collect(readings(high_water=True, float_dry=False, raining=True),
                       timing(rain_wet_elapsed_ms=30000), initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == CONTAINER_FULL


def test_container_full_outranks_confirmed_rain():
    # Rain says collect, the container says stop. Overflow is water damage.
    d = decide_collect(readings(high_water=True, float_dry=False, raining=True),
                       timing(rain_wet_elapsed_ms=999999), initial_state(), cfg())
    assert d["reason"] == CONTAINER_FULL


def test_drain_and_collect_disagree_on_the_same_input():
    # The whole point of Mode C, asserted explicitly so nobody "fixes" it.
    from control_logic import decide
    r = readings(high_water=True, float_dry=False)
    assert decide(r, timing(), initial_state(), cfg())["action"] == "ON"
    assert decide_collect(r, timing(), initial_state(), cfg())["action"] == "OFF"


# ---- Rain starts collection ----

def test_confirmed_rain_starts_collection():
    d = decide_collect(readings(raining=True, float_dry=False),
                       timing(rain_wet_elapsed_ms=30000), initial_state(), cfg())
    assert d["action"] == "ON" and d["reason"] == COLLECT_RAIN_ON


def test_unconfirmed_rain_does_not_start_collection():
    d = decide_collect(readings(raining=True, float_dry=False),
                       timing(rain_wet_elapsed_ms=29999), initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == "STANDBY"


# ---- Source dry ----

def test_dry_source_stops_after_the_delay():
    st = dict(initial_state(), pump_state="ON")
    d = decide_collect(readings(level=10, float_dry=False),
                       timing(level_low_elapsed_ms=30000), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == SOURCE_DRY


def test_dry_source_holds_before_the_delay():
    st = dict(initial_state(), pump_state="ON")
    d = decide_collect(readings(level=10, float_dry=False),
                       timing(level_low_elapsed_ms=5000), st, cfg())
    assert d["action"] == "HOLD"


def test_dry_source_prevents_starting_immediately():
    d = decide_collect(readings(level=10, float_dry=False, raining=True),
                       timing(rain_wet_elapsed_ms=30000), initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == SOURCE_DRY


# ---- The shared safety core still governs ----

def test_dry_run_interlock_still_applies():
    d = decide_collect(readings(float_dry=True), timing(), initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == "DRY_RUN_OFF"


def test_max_runtime_rest_still_applies():
    st = dict(initial_state(), resting=True)
    d = decide_collect(readings(raining=True, float_dry=False),
                       timing(rain_wet_elapsed_ms=30000, rest_elapsed_ms=0),
                       st, cfg())
    assert d["reason"] == "MAX_RUNTIME_REST"


def test_min_off_guard_still_applies():
    from control_logic import MIN_OFF_WAIT
    d = decide_collect(readings(raining=True, float_dry=False),
                       timing(rain_wet_elapsed_ms=30000, min_off_elapsed_ms=1000),
                       initial_state(), cfg(min_off_ms=180000))
    assert d["reason"] == MIN_OFF_WAIT


def test_conflict_burst_still_applies():
    # float says dry + two wet votes -> the guarded override, unchanged.
    d = decide_collect(readings(level=90, float_dry=True, high_water=True),
                       timing(burst_phase_elapsed_ms=0, conflict_elapsed_ms=0),
                       initial_state(), cfg())
    assert d["flags"]["sensor_conflict"] is True
    assert d["reason"] in ("CONFLICT_BURST_ON", "CONFLICT_BURST_REST")


def test_pump_holds_while_collecting_mid_band():
    st = dict(initial_state(), pump_state="ON")
    d = decide_collect(readings(level=50, float_dry=False), timing(), st, cfg())
    assert d["action"] == "HOLD"
