import control_logic
from main import apply_overload_interlock


def base_decision(action="ON"):
    return {"action": action,
            "next_state": dict(control_logic.initial_state(), pump_state=action),
            "flags": {"raining": False, "max_runtime_rest": False},
            "reason": control_logic.HIGH_WATER}


def test_non_overload_verdicts_pass_through_untouched():
    d = base_decision()
    assert apply_overload_interlock(d, None) is d
    assert apply_overload_interlock(d, "PUMP_NOT_RUNNING") is d
    assert apply_overload_interlock(d, "WELDED_CONTACT") is d


def test_overload_forces_off():
    out = apply_overload_interlock(base_decision(), "OVERLOAD")
    assert out["action"] == "OFF"
    assert out["reason"] == control_logic.OVERLOAD_TRIP
    assert out["flags"]["overload_trip"] is True


def test_overload_returns_a_complete_decision_dict():
    # The whole point (spec §5.5): driving the relay directly would leave
    # ctrl_state saying ON while the contactor is open, _on_since
    # accumulating against a pump that is not running, and — because no
    # ON->OFF transition was ever recorded — the rest timer never starting.
    out = apply_overload_interlock(base_decision(), "OVERLOAD")
    assert set(out) == {"action", "next_state", "flags", "reason"}
    assert out["next_state"]["pump_state"] == "OFF"


def test_overload_preserves_the_underlying_safety_flags():
    d = base_decision()
    d["flags"]["sensor_conflict"] = True
    out = apply_overload_interlock(d, "OVERLOAD")
    assert out["flags"]["sensor_conflict"] is True


def test_overload_does_not_mutate_the_input_decision():
    d = base_decision()
    apply_overload_interlock(d, "OVERLOAD")
    assert d["action"] == "ON"
    assert d["next_state"]["pump_state"] == "ON"


def test_interlock_result_drives_the_controller_state_machine():
    from pump_controller import PumpController
    from tests.fakes import FakeClock, FakePin
    clock = FakeClock()
    relay = FakePin(0)
    pump = PumpController(relay, FakePin(0), FakePin(0),
                          {"low_threshold": 20.0}, clock)

    on = base_decision()
    pump.apply(on)
    assert relay.value() == 1 and pump.state == "ON"

    clock.advance(5000)
    pump.apply(apply_overload_interlock(base_decision(), "OVERLOAD"))
    assert relay.value() == 0
    assert pump.state == "OFF"
    # The ON->OFF transition WAS recorded, so the rest clock is running.
    clock.advance(1000)
    assert pump.snapshot_timing({})["rest_elapsed_ms"] == 1000
