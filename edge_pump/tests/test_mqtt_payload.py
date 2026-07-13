from mqtt_client import build_payload


def test_payload_has_additive_fields_and_preserves_core():
    flags = {"raining": True, "float_safe": True, "high_water": False,
             "sensor_conflict": False, "dry_run_protect": False,
             "max_runtime_rest": False}
    p = build_payload("pump_node_01", "2026-07-10T00:00:00Z", "ON", 82.4,
                      flags, "RAIN_TRIGGER", battery_voltage=12.6, power_source="mains")
    # core fields preserved verbatim
    assert p["node_id"] == "pump_node_01"
    assert p["pump_state"] == "ON"
    assert p["water_level"] == 82.4
    assert p["battery_voltage"] == 12.6 and p["power_source"] == "mains"
    # additive fields present
    assert p["raining"] is True and p["float_safe"] is True
    assert p["sensor_conflict"] is False and p["dry_run_protect"] is False
    assert p["reason"] == "RAIN_TRIGGER"


def test_payload_omits_optional_when_none():
    flags = {"raining": False, "float_safe": None, "high_water": None,
             "sensor_conflict": False, "dry_run_protect": False,
             "max_runtime_rest": False}
    p = build_payload("n", "t", "OFF", 10.0, flags, "STANDBY")
    assert "battery_voltage" not in p and "power_source" not in p
