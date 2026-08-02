# -*- coding: utf-8 -*-
"""Deterministic input grid for decide() characterization (spec §8.1.1).

Shared by tools/gen_decide_golden.py (writes the baseline) and
tests/test_decide_golden.py (compares against it).

KEEP THE GRID STABLE. Changing LEVELS/TRISTATE/SCENARIOS invalidates every
line of the baseline at once, which destroys its value as a refactor guard —
a real regression would be invisible inside thousands of churned lines. If
the grid genuinely must grow, APPEND scenarios; never reorder or remove.

This fixture records CURRENT behaviour, bugs included. That is deliberate:
the acceptance test for a refactor is "nothing changed", not "everything is
correct". Behaviour fixes are separate commits with their own tests.
"""

import os

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "golden", "decide_golden.jsonl")

# Boundary-dense: each threshold appears with a value below, on, and above it.
LEVELS = [None, 0.0, 20.0, 21.0, 79.0, 80.0, 100.0]
TRISTATE = [None, True, False]

BASE_TIMING = {
    "pump_on_elapsed_ms": None, "rain_wet_elapsed_ms": None,
    "level_low_elapsed_ms": None, "burst_phase_elapsed_ms": None,
    "conflict_elapsed_ms": None, "rest_elapsed_ms": None,
}

# (scenario name, timing overrides, ctrl_state overrides)
SCENARIOS = [
    ("cold_off", {}, {}),
    ("running", {"pump_on_elapsed_ms": 5000}, {"pump_state": "ON"}),
    ("running_at_max", {"pump_on_elapsed_ms": 600000}, {"pump_state": "ON"}),
    ("running_under_max", {"pump_on_elapsed_ms": 599999}, {"pump_state": "ON"}),
    ("resting_early", {"rest_elapsed_ms": 0}, {"resting": True}),
    ("resting_done", {"rest_elapsed_ms": 60000}, {"resting": True}),
    ("resting_null_timer", {"rest_elapsed_ms": None}, {"resting": True}),
    ("rain_unconfirmed", {"rain_wet_elapsed_ms": 29999}, {}),
    ("rain_confirmed", {"rain_wet_elapsed_ms": 30000}, {}),
    ("low_held_on", {"level_low_elapsed_ms": 30000}, {"pump_state": "ON"}),
    ("low_brief_on", {"level_low_elapsed_ms": 5000}, {"pump_state": "ON"}),
    ("burst_on_fresh", {"burst_phase_elapsed_ms": 0, "conflict_elapsed_ms": 0},
     {"conflict_latched": True, "burst_phase": "ON"}),
    ("burst_on_expired", {"burst_phase_elapsed_ms": 60000, "conflict_elapsed_ms": 60000},
     {"conflict_latched": True, "burst_phase": "ON"}),
    ("burst_rest_fresh", {"burst_phase_elapsed_ms": 0, "conflict_elapsed_ms": 60000},
     {"conflict_latched": True, "burst_phase": "REST"}),
    ("burst_rest_expired", {"burst_phase_elapsed_ms": 30000, "conflict_elapsed_ms": 60000},
     {"conflict_latched": True, "burst_phase": "REST"}),
    ("conflict_ceiling", {"burst_phase_elapsed_ms": 0, "conflict_elapsed_ms": 900000},
     {"conflict_latched": True, "burst_phase": "ON"}),
    ("holdoff_latched", {}, {"conflict_holdoff": True, "conflict_latched": True}),
]


def iter_cases():
    """Yield (case_id, readings, timing, ctrl_state) in a STABLE order."""
    from control_logic import initial_state
    for sname, t_over, s_over in SCENARIOS:
        timing = dict(BASE_TIMING)
        timing.update(t_over)
        for level in LEVELS:
            for fd in TRISTATE:
                for hw in TRISTATE:
                    for rn in TRISTATE:
                        state = initial_state()
                        state.update(s_over)
                        readings = {"level_pct": level, "float_dry": fd,
                                    "high_water": hw, "raining": rn}
                        cid = "%s|L=%s|F=%s|H=%s|R=%s" % (sname, level, fd, hw, rn)
                        yield cid, readings, dict(timing), state


def record(case_id, decision):
    """Flatten a decision into a stable, diff-friendly JSON record."""
    nxt = decision["next_state"]
    return {
        "case": case_id,
        "action": decision["action"],
        "reason": decision["reason"],
        "pump_state": nxt.get("pump_state"),
        "conflict_latched": nxt.get("conflict_latched"),
        "conflict_holdoff": nxt.get("conflict_holdoff"),
        "burst_phase": nxt.get("burst_phase"),
        "resting": nxt.get("resting"),
        "flags": {k: decision["flags"][k] for k in sorted(decision["flags"])},
    }
