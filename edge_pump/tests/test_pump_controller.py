from tests.fakes import FakeClock, FakePin
from pump_controller import PumpController
import control_logic

CONFIG = {"low_threshold": 20.0}


def make_pc(clk):
    return PumpController(FakePin(0), FakePin(0), FakePin(1), CONFIG, clk)


def test_apply_on_sets_relay_and_records_on_since():
    clk = FakeClock(1000)
    pc = make_pc(clk)
    d = control_logic._mk("ON", dict(pc.ctrl_state), {}, "X")
    pc.apply(d)
    assert pc.state == "ON"
    clk.advance(5000)
    t = pc.snapshot_timing({"level_pct": 90, "raining": None})
    assert t["pump_on_elapsed_ms"] == 5000


def test_off_clears_on_since():
    clk = FakeClock()
    pc = make_pc(clk)
    pc.apply(control_logic._mk("ON", dict(pc.ctrl_state), {}, "X"))
    pc.apply(control_logic._mk("OFF", dict(pc.ctrl_state), {}, "X"))
    t = pc.snapshot_timing({"level_pct": 10, "raining": None})
    assert t["pump_on_elapsed_ms"] is None


def test_hold_does_not_change_relay():
    clk = FakeClock()
    pc = make_pc(clk)
    pc.apply(control_logic._mk("ON", dict(pc.ctrl_state), {}, "X"))
    relay_before = pc._relay.value()
    pc.apply(control_logic._mk("HOLD", dict(pc.ctrl_state), {}, "X"))
    assert pc._relay.value() == relay_before and pc.state == "ON"


def test_rain_wet_timer_accumulates_and_resets():
    clk = FakeClock()
    pc = make_pc(clk)
    pc.snapshot_timing({"level_pct": 50, "raining": True})
    clk.advance(4000)
    t = pc.snapshot_timing({"level_pct": 50, "raining": True})
    assert t["rain_wet_elapsed_ms"] == 4000
    t = pc.snapshot_timing({"level_pct": 50, "raining": False})
    assert t["rain_wet_elapsed_ms"] is None


def test_level_low_timer_tracks_below_threshold():
    clk = FakeClock()
    pc = make_pc(clk)
    pc.snapshot_timing({"level_pct": 15, "raining": None})
    clk.advance(3000)
    t = pc.snapshot_timing({"level_pct": 15, "raining": None})
    assert t["level_low_elapsed_ms"] == 3000


def test_burst_phase_timer_resets_on_phase_change():
    clk = FakeClock()
    pc = make_pc(clk)
    st = dict(pc.ctrl_state, conflict_latched=True, burst_phase="ON")
    pc.apply({"action": "ON", "next_state": st, "flags": {}, "reason": "X"})
    clk.advance(2000)
    st2 = dict(pc.ctrl_state, burst_phase="REST")
    pc.apply({"action": "OFF", "next_state": st2, "flags": {}, "reason": "X"})
    t = pc.snapshot_timing({"level_pct": None, "raining": None})
    assert t["burst_phase_elapsed_ms"] == 0


def test_rest_timer_tracks_off_duration_and_restarts_after_on():
    clk = FakeClock()
    pc = make_pc(clk)
    pc.apply(control_logic._mk("ON", dict(pc.ctrl_state), {}, "X"))
    clk.advance(1000)
    pc.apply(control_logic._mk("OFF", dict(pc.ctrl_state), {}, "X"))
    clk.advance(2000)
    t = pc.snapshot_timing({"level_pct": None, "raining": None})
    assert t["rest_elapsed_ms"] == 2000     # continuous-off duration = actual rest
    # pump turning back ON clears the rest/off clock
    pc.apply(control_logic._mk("ON", dict(pc.ctrl_state), {}, "X"))
    t = pc.snapshot_timing({"level_pct": None, "raining": None})
    assert t["rest_elapsed_ms"] is None
