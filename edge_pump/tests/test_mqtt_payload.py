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


def test_extra_fields_are_merged():
    from mqtt_client import build_payload
    p = build_payload("n1", "2026-08-02T00:00:00Z", "OFF", 0.0, {}, "STANDBY",
                      extra={"actuator_profile": "SOCKET_220V",
                             "pump_mode": "DRAIN",
                             "current_band": "none",
                             "hoa_hand": False,
                             "contactor_ops": 42,
                             "boot_count": 1})
    assert p["actuator_profile"] == "SOCKET_220V"
    assert p["current_band"] == "none"
    assert p["contactor_ops"] == 42


def test_extra_omitted_keeps_the_legacy_payload_shape():
    # A 12V node must publish exactly what it published before.
    from mqtt_client import build_payload
    p = build_payload("n1", "2026-08-02T00:00:00Z", "OFF", 0.0, {}, "STANDBY")
    for key in ("actuator_profile", "current_band", "contactor_ops"):
        assert key not in p


def test_extra_cannot_overwrite_core_fields():
    # Telemetry must never be able to lie about pump_state.
    from mqtt_client import build_payload
    p = build_payload("n1", "2026-08-02T00:00:00Z", "ON", 50.0, {}, "HIGH_WATER",
                      extra={"pump_state": "OFF", "node_id": "spoofed"})
    assert p["pump_state"] == "ON"
    assert p["node_id"] == "n1"


def test_no_precise_amp_field_is_published():
    # Spec §6.1: an uncalibrated ADC reading published as amps gets believed.
    from mqtt_client import build_payload
    p = build_payload("n1", "2026-08-02T00:00:00Z", "ON", 50.0, {}, "HIGH_WATER",
                      extra={"current_band": "normal"})
    assert "current_a" not in p
    assert "current_amps" not in p


def test_timing_flags_and_remaining_times_are_published():
    """Spec §5.7 promises the dashboard can say WHY a manual ON did nothing
    and WHEN it will work. build_payload cherry-picks flags by name, so
    every flag added since — min_off_wait, boot_holdoff, container_full,
    overload_trip and the *_remaining_ms values — was being computed and
    then dropped at this boundary. Without this the bench matrix item
    'reject and report the remaining seconds' has nothing to observe."""
    from mqtt_client import build_payload
    p = build_payload("n1", "2026-08-02T00:00:00Z", "OFF", 0.0,
                      {"min_off_wait": True, "min_off_remaining_ms": 120000},
                      "MIN_OFF_WAIT")
    assert p["min_off_wait"] is True
    assert p["min_off_remaining_ms"] == 120000


def test_falsy_timing_flags_are_omitted_rather_than_published_as_false():
    # These flags are False on almost every tick. Publishing them all would
    # roughly double a 2-second payload for no information.
    from mqtt_client import build_payload
    p = build_payload("n1", "2026-08-02T00:00:00Z", "ON", 50.0,
                      {"min_off_wait": False, "container_full": False,
                       "overload_trip": False},
                      "HIGH_WATER")
    for key in ("min_off_wait", "container_full", "overload_trip"):
        assert key not in p
