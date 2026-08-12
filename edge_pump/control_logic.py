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
MANUAL_ON = "MANUAL_ON"
MANUAL_OFF = "MANUAL_OFF"
MANUAL_REJECTED = "MANUAL_REJECTED"
MIN_OFF_WAIT = "MIN_OFF_WAIT"
CONTAINER_FULL = "CONTAINER_FULL"
COLLECT_RAIN_ON = "COLLECT_RAIN_ON"
SOURCE_DRY = "SOURCE_DRY"
# Imposed ABOVE the pure core (main.apply_boot_holdoff / apply_overload_interlock
# / the config-error loop), so decide() never returns these — they live here only
# so every reason string the fleet can publish has one home. Adding them does not
# change decide()'s output, so the golden baseline is unaffected.
BOOT_HOLDOFF = "BOOT_HOLDOFF"
CONFIG_ERROR = "CONFIG_ERROR"
OVERLOAD_TRIP = "OVERLOAD_TRIP"

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
    "min_off_ms": 0,          # 0 disables Layer 3.5 (12V profile)
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


def _preamble(readings, timing, config):
    """Derive the mode-independent view of the world: sensor truth, vote
    tally, conflict state, and the flags dict every layer annotates."""
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
        "min_off_wait": False,
        "container_full": False,
    }
    return float_dry, rain_confirmed, conflict_now, flags


def _safety_guards(state, timing, config, float_dry, conflict_now, flags):
    """Layers 1-3: the mode-INDEPENDENT safety core.

    Returns a decision dict to return immediately, or None to fall through
    to the mode's trigger layer. Mutates `state` in place on fall-through
    paths (clearing latches), which the caller relies on.

    Nothing in here may consult PUMP_MODE. Whether high water means "flood"
    or "container full" is the trigger layer's business; whether the pump is
    allowed to run at all is this function's, and the answer is the same in
    both modes.
    """
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
            flags["rest_remaining_ms"] = config["rest_ms"] - rest_elapsed
            return _mk("OFF", state, flags, MAX_RUNTIME_REST)
        state["resting"] = False  # rest complete -> resume normal control
    elif state.get("pump_state") == "ON" and on_elapsed is not None \
            and on_elapsed >= config["max_run_ms"]:
        state["resting"] = True
        flags["max_runtime_rest"] = True
        return _mk("OFF", state, flags, MAX_RUNTIME_REST)

    # ---- Layer 3.5: minimum-off short-cycle guard ----
    # Deliberately uses its OWN timer rather than rest_elapsed_ms. The two
    # have opposite None contracts: Layer 3 coerces None to 0 (blocking),
    # min-off treats None as not-blocking (cold boot has no off-period to
    # measure). One timer read two opposite ways is how a future edit to
    # that `or 0` silently changes this guard (spec §5.4).
    min_off_ms = config.get("min_off_ms") or 0
    if min_off_ms and state.get("pump_state") != "ON":
        mo = timing.get("min_off_elapsed_ms")
        if mo is not None and mo < min_off_ms:
            flags["min_off_wait"] = True
            flags["min_off_remaining_ms"] = min_off_ms - mo
            return _mk("OFF", state, flags, MIN_OFF_WAIT)

    return None


def _trigger_drain(readings, timing, state, config, flags, float_dry, rain_confirmed):
    """Layers 4-5 for DRAIN: high water is an emergency, pump it out."""
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


def decide(readings, timing, ctrl_state, config):
    """Return a pump decision from readings, elapsed-ms timers, and state.

    DRAIN mode (the historical default). COLLECT lives in decide_collect();
    both share _safety_guards().

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
    float_dry, rain_confirmed, conflict_now, flags = _preamble(readings, timing, config)

    guarded = _safety_guards(state, timing, config, float_dry, conflict_now, flags)
    if guarded is not None:
        return guarded

    return _trigger_drain(readings, timing, state, config, flags,
                          float_dry, rain_confirmed)


def _trigger_collect(readings, timing, state, config, flags, float_dry, rain_confirmed):
    """Layers 4-5 for COLLECT: the pump fills a container of finite size.

    high_water INVERTS relative to DRAIN. In DRAIN it means "flood, run
    hard"; here it means "container full, stop" — and it is also the alert
    a human must act on by emptying the container.
    """
    # Container full wins over everything below: overflow is water damage.
    if readings.get("high_water") is True:
        flags["container_full"] = True
        return _mk("OFF", state, flags, CONTAINER_FULL)

    # Source exhausted. Distinct from Layer 2's hard dry-run interlock:
    # that one protects the pump, this one just says there is nothing left
    # to collect.
    level = readings.get("level_pct")
    if level is not None and level <= config["low_threshold"]:
        if state.get("pump_state") == "ON":
            low_elapsed = timing.get("level_low_elapsed_ms")
            if low_elapsed is not None and low_elapsed >= config["dry_off_delay_ms"]:
                return _mk("OFF", state, flags, SOURCE_DRY)
            return _mk("HOLD", state, flags, HOLD)
        return _mk("OFF", state, flags, SOURCE_DRY)

    if rain_confirmed:
        return _mk("ON", state, flags, COLLECT_RAIN_ON)

    if state.get("pump_state") == "ON":
        return _mk("HOLD", state, flags, HOLD)
    return _mk("OFF", state, flags, STANDBY)


def decide_collect(readings, timing, ctrl_state, config):
    """COLLECT-mode counterpart to decide(). Same signature, same return
    shape, same safety core — only Layer 4/5 differs (spec §3, §5.4).

    The caller timing contract in decide()'s docstring applies unchanged.
    """
    state = dict(ctrl_state)
    float_dry, rain_confirmed, conflict_now, flags = _preamble(readings, timing, config)

    guarded = _safety_guards(state, timing, config, float_dry, conflict_now, flags)
    if guarded is not None:
        return guarded

    return _trigger_collect(readings, timing, state, config, flags,
                            float_dry, rain_confirmed)
