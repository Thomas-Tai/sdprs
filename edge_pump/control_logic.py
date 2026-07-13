# -*- coding: utf-8 -*-
"""Pure pump control decision logic — the safety core.

No hardware, no wall clock. Portable across MicroPython (device) and
CPython (desktop pytest). All time enters as pre-computed elapsed-ms
durations so the function is wrap-around safe.
"""

# ----- Reason codes -----
STANDBY = "STANDBY"
HYSTERESIS_ON = "HYSTERESIS_ON"
RAIN_TRIGGER = "RAIN_TRIGGER"
HIGH_WATER = "HIGH_WATER"
HOLD = "HOLD"
CONFLICT_BURST_ON = "CONFLICT_BURST_ON"
CONFLICT_BURST_REST = "CONFLICT_BURST_REST"
CONFLICT_LATCH_OFF = "CONFLICT_LATCH_OFF"
DRY_RUN_OFF = "DRY_RUN_OFF"
MAX_RUNTIME_REST = "MAX_RUNTIME_REST"

DEFAULT_CONFIG = {
    "high_threshold": 80.0,
    "low_threshold": 20.0,
    "rain_on_threshold": 60.0,
    "rain_confirm_ms": 30000,
    "dry_off_delay_ms": 30000,
    "burst_on_ms": 60000,
    "burst_cooldown_ms": 30000,
    "conflict_max_ms": 900000,
    "max_run_ms": 600000,
    "rest_ms": 60000,
}


def initial_state():
    return {"pump_state": "OFF", "conflict_latched": False,
            "conflict_holdoff": False, "burst_phase": None, "resting": False}


def _rain_confirmed(readings, timing, config):
    if readings.get("raining") is not True:
        return False
    e = timing.get("rain_wet_elapsed_ms")
    return e is not None and e >= config["rain_confirm_ms"]


def _wet_votes(readings, config, rain_confirmed):
    votes = 0
    if readings.get("high_water") is True:
        votes += 1
    if rain_confirmed:
        votes += 1
    level = readings.get("level_pct")
    if level is not None and level >= config["high_threshold"]:
        votes += 1
    return votes


def _mk(action, state, flags, reason):
    if action != "HOLD":
        state["pump_state"] = action
    return {"action": action, "next_state": state, "flags": flags, "reason": reason}


def decide(readings, timing, ctrl_state, config):
    """Return a pump decision from readings, elapsed-ms timers, and state.

    Caller (pump_controller) contract — the pure function relies on the caller
    to maintain these elapsed-ms timers in `timing` and reset them on the
    transitions decide() signals via `next_state`:
      - pump_on_elapsed_ms:    reset to 0 when the pump transitions to ON.
      - level_low_elapsed_ms:  continuous ms the analog level has been <= low.
      - rain_wet_elapsed_ms:   continuous ms rain has been asserted.
      - burst_phase_elapsed_ms: reset to 0 when next_state["burst_phase"] changes.
      - conflict_elapsed_ms:   reset to 0 when the conflict first latches.
      - rest_elapsed_ms:       continuous-OFF duration — reset to 0 on each ON->OFF
                               and cleared while ON, so the max-runtime rest measures
                               ACTUAL rest (a conflict burst that runs the pump
                               mid-rest restarts it rather than consuming it).
    Failing to reset a timer on its transition causes chatter (e.g. the conflict
    burst flapping every tick), so this contract is load-bearing.
    """
    state = dict(ctrl_state)
    float_dry = readings.get("float_dry")  # True=dry(danger), False=safe, None=off
    rain_confirmed = _rain_confirmed(readings, timing, config)
    votes = _wet_votes(readings, config, rain_confirmed)
    conflict_now = (float_dry is True) and (votes >= 2)

    flags = {
        "raining": readings.get("raining") is True,
        "float_safe": (float_dry is False) if float_dry is not None else None,
        "high_water": readings.get("high_water"),
        "sensor_conflict": False,
        "dry_run_protect": False,
        "max_runtime_rest": False,
    }

    # ---- Holdoff: ceiling was hit; stay OFF+alarm until sensors re-agree ----
    if state.get("conflict_holdoff"):
        if conflict_now:
            flags["sensor_conflict"] = True
            return _mk("OFF", state, flags, CONFLICT_LATCH_OFF)
        state["conflict_holdoff"] = False
        state["conflict_latched"] = False
        state["burst_phase"] = None

    # ---- Layer 1: guarded conflict override (bounded bursts) ----
    if conflict_now or state.get("conflict_latched"):
        if conflict_now:
            state["conflict_latched"] = True
            flags["sensor_conflict"] = True

            ce = timing.get("conflict_elapsed_ms")
            if ce is not None and ce >= config["conflict_max_ms"]:
                state["conflict_holdoff"] = True
                state["burst_phase"] = None
                return _mk("OFF", state, flags, CONFLICT_LATCH_OFF)

            phase = state.get("burst_phase") or "ON"
            pe = timing.get("burst_phase_elapsed_ms") or 0
            if phase == "ON":
                if pe >= config["burst_on_ms"]:
                    state["burst_phase"] = "REST"
                    return _mk("OFF", state, flags, CONFLICT_BURST_REST)
                state["burst_phase"] = "ON"
                return _mk("ON", state, flags, CONFLICT_BURST_ON)
            else:  # REST
                if pe >= config["burst_cooldown_ms"]:
                    state["burst_phase"] = "ON"
                    return _mk("ON", state, flags, CONFLICT_BURST_ON)
                state["burst_phase"] = "REST"
                return _mk("OFF", state, flags, CONFLICT_BURST_REST)

        # latched but no longer conflicting -> clear and fall through
        state["conflict_latched"] = False
        state["burst_phase"] = None

    state["burst_phase"] = None

    # ---- Layer 2: dry-run protection (hard interlock) ----
    if float_dry is True:
        flags["dry_run_protect"] = True
        return _mk("OFF", state, flags, DRY_RUN_OFF)

    # ---- Layer 3: max-runtime duty cycle (bounded rest prevents burnout) ----
    # After max_run_ms of continuous running the pump must rest for rest_ms
    # before any lower layer may restart it. `resting` is latched here and
    # cleared only once rest_elapsed_ms reaches rest_ms.
    on_elapsed = timing.get("pump_on_elapsed_ms")
    if state.get("resting"):
        rest_elapsed = timing.get("rest_elapsed_ms") or 0
        if rest_elapsed < config["rest_ms"]:
            flags["max_runtime_rest"] = True
            return _mk("OFF", state, flags, MAX_RUNTIME_REST)
        state["resting"] = False  # rest complete -> resume normal control
    elif state.get("pump_state") == "ON" and on_elapsed is not None \
            and on_elapsed >= config["max_run_ms"]:
        state["resting"] = True
        flags["max_runtime_rest"] = True
        return _mk("OFF", state, flags, MAX_RUNTIME_REST)

    # ---- Layer 4: trigger + hysteresis ----
    level = readings.get("level_pct")
    high_water = readings.get("high_water") is True
    on_threshold = config["rain_on_threshold"] if rain_confirmed else config["high_threshold"]

    if high_water:
        return _mk("ON", state, flags, HIGH_WATER)
    if level is not None and level >= on_threshold:
        return _mk("ON", state, flags, RAIN_TRIGGER if rain_confirmed else HYSTERESIS_ON)

    if state.get("pump_state") == "ON":
        if level is None:
            # digital-only mode: high_water already cleared -> stop
            return _mk("OFF", state, flags, STANDBY)
        low_elapsed = timing.get("level_low_elapsed_ms")
        # Confirmed rain suppresses the analog dry-off ONLY when the float
        # switch is present and reports safe (float_dry is False). With the
        # float disabled (None) the analog reading is the sole dry protection,
        # so rain must not override it.
        rain_holds_pump = rain_confirmed and (float_dry is False)
        if level <= config["low_threshold"] and not high_water \
                and not rain_holds_pump and low_elapsed is not None \
                and low_elapsed >= config["dry_off_delay_ms"]:
            return _mk("OFF", state, flags, STANDBY)
        return _mk("HOLD", state, flags, HOLD)

    # ---- Layer 5: standby ----
    return _mk("OFF", state, flags, STANDBY)
