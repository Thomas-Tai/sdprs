# -*- coding: utf-8 -*-
"""SDPRS 水泵節點主程式 — 精簡協調層。
讀感測器 -> decide() -> 執行 -> 發布(盡力) -> 餵狗(僅在成功迭代後)。
離線自治：所有網路操作為盡力而為且有時限，永不影響控制。"""

import time
import config
import control_logic
from sensors import SensorSet
from pump_controller import PumpController
from mqtt_client import PumpMQTTClient


def build_config():
    return {
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
    }


def build_sensor_config():
    return {
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
    }


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


def apply_manual_override(decision, manual_state, clock):
    """Optionally override the pure control decision with an operator command.

    Two-slot manual state: {"action": "ON"|"OFF"|None, "expires_ms": int|None}.
    Returns a new (decision, manual_state) tuple — either passed through
    unchanged or mutated by the override / expiry / rejection paths.

    Contract:
      - Manual OFF is ALWAYS honored (safe direction — stopping never damages).
      - Manual ON is REJECTED (dropped, no retry) when the safety core has
        engaged `dry_run_protect` or `sensor_conflict` — the pump would
        damage itself running dry or with contradicting sensors.
      - Both commands auto-expire once `expires_ms` is reached (bounded pulse).
        `expires_ms=None` means indefinite (used for OFF-latch, discouraged
        for ON via the server-side API).

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
        if flags.get("dry_run_protect") or flags.get("sensor_conflict"):
            # Refuse and DROP the override so it doesn't retry every tick.
            # The wrapper's caller can inspect the `manual_state["last_rejected"]`
            # field to surface why the operator's ON click didn't take.
            return decision, {"action": None, "expires_ms": None,
                              "last_rejected": control_logic.MANUAL_REJECTED}
        next_state = dict(decision["next_state"])
        next_state["pump_state"] = "ON"
        return {
            "action": "ON",
            "next_state": next_state,
            "flags": dict(flags, manual_override="ON"),
            "reason": control_logic.MANUAL_ON,
        }, manual_state

    # Unknown action — ignore, clear the slot to avoid a stuck state.
    return decision, {"action": None, "expires_ms": None}


def run_iteration(sensor_set, pump, mqtt, cfg, publish_cb,
                  manual_state=None, clock=None):
    """One control-loop body. Pure of hardware except via injected objects.

    `manual_state`/`clock` are optional so existing callers (tests + the
    device main loop before the manual-override wiring) keep working; when
    both are provided, an outstanding manual command is layered on top of
    the pure decision via `apply_manual_override` before it hits the pump.
    """
    readings = sensor_set.read_all()
    timing = pump.snapshot_timing(readings)
    decision = control_logic.decide(readings, timing, pump.ctrl_state, cfg)
    if manual_state is not None and clock is not None:
        decision, new_manual = apply_manual_override(decision, manual_state, clock)
        # In-place mutation so the caller's dict reference stays valid.
        # BUT: apply_manual_override may return the SAME reference for the
        # "still-active, pass through" case — clearing manual_state would
        # then wipe new_manual too and the override would evaporate after
        # one publish cycle. Only copy back when it's a distinct object.
        if new_manual is not manual_state:
            manual_state.clear()
            manual_state.update(new_manual)
    pump.apply(decision)
    publish_cb(pump_state=pump.state,
               water_level=synthesize_display_level(readings),
               flags=decision["flags"], reason=decision["reason"])
    return decision


def main():
    print("[MAIN] SDPRS Pump Node starting (merged firmware)...")
    wdt = None
    try:
        import machine
        if config.WDT_ENABLED:
            from machine import WDT
            wdt = WDT(timeout=config.WDT_TIMEOUT)
        from sensors import build_readers
        readers = build_readers(build_sensor_config())
        relay = machine.Pin(config.RELAY_PIN, machine.Pin.OUT)
        led_red = machine.Pin(config.LED_RED_PIN, machine.Pin.OUT)
        led_green = machine.Pin(config.LED_GREEN_PIN, machine.Pin.OUT)
        clock = _RealClockShim()
        sensor_set = SensorSet(build_sensor_config(), readers, clock)
        pump = PumpController(relay, led_red, led_green,
                              {"low_threshold": float(config.LOW_THRESHOLD)}, clock)
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

    cfg = build_config()
    last_publish = time.ticks_ms()
    ntp_synced = False

    # Manual override slot. Written by MQTT command callback (called inside
    # mqtt.check_msg → PumpMQTTClient._dispatch_incoming), read every tick by
    # apply_manual_override(). Mutation is single-threaded — MicroPython's
    # umqtt callback runs on the same task as the main loop.
    manual_state = {"action": None, "expires_ms": None}

    def on_pump_command(data):
        action = data.get("action")
        if action not in ("ON", "OFF"):
            print("[CMD] bad action: %r (ignored)" % action)
            return
        # Duration in seconds. ON commands MUST specify a positive duration
        # so a lost operator/network can't leave the pump running dry
        # forever; OFF may be indefinite (safe direction).
        duration_s = data.get("duration_s")
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

    def publish_cb(pump_state, water_level, flags, reason):
        nonlocal last_publish, ntp_synced
        now = time.ticks_ms()
        if time.ticks_diff(now, last_publish) >= config.PUBLISH_INTERVAL * 1000:
            battery_voltage, power_source = _read_power(battery_adc, power_source_pin)
            mqtt.publish_status(pump_state, water_level, flags, reason,
                                battery_voltage, power_source)
            last_publish = now
            if not ntp_synced and mqtt._wifi_connected:
                _sync_ntp(); ntp_synced = True
        mqtt.check_msg()

    while True:
        try:
            run_iteration(sensor_set, pump, mqtt, cfg, publish_cb,
                          manual_state=manual_state, clock=clock)
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
