from tests.fakes import FakeClock, make_reader
import sensors


def test_analog_inversion_and_clamp():
    # raw 0 -> 100% (fully wet/high), raw 4095 -> 0%
    assert sensors.analog_to_level([0, 0, 0]) == 100.0
    assert sensors.analog_to_level([4095, 4095, 4095]) == 0.0
    assert 49.0 <= sensors.analog_to_level([2000, 2100, 2050]) <= 51.5


def test_digital_debounce_holds_until_stable():
    clk = FakeClock()
    # active_low: raw 0 => asserted True. The debounce hold is measured from the
    # first asserted read (t=1000), so the flip lands at t=1000+2500 = 3500.
    rd = make_reader([1, 0, 0, 0, 0])  # idle then asserted and held
    s = sensors.DigitalSensor(rd, active_low=True, clock=clk, debounce_ms=2500)
    assert s.update() is False           # t=0 idle baseline
    clk.advance(1000); assert s.update() is False   # t=1000 asserted begins, 0ms held
    clk.advance(1000); assert s.update() is False   # t=2000 1000ms held < debounce
    clk.advance(1000); assert s.update() is False   # t=3000 2000ms held < debounce
    clk.advance(500);  assert s.update() is True    # t=3500 2500ms held -> flips


def test_digital_bounce_resets_timer():
    clk = FakeClock()
    rd = make_reader([1, 0, 1, 0, 0, 0])  # assert, bounce back to idle, then hold asserted
    s = sensors.DigitalSensor(rd, active_low=True, clock=clk, debounce_ms=2500)
    assert s.update() is False           # t=0 idle baseline
    clk.advance(1000); assert s.update() is False   # t=1000 first asserted read
    clk.advance(1000); assert s.update() is False   # t=2000 bounced to idle -> candidate resets
    clk.advance(1000); assert s.update() is False   # t=3000 asserted again -> timer restarts here
    clk.advance(2000); assert s.update() is False   # t=5000 only 2000ms held < debounce
    clk.advance(500);  assert s.update() is True    # t=5500 2500ms held -> flips


def test_read_all_disabled_sensors_are_none():
    clk = FakeClock()
    config = {"level_enabled": True, "float_enabled": False,
              "rain_enabled": False, "high_water_enabled": False,
              "float_active_low": True, "rain_active_low": True,
              "high_water_active_low": False, "debounce_ms": 2500}
    readers = {"adc": make_reader(0), "float": make_reader(1),
               "rain": make_reader(1), "high_water": make_reader(0)}
    ss = sensors.SensorSet(config, readers, clk)
    r = ss.read_all()
    assert r["level_pct"] == 100.0
    assert r["float_dry"] is None and r["raining"] is None and r["high_water"] is None
