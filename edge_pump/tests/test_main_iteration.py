from tests.fakes import FakeClock, FakePin, make_reader
import control_logic
import sensors, main
from pump_controller import PumpController


def build_pc(clk):
    return PumpController(FakePin(0), FakePin(0), FakePin(1),
                          {"low_threshold": 20.0}, clk)


def test_run_iteration_turns_pump_on_when_flooded(monkeypatch):
    clk = FakeClock()
    cfg = main.build_config()
    config = {"level_enabled": True, "float_enabled": True, "rain_enabled": False,
              "high_water_enabled": False, "float_active_low": True,
              "rain_active_low": True, "high_water_active_low": False, "debounce_ms": 2500}
    readers = {"adc": make_reader(0), "float": make_reader(1),  # adc 0 -> 100%, float idle=safe
               "rain": make_reader(1), "high_water": make_reader(0)}
    ss = sensors.SensorSet(config, readers, clk)
    pc = build_pc(clk)
    published = []
    d = main.run_iteration(ss, pc, None, cfg, lambda **kw: published.append(kw))
    assert d["action"] == "ON"
    assert pc.state == "ON"
    assert published and published[0]["pump_state"] == "ON"


def test_build_config_maps_thresholds():
    cfg = main.build_config()
    assert cfg["high_threshold"] == 80.0 and cfg["low_threshold"] == 20.0
    assert cfg["rain_on_threshold"] == 60.0


def test_build_config_matches_control_logic_defaults():
    # Drift guard: control_logic.DEFAULT_CONFIG (used by the pure-logic tests)
    # and the real device config marshaled by main.build_config() cover the
    # same keys today — if either side changes without the other, the logic
    # tests would silently validate values the device never runs with.
    assert main.build_config() == control_logic.DEFAULT_CONFIG


def test_synthesize_display_level_passes_through_analog_when_wired():
    assert main.synthesize_display_level({"level_pct": 42.5, "high_water": True}) == 42.5
    assert main.synthesize_display_level({"level_pct": 0.0, "high_water": False}) == 0.0


def test_synthesize_display_level_digital_only_high_water():
    assert main.synthesize_display_level(
        {"level_pct": None, "high_water": True, "float_dry": True}) == 100.0


def test_synthesize_display_level_digital_only_float_safe():
    assert main.synthesize_display_level(
        {"level_pct": None, "high_water": False, "float_dry": False}) == 50.0


def test_manual_override_persists_across_iterations_until_expiry():
    """Regression: run_iteration must not lose the manual_state between
    calls. The initial bug was `manual_state.clear() ; manual_state.update(
    new_manual)` when apply_manual_override returned the SAME reference —
    clear() wiped new_manual too and the override evaporated after one
    tick. This test flexes 4 consecutive run_iteration calls with a live
    OFF command and asserts the payload keeps carrying MANUAL_OFF the
    whole time, then flips to STANDBY exactly once after expiry."""
    clk = FakeClock(start_ms=0)
    cfg = main.build_config()
    config = {"level_enabled": False, "float_enabled": False,
              "rain_enabled": False, "high_water_enabled": False,
              "float_active_low": True, "rain_active_low": True,
              "high_water_active_low": False, "debounce_ms": 2500}
    ss = sensors.SensorSet(config, {"adc": make_reader(0),
                                    "float": make_reader(1),
                                    "rain": make_reader(1),
                                    "high_water": make_reader(0)}, clk)
    pc = build_pc(clk)
    manual = {"action": "OFF", "expires_ms": 3500}  # 3.5s window at clk=0
    published = []
    # 4 iterations, each spaced 1s apart — first 4 (t=0..3s) inside window.
    for _ in range(4):
        main.run_iteration(ss, pc, None, cfg,
                           lambda **kw: published.append(kw),
                           manual_state=manual, clock=clk)
        clk.advance(1000)
    # clk=4000 now, past expires_ms=3500 — next iteration MUST see expiry.
    main.run_iteration(ss, pc, None, cfg,
                       lambda **kw: published.append(kw),
                       manual_state=manual, clock=clk)
    reasons = [p["reason"] for p in published]
    assert reasons[0:4] == ["MANUAL_OFF"] * 4, f"expected 4 MANUAL_OFF, got {reasons}"
    assert reasons[4] != "MANUAL_OFF"  # expired -> back to natural
    assert manual["action"] is None    # slot cleared


def test_synthesize_display_level_digital_only_all_dry():
    assert main.synthesize_display_level(
        {"level_pct": None, "high_water": False, "float_dry": True}) == 0.0
    # sensors disabled entirely (None everywhere)
    assert main.synthesize_display_level(
        {"level_pct": None, "high_water": None, "float_dry": None}) == 0.0
