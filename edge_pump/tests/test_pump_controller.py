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


def test_min_off_timer_starts_on_off_transition():
    from pump_controller import PumpController
    from tests.fakes import FakeClock, FakePin
    clock = FakeClock()
    pump = PumpController(FakePin(0), FakePin(0), FakePin(0),
                          {"low_threshold": 20.0}, clock)

    on_state = dict(pump.ctrl_state, pump_state="ON")
    pump.apply({"action": "ON", "next_state": on_state, "flags": {}, "reason": "HIGH_WATER"})
    assert pump.snapshot_timing({})["min_off_elapsed_ms"] is None

    clock.advance(5000)
    off_state = dict(pump.ctrl_state, pump_state="OFF")
    pump.apply({"action": "OFF", "next_state": off_state, "flags": {}, "reason": "STANDBY"})
    clock.advance(30000)
    assert pump.snapshot_timing({})["min_off_elapsed_ms"] == 30000


def test_min_off_and_rest_timers_stay_in_lockstep():
    """Invariant guard. `_min_off_since` and `_off_since` are deliberately
    SEPARATE fields (spec §5.4) but are maintained by identical rules in the
    same block of apply(), so they must hold identical values at every
    instant. The separation buys a different None reading at the CONSUMER,
    not different timing. If a future edit changes one transition and
    forgets the other, the two silently diverge and Layer 3.5 starts
    measuring something nobody designed — this test is what catches it."""
    from pump_controller import PumpController
    from tests.fakes import FakeClock, FakePin
    clock = FakeClock()
    pump = PumpController(FakePin(0), FakePin(0), FakePin(0),
                          {"low_threshold": 20.0}, clock)

    def both():
        t = pump.snapshot_timing({})
        return t["rest_elapsed_ms"], t["min_off_elapsed_ms"]

    on = dict(pump.ctrl_state, pump_state="ON")
    off = dict(pump.ctrl_state, pump_state="OFF")

    assert both()[0] == both()[1]                      # cold: both None
    pump.apply({"action": "ON", "next_state": on, "flags": {}, "reason": "HIGH_WATER"})
    clock.advance(5000)
    assert both()[0] == both()[1]                      # running: both None
    pump.apply({"action": "OFF", "next_state": off, "flags": {}, "reason": "STANDBY"})
    clock.advance(7000)
    r, m = both()
    assert r == m == 7000
    # A burst that runs the pump mid-rest must restart BOTH clocks.
    pump.apply({"action": "ON", "next_state": dict(on), "flags": {}, "reason": "CONFLICT_BURST_ON"})
    pump.apply({"action": "OFF", "next_state": dict(off), "flags": {}, "reason": "STANDBY"})
    clock.advance(1000)
    r, m = both()
    assert r == m == 1000


def test_contactor_close_callback_fires_only_on_off_to_on():
    from pump_controller import PumpController
    from tests.fakes import FakeClock, FakePin
    calls = []
    pump = PumpController(FakePin(0), FakePin(0), FakePin(0),
                          {"low_threshold": 20.0}, FakeClock(),
                          on_contactor_close=lambda: calls.append(1))

    on = dict(pump.ctrl_state, pump_state="ON")
    pump.apply({"action": "ON", "next_state": on, "flags": {}, "reason": "HIGH_WATER"})
    assert len(calls) == 1

    # HOLD while already ON must not count a second closure.
    pump.apply({"action": "HOLD", "next_state": dict(on), "flags": {}, "reason": "HOLD"})
    assert len(calls) == 1

    off = dict(pump.ctrl_state, pump_state="OFF")
    pump.apply({"action": "OFF", "next_state": off, "flags": {}, "reason": "STANDBY"})
    assert len(calls) == 1

    pump.apply({"action": "ON", "next_state": dict(on), "flags": {}, "reason": "HIGH_WATER"})
    assert len(calls) == 2


def test_contactor_counter_is_optional():
    from pump_controller import PumpController
    from tests.fakes import FakeClock, FakePin
    pump = PumpController(FakePin(0), FakePin(0), FakePin(0),
                          {"low_threshold": 20.0}, FakeClock())
    on = dict(pump.ctrl_state, pump_state="ON")
    pump.apply({"action": "ON", "next_state": on, "flags": {}, "reason": "HIGH_WATER"})
    assert pump.contactor_ops == 1


def test_contactor_callback_failure_does_not_stop_the_pump():
    # A worn flash partition must not prevent the pump from running.
    from pump_controller import PumpController
    from tests.fakes import FakeClock, FakePin

    def explode():
        raise OSError("flash worn out")

    relay = FakePin(0)
    pump = PumpController(relay, FakePin(0), FakePin(0),
                          {"low_threshold": 20.0}, FakeClock(),
                          on_contactor_close=explode)
    on = dict(pump.ctrl_state, pump_state="ON")
    pump.apply({"action": "ON", "next_state": on, "flags": {}, "reason": "HIGH_WATER"})
    assert relay.value() == 1
