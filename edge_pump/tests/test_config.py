import config


def test_thresholds_ordered():
    assert config.HIGH_THRESHOLD > config.LOW_THRESHOLD
    assert config.RAIN_ON_THRESHOLD >= config.LOW_THRESHOLD
    assert config.RAIN_ON_THRESHOLD <= config.HIGH_THRESHOLD


def test_new_sensor_pins_distinct_from_existing():
    used = {config.RELAY_PIN, config.LED_RED_PIN, config.LED_GREEN_PIN,
            config.ADC_PIN, config.BATTERY_ADC_PIN, config.POWER_SOURCE_PIN}
    for pin in (config.FLOAT_PIN, config.RAIN_PIN, config.HIGH_WATER_PIN):
        assert pin not in used


def test_enable_flags_exist():
    for name in ("LEVEL_ENABLED", "FLOAT_ENABLED", "RAIN_ENABLED", "HIGH_WATER_ENABLED"):
        assert isinstance(getattr(config, name), bool)
