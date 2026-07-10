from control_logic import decide, initial_state, DEFAULT_CONFIG


def cfg(**over):
    c = dict(DEFAULT_CONFIG)
    c.update(over)
    return c


def timing(**over):
    t = {
        "pump_on_elapsed_ms": None, "rain_wet_elapsed_ms": None,
        "level_low_elapsed_ms": None, "burst_phase_elapsed_ms": None,
        "conflict_elapsed_ms": None, "rest_elapsed_ms": None,
    }
    t.update(over)
    return t


def readings(level=None, float_dry=None, high_water=None, raining=None):
    return {"level_pct": level, "float_dry": float_dry,
            "high_water": high_water, "raining": raining}


# ---- Layer 4/5: hysteresis + standby ----

def test_off_turns_on_above_high_threshold():
    d = decide(readings(level=85, float_dry=False), timing(),
               initial_state(), cfg())
    assert d["action"] == "ON" and d["reason"] == "HYSTERESIS_ON"
    assert d["next_state"]["pump_state"] == "ON"


def test_on_holds_inside_hysteresis_band():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=50, float_dry=False), timing(), st, cfg())
    assert d["action"] == "HOLD" and d["next_state"]["pump_state"] == "ON"


def test_off_stays_off_inside_band():
    d = decide(readings(level=50, float_dry=False), timing(),
               initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == "STANDBY"


def test_on_turns_off_below_low_after_dry_off_delay():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=15, float_dry=False),
               timing(level_low_elapsed_ms=30000), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "STANDBY"


def test_on_holds_below_low_before_dry_off_delay():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=15, float_dry=False),
               timing(level_low_elapsed_ms=5000), st, cfg())
    assert d["action"] == "HOLD"


# ---- Rain trigger ----

def test_rain_confirmed_lowers_on_threshold():
    d = decide(readings(level=65, float_dry=False, raining=True),
               timing(rain_wet_elapsed_ms=30000), initial_state(), cfg())
    assert d["action"] == "ON" and d["reason"] == "RAIN_TRIGGER"


def test_rain_not_yet_confirmed_does_not_lower_threshold():
    d = decide(readings(level=65, float_dry=False, raining=True),
               timing(rain_wet_elapsed_ms=1000), initial_state(), cfg())
    assert d["action"] == "OFF"


# ---- High-water digital trigger ----

def test_high_water_forces_on():
    d = decide(readings(level=10, float_dry=False, high_water=True),
               timing(), initial_state(), cfg())
    assert d["action"] == "ON" and d["reason"] == "HIGH_WATER"


def test_digital_only_off_when_high_water_clears():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=None, float_dry=False, high_water=False),
               timing(), st, cfg())
    assert d["action"] == "OFF"


# ---- Layer 2: dry-run protection ----

def test_dry_float_forces_off():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=90, float_dry=True), timing(), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "DRY_RUN_OFF"
    assert d["flags"]["dry_run_protect"] is True


def test_dry_float_absolute_with_single_wet_vote():
    # float dry + only high_water true (1 vote) -> dry-run wins, no conflict
    d = decide(readings(level=10, float_dry=True, high_water=True, raining=False),
               timing(), initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == "DRY_RUN_OFF"


# ---- Layer 3: max-runtime duty cycle ----

def test_max_runtime_forces_rest():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=90, float_dry=False),
               timing(pump_on_elapsed_ms=600000), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "MAX_RUNTIME_REST"
    assert d["flags"]["max_runtime_rest"] is True


# ---- Layer 1: guarded conflict override ----

def test_conflict_two_wet_votes_starts_burst_on():
    # float dry BUT high_water true AND rain confirmed => 2 votes => burst
    d = decide(readings(level=10, float_dry=True, high_water=True, raining=True),
               timing(rain_wet_elapsed_ms=30000), initial_state(), cfg())
    assert d["action"] == "ON" and d["reason"] == "CONFLICT_BURST_ON"
    assert d["flags"]["sensor_conflict"] is True
    assert d["next_state"]["conflict_latched"] is True
    assert d["next_state"]["burst_phase"] == "ON"


def test_conflict_rain_plus_analog_high_is_two_votes():
    d = decide(readings(level=85, float_dry=True, high_water=False, raining=True),
               timing(rain_wet_elapsed_ms=30000), initial_state(), cfg())
    assert d["reason"] == "CONFLICT_BURST_ON"


def test_conflict_burst_on_expires_to_rest():
    st = dict(initial_state(), conflict_latched=True, burst_phase="ON")
    d = decide(readings(float_dry=True, high_water=True, raining=True),
               timing(rain_wet_elapsed_ms=30000, burst_phase_elapsed_ms=60000,
                      conflict_elapsed_ms=60000), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "CONFLICT_BURST_REST"
    assert d["next_state"]["burst_phase"] == "REST"


def test_conflict_burst_rest_expires_back_to_on():
    st = dict(initial_state(), conflict_latched=True, burst_phase="REST")
    d = decide(readings(float_dry=True, high_water=True, raining=True),
               timing(rain_wet_elapsed_ms=30000, burst_phase_elapsed_ms=30000,
                      conflict_elapsed_ms=120000), st, cfg())
    assert d["action"] == "ON" and d["reason"] == "CONFLICT_BURST_ON"


def test_conflict_ceiling_latches_off_alarm():
    st = dict(initial_state(), conflict_latched=True, burst_phase="ON")
    d = decide(readings(float_dry=True, high_water=True, raining=True),
               timing(rain_wet_elapsed_ms=30000, conflict_elapsed_ms=900000), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "CONFLICT_LATCH_OFF"
    assert d["next_state"]["conflict_holdoff"] is True
    assert d["flags"]["sensor_conflict"] is True


def test_holdoff_clears_when_sensors_reagree():
    st = dict(initial_state(), conflict_latched=True, conflict_holdoff=True)
    d = decide(readings(float_dry=False, high_water=False, raining=False),
               timing(), st, cfg())
    # sensors agree again -> holdoff cleared, normal ladder resumes
    assert d["next_state"]["conflict_holdoff"] is False
    assert d["next_state"]["conflict_latched"] is False


def test_conflict_clears_when_float_recovers():
    st = dict(initial_state(), conflict_latched=True, burst_phase="ON")
    d = decide(readings(level=90, float_dry=False, high_water=True, raining=True),
               timing(rain_wet_elapsed_ms=30000, conflict_elapsed_ms=5000), st, cfg())
    # float safe now -> conflict clears, normal ladder (high water -> ON)
    assert d["next_state"]["conflict_latched"] is False
    assert d["action"] == "ON" and d["reason"] == "HIGH_WATER"


# ---- Layer 3: max-runtime rest actually holds across ticks ----

def test_max_runtime_rest_holds_until_rest_ms_then_resumes():
    st = dict(initial_state(), pump_state="ON")
    # tick 1: hit the ceiling -> forced OFF, resting latched
    d1 = decide(readings(level=90, float_dry=False),
                timing(pump_on_elapsed_ms=600000), st, cfg())
    assert d1["action"] == "OFF" and d1["reason"] == "MAX_RUNTIME_REST"
    assert d1["next_state"]["resting"] is True
    # tick 2: still resting, rest_ms not elapsed -> stays OFF despite high level
    d2 = decide(readings(level=90, float_dry=False),
                timing(rest_elapsed_ms=1000), d1["next_state"], cfg())
    assert d2["action"] == "OFF" and d2["reason"] == "MAX_RUNTIME_REST"
    assert d2["next_state"]["resting"] is True
    # tick 3: rest complete -> resumes normal control, turns back ON
    d3 = decide(readings(level=90, float_dry=False),
                timing(rest_elapsed_ms=60000), d2["next_state"], cfg())
    assert d3["action"] == "ON" and d3["reason"] == "HYSTERESIS_ON"
    assert d3["next_state"]["resting"] is False


def test_dry_run_preempts_max_runtime():
    # float dry AND over max-run: Layer 2 (dry-run) must win over Layer 3
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=90, float_dry=True),
               timing(pump_on_elapsed_ms=600000), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "DRY_RUN_OFF"


# ---- Degradation: all sensors disabled/None ----

def test_all_sensors_none_off_stays_standby():
    d = decide(readings(), timing(), initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == "STANDBY"


def test_all_sensors_none_running_stops_safely():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(), timing(), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "STANDBY"


# ---- Dry-off vs rain suppression depends on float availability ----

def test_low_level_holds_during_rain_when_float_safe():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=15, float_dry=False, raining=True),
               timing(rain_wet_elapsed_ms=30000, level_low_elapsed_ms=30000),
               st, cfg())
    assert d["action"] == "HOLD"


def test_low_level_dry_off_during_rain_when_float_disabled():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=15, float_dry=None, raining=True),
               timing(rain_wet_elapsed_ms=30000, level_low_elapsed_ms=30000),
               st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "STANDBY"
