# -*- coding: utf-8 -*-
"""SDPRS 水泵節點主程式 — 精簡協調層。
讀感測器 -> decide() -> 執行 -> 發布(盡力) -> 餵狗(僅在成功迭代後)。
離線自治：所有網路操作為盡力而為且有時限，永不影響控制。"""

import time
import config
import control_logic
import profiles
from sensors import SensorSet
from pump_controller import PumpController
from mqtt_client import PumpMQTTClient


def build_config(profile=None):
    cfg = {
        "high_threshold": float(config.HIGH_THRESHOLD),
        "low_threshold": float(config.LOW_THRESHOLD),
        "rain_on_threshold": float(config.RAIN_ON_THRESHOLD),
        "rain_confirm_ms": config.RAIN_CONFIRM_MS,
        "dry_off_delay_ms": config.DRY_OFF_DELAY_MS,
        "burst_on_ms": config.BURST_ON_MS,
        "burst_cooldown_ms": config.BURST_COOLDOWN_MS,
        "conflict_max_ms": config.CONFLICT_MAX_MS,
        "max_run_ms": config.MAX_RUN_MS,
        "rest_ms": config.REST_MS,
        "min_off_ms": 0,
    }
    if profile is not None:
        # Profile timings OVERRIDE the module defaults — config.py carries
        # the 12V-era values and the mains profile must win on a mains node.
        cfg["burst_cooldown_ms"] = profile["burst_cooldown_ms"]
        cfg["min_off_ms"] = profile["min_off_ms"]
    return cfg


def build_decider(mode):
    """Pick the trigger layer for this node's PUMP_MODE.

    Resolved ONCE at boot rather than per iteration: a mode that could
    change mid-run would mean high_water flipping meaning between ticks.
    """
    if mode == "DRAIN":
        return control_logic.decide
    if mode == "COLLECT":
        return control_logic.decide_collect
    raise ValueError("unknown PUMP_MODE: %r" % (mode,))


def build_sensor_config(profile=None):
    cfg = {
        "level_enabled": config.LEVEL_ENABLED,
        "float_enabled": config.FLOAT_ENABLED,
        "rain_enabled": config.RAIN_ENABLED,
        "high_water_enabled": config.HIGH_WATER_ENABLED,
        "float_active_low": config.FLOAT_ACTIVE_LOW,
        "rain_active_low": config.RAIN_ACTIVE_LOW,
        "high_water_active_low": config.HIGH_WATER_ACTIVE_LOW,
        "debounce_ms": config.DEBOUNCE_MS,
        "adc_pin": config.ADC_PIN, "float_pin": config.FLOAT_PIN,
        "rain_pin": config.RAIN_PIN, "high_water_pin": config.HIGH_WATER_PIN,
        "ct_enabled": False, "hoa_enabled": False,
        "ct_adc_pin": config.CT_ADC_PIN, "hoa_hand_pin": config.HOA_HAND_PIN,
        "hoa_hand_active_low": config.HOA_HAND_ACTIVE_LOW,
    }
    if profile is not None:
        cfg["ct_enabled"] = profile["ct_enabled"]
        cfg["hoa_enabled"] = profile["hoa_enabled"]
    return cfg


def synthesize_display_level(readings):
    """Publish-only water level for the dashboard % bar.

    When an analog probe is wired (LEVEL_ENABLED=True) returns its reading
    unchanged. When no analog probe is present, derives a coarse 3-step value
    from the digital sensors so the dashboard % bar still responds:
      100.0 if high_water is asserted
       50.0 if the float reports safe (water above the dry-run float)
        0.0 otherwise
    Never used by control_logic — that path handles level_pct=None natively
    (digital-only mode with high_water as the sole ON trigger). This is the
    publish-side display fallback only.
    """
    level = readings.get("level_pct")
    if level is not None:
        return level
    if readings.get("high_water") is True:
        return 100.0
    if readings.get("float_dry") is False:
        return 50.0
    return 0.0


# Flag -> the flag carrying its remaining time, for operator feedback.
_REMAINING_KEY = {
    "max_runtime_rest": "rest_remaining_ms",
    "min_off_wait": "min_off_remaining_ms",
    "boot_holdoff": "boot_holdoff_remaining_ms",
}


def apply_manual_override(decision, manual_state, clock, strict=False):
    """Optionally override the pure control decision with an operator command.

    Two-slot manual state:
    {"action": "ON"|"OFF"|"AUTO"|None, "expires_ms": int|None}.
    Returns a new (decision, manual_state) tuple — either passed through
    unchanged or mutated by the override / expiry / release / rejection paths.

    Contract:
      - Manual OFF is ALWAYS honored (safe direction — stopping never damages).
      - Manual ON is REJECTED (dropped, no retry) when the safety core has
        engaged `dry_run_protect` or `sensor_conflict` — the pump would
        damage itself running dry or with contradicting sensors.
      - Under `strict=True` (the SOCKET_220V profile) the rejection list
        also covers `max_runtime_rest` and `min_off_wait`. Restarting a
        motor that just ran for ten minutes is how you burn it; at 12V the
        same click is harmless, which is why this is profile-gated rather
        than unconditional (spec §5.7).
      - Manual AUTO releases the hold: the slot is cleared and the natural
        control decision passes through untouched.
      - ON/OFF auto-expire once `expires_ms` is reached (bounded pulse).
        `expires_ms=None` means indefinite (used for OFF-latch, discouraged
        for ON via the server-side API). An indefinite OFF is exactly why
        AUTO exists — without it a hold placed before an operator went off
        shift would survive into the next rain event.

    Design note: no state is added to control_logic — that module stays pure
    and decides the "normal" outcome, this wrapper layers an override on top.
    All safety flags from the underlying decision are preserved so the payload
    still reports why the pump would-otherwise-be-doing what it's doing.
    """
    action = manual_state.get("action")
    if action is None:
        return decision, manual_state

    expires = manual_state.get("expires_ms")
    if expires is not None and clock.ticks_diff(clock.ticks_ms(), expires) >= 0:
        # Expired — clear and pass through the natural decision.
        return decision, {"action": None, "expires_ms": None}

    flags = decision["flags"]

    if action == "OFF":
        next_state = dict(decision["next_state"])
        next_state["pump_state"] = "OFF"
        return {
            "action": "OFF",
            "next_state": next_state,
            "flags": dict(flags, manual_override="OFF"),
            "reason": control_logic.MANUAL_OFF,
        }, manual_state

    if action == "ON":
        # `boot_holdoff` blocks unconditionally rather than only under
        # strict: the flag exists only while a hold-off is actually
        # running, and a profile with no hold-off configured never sets
        # it. Gating it on `strict` would add a second way to be wrong
        # without adding any 12V freedom.
        blocking = ["dry_run_protect", "sensor_conflict", "boot_holdoff"]
        if strict:
            blocking += ["max_runtime_rest", "min_off_wait"]
        hit = next((f for f in blocking if flags.get(f)), None)
        if hit is not None:
            # Refuse and DROP the override so it doesn't retry every tick.
            # `last_rejected_remaining_ms` lets the dashboard say WHY the
            # click did nothing and when it will work, instead of silently
            # doing nothing.
            remaining_key = _REMAINING_KEY.get(hit)
            remaining = flags.get(remaining_key) if remaining_key else None
            return decision, {"action": None, "expires_ms": None,
                              "last_rejected": control_logic.MANUAL_REJECTED,
                              "last_rejected_flag": hit,
                              "last_rejected_remaining_ms": remaining}
        next_state = dict(decision["next_state"])
        next_state["pump_state"] = "ON"
        return {
            "action": "ON",
            "next_state": next_state,
            "flags": dict(flags, manual_override="ON"),
            "reason": control_logic.MANUAL_ON,
        }, manual_state

    if action == "AUTO":
        # Explicit release — drop the hold, pass the natural decision through
        # untouched (no manual_override flag, so telemetry stops reporting a
        # hold on the very next publish).
        #
        # Back-compat note: firmware flashed BEFORE this branch existed also
        # releases correctly on "AUTO" — it falls through to the unknown-action
        # path below, which clears the slot exactly the same way. The server
        # may therefore send AUTO to any node in the fleet without waiting for
        # a reflash. Handling it explicitly here is about intent, not function:
        # a release is a first-class command, not a malformed payload.
        return decision, {"action": None, "expires_ms": None}

    # Unknown action — ignore, clear the slot to avoid a stuck state.
    return decision, {"action": None, "expires_ms": None}


def apply_overload_interlock(decision, verdict):
    """Force OFF on an overload verdict, as a COMPLETE decision dict.

    This is the ONE place CT data touches the control path, and it is
    hardware protection sitting ABOVE decide() rather than a control
    decision inside it — the pure safety core stays CT-free (spec §5.5).

    NEVER drive the relay directly here. Poking the pin behind the state
    machine leaves ctrl_state saying ON while the contactor is open:
    _on_since keeps accumulating against a pump that is not running, the
    next decide() returns HOLD (which correctly leaves the relay alone, so
    everything LOOKS fine), and because no ON->OFF transition was recorded
    the rest timer never starts. apply_manual_override() already returns a
    decision rather than acting — this follows the same shape.
    """
    if verdict != "OVERLOAD":
        return decision
    next_state = dict(decision["next_state"])
    next_state["pump_state"] = "OFF"
    return {
        "action": "OFF",
        "next_state": next_state,
        "flags": dict(decision["flags"], overload_trip=True),
        "reason": control_logic.OVERLOAD_TRIP,
    }


def apply_boot_holdoff(decision, remaining_ms):
    """Force the pump OFF while the post-reset hold-off is still running.

    NOTE THE ASYMMETRY WITH ITS NAME: this does not merely veto a *start*,
    it drives the actuator OFF on any tick where time remains — including
    a tick where the pump is already running. During the boot window that
    is the same thing (the pump starts OFF), which is why the caller must
    never let the hold-off come back after it has expired. See
    `boot_guard.make_holdoff_tracker`.

    `_off_since` lives in RAM, so every reset makes the node believe the
    pump has never run and the min-off guard passes instantly. A WDT reset
    loop at the 30s timeout would therefore restart a 2200W motor every 30
    seconds, and each boot would consider that correct (spec §5.6).

    This sits ABOVE decide() for the same reason apply_overload_interlock()
    does: it is not a control decision, and a fresh decision must not be
    able to clear it. Returns the decision object UNCHANGED once the
    hold-off expires, so the normal path resumes with no residue.

    The `boot_holdoff` flag it sets is also read by apply_manual_override(),
    which is what stops an operator clicking ON straight through a hold-off.
    """
    if not remaining_ms or remaining_ms <= 0:
        return decision
    next_state = dict(decision["next_state"])
    next_state["pump_state"] = "OFF"
    return {
        "action": "OFF",
        "next_state": next_state,
        "flags": dict(decision["flags"],
                      boot_holdoff=True,
                      boot_holdoff_remaining_ms=remaining_ms),
        "reason": control_logic.BOOT_HOLDOFF,
    }


def run_iteration(sensor_set, pump, mqtt, cfg, publish_cb,
                  manual_state=None, clock=None, decider=None, strict=False,
                  boot_holdoff=None, ct_read=None):
    """One control-loop body. Pure of hardware except via injected objects.

    `decider` defaults to DRAIN so pre-existing callers and tests keep
    working; `main()` passes the mode-resolved function.

    `boot_holdoff` is an optional callable `(readings) -> remaining_ms`.
    It is re-evaluated EVERY tick, not resolved once at boot, because the
    hold-off shortens when the mode layer reports urgency — a live flood
    must not wait the full 60s (spec §5.6).
    """
    readings = sensor_set.read_all()
    timing = pump.snapshot_timing(readings)
    decide_fn = decider or control_logic.decide
    decision = decide_fn(readings, timing, pump.ctrl_state, cfg)

    # Post-reset hold-off sits directly above decide() and BEFORE the
    # manual override, so the flag it sets is visible to the override's
    # rejection list.
    if boot_holdoff is not None:
        decision = apply_boot_holdoff(decision, boot_holdoff(readings))

    # CT feedback sits ABOVE decide() — hardware protection, not a control
    # decision, so the pure safety core stays CT-free (spec §5.5).
    # `pump.state` is the ACTUATOR's current state, not this tick's
    # decision, so the CT read is unaffected by the hold-off above it.
    ct_verdict = None
    if ct_read is not None:
        band, hoa_hand, ct_verdict = ct_read(pump.state == "ON")
        decision = apply_overload_interlock(decision, ct_verdict)
        readings["_current_band"] = band
        readings["_hoa_hand"] = hoa_hand

    if manual_state is not None and clock is not None:
        decision, new_manual = apply_manual_override(decision, manual_state, clock,
                                                     strict=strict)
        # In-place mutation so the caller's dict reference stays valid.
        # BUT: apply_manual_override may return the SAME reference for the
        # "still-active, pass through" case — clearing manual_state would
        # then wipe new_manual too and the override would evaporate after
        # one publish cycle. Only copy back when it's a distinct object.
        if new_manual is not manual_state:
            manual_state.clear()
            manual_state.update(new_manual)
    pump.apply(decision)
    # `manual_state` has already been updated in place above, so a rejection
    # recorded this tick is visible here. Without these two keys the
    # remaining time computed by apply_manual_override never leaves the
    # device and spec §5.7's "tell the operator why the click did nothing"
    # is not delivered by anything.
    manual = manual_state or {}
    publish_cb(pump_state=pump.state,
               water_level=synthesize_display_level(readings),
               flags=decision["flags"], reason=decision["reason"],
               extra={"current_band": readings.get("_current_band"),
                      "hoa_hand": readings.get("_hoa_hand"),
                      "ct_verdict": ct_verdict,
                      "manual_rejected": manual.get("last_rejected_flag"),
                      "manual_rejected_remaining_ms":
                          manual.get("last_rejected_remaining_ms")})
    return decision


def resolve_runtime():
    """Resolve (profile, decider) from config WITHOUT raising.

    Returns (profile, decider, error_message|None).

    Why this exists: main()'s init block ends in
    `except Exception: ... machine.reset()`. A ValueError from
    profiles.validate() or build_decider() is a CONFIGURATION error, so it
    recurs on every boot — reset, re-raise, reset, forever. It also fires
    before register_boot(), so the boot counter never moves and the node
    leaves no trace of why it vanished. `WDT_ENABLED = False` is a
    documented debugging step and `PUMP_MODE` is one typo away from
    unknown, so this is a likely mistake; under the §4.8 split-bay decision
    recovering from it means a site visit.

    On error we return an INERT stand-in profile. It is not a fallback the
    node runs on — main() refuses to enter the control loop at all. It
    exists only so the rest of init can finish constructing the objects the
    error loop needs (a PUMP_12V profile touches no CT or HOA pins and
    no-ops the contactor counter), and so nothing downstream has to handle
    `profile is None`.
    """
    try:
        profile = profiles.get_profile(config.ACTUATOR_PROFILE)
        profiles.validate(profile, config.WDT_ENABLED)
        return profile, build_decider(config.PUMP_MODE), None
    except (ValueError, KeyError) as e:
        return profiles.get_profile("PUMP_12V"), control_logic.decide, str(e)


def _run_config_error_loop(pump, mqtt, wdt, message):
    """Hold the pump OFF and keep saying why, without ever resetting.

    A configuration error is not transient — resetting reproduces it on the
    next boot and the node disappears instead of reporting. OFF is already
    the safe physical state (the coil is de-energised and the contactor
    drops out), so the useful thing left to do is stay reachable and keep
    publishing the reason. An operator seeing CONFIG_ERROR knows to reflash;
    an operator seeing nothing at all has to drive out and open the box.
    """
    print("[MAIN] CONFIG ERROR (refusing to run): %s" % message)
    off_state = dict(pump.ctrl_state)
    off_state["pump_state"] = "OFF"
    pump.apply({"action": "OFF", "next_state": off_state,
                "flags": {"config_error": True},
                "reason": control_logic.CONFIG_ERROR})
    while True:
        try:
            mqtt.publish_status("OFF", 0.0, {"config_error": True},
                                control_logic.CONFIG_ERROR)
            mqtt.check_msg()
        except Exception:
            pass          # network trouble must not turn this into a reset
        if wdt:
            wdt.feed()
        time.sleep(config.POLL_INTERVAL)


def main():
    print("[MAIN] SDPRS Pump Node starting (merged firmware)...")
    wdt = None
    try:
        import machine
        import persist
        import boot_guard
        profile, decider, config_error = resolve_runtime()
        nvs = persist.open_nvs()
        # Only profiles that USE the counter pay for it. Under PUMP_12V,
        # boot_loop_threshold is 0 (detection off) and boot_healthy_ms is 0
        # (is_boot_healthy is never True, so clear_boot_count is never
        # called) — registering there would burn one flash erase cycle per
        # boot on a monotonic counter with no reset path, on exactly the
        # bench nodes that get power-cycled most.
        if profile["boot_loop_threshold"] > 0:
            boot_count = persist.register_boot(nvs)
        else:
            boot_count = 0
        reset_loop = boot_guard.is_reset_loop(boot_count,
                                              profile["boot_loop_threshold"])
        print("[MAIN] profile=%s mode=%s boot_count=%d reset_loop=%s"
              % (config.ACTUATOR_PROFILE, config.PUMP_MODE, boot_count, reset_loop))
        if config.WDT_ENABLED:
            from machine import WDT
            wdt = WDT(timeout=config.WDT_TIMEOUT)
        from sensors import build_readers
        readers = build_readers(build_sensor_config(profile))
        relay = machine.Pin(config.RELAY_PIN, machine.Pin.OUT)
        led_red = machine.Pin(config.LED_RED_PIN, machine.Pin.OUT)
        led_green = machine.Pin(config.LED_GREEN_PIN, machine.Pin.OUT)
        clock = _RealClockShim()
        sensor_set = SensorSet(build_sensor_config(profile), readers, clock)

        ct_read = None
        if profile["ct_enabled"]:
            import current_sense
            ct_thresholds = current_sense.build_thresholds(
                config.CT_BAND_LOW, config.CT_BAND_NORMAL, config.CT_BAND_HIGH)
            ct_samples = current_sense.sample_count_for_cycles(
                config.CT_SAMPLE_RATE_HZ, config.CT_SAMPLE_CYCLES)
            ct_reader = readers.get("ct")
            hoa_reader = readers.get("hoa_hand")

            def ct_read(commanded_on):
                """Blocking RMS burst. ~60ms at the shipped defaults — the
                budget spec §9.5 flags as unverified against MQTT + the 30s
                WDT. Measure on the bench before trusting it."""
                try:
                    samples = [ct_reader() for _ in range(ct_samples)]
                    rms = current_sense.rms_from_samples(samples)
                    band = current_sense.classify_band(rms, ct_thresholds)
                except Exception as e:
                    print("[CT] read failed: %s" % str(e))
                    return None, None, None
                hoa_hand = None
                if hoa_reader is not None:
                    raw = hoa_reader()
                    hoa_hand = (raw == 0) if config.HOA_HAND_ACTIVE_LOW else (raw == 1)
                return band, hoa_hand, current_sense.diagnose(commanded_on, band,
                                                              bool(hoa_hand))

        def _count_contactor_close():
            if profile["contactor_service_ops"]:
                persist.bump_contactor_ops(nvs)

        pump = PumpController(relay, led_red, led_green,
                              {"low_threshold": float(config.LOW_THRESHOLD)}, clock,
                              on_contactor_close=_count_contactor_close)
        mqtt = PumpMQTTClient(
            ssid=config.SSID, password=config.WIFI_PASS, broker=config.MQTT_BROKER,
            port=config.MQTT_PORT, node_id=config.NODE_ID, topic=config.MQTT_TOPIC_STATUS,
            retry_interval=config.WIFI_RETRY_INTERVAL,
            username=config.MQTT_USERNAME, mqtt_password=config.MQTT_PASSWORD,
            wifi_connect_timeout=config.WIFI_CONNECT_TIMEOUT,
            socket_timeout_s=config.SOCKET_TIMEOUT_S)
        # Item 12: battery/power monitoring (optional — pins ship as None until
        # wired per §6, so an un-commissioned node publishes no floating-pin
        # noise; inner try so a bad pin disables it without boot-looping).
        battery_adc = None
        power_source_pin = None
        try:
            if config.BATTERY_ADC_PIN is not None:
                battery_adc = machine.ADC(machine.Pin(config.BATTERY_ADC_PIN))
                battery_adc.atten(machine.ADC.ATTN_11DB)
                battery_adc.width(machine.ADC.WIDTH_12BIT)
            if config.POWER_SOURCE_PIN is not None:
                power_source_pin = machine.Pin(config.POWER_SOURCE_PIN, machine.Pin.IN)
        except Exception as e:
            print("[MAIN] Battery/power pins unavailable: %s (continuing)" % str(e))
    except Exception as e:
        print("[MAIN] Init failed, resetting: %s" % str(e))
        import machine
        machine.reset()
        return

    if config_error is not None:
        _run_config_error_loop(pump, mqtt, wdt, config_error)
        return

    cfg = build_config(profile)

    # Post-reset hold-off. Re-evaluated per tick because urgency can SHORTEN
    # it mid-run; the tracker is what stops it ever getting LONGER again.
    # `boot_started` is captured here rather than read from a timer the reset
    # cleared, which is the whole reason the counter had to move to NVS.
    boot_holdoff = None
    boot_cleared = False
    if profile["boot_holdoff_ms"] or profile["boot_loop_holdoff_ms"]:
        boot_started = clock.ticks_ms()
        holdoff_tracker = boot_guard.make_holdoff_tracker(profile, reset_loop)

        def boot_holdoff(readings):
            nonlocal boot_cleared
            uptime = clock.ticks_diff(clock.ticks_ms(), boot_started)
            # Urgency is a MODE question, not a profile one: high water is a
            # live flood in DRAIN and merely 'container full' in COLLECT,
            # where starting the pump is never the time-critical response.
            urgent = (config.PUMP_MODE == "DRAIN"
                      and readings.get("high_water") is True)
            if not boot_cleared and boot_guard.is_boot_healthy(uptime, profile):
                # This boot has run long enough to disprove a loop. Clearing
                # here rather than at boot is the point: a node that resets
                # before reaching the healthy window never clears, so the
                # count climbs and the loop is detected.
                persist.clear_boot_count(nvs)
                boot_cleared = True
            return holdoff_tracker(uptime, urgent)

    last_publish = time.ticks_ms()
    ntp_synced = False

    # Manual override slot. Written by MQTT command callback (called inside
    # mqtt.check_msg → PumpMQTTClient._dispatch_incoming), read every tick by
    # apply_manual_override(). Mutation is single-threaded — MicroPython's
    # umqtt callback runs on the same task as the main loop.
    manual_state = {"action": None, "expires_ms": None}

    def on_pump_command(data):
        action = data.get("action")
        if action not in ("ON", "OFF", "AUTO"):
            print("[CMD] bad action: %r (ignored)" % action)
            return
        # Duration in seconds. ON commands MUST specify a positive duration
        # so a lost operator/network can't leave the pump running dry
        # forever; OFF may be indefinite (safe direction).
        duration_s = data.get("duration_s")
        if action == "AUTO":
            # Release the hold and return to automatic control. Any duration_s
            # is meaningless here and ignored (the server rejects it with a
            # 400 before it ever reaches the wire). The slot is WRITTEN rather
            # than cleared in place so the release travels the same path as
            # every other command — apply_manual_override() clears it on the
            # next tick and the natural decision resumes.
            manual_state["action"] = "AUTO"
            manual_state["expires_ms"] = None
            manual_state["last_rejected"] = None
            print("[CMD] manual AUTO (release hold)")
            return
        if action == "ON" and (not isinstance(duration_s, (int, float)) or duration_s <= 0):
            print("[CMD] ON refused: positive duration_s required")
            return
        if action == "OFF" and (isinstance(duration_s, (int, float)) and duration_s <= 0):
            duration_s = None
        now = time.ticks_ms()
        expires = None
        if isinstance(duration_s, (int, float)) and duration_s > 0:
            expires = time.ticks_add(now, int(duration_s * 1000))
        manual_state["action"] = action
        manual_state["expires_ms"] = expires
        manual_state["last_rejected"] = None
        print("[CMD] manual %s duration_s=%s" % (action, duration_s))

    mqtt._on_pump_command = on_pump_command

    def publish_cb(pump_state, water_level, flags, reason, extra=None):
        nonlocal last_publish, ntp_synced
        now = time.ticks_ms()
        if time.ticks_diff(now, last_publish) >= config.PUBLISH_INTERVAL * 1000:
            battery_voltage, power_source = _read_power(battery_adc, power_source_pin)
            # `nvs_ok` exists so "boot_count is 0" and "boot_count is
            # unreadable" are distinguishable. persist._read() coerces every
            # failure to 0, so without this a node with a dead flash
            # partition reports boot_count=0 forever — which reads as a
            # perfectly healthy node while reset-loop detection is silently
            # off, the same failure DIRECTION that got RTC memory rejected
            # in spec finding B2. This catches an unavailable namespace,
            # not a partially-worn one; it narrows the blind spot rather
            # than closing it.
            merged = {"actuator_profile": config.ACTUATOR_PROFILE,
                      "pump_mode": config.PUMP_MODE,
                      "boot_count": boot_count,
                      "nvs_ok": nvs is not None}
            if profile["contactor_service_ops"]:
                ops = persist.read_contactor_ops(nvs)
                merged["contactor_ops"] = ops
                # Compared on-device: the threshold has to mean something
                # before the Phase 2 server exists to evaluate it.
                merged["contactor_service_due"] = profiles.service_due(profile, ops)
            if extra:
                merged.update(extra)
            mqtt.publish_status(pump_state, water_level, flags, reason,
                                battery_voltage, power_source, extra=merged)
            last_publish = now
            if not ntp_synced and mqtt._wifi_connected:
                _sync_ntp(); ntp_synced = True
        mqtt.check_msg()

    while True:
        try:
            run_iteration(sensor_set, pump, mqtt, cfg, publish_cb,
                          manual_state=manual_state, clock=clock,
                          decider=decider, strict=profile["ct_enabled"],
                          boot_holdoff=boot_holdoff, ct_read=ct_read)
            if wdt:
                wdt.feed()           # feed ONLY after a full successful iteration
            import gc
            gc.collect()
            time.sleep(config.POLL_INTERVAL)
        except KeyboardInterrupt:
            off_state = dict(pump.ctrl_state)
            off_state["pump_state"] = "OFF"
            pump.apply({"action": "OFF", "next_state": off_state,
                        "flags": {}, "reason": "STANDBY"})
            mqtt.disconnect()
            break
        except Exception as e:
            print("[ERROR] %s" % str(e))
            time.sleep(config.POLL_INTERVAL)


class _RealClockShim:
    def ticks_ms(self):
        return time.ticks_ms()

    def ticks_diff(self, a, b):
        return time.ticks_diff(a, b)


def _sync_ntp():
    import ntptime
    ntptime.timeout = 5
    for srv in ("pool.ntp.org", "time.cloudflare.com", "216.239.35.0"):
        try:
            ntptime.host = srv
            ntptime.settime()
            return True
        except Exception as e:
            print("[MAIN] NTP %s failed: %s" % (srv, str(e)))
    return False


def _read_power(battery_adc, power_source_pin):
    """Best-effort battery voltage + power source. Device-only; returns
    (None, None) when the pins are unwired so build_payload omits the fields."""
    battery_voltage = None
    power_source = None
    if battery_adc is not None:
        raw = battery_adc.read()
        battery_voltage = raw * 3.3 / 4095.0 * 2.0  # 1:2 divider — tune per wiring
    if power_source_pin is not None:
        power_source = "mains" if power_source_pin.value() else "battery"
    return battery_voltage, power_source


if __name__ == "__main__":
    main()
