import config


def test_thresholds_ordered():
    assert config.HIGH_THRESHOLD > config.LOW_THRESHOLD
    assert config.RAIN_ON_THRESHOLD >= config.LOW_THRESHOLD
    assert config.RAIN_ON_THRESHOLD <= config.HIGH_THRESHOLD


def test_new_sensor_pins_distinct_from_existing():
    # Battery/power pins ship as None (unwired) — only wired pins can clash.
    used = {p for p in (config.RELAY_PIN, config.LED_RED_PIN, config.LED_GREEN_PIN,
                        config.ADC_PIN, config.BATTERY_ADC_PIN, config.POWER_SOURCE_PIN)
            if p is not None}
    for pin in (config.FLOAT_PIN, config.RAIN_PIN, config.HIGH_WATER_PIN):
        assert pin not in used


def test_enable_flags_exist():
    for name in ("LEVEL_ENABLED", "FLOAT_ENABLED", "RAIN_ENABLED", "HIGH_WATER_ENABLED"):
        assert isinstance(getattr(config, name), bool)


def test_battery_pins_ship_disabled():
    # Ship-OFF precedent (§6 bench commissioning): unwired ADC/GPIO pins would
    # publish floating-pin garbage voltage and a flapping power_source, so the
    # defaults are None until the bench flips them to 35 / 21.
    assert config.BATTERY_ADC_PIN is None
    assert config.POWER_SOURCE_PIN is None


def test_network_timeouts_wired_values():
    # These are consumed by mqtt_client (no longer dead knobs). Lock the values
    # that reproduce the previously-hardcoded behavior: 15s WiFi wait, 3s socket.
    assert config.WIFI_CONNECT_TIMEOUT == 15
    assert config.SOCKET_TIMEOUT_S == 3
    # The boot-retry loop WIFI_MAX_RETRIES configured no longer exists.
    assert not hasattr(config, "WIFI_MAX_RETRIES")
