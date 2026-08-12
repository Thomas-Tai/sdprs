from control_logic import decide, initial_state, DEFAULT_CONFIG, MIN_OFF_WAIT


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


def test_min_off_blocks_restart_inside_the_window():
    d = decide(readings(high_water=True, float_dry=False),
               timing(min_off_elapsed_ms=60000),
               initial_state(), cfg(min_off_ms=180000))
    assert d["action"] == "OFF" and d["reason"] == MIN_OFF_WAIT
    assert d["flags"]["min_off_wait"] is True


def test_min_off_reports_remaining_time():
    d = decide(readings(high_water=True, float_dry=False),
               timing(min_off_elapsed_ms=60000),
               initial_state(), cfg(min_off_ms=180000))
    assert d["flags"]["min_off_remaining_ms"] == 120000


def test_min_off_releases_after_the_window():
    d = decide(readings(high_water=True, float_dry=False),
               timing(min_off_elapsed_ms=180000),
               initial_state(), cfg(min_off_ms=180000))
    assert d["action"] == "ON" and d["reason"] == "HIGH_WATER"


def test_min_off_disabled_by_zero():
    d = decide(readings(high_water=True, float_dry=False),
               timing(min_off_elapsed_ms=0),
               initial_state(), cfg(min_off_ms=0))
    assert d["action"] == "ON"


def test_min_off_ignores_a_null_timer():
    # Cold boot: the pump has never run, so there is no off-period to
    # measure. Boot hold-off covers this window instead (spec §5.6).
    d = decide(readings(high_water=True, float_dry=False),
               timing(min_off_elapsed_ms=None),
               initial_state(), cfg(min_off_ms=180000))
    assert d["action"] == "ON"


def test_min_off_does_not_stop_a_running_pump():
    # The guard limits START frequency. Interrupting a running pump would
    # be the very short-cycling it exists to prevent.
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(high_water=True, float_dry=False),
               timing(min_off_elapsed_ms=0), st, cfg(min_off_ms=180000))
    assert d["action"] != "OFF"


def test_dry_run_still_outranks_min_off():
    d = decide(readings(float_dry=True),
               timing(min_off_elapsed_ms=0),
               initial_state(), cfg(min_off_ms=180000))
    assert d["reason"] == "DRY_RUN_OFF"


def test_max_runtime_rest_still_outranks_min_off():
    st = dict(initial_state(), resting=True)
    d = decide(readings(high_water=True, float_dry=False),
               timing(rest_elapsed_ms=0, min_off_elapsed_ms=0), st,
               cfg(min_off_ms=180000))
    assert d["reason"] == "MAX_RUNTIME_REST"


def test_min_off_does_not_consume_the_rest_timer():
    # Independent timers: rest_elapsed_ms=None means "not resting" to
    # Layer 3 only because it coerces None to 0. min-off must not inherit
    # that coercion (spec §5.4).
    d = decide(readings(high_water=True, float_dry=False),
               timing(rest_elapsed_ms=None, min_off_elapsed_ms=1000),
               initial_state(), cfg(min_off_ms=180000))
    assert d["reason"] == MIN_OFF_WAIT
