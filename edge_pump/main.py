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


def run_iteration(sensor_set, pump, mqtt, cfg, publish_cb):
    """One control-loop body. Pure of hardware except via injected objects."""
    readings = sensor_set.read_all()
    timing = pump.snapshot_timing(readings)
    decision = control_logic.decide(readings, timing, pump.ctrl_state, cfg)
    pump.apply(decision)
    publish_cb(pump_state=pump.state, water_level=readings.get("level_pct") or 0.0,
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
        sensor_set = SensorSet(build_sensor_config(), readers, _RealClockShim())
        pump = PumpController(relay, led_red, led_green, {"low_threshold": float(config.LOW_THRESHOLD)})
        mqtt = PumpMQTTClient(
            ssid=config.SSID, password=config.WIFI_PASS, broker=config.MQTT_BROKER,
            port=config.MQTT_PORT, node_id=config.NODE_ID, topic=config.MQTT_TOPIC_STATUS,
            retry_interval=config.WIFI_RETRY_INTERVAL,
            username=config.MQTT_USERNAME, mqtt_password=config.MQTT_PASSWORD)
        # Item 12: battery/power monitoring (optional — preserve existing telemetry;
        # inner try so an unwired pin disables it without boot-looping the node).
        battery_adc = None
        power_source_pin = None
        try:
            if config.BATTERY_ADC_PIN:
                battery_adc = machine.ADC(machine.Pin(config.BATTERY_ADC_PIN))
                battery_adc.atten(machine.ADC.ATTN_11DB)
                battery_adc.width(machine.ADC.WIDTH_12BIT)
            if config.POWER_SOURCE_PIN:
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
            run_iteration(sensor_set, pump, mqtt, cfg, publish_cb)
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
