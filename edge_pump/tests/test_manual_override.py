"""Pure-logic tests for the manual pump override wrapper.

The wrapper sits between control_logic.decide() (safety core) and pump.apply()
so the safety flags still gate what a manual command can actually do."""

from tests.fakes import FakeClock
import control_logic
import main


def _natural_decision(action="OFF", reason=control_logic.STANDBY, flags=None):
    """Build a pretend control_logic.decide() output as a starting point."""
    return {
        "action": action,
        "next_state": {"pump_state": action, "conflict_latched": False,
                       "conflict_holdoff": False, "burst_phase": None,
                       "resting": False},
        "flags": dict(flags or {}),
        "reason": reason,
    }


def test_pass_through_when_no_manual_action():
    clk = FakeClock()
    d = _natural_decision(action="OFF", reason=control_logic.STANDBY)
    state = {"action": None, "expires_ms": None}
    out, new_state = main.apply_manual_override(d, state, clk)
    assert out is d  # unchanged reference
    assert new_state == state


def test_manual_off_always_honored_even_when_natural_wants_on():
    """Manual OFF is safe direction — must override any ON decision."""
    clk = FakeClock()
    d = _natural_decision(action="ON", reason=control_logic.HIGH_WATER,
                          flags={"high_water": True})
    state = {"action": "OFF", "expires_ms": None}
    out, new_state = main.apply_manual_override(d, state, clk)
    assert out["action"] == "OFF"
    assert out["reason"] == control_logic.MANUAL_OFF
    assert out["flags"]["manual_override"] == "OFF"
    # High-water flag survives — telemetry still tells the operator WHY the
    # pump would normally be running.
    assert out["flags"]["high_water"] is True
    assert new_state == state  # not expired, retained


def test_manual_on_forces_pump_when_safety_clear():
    clk = FakeClock()
    d = _natural_decision(action="OFF", reason=control_logic.STANDBY,
                          flags={"dry_run_protect": False, "sensor_conflict": False})
    state = {"action": "ON", "expires_ms": None}
    out, new_state = main.apply_manual_override(d, state, clk)
    assert out["action"] == "ON"
    assert out["reason"] == control_logic.MANUAL_ON
    assert out["flags"]["manual_override"] == "ON"
    assert new_state == state


def test_manual_on_refused_when_dry_run_engaged():
    """Safety-first: manual ON must NOT override dry-run interlock."""
    clk = FakeClock()
    d = _natural_decision(action="OFF", reason=control_logic.DRY_RUN_OFF,
                          flags={"dry_run_protect": True})
    state = {"action": "ON", "expires_ms": None}
    out, new_state = main.apply_manual_override(d, state, clk)
    assert out is d  # natural decision unchanged
    assert out["action"] == "OFF"
    # State DROPPED with a rejection marker so the wrapper doesn't retry
    # every tick — a stuck rejected slot would spam the log.
    assert new_state["action"] is None
    assert new_state["last_rejected"] == control_logic.MANUAL_REJECTED


def test_manual_on_refused_when_sensor_conflict_engaged():
    """Same interlock rule for sensor_conflict — safety core keeps control."""
    clk = FakeClock()
    d = _natural_decision(action="OFF", reason=control_logic.CONFLICT_BURST_REST,
                          flags={"sensor_conflict": True})
    state = {"action": "ON", "expires_ms": None}
    out, new_state = main.apply_manual_override(d, state, clk)
    assert out["action"] == "OFF"
    assert new_state["action"] is None
    assert new_state["last_rejected"] == control_logic.MANUAL_REJECTED


def test_manual_on_expires_and_clears():
    """A time-bounded ON command must auto-clear at the expires_ms tick,
    letting the natural decision take over. No leftover state."""
    clk = FakeClock(start_ms=1000)
    d = _natural_decision(action="OFF", reason=control_logic.STANDBY, flags={})
    state = {"action": "ON", "expires_ms": 1500}
    # Before expiry
    out, s = main.apply_manual_override(d, state, clk)
    assert out["action"] == "ON"
    assert s == state
    # Advance past expiry
    clk.advance(600)
    out2, s2 = main.apply_manual_override(d, state, clk)
    assert out2 is d
    assert s2 == {"action": None, "expires_ms": None}


def test_manual_off_expires_returns_to_natural():
    """OFF with duration also expires — after which the natural decision
    (which may be ON, e.g. high_water was asserted) resumes."""
    clk = FakeClock(start_ms=0)
    d_on = _natural_decision(action="ON", reason=control_logic.HIGH_WATER,
                             flags={"high_water": True})
    state = {"action": "OFF", "expires_ms": 500}
    # Held OFF while active
    out, _ = main.apply_manual_override(d_on, state, clk)
    assert out["action"] == "OFF"
    # Expired — resumes natural ON
    clk.advance(600)
    out2, s2 = main.apply_manual_override(d_on, state, clk)
    assert out2["action"] == "ON"
    assert out2["reason"] == control_logic.HIGH_WATER
    assert s2 == {"action": None, "expires_ms": None}


def test_auto_releases_an_indefinite_off_hold():
    """MSP-F6: AUTO is the way out of an indefinite manual OFF. The slot must
    clear and the natural decision (here: ON, because high_water is asserted)
    must resume — this is the difference between a serviced pump coming back
    for the next rain event and a flooded station."""
    clk = FakeClock()
    d_on = _natural_decision(action="ON", reason=control_logic.HIGH_WATER,
                             flags={"high_water": True})
    state = {"action": "AUTO", "expires_ms": None}
    out, s = main.apply_manual_override(d_on, state, clk)
    assert out is d_on                      # natural decision, untouched
    assert out["action"] == "ON"
    assert out["reason"] == control_logic.HIGH_WATER
    # No manual_override flag — telemetry stops reporting a hold immediately.
    assert "manual_override" not in out["flags"]
    assert s == {"action": None, "expires_ms": None}


def test_auto_is_a_noop_when_nothing_is_held():
    """Releasing an already-released pump must be harmless — operators will
    click it defensively when they can't tell whether a hold is active."""
    clk = FakeClock()
    d = _natural_decision(action="OFF", reason=control_logic.STANDBY)
    out, s = main.apply_manual_override(d, {"action": "AUTO", "expires_ms": None}, clk)
    assert out is d
    assert s == {"action": None, "expires_ms": None}


def test_unknown_action_drops_the_slot():
    """A malformed action ('MAYBE') must not stick — clear it and pass
    through, otherwise a bad payload wedges the override forever."""
    clk = FakeClock()
    d = _natural_decision(action="OFF")
    state = {"action": "MAYBE", "expires_ms": None}
    out, s = main.apply_manual_override(d, state, clk)
    assert out is d
    assert s == {"action": None, "expires_ms": None}
