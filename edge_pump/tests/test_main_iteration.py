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
