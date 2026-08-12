# 220V Switched-Socket Pump Controller — Firmware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `edge_pump` safe to drive a 220V mains contactor instead of a 12V relay, by adding an actuator-profile axis, a switchable DRAIN/COLLECT mode, reset-loop and short-cycle protection that survives brownout, and CT-based actuation feedback — without disturbing the bench-verified safety core.

**Architecture:** The pure safety core (`control_logic.decide()`, Layers 1–3) is mode-independent and gets extracted once, behaviour-preserving, guarded by a golden fixture captured before the refactor. Two mode-specific trigger layers then sit on top of that shared core. Everything mains-specific — timings, CT, HOA, reset-loop persistence — enters through a profile table that the mode layer never reads. The CT stays out of `decide()`; its only control-path authority is an overload interlock that routes through `pump_controller.apply()` like `apply_manual_override()` already does.

**Tech Stack:** MicroPython on ESP32 (device) / CPython 3.14 + pytest (desktop). Pure-logic modules stay desktop-importable — no `machine`, no `time`, no `esp32` at module top level. Persistence via `esp32.NVS`. No new third-party dependencies.

## Global Constraints

Copied verbatim from the spec (`docs/superpowers/specs/2026-08-02-pump-socket-switch-design.md`). Every task's requirements implicitly include this section.

- **Declared maximum: 10A / 2200W @ 220V.** All safety timings are derived for this load.
- **`ACTUATOR_PROFILE` and `PUMP_MODE` are orthogonal and MUST NOT read each other** (spec §3). A profile carries safety timing and which peripherals exist; a mode carries what the water means.
- **Defaults must preserve current behaviour**: `ACTUATOR_PROFILE = "PUMP_12V"`, `PUMP_MODE = "DRAIN"`. Reflashing an existing node is behaviour-neutral (spec §12.2).
- **The CT never enters `decide()`** (spec §5.5). The single exception, overload trip, must produce a complete decision dict applied via `pump_controller.apply()` — **never** poke the relay directly.
- **`WDT_ENABLED` must not be `False` under `SOCKET_220V`** — enforced by an assertion, not a comment (spec §7).
- **NVS writes are boot-time and transition-time only** — never per control-loop iteration (spec §5.6 flash-endurance note).
- **Never publish a precise amp value.** CT telemetry is a coarse band: `none / low / normal / high` (spec §6.1).
- **Timing contract**: all time enters `decide()` as pre-computed elapsed-ms durations. `pump_controller` owns every timer. Failing to reset a timer on its transition causes chatter — the contract in `control_logic.decide()`'s docstring is load-bearing.
- **Pull-resistor polarity idiom**: pull toward the DE-ASSERTED level so a broken wire can never assert pump-ON (`sensors.build_readers`).
- **`SOCKET_220V` must not be deployed until Task 5 (NVS boot counter) lands** (spec §5.6.1).
- Environment: run pytest **per-suite from the suite directory** — `cd edge_pump && /c/Python314/python -m pytest tests -q`. Never from the repo root; the `[Cloud]` bracket in the path breaks pytest's test-id parser. Python is `/c/Python314/python` (no `python3`).
- Branch: `design/pump-socket-switch-2026-08-02`. Baseline at plan time: **70 tests passing** in `edge_pump`.

---

## Scope

**This plan covers Phase 1 firmware and the documentation deliverables.** It does not cover:

| Excluded | Why | Where it lives |
|---|---|---|
| Physical mains box assembly | Electrician work, not code; gated on spec §10 open items 1, 2 | Task 12 writes the procedure; the build itself is a bench session |
| Server / SPA telemetry, 5 alert types | Spec §6 sequences this behind the in-flight SPA lane to avoid file collisions | A separate Phase 2 plan |
| OTA firmware update (spec §4.8 option C) | Deliberately deferred, not dropped — see below | Spec §10 item 7; needs its own spec because it reopens §8.3/§8.4 |

Tasks 1–11 are firmware. Task 12 is the documentation set the spec makes a delivery requirement (§8.2, §8.3, §8.4, §12.2, §12.3).

### Serviceability route — spec §4.8 requires this plan to name one

**Option A (split compartment). Decided 2026-08-02, user-approved, recorded in spec §4.8.1.**

The box build is out of scope for this plan, but the *decision* cannot be, because A is the only option whose cost is asymmetric in time — roughly NT$200–400 during fabrication, a box rebuild afterwards. Leaving it unstated means it gets decided by default by whoever assembles the enclosure, and the default is "sealed". B (external USB gland) is rejected: an ingress and corrosion path on an outdoor typhoon enclosure. C (OTA) is deferred: MicroPython's A/B rollback on ESP32 is weak, and OTA lets firmware change without the §8.3 signed acceptance record or §8.4 configuration control, so adopting it reopens both sections.

Three build constraints follow, and they bind Task 12's commissioning document even though the build itself does not appear in any task here:

1. **CT burden resistor and TVS clamp permanently soldered at the CT secondary; any service connector downstream of them** (spec §4.6.1). A split bay means people will routinely work in the low-voltage compartment while the primary is live. An open CT secondary on a live primary develops hundreds of volts to low kV. This is the risk option A introduces, and two components eliminate it.
2. **Enclosure-door sensing is telemetry only — never an interlock.** A "door open → inhibit pump" wiring creates a corroded-microswitch path to a pump that refuses to run during a flood. Same reasoning as the pull-toward-de-asserted idiom in `sensors.build_readers`.
3. **SMPS primary stays in the mains bay.** Only the 24V secondary, the ESP32 and the CT front-end cross into the low-voltage bay, or the "openable without isolating mains" label is false — worse than having no split at all.

Task 12's mains-box commissioning document must carry all three as sign-off items; spec §8.3 now lists them.

> **Round-6 (installer/bench electrician review), 2026-08-12 — spec amended, firmware tasks unchanged.** Four hardware/BOM/commissioning findings (E1–E4) were adjudicated and folded into the design spec (§4.1/§4.2 BOM, §4.2.2(2b), §4.3, §7, §8.3, §9.1, §11 Round 6): **E1** coil suppression = flyback diode + series Zener/TVS, with MOSFET **`V_DS ≥ 60V`**; **E2** contactor **AC3 ≥ ~18A frame**; **E3** **Type-2 coordinated** MPCB+contactor pair; **E4** control supply kept downstream of the RCD (attribution gap documented; the pre-sized 24V DC-UPS is the Phase-2 fix). **No firmware task (1–11) changes.** Task 12 inherits the new §8.3 sign-off rows by reference.

**Task ordering is not arbitrary.** Task 1 must precede Task 2 (the fixture is worthless captured after the refactor). Task 2 must precede Tasks 4 and 6 (spec §5.4 mandates the two-commit sequence). Tasks 8–10 depend on Task 3's profile table.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `edge_pump/control_logic.py` | Modify | Pure safety core. Gains `_preamble()`, `_safety_guards()`, `_trigger_drain()`, `_trigger_collect()`, `decide_collect()`, Layer 3.5, new reason codes |
| `edge_pump/profiles.py` | **Create** | Actuator profile parameter table + config validation |
| `edge_pump/boot_guard.py` | **Create** | Pure reset-loop / boot-hold-off decisions. No NVS import |
| `edge_pump/persist.py` | **Create** | All NVS key access (boot count, contactor ops) with write throttling |
| `edge_pump/current_sense.py` | **Create** | CT: RMS from samples, band classification, actuation-feedback truth table |
| `edge_pump/pump_controller.py` | Modify | Adds `_min_off_since` timer, contactor-operation counting hook |
| `edge_pump/sensors.py` | Modify | Adds CT ADC reader and HOA digital reader to `build_readers()` |
| `edge_pump/config.py` | Modify | `ACTUATOR_PROFILE`, `PUMP_MODE`, `CT_ADC_PIN`, `HOA_HAND_PIN`, CT thresholds |
| `edge_pump/main.py` | Modify | Mode dispatch, profile wiring, boot registration, CT read + interlock, strict manual rejection |
| `edge_pump/mqtt_client.py` | Modify | `build_payload()` gains the additive telemetry fields |
| `edge_pump/tests/golden_grid.py` | **Create** | Deterministic input grid, shared by generator and comparison test |
| `edge_pump/tools/gen_decide_golden.py` | **Create** | Writes/regenerates the golden baseline |
| `edge_pump/tests/golden/decide_golden.jsonl` | **Create** | The baseline itself (JSONL so git diffs line-by-line) |
| `edge_pump/tests/test_decide_golden.py` | **Create** | Compares live `decide()` against the baseline |
| `edge_pump/tests/test_profiles.py` | **Create** | Profile table + validation |
| `edge_pump/tests/test_boot_guard.py` | **Create** | Reset-loop and hold-off logic |
| `edge_pump/tests/test_persist.py` | **Create** | NVS access + write throttling |
| `edge_pump/tests/test_current_sense.py` | **Create** | RMS, banding, diagnosis truth table |
| `edge_pump/tests/test_control_logic_collect.py` | **Create** | COLLECT trigger layer |
| `edge_pump/tests/test_min_off.py` | **Create** | Layer 3.5 |
| `edge_pump/tests/fakes.py` | Modify | Adds `FakeNVS` |
| `docs/deployment/pump-bench-commissioning.md` | Modify | 4-configuration matrix |
| `docs/deployment/mains-box-commissioning.md` | **Create** | Signed-off mains acceptance record |
| `docs/deployment/pump-fleet-update.md` | **Create** | Canary rollout + rehearsed rollback runbook |
| `docs/deployment/pump-handover-checklist.md` | **Create** | The nine §12.3 deliverables |

---

## Task 1: Golden-fixture characterization harness

**Why this is first:** Spec §8.1.1. Task 2 claims to be a behaviour-preserving refactor, and "the existing 70 tests still pass" is a necessary but *not sufficient* proof — those tests cover what their author thought of, and a refactor's risk lives in what nobody thought of. This task captures current behaviour across a boundary-dense grid, **including current bugs**, so Task 2 has something to be measured against.

**Files:**
- Create: `edge_pump/tests/golden_grid.py`
- Create: `edge_pump/tools/gen_decide_golden.py`
- Create: `edge_pump/tests/golden/decide_golden.jsonl`
- Create: `edge_pump/tests/test_decide_golden.py`

**Interfaces:**
- Consumes: `control_logic.decide`, `control_logic.initial_state` (unchanged public API)
- Produces: `golden_grid.iter_cases() -> iterator of (case_id, readings, timing, ctrl_state)`; `golden_grid.record(case_id, decision) -> dict`; `golden_grid.GOLDEN_PATH` (str, absolute path to the baseline)

- [ ] **Step 1: Write the grid module**

Create `edge_pump/tests/golden_grid.py`:

```python
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
```

- [ ] **Step 2: Write the generator script**

Create `edge_pump/tools/gen_decide_golden.py`:

```python
# -*- coding: utf-8 -*-
"""Regenerate the decide() golden baseline.

    cd edge_pump && /c/Python314/python tools/gen_decide_golden.py

Run this ONLY when a behaviour change is intended. After running, inspect
`git diff` on the baseline: every changed line is a behaviour change you
are asserting is correct. A refactor that changes even one line is not a
refactor (spec §8.1.1).
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))          # edge_pump/
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "tests"))

import control_logic                                 # noqa: E402
import golden_grid                                   # noqa: E402


def main():
    os.makedirs(os.path.dirname(golden_grid.GOLDEN_PATH), exist_ok=True)
    n = 0
    with open(golden_grid.GOLDEN_PATH, "w", encoding="utf-8", newline="\n") as fh:
        for cid, readings, timing, state in golden_grid.iter_cases():
            decision = control_logic.decide(readings, timing, state,
                                            control_logic.DEFAULT_CONFIG)
            fh.write(json.dumps(golden_grid.record(cid, decision),
                                sort_keys=True, ensure_ascii=False))
            fh.write("\n")
            n += 1
    print("wrote %d cases -> %s" % (n, golden_grid.GOLDEN_PATH))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Write the comparison test**

Create `edge_pump/tests/test_decide_golden.py`:

```python
import json
import os

import control_logic
# NOT `import golden_grid`. conftest.py puts only edge_pump/ on sys.path and
# tests/ is a package (it has __init__.py), so tests/ itself is NOT on the
# path — a bare top-level import fails at COLLECTION time, taking both tests
# in this file with it. The generator script gets away with `import
# golden_grid` because it inserts tests/ into sys.path itself.
from tests import golden_grid


def _load_baseline():
    assert os.path.exists(golden_grid.GOLDEN_PATH), (
        "golden baseline missing — run tools/gen_decide_golden.py")
    with open(golden_grid.GOLDEN_PATH, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_grid_size_is_stable():
    # Guards against an accidental grid edit silently shrinking coverage.
    assert len(list(golden_grid.iter_cases())) == 17 * 7 * 3 * 3 * 3


def test_decide_matches_golden_baseline():
    baseline = _load_baseline()
    live = [golden_grid.record(cid, control_logic.decide(
                r, t, s, control_logic.DEFAULT_CONFIG))
            for cid, r, t, s in golden_grid.iter_cases()]

    assert len(live) == len(baseline), (
        "case count changed: grid edited without regenerating the baseline")

    diffs = [(b["case"], b, l) for b, l in zip(baseline, live) if b != l]
    assert not diffs, "behaviour changed in %d case(s); first: %s\n  was: %s\n  now: %s" % (
        len(diffs), diffs[0][0], diffs[0][1], diffs[0][2])
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_decide_golden.py -q`
Expected: FAIL — `test_decide_matches_golden_baseline` errors with "golden baseline missing". `test_grid_size_is_stable` should PASS.

If instead you get `ModuleNotFoundError: No module named 'golden_grid'` and a
**collection error** (both tests failing to run at all), the import in Step 3
was written as `import golden_grid` rather than `from tests import
golden_grid`. See the comment in that file.

- [ ] **Step 5: Generate the baseline**

Run: `cd edge_pump && /c/Python314/python tools/gen_decide_golden.py`
Expected: `wrote 3213 cases -> .../tests/golden/decide_golden.jsonl`

- [ ] **Step 6: Run the full suite**

Run: `cd edge_pump && /c/Python314/python -m pytest tests -q`
Expected: PASS — 72 tests (70 baseline + 2 new).

- [ ] **Step 7: Sanity-check the baseline is not degenerate**

Run: `cd edge_pump && /c/Python314/python -c "import json,collections; rs=collections.Counter(json.loads(l)['reason'] for l in open('tests/golden/decide_golden.jsonl',encoding='utf-8')); print(sorted(rs.items()))"`
Expected: at least 8 distinct reason codes present, including `CONFLICT_BURST_ON`, `CONFLICT_LATCH_OFF`, `DRY_RUN_OFF`, `MAX_RUNTIME_REST`, `HIGH_WATER`, `HYSTERESIS_ON`, `RAIN_TRIGGER`, `STANDBY`, `HOLD`. A baseline dominated by one reason would mean the grid never reaches the interesting branches.

- [ ] **Step 8: Commit**

```bash
git add edge_pump/tests/golden_grid.py edge_pump/tools/gen_decide_golden.py \
        edge_pump/tests/golden/decide_golden.jsonl edge_pump/tests/test_decide_golden.py
git commit -m "test: capture decide() golden baseline before refactor

3213-case boundary-dense grid recording CURRENT behaviour, bugs included.
This is the acceptance gate for the _safety_guards() extraction: a
behaviour-preserving refactor must not change a single line (spec 8.1.1)."
```

---

## Task 2: Behaviour-preserving `_safety_guards()` extraction

**Why this is its own commit:** Spec §5.4 makes the two-commit sequence mandatory. Mixed with COLLECT mode, a bug in either becomes indistinguishable from the other, and this code currently runs on four commissioned nodes.

**Files:**
- Modify: `edge_pump/control_logic.py:68-192` (the body of `decide()`)

**Interfaces:**
- Consumes: nothing new
- Produces: `_preamble(readings, timing, config) -> (float_dry, rain_confirmed, conflict_now, flags)`; `_safety_guards(state, timing, config, float_dry, conflict_now, flags) -> decision dict | None` (None means "fall through to the trigger layer"); `_trigger_drain(readings, timing, state, config, flags, float_dry, rain_confirmed) -> decision dict`. `decide()` keeps its exact existing signature and return shape.

- [ ] **Step 1: Extract, preserving behaviour exactly**

In `edge_pump/control_logic.py`, replace the whole body of `decide()` (lines 68–192, from `def decide(` to the end of the file) with:

```python
def _preamble(readings, timing, config):
    """Derive the mode-independent view of the world: sensor truth, vote
    tally, conflict state, and the flags dict every layer annotates."""
    float_dry = readings.get("float_dry")  # True=dry(danger), False=safe, None=off
    rain_confirmed = _rain_confirmed(readings, timing, config)
    votes = _wet_votes(readings, config, rain_confirmed)
    conflict_now = (float_dry is True) and (votes >= 2)

    flags = {
        "raining": readings.get("raining") is True,
        "float_safe": (float_dry is False) if float_dry is not None else None,
        "high_water": readings.get("high_water"),
        "sensor_conflict": False,
        "dry_run_protect": False,
        "max_runtime_rest": False,
    }
    return float_dry, rain_confirmed, conflict_now, flags


def _safety_guards(state, timing, config, float_dry, conflict_now, flags):
    """Layers 1-3: the mode-INDEPENDENT safety core.

    Returns a decision dict to return immediately, or None to fall through
    to the mode's trigger layer. Mutates `state` in place on fall-through
    paths (clearing latches), which the caller relies on.

    Nothing in here may consult PUMP_MODE. Whether high water means "flood"
    or "container full" is the trigger layer's business; whether the pump is
    allowed to run at all is this function's, and the answer is the same in
    both modes.
    """
    # ---- Holdoff: ceiling was hit; stay OFF+alarm until sensors re-agree ----
    if state.get("conflict_holdoff"):
        if conflict_now:
            flags["sensor_conflict"] = True
            return _mk("OFF", state, flags, CONFLICT_LATCH_OFF)
        state["conflict_holdoff"] = False
        state["conflict_latched"] = False
        state["burst_phase"] = None

    # ---- Layer 1: guarded conflict override (bounded bursts) ----
    if conflict_now or state.get("conflict_latched"):
        if conflict_now:
            state["conflict_latched"] = True
            flags["sensor_conflict"] = True

            ce = timing.get("conflict_elapsed_ms")
            if ce is not None and ce >= config["conflict_max_ms"]:
                state["conflict_holdoff"] = True
                state["burst_phase"] = None
                return _mk("OFF", state, flags, CONFLICT_LATCH_OFF)

            phase = state.get("burst_phase") or "ON"
            pe = timing.get("burst_phase_elapsed_ms") or 0
            if phase == "ON":
                if pe >= config["burst_on_ms"]:
                    state["burst_phase"] = "REST"
                    return _mk("OFF", state, flags, CONFLICT_BURST_REST)
                state["burst_phase"] = "ON"
                return _mk("ON", state, flags, CONFLICT_BURST_ON)
            else:  # REST
                if pe >= config["burst_cooldown_ms"]:
                    state["burst_phase"] = "ON"
                    return _mk("ON", state, flags, CONFLICT_BURST_ON)
                state["burst_phase"] = "REST"
                return _mk("OFF", state, flags, CONFLICT_BURST_REST)

        # latched but no longer conflicting -> clear and fall through
        state["conflict_latched"] = False
        state["burst_phase"] = None

    state["burst_phase"] = None

    # ---- Layer 2: dry-run protection (hard interlock) ----
    if float_dry is True:
        flags["dry_run_protect"] = True
        return _mk("OFF", state, flags, DRY_RUN_OFF)

    # ---- Layer 3: max-runtime duty cycle (bounded rest prevents burnout) ----
    on_elapsed = timing.get("pump_on_elapsed_ms")
    if state.get("resting"):
        rest_elapsed = timing.get("rest_elapsed_ms") or 0
        if rest_elapsed < config["rest_ms"]:
            flags["max_runtime_rest"] = True
            return _mk("OFF", state, flags, MAX_RUNTIME_REST)
        state["resting"] = False  # rest complete -> resume normal control
    elif state.get("pump_state") == "ON" and on_elapsed is not None \
            and on_elapsed >= config["max_run_ms"]:
        state["resting"] = True
        flags["max_runtime_rest"] = True
        return _mk("OFF", state, flags, MAX_RUNTIME_REST)

    return None


def _trigger_drain(readings, timing, state, config, flags, float_dry, rain_confirmed):
    """Layers 4-5 for DRAIN: high water is an emergency, pump it out."""
    level = readings.get("level_pct")
    high_water = readings.get("high_water") is True
    on_threshold = config["rain_on_threshold"] if rain_confirmed else config["high_threshold"]

    if high_water:
        return _mk("ON", state, flags, HIGH_WATER)
    if level is not None and level >= on_threshold:
        return _mk("ON", state, flags, RAIN_TRIGGER if rain_confirmed else HYSTERESIS_ON)

    if state.get("pump_state") == "ON":
        if level is None:
            # digital-only mode: high_water already cleared -> stop
            return _mk("OFF", state, flags, STANDBY)
        low_elapsed = timing.get("level_low_elapsed_ms")
        # Confirmed rain suppresses the analog dry-off ONLY when the float
        # switch is present and reports safe (float_dry is False). With the
        # float disabled (None) the analog reading is the sole dry protection,
        # so rain must not override it.
        rain_holds_pump = rain_confirmed and (float_dry is False)
        if level <= config["low_threshold"] and not high_water \
                and not rain_holds_pump and low_elapsed is not None \
                and low_elapsed >= config["dry_off_delay_ms"]:
            return _mk("OFF", state, flags, STANDBY)
        return _mk("HOLD", state, flags, HOLD)

    # ---- Layer 5: standby ----
    return _mk("OFF", state, flags, STANDBY)


def decide(readings, timing, ctrl_state, config):
    """Return a pump decision from readings, elapsed-ms timers, and state.

    DRAIN mode (the historical default). COLLECT lives in decide_collect();
    both share _safety_guards().

    Caller (pump_controller) contract — the pure function relies on the caller
    to maintain these elapsed-ms timers in `timing` and reset them on the
    transitions decide() signals via `next_state`:
      - pump_on_elapsed_ms:    reset to 0 when the pump transitions to ON.
      - level_low_elapsed_ms:  continuous ms the analog level has been <= low.
      - rain_wet_elapsed_ms:   continuous ms rain has been asserted.
      - burst_phase_elapsed_ms: reset to 0 when next_state["burst_phase"] changes.
      - conflict_elapsed_ms:   reset to 0 when the conflict first latches.
      - rest_elapsed_ms:       continuous-OFF duration — reset to 0 on each ON->OFF
                               and cleared while ON, so the max-runtime rest measures
                               ACTUAL rest (a conflict burst that runs the pump
                               mid-rest restarts it rather than consuming it).
    Failing to reset a timer on its transition causes chatter (e.g. the conflict
    burst flapping every tick), so this contract is load-bearing.
    """
    state = dict(ctrl_state)
    float_dry, rain_confirmed, conflict_now, flags = _preamble(readings, timing, config)

    guarded = _safety_guards(state, timing, config, float_dry, conflict_now, flags)
    if guarded is not None:
        return guarded

    return _trigger_drain(readings, timing, state, config, flags,
                          float_dry, rain_confirmed)
```

- [ ] **Step 2: Run the golden comparison — this is the acceptance gate**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_decide_golden.py -q`
Expected: PASS. **Any failure means the extraction changed behaviour — fix the extraction, do not regenerate the baseline.**

- [ ] **Step 3: Run the full suite**

Run: `cd edge_pump && /c/Python314/python -m pytest tests -q`
Expected: PASS — 72 tests.

- [ ] **Step 4: Confirm the baseline file is untouched**

Run: `cd .. && git status --short edge_pump/tests/golden/`
Expected: **no output.** If the baseline shows as modified, someone regenerated it to make the test pass — revert it and fix the refactor.

- [ ] **Step 5: Commit**

```bash
git add edge_pump/control_logic.py
git commit -m "refactor(pump): extract _safety_guards() from decide()

Behaviour-preserving. Splits the mode-INDEPENDENT safety core (Layers 1-3)
from the DRAIN trigger layer so COLLECT can reuse the former without
doubling the code surface that can damage a pump (spec 5.4).

Verified against the 3213-case golden baseline: zero differences. The
baseline file is unchanged by this commit, which is the proof."
```

---

## Task 3: Actuator profile table and config additions

**Files:**
- Create: `edge_pump/profiles.py`
- Create: `edge_pump/tests/test_profiles.py`
- Modify: `edge_pump/config.py` (append a new section; update the pin-comment block)
- Modify: `edge_pump/tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `profiles.get_profile(name) -> dict` (a copy; raises `ValueError` on unknown name); `profiles.validate(profile, wdt_enabled) -> None` (raises `ValueError`); module constants `profiles.PUMP_12V`, `profiles.SOCKET_220V`. Profile keys: `min_off_ms`, `burst_cooldown_ms`, `boot_holdoff_ms`, `boot_holdoff_urgent_ms`, `boot_loop_holdoff_ms`, `boot_loop_threshold`, `boot_healthy_ms`, `ct_enabled`, `hoa_enabled`, `wdt_required`, `contactor_service_ops`.

- [ ] **Step 1: Write the failing test**

Create `edge_pump/tests/test_profiles.py`:

```python
import pytest

import profiles


def test_known_profiles_exist():
    assert profiles.get_profile("PUMP_12V")["min_off_ms"] == 0
    assert profiles.get_profile("SOCKET_220V")["min_off_ms"] == 180000


def test_unknown_profile_rejected():
    with pytest.raises(ValueError):
        profiles.get_profile("SOCKET_110V")


def test_get_profile_returns_a_copy():
    # A caller mutating its profile must not poison the module table.
    p = profiles.get_profile("SOCKET_220V")
    p["min_off_ms"] = 1
    assert profiles.get_profile("SOCKET_220V")["min_off_ms"] == 180000


def test_both_profiles_have_identical_key_sets():
    # A key present in one profile and missing from the other is how a
    # KeyError reaches the field at 2am instead of the bench.
    assert set(profiles.PUMP_12V) == set(profiles.SOCKET_220V)


def test_mains_profile_requires_watchdog():
    # A hung controller holds the contactor closed; the WDT is the only
    # thing that stops a 2200W motor (spec 7).
    with pytest.raises(ValueError):
        profiles.validate(profiles.get_profile("SOCKET_220V"), wdt_enabled=False)


def test_mains_profile_accepts_watchdog_enabled():
    profiles.validate(profiles.get_profile("SOCKET_220V"), wdt_enabled=True)


def test_demo_profile_tolerates_watchdog_disabled():
    profiles.validate(profiles.get_profile("PUMP_12V"), wdt_enabled=False)


def test_mains_burst_cooldown_not_shorter_than_min_off():
    # Layer 1 conflict bursts sit ABOVE the min-off guard, so a short
    # cooldown would restart an AC motor ~30 times in 15 minutes through
    # the one door min-off cannot close (spec 5.2).
    p = profiles.get_profile("SOCKET_220V")
    assert p["burst_cooldown_ms"] >= p["min_off_ms"]


def test_urgent_holdoff_shorter_than_normal():
    p = profiles.get_profile("SOCKET_220V")
    assert 0 < p["boot_holdoff_urgent_ms"] < p["boot_holdoff_ms"]


def test_reset_loop_holdoff_is_the_longest():
    p = profiles.get_profile("SOCKET_220V")
    assert p["boot_loop_holdoff_ms"] > p["boot_holdoff_ms"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_profiles.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'profiles'`

- [ ] **Step 3: Write the profile table**

Create `edge_pump/profiles.py`:

```python
# -*- coding: utf-8 -*-
"""Actuator profile parameter table (spec §5.2).

A profile answers ONE question: what is on the other side of GPIO 33, and
therefore how gently must it be treated. It carries safety timing and which
peripherals exist.

It must NEVER carry anything about what the water MEANS — that is PUMP_MODE's
job, and the two axes are deliberately orthogonal (spec §3). If you find
yourself wanting `if profile == ... and mode == ...`, the logic belongs in
neither and the split is wrong.

Desktop-importable: no `machine`, no `esp32`.
"""

PUMP_12V = {
    # 0 disables the Layer 3.5 guard entirely — a 12V DC pump has no
    # start-frequency limit worth protecting.
    "min_off_ms": 0,
    "burst_cooldown_ms": 30000,
    "boot_holdoff_ms": 0,
    "boot_holdoff_urgent_ms": 0,
    "boot_loop_holdoff_ms": 0,
    "boot_loop_threshold": 0,        # 0 disables reset-loop detection
    "boot_healthy_ms": 0,
    "ct_enabled": False,
    "hoa_enabled": False,
    "wdt_required": False,
    "contactor_service_ops": 0,      # 0 disables the service-due counter
}

SOCKET_220V = {
    # Small AC pumps tolerate roughly 10-20 starts/hour. 180s == 20/hr.
    "min_off_ms": 180000,
    # Layer 1 bursts bypass min-off by design, so the cooldown must itself
    # be long enough to be safe for an AC motor.
    "burst_cooldown_ms": 180000,
    "boot_holdoff_ms": 60000,
    # A flat 60s would refuse to pump while water rises after a brownout
    # reset — the exact scenario the node exists for (spec §5.6).
    "boot_holdoff_urgent_ms": 10000,
    # In a confirmed reset loop, urgency does NOT shorten the hold-off:
    # a node rebooting every 30s cannot be trusted to have read its
    # sensors correctly. 5 min breaks the WDT loop cycle decisively while
    # still returning a genuinely-recovered node within one surge window.
    "boot_loop_holdoff_ms": 300000,
    "boot_loop_threshold": 3,
    "boot_healthy_ms": 300000,
    "ct_enabled": True,
    "hoa_enabled": True,
    "wdt_required": True,
    # Placeholder pending the purchased contactor's AC3 electrical life
    # (spec §10 open item 5). 60_000 == 60% of a 100k-operation part.
    "contactor_service_ops": 60000,
}

_PROFILES = {"PUMP_12V": PUMP_12V, "SOCKET_220V": SOCKET_220V}


def get_profile(name):
    """Return a COPY of the named profile. Raises ValueError if unknown."""
    if name not in _PROFILES:
        raise ValueError("unknown ACTUATOR_PROFILE: %r" % (name,))
    return dict(_PROFILES[name])


def validate(profile, wdt_enabled):
    """Raise ValueError if the runtime configuration contradicts the profile.

    Called at boot, before the relay is ever driven. Failing loudly here is
    the point: a misconfigured mains node must not reach the control loop.
    """
    if profile["wdt_required"] and not wdt_enabled:
        raise ValueError(
            "ACTUATOR_PROFILE requires WDT_ENABLED=True: a hung controller "
            "would hold the contactor closed indefinitely")
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_profiles.py -q`
Expected: PASS — 10 tests.

- [ ] **Step 5: Add the config entries**

In `edge_pump/config.py`, append after the `SOCKET_TIMEOUT_S` line (currently line 95):

```python

# ============ 致動器 profile 與運作模式（spec §5.1）============
# 兩個正交軸，互不參照：
#   ACTUATOR_PROFILE — GPIO 33 另一端是什麼（安全時序、周邊是否存在）
#   PUMP_MODE        — 水位代表什麼意義（觸發邏輯）
# 預設值即為現行行為，既有節點重新燒錄後行為不變（spec §12.2）。
ACTUATOR_PROFILE = "PUMP_12V"   # "SOCKET_220V" | "PUMP_12V"
PUMP_MODE = "DRAIN"             # "DRAIN" | "COLLECT"

# ============ 市電箱周邊腳位（僅 SOCKET_220V 建構）============
CT_ADC_PIN = 39        # 電流互感器 — ADC1_CH3 只讀腳，WiFi 安全。35 已保留給電池
HOA_HAND_PIN = 13      # HOA 面板開關第 2 極；HIGH_WATER 移至 26 後釋出

# HOA 拉向「非 HAND」：斷線時韌體判定為 AUTO，熔接偵測維持啟用（傾向誤報）。
# 反向配置會讓斷線永久停用熔接偵測（傾向漏報）。誤報優於盲點。
HOA_HAND_ACTIVE_LOW = True

# CT 分級門檻（ADC counts，非安培 — spec §6.1 禁止發布精確電流值）。
# 這些是佔位值，必須在 §8.3 台架以已知電阻性負載校正後改寫。
CT_BAND_LOW = 40       # 低於此值視為無電流
CT_BAND_NORMAL = 120   # 低於此值視為輕載
CT_BAND_HIGH = 900     # 高於此值視為過載
CT_SAMPLE_RATE_HZ = 1000
CT_SAMPLE_CYCLES = 3   # 整數個市電週期，否則殘留基波會偏移 RMS
```

- [ ] **Step 6: Extend the config test**

In `edge_pump/tests/test_config.py`, replace `test_new_sensor_pins_distinct_from_existing` with:

```python
def test_new_sensor_pins_distinct_from_existing():
    # Battery/power pins ship as None (unwired) — only wired pins can clash.
    used = {p for p in (config.RELAY_PIN, config.LED_RED_PIN, config.LED_GREEN_PIN,
                        config.ADC_PIN, config.BATTERY_ADC_PIN, config.POWER_SOURCE_PIN,
                        config.CT_ADC_PIN, config.HOA_HAND_PIN)
            if p is not None}
    for pin in (config.FLOAT_PIN, config.RAIN_PIN, config.HIGH_WATER_PIN):
        assert pin not in used


def test_all_wired_pins_are_unique():
    pins = [config.RELAY_PIN, config.LED_RED_PIN, config.LED_GREEN_PIN,
            config.ADC_PIN, config.BATTERY_ADC_PIN, config.POWER_SOURCE_PIN,
            config.CT_ADC_PIN, config.HOA_HAND_PIN,
            config.FLOAT_PIN, config.RAIN_PIN, config.HIGH_WATER_PIN]
    wired = [p for p in pins if p is not None]
    assert len(wired) == len(set(wired)), "duplicate GPIO assignment"


def test_ct_pin_is_input_only_adc1():
    # GPIO 39 is ADC1_CH3: input-only and unaffected by WiFi (ADC2 is not).
    # 35 is reserved for BATTERY_ADC_PIN per the §6 commissioning note.
    assert config.CT_ADC_PIN == 39


def test_defaults_preserve_existing_behaviour():
    # Reflashing a commissioned node must be behaviour-neutral (spec §12.2).
    assert config.ACTUATOR_PROFILE == "PUMP_12V"
    assert config.PUMP_MODE == "DRAIN"


def test_ct_bands_ascend():
    assert config.CT_BAND_LOW < config.CT_BAND_NORMAL < config.CT_BAND_HIGH
```

- [ ] **Step 7: Run the full suite**

Run: `cd edge_pump && /c/Python314/python -m pytest tests -q`
Expected: PASS — 86 tests (72 + 10 profile + 4 config; `test_config.py` nets
**four** new tests because Step 6 rewrites an existing one rather than adding it).

- [ ] **Step 8: Commit**

```bash
git add edge_pump/profiles.py edge_pump/tests/test_profiles.py \
        edge_pump/config.py edge_pump/tests/test_config.py
git commit -m "feat(pump): add actuator profile table and mains config

ACTUATOR_PROFILE and PUMP_MODE are orthogonal axes that never read each
other (spec 3). Defaults are PUMP_12V/DRAIN so reflashing a commissioned
node is behaviour-neutral.

profiles.validate() refuses to run a mains profile with the watchdog off."
```

---

## Task 4: Layer 3.5 — minimum-off short-cycle guard

**Files:**
- Modify: `edge_pump/control_logic.py` (new reason code, `_preamble` flags, `_safety_guards`)
- Modify: `edge_pump/pump_controller.py` (new `_min_off_since` timer)
- Modify: `edge_pump/main.py:14-26` (`build_config` — drift guard, see Step 9)
- Create: `edge_pump/tests/test_min_off.py`
- Regenerate: `edge_pump/tests/golden/decide_golden.jsonl`

**Interfaces:**
- Consumes: `profiles.get_profile()["min_off_ms"]` (Task 3)
- Produces: `control_logic.MIN_OFF_WAIT` (str constant `"MIN_OFF_WAIT"`); flags gain `min_off_wait` (bool) and, when blocking, `min_off_remaining_ms` (int); `timing` gains `min_off_elapsed_ms`; config key `min_off_ms`

- [ ] **Step 1: Write the failing test**

Create `edge_pump/tests/test_min_off.py`:

```python
from control_logic import decide, initial_state, DEFAULT_CONFIG, MIN_OFF_WAIT


def cfg(**over):
    c = dict(DEFAULT_CONFIG)
    c.update(over)
    return c


def timing(**over):
    t = {"pump_on_elapsed_ms": None, "rain_wet_elapsed_ms": None,
         "level_low_elapsed_ms": None, "burst_phase_elapsed_ms": None,
         "conflict_elapsed_ms": None, "rest_elapsed_ms": None,
         "min_off_elapsed_ms": None}
    t.update(over)
    return t


def readings(level=None, float_dry=None, high_water=None, raining=None):
    return {"level_pct": level, "float_dry": float_dry,
            "high_water": high_water, "raining": raining}


def test_min_off_blocks_restart_inside_the_window():
    d = decide(readings(high_water=True, float_dry=False),
               timing(min_off_elapsed_ms=60000),
               initial_state(), cfg(min_off_ms=180000))
    assert d["action"] == "OFF" and d["reason"] == MIN_OFF_WAIT
    assert d["flags"]["min_off_wait"] is True


def test_min_off_reports_remaining_time():
    d = decide(readings(high_water=True, float_dry=False),
               timing(min_off_elapsed_ms=60000),
               initial_state(), cfg(min_off_ms=180000))
    assert d["flags"]["min_off_remaining_ms"] == 120000


def test_min_off_releases_after_the_window():
    d = decide(readings(high_water=True, float_dry=False),
               timing(min_off_elapsed_ms=180000),
               initial_state(), cfg(min_off_ms=180000))
    assert d["action"] == "ON" and d["reason"] == "HIGH_WATER"


def test_min_off_disabled_by_zero():
    d = decide(readings(high_water=True, float_dry=False),
               timing(min_off_elapsed_ms=0),
               initial_state(), cfg(min_off_ms=0))
    assert d["action"] == "ON"


def test_min_off_ignores_a_null_timer():
    # Cold boot: the pump has never run, so there is no off-period to
    # measure. Boot hold-off covers this window instead (spec §5.6).
    d = decide(readings(high_water=True, float_dry=False),
               timing(min_off_elapsed_ms=None),
               initial_state(), cfg(min_off_ms=180000))
    assert d["action"] == "ON"


def test_min_off_does_not_stop_a_running_pump():
    # The guard limits START frequency. Interrupting a running pump would
    # be the very short-cycling it exists to prevent.
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(high_water=True, float_dry=False),
               timing(min_off_elapsed_ms=0), st, cfg(min_off_ms=180000))
    assert d["action"] != "OFF"


def test_dry_run_still_outranks_min_off():
    d = decide(readings(float_dry=True),
               timing(min_off_elapsed_ms=0),
               initial_state(), cfg(min_off_ms=180000))
    assert d["reason"] == "DRY_RUN_OFF"


def test_max_runtime_rest_still_outranks_min_off():
    st = dict(initial_state(), resting=True)
    d = decide(readings(high_water=True, float_dry=False),
               timing(rest_elapsed_ms=0, min_off_elapsed_ms=0), st,
               cfg(min_off_ms=180000))
    assert d["reason"] == "MAX_RUNTIME_REST"


def test_min_off_does_not_consume_the_rest_timer():
    # Independent timers: rest_elapsed_ms=None means "not resting" to
    # Layer 3 only because it coerces None to 0. min-off must not inherit
    # that coercion (spec §5.4).
    d = decide(readings(high_water=True, float_dry=False),
               timing(rest_elapsed_ms=None, min_off_elapsed_ms=1000),
               initial_state(), cfg(min_off_ms=180000))
    assert d["reason"] == MIN_OFF_WAIT
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_min_off.py -q`
Expected: FAIL — `ImportError: cannot import name 'MIN_OFF_WAIT'`

- [ ] **Step 3: Add the reason code and default**

In `edge_pump/control_logic.py`, after the `MANUAL_REJECTED` line (currently line 22) add:

```python
MIN_OFF_WAIT = "MIN_OFF_WAIT"
```

And in `DEFAULT_CONFIG`, after `"rest_ms": 60000,` add:

```python
    "min_off_ms": 0,          # 0 disables Layer 3.5 (12V profile)
```

- [ ] **Step 4: Add the flags**

In `_preamble()`, add to the `flags` dict after `"max_runtime_rest": False,`:

```python
        "min_off_wait": False,
```

- [ ] **Step 5: Add Layer 3.5 to `_safety_guards()`**

In `_safety_guards()`, immediately before the final `return None`:

```python
    # ---- Layer 3.5: minimum-off short-cycle guard ----
    # Deliberately uses its OWN timer rather than rest_elapsed_ms. The two
    # have opposite None contracts: Layer 3 coerces None to 0 (blocking),
    # min-off treats None as not-blocking (cold boot has no off-period to
    # measure). One timer read two opposite ways is how a future edit to
    # that `or 0` silently changes this guard (spec §5.4).
    min_off_ms = config.get("min_off_ms") or 0
    if min_off_ms and state.get("pump_state") != "ON":
        mo = timing.get("min_off_elapsed_ms")
        if mo is not None and mo < min_off_ms:
            flags["min_off_wait"] = True
            flags["min_off_remaining_ms"] = min_off_ms - mo
            return _mk("OFF", state, flags, MIN_OFF_WAIT)

    return None
```

- [ ] **Step 6: Run the new test**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_min_off.py -q`
Expected: PASS — 9 tests.

- [ ] **Step 7: Maintain the timer in `pump_controller`**

In `edge_pump/pump_controller.py`, in `__init__` after `self._off_since = None` (line 19):

```python
        self._min_off_since = None  # Layer 3.5's own clock — see control_logic
```

In `snapshot_timing()`'s returned dict, after the `rest_elapsed_ms` entry:

```python
            # Layer 3.5's timer. Maintained on the same transitions as
            # rest_elapsed_ms but kept separate on purpose: the two layers
            # read a None value in opposite directions (spec §5.4).
            "min_off_elapsed_ms": self._elapsed(self._min_off_since, now),
```

In `apply()`, extend the pump-on/continuous-off block:

```python
        if nxt["pump_state"] == "ON":
            if prev["pump_state"] != "ON":
                self._on_since = now
            self._off_since = None            # running -> not resting
            self._min_off_since = None
        else:  # nxt OFF
            if prev["pump_state"] == "ON":
                self._off_since = now          # ON->OFF: start the rest/off clock
                self._min_off_since = now
            self._on_since = None
```

- [ ] **Step 8: Add the controller-level tests**

Append to `edge_pump/tests/test_pump_controller.py`:

```python
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
```

- [ ] **Step 9: Keep `build_config()` in step with `DEFAULT_CONFIG`**

`tests/test_main_iteration.py::test_build_config_matches_control_logic_defaults` asserts the two dicts are equal — it is a drift guard, and adding a key to one side alone breaks it.

In `edge_pump/main.py`, add to the dict returned by `build_config()`, after `"rest_ms": config.REST_MS,`:

```python
        "min_off_ms": 0,          # profile-supplied in Task 7; 0 = guard off
```

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_main_iteration.py -q`
Expected: PASS.

- [ ] **Step 10: Regenerate the golden baseline and review the diff**

The new `min_off_wait` flag appears in every record, so the baseline legitimately changes.

Run: `cd edge_pump && /c/Python314/python tools/gen_decide_golden.py`
Then: `cd .. && git diff --stat edge_pump/tests/golden/decide_golden.jsonl`
Expected: all 3213 lines changed.

Verify the change is **only** the added flag:

Run: `cd edge_pump && /c/Python314/python -c "import json,subprocess; old=[json.loads(l) for l in subprocess.run(['git','show','HEAD:edge_pump/tests/golden/decide_golden.jsonl'],capture_output=True,text=True,cwd='..').stdout.splitlines() if l.strip()]; new=[json.loads(l) for l in open('tests/golden/decide_golden.jsonl',encoding='utf-8')]; bad=[(o['case']) for o,n in zip(old,new) if {k:v for k,v in n.items() if k!='flags'}!={k:v for k,v in o.items() if k!='flags'} or {k:v for k,v in n['flags'].items() if k not in ('min_off_wait','min_off_remaining_ms')}!=o['flags']]; print('unexpected changes:',len(bad), bad[:3])"`
Expected: `unexpected changes: 0 []`

**If that count is non-zero, Layer 3.5 changed a decision it should not have** — `DEFAULT_CONFIG["min_off_ms"]` is 0, so the guard must be inert in the baseline grid.

- [ ] **Step 11: Run the full suite**

Run: `cd edge_pump && /c/Python314/python -m pytest tests -q`
Expected: PASS — 97 tests (86 + 9 min-off + 2 controller).

- [ ] **Step 12: Commit**

```bash
git add edge_pump/control_logic.py edge_pump/pump_controller.py edge_pump/main.py \
        edge_pump/tests/test_min_off.py edge_pump/tests/test_pump_controller.py \
        edge_pump/tests/golden/decide_golden.jsonl
git commit -m "feat(pump): add Layer 3.5 minimum-off short-cycle guard

An AC motor restarted every 30s burns out; 180s under SOCKET_220V caps
starts at ~20/hour (spec 5.2).

Uses its own min_off_elapsed_ms timer rather than reusing rest_elapsed_ms:
Layer 3 coerces None to 0 (blocking) and min-off treats None as
not-blocking, and one timer read two opposite ways is a latent bug.

Golden baseline regenerated: the only delta is the added min_off_wait
flag; no decision changed (the guard is inert at min_off_ms=0)."
```

---

## Task 5: NVS persistence and boot hold-off

**Why NVS and not RTC memory:** RTC memory survives soft reset and deep sleep but is **cleared by power-on and brownout reset**. Brownout is the dominant reset cause during a typhoon — unstable mains, pump inrush dragging the rail down. An RTC-backed counter would read zero exactly when the reset loop it exists to catch is happening, and it would fail toward *no protection* rather than a false alarm. This is spec finding B2, the third attempt at this problem.

**Files:**
- Create: `edge_pump/persist.py`
- Create: `edge_pump/boot_guard.py`
- Create: `edge_pump/tests/test_persist.py`
- Create: `edge_pump/tests/test_boot_guard.py`
- Modify: `edge_pump/tests/fakes.py`

**Interfaces:**
- Consumes: profile keys `boot_holdoff_ms`, `boot_holdoff_urgent_ms`, `boot_loop_holdoff_ms`, `boot_loop_threshold`, `boot_healthy_ms` (Task 3)
- Produces: `persist.open_nvs() -> NVS | None`; `persist.read_boot_count(nvs) -> int`; `persist.register_boot(nvs) -> int`; `persist.clear_boot_count(nvs) -> None`; `boot_guard.is_reset_loop(boot_count, threshold) -> bool`; `boot_guard.holdoff_total_ms(profile, urgent, reset_loop) -> int`; `boot_guard.holdoff_remaining_ms(elapsed_ms, total_ms) -> int`; `boot_guard.is_boot_healthy(uptime_ms, profile) -> bool`; `boot_guard.make_holdoff_tracker(profile, reset_loop) -> callable(uptime_ms, urgent) -> int`; `fakes.FakeNVS`

> **Nothing calls any of this until Task 7.** This task ends with a green
> suite and a hold-off that protects nothing — the modules exist, the unit
> tests pass, and `main()` has never heard of them. That is deliberate
> sequencing, not an oversight, but it means **a green suite here is not
> evidence the protection works**. Task 7 Steps 7–9 are what put it on the
> control path; `SOCKET_220V` must not be flashed to a mains node until
> those have landed.

- [ ] **Step 1: Add the NVS fake**

Append to `edge_pump/tests/fakes.py`:

```python
class FakeNVS:
    """Mimics esp32.NVS closely enough to test the persistence rules.

    The important fidelity detail: get_i32() RAISES OSError for a missing
    key rather than returning None. Code that assumes a None return works
    on the desktop and throws on the device.
    """

    def __init__(self, initial=None):
        self._store = dict(initial or {})
        self._uncommitted = {}
        self.commits = 0
        self.writes = 0

    def get_i32(self, key):
        if key in self._uncommitted:
            return self._uncommitted[key]
        if key not in self._store:
            raise OSError("NVS key not found: %s" % key)
        return self._store[key]

    def set_i32(self, key, value):
        self._uncommitted[key] = int(value)
        self.writes += 1

    def commit(self):
        self._store.update(self._uncommitted)
        self._uncommitted = {}
        self.commits += 1
```

- [ ] **Step 2: Write the failing persistence test**

Create `edge_pump/tests/test_persist.py`:

```python
import pytest

import persist
from tests.fakes import FakeNVS


def test_missing_key_reads_as_zero():
    # First-ever boot: get_i32 raises OSError and that is normal flow.
    assert persist.read_boot_count(FakeNVS()) == 0


def test_register_boot_increments_and_commits():
    nvs = FakeNVS()
    assert persist.register_boot(nvs) == 1
    assert persist.register_boot(nvs) == 2
    assert persist.read_boot_count(nvs) == 2
    assert nvs.commits == 2


def test_clear_resets_to_zero():
    nvs = FakeNVS({"boot_count": 7})
    persist.clear_boot_count(nvs)
    assert persist.read_boot_count(nvs) == 0


def test_clear_is_a_no_op_when_already_zero():
    # Flash endurance: the healthy-uptime clear fires once per boot, but a
    # caller looping on it must not burn a write cycle every pass.
    nvs = FakeNVS({"boot_count": 0})
    persist.clear_boot_count(nvs)
    assert nvs.writes == 0


def test_contactor_ops_start_at_zero_and_increment():
    nvs = FakeNVS()
    assert persist.read_contactor_ops(nvs) == 0
    assert persist.bump_contactor_ops(nvs) == 1
    assert persist.read_contactor_ops(nvs) == 1


def test_reset_contactor_ops():
    nvs = FakeNVS({"contactor_ops": 12345})
    persist.reset_contactor_ops(nvs)
    assert persist.read_contactor_ops(nvs) == 0


def test_none_nvs_is_tolerated_everywhere():
    # A node whose NVS is unavailable must still run. Degraded protection
    # beats a boot loop caused by the loop detector itself.
    assert persist.read_boot_count(None) == 0
    assert persist.register_boot(None) == 0
    assert persist.read_contactor_ops(None) == 0
    assert persist.bump_contactor_ops(None) == 0
    persist.clear_boot_count(None)
    persist.reset_contactor_ops(None)


def test_write_failure_does_not_propagate():
    class ExplodingNVS(FakeNVS):
        def set_i32(self, key, value):
            raise OSError("flash worn out")

    assert persist.register_boot(ExplodingNVS()) == 0
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_persist.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'persist'`

- [ ] **Step 4: Write the persistence module**

Create `edge_pump/persist.py`:

```python
# -*- coding: utf-8 -*-
"""NVS-backed counters that must survive a brownout (spec §5.6, §5.8).

WHY NVS AND NOT RTC MEMORY: RTC memory survives soft reset and deep sleep
but is CLEARED by power-on and brownout reset. Brownout is the dominant
reset cause during a typhoon — unstable mains, pump inrush dragging the
rail down. An RTC-backed reset-loop counter reads zero exactly when the
loop it detects is happening, and fails toward NO protection. NVS (flash)
survives both.

FLASH ENDURANCE: every write here costs part of a finite erase budget
(~10^5 cycles). Callers write at boot, on a confirmed-healthy uptime, and
on OFF->ON contactor transitions — NEVER per control-loop iteration.

Every function tolerates `nvs=None` so a node with unavailable NVS still
runs with degraded protection rather than boot-looping on its own loop
detector. `machine`/`esp32` are imported lazily, so this stays
desktop-importable.
"""

BOOT_COUNT_KEY = "boot_count"
CONTACTOR_OPS_KEY = "contactor_ops"
_NAMESPACE = "sdprs"


def open_nvs():
    """Return an esp32.NVS handle, or None when unavailable (desktop, or a
    device whose flash partition is missing)."""
    try:
        from esp32 import NVS
        return NVS(_NAMESPACE)
    except Exception:
        return None


def _read(nvs, key):
    if nvs is None:
        return 0
    try:
        return nvs.get_i32(key)
    except OSError:
        return 0          # key never written — normal on first boot
    except Exception:
        return 0


def _write(nvs, key, value):
    if nvs is None:
        return False
    try:
        nvs.set_i32(key, value)
        nvs.commit()
        return True
    except Exception:
        return False      # worn flash must not take the node down


def read_boot_count(nvs):
    return _read(nvs, BOOT_COUNT_KEY)


def register_boot(nvs):
    """Increment the persisted boot counter. Call EXACTLY ONCE per boot,
    and only on a profile that actually uses it (boot_loop_threshold > 0) —
    every call costs a flash erase cycle.

    Returns the new count, or 0 if it could not be persisted.

    BE CLEAR ABOUT WHAT A 0 MEANS: it makes is_reset_loop() False, so the
    node falls back to the NORMAL hold-off instead of the longer reset-loop
    one. That is the UNSAFE direction — the detector reads "no anomaly"
    when it simply cannot see. It is the same failure direction that got
    RTC memory rejected in spec finding B2, reached by a different route
    (unavailable or worn flash rather than a brownout clearing the store).

    It is tolerated because the alternative — refusing to run without NVS —
    would let a dead flash partition stop a pump during a flood, and that
    is worse. It is NOT tolerated silently: publish `nvs_ok` so "counter is
    zero" and "counter is unreadable" are distinguishable at the dashboard
    (Task 11), because from the telemetry alone they otherwise look
    identical.
    """
    n = read_boot_count(nvs) + 1
    return n if _write(nvs, BOOT_COUNT_KEY, n) else 0


def clear_boot_count(nvs):
    """Mark this boot healthy. No-op when already zero (saves a write)."""
    if read_boot_count(nvs) == 0:
        return
    _write(nvs, BOOT_COUNT_KEY, 0)


def read_contactor_ops(nvs):
    return _read(nvs, CONTACTOR_OPS_KEY)


def bump_contactor_ops(nvs):
    """Count one contactor closure. Call ONLY on an OFF->ON transition."""
    n = read_contactor_ops(nvs) + 1
    return n if _write(nvs, CONTACTOR_OPS_KEY, n) else 0


def reset_contactor_ops(nvs):
    """Called by a technician after physically replacing the contactor."""
    _write(nvs, CONTACTOR_OPS_KEY, 0)
```

- [ ] **Step 5: Run the persistence test**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_persist.py -q`
Expected: PASS — 8 tests.

- [ ] **Step 6: Write the failing boot-guard test**

Create `edge_pump/tests/test_boot_guard.py`:

```python
import boot_guard
import profiles


def mains():
    return profiles.get_profile("SOCKET_220V")


def demo():
    return profiles.get_profile("PUMP_12V")


def test_reset_loop_detected_at_threshold():
    assert boot_guard.is_reset_loop(3, 3) is True
    assert boot_guard.is_reset_loop(2, 3) is False


def test_reset_loop_detection_disabled_by_zero_threshold():
    assert boot_guard.is_reset_loop(99, 0) is False


def test_normal_boot_uses_the_full_holdoff():
    assert boot_guard.holdoff_total_ms(mains(), urgent=False, reset_loop=False) == 60000


def test_urgent_boot_is_shortened():
    # Water is rising after a brownout reset. A flat 60s refusal to pump is
    # a worse failure than the short-cycling the hold-off prevents.
    assert boot_guard.holdoff_total_ms(mains(), urgent=True, reset_loop=False) == 10000


def test_reset_loop_overrides_urgency():
    # A node rebooting every 30s has not earned the benefit of the doubt on
    # its own sensor readings.
    assert boot_guard.holdoff_total_ms(mains(), urgent=True, reset_loop=True) == 300000


def test_demo_profile_has_no_holdoff():
    assert boot_guard.holdoff_total_ms(demo(), urgent=False, reset_loop=False) == 0


def test_remaining_counts_down():
    assert boot_guard.holdoff_remaining_ms(0, 60000) == 60000
    assert boot_guard.holdoff_remaining_ms(20000, 60000) == 40000
    assert boot_guard.holdoff_remaining_ms(60000, 60000) == 0
    assert boot_guard.holdoff_remaining_ms(99000, 60000) == 0


def test_remaining_is_zero_when_disabled():
    assert boot_guard.holdoff_remaining_ms(0, 0) == 0


def test_null_uptime_means_full_holdoff():
    assert boot_guard.holdoff_remaining_ms(None, 60000) == 60000


def test_boot_healthy_after_the_window():
    assert boot_guard.is_boot_healthy(300000, mains()) is True
    assert boot_guard.is_boot_healthy(299999, mains()) is False


def test_boot_healthy_is_false_when_disabled():
    # boot_healthy_ms == 0 means the counter is unused; never clear it.
    assert boot_guard.is_boot_healthy(999999, demo()) is False


# ---- The tracker: the hold-off must never come BACK ----

def test_tracker_counts_down_and_then_stays_released():
    t = boot_guard.make_holdoff_tracker(mains(), reset_loop=False)
    assert t(0, urgent=False) == 60000
    assert t(20000, urgent=False) == 40000
    assert t(60000, urgent=False) == 0
    assert t(60001, urgent=False) == 0


def test_tracker_does_not_reengage_when_urgency_ends():
    """THE regression this tracker exists for.

    Boot into a flood: high_water is True, so the hold-off shortens to 10s
    and the pump starts. Thirty seconds later the pump has done its job and
    high_water clears — which makes `urgent` False, which makes
    holdoff_total_ms() return to 60000. Recomputing remaining time from
    that would give 60000-40000 = 20000 and force a RUNNING pump off, and
    under SOCKET_220V that ON->OFF transition starts a 180s min-off
    lockout. A basement that pumped for 30 seconds would then refuse to
    pump for another three minutes, in the exact scenario the urgent
    shortening was added for (spec §5.6, finding A5).

    Once released, released stays."""
    t = boot_guard.make_holdoff_tracker(mains(), reset_loop=False)
    assert t(0, urgent=True) == 10000        # flood: shortened
    assert t(10000, urgent=True) == 0        # released, pump may start
    assert t(40000, urgent=False) == 0       # water gone — must NOT come back
    assert t(41000, urgent=False) == 0


def test_tracker_still_shortens_when_urgency_appears_mid_holdoff():
    # The shortening direction must keep working: water rises during a
    # normal 60s hold-off and the response must not wait the full minute.
    t = boot_guard.make_holdoff_tracker(mains(), reset_loop=False)
    assert t(0, urgent=False) == 60000
    assert t(15000, urgent=True) == 0        # 15s > the 10s urgent total


def test_tracker_reset_loop_ignores_urgency():
    t = boot_guard.make_holdoff_tracker(mains(), reset_loop=True)
    assert t(0, urgent=True) == 300000
    assert t(60000, urgent=True) == 240000


def test_tracker_is_inert_on_the_demo_profile():
    t = boot_guard.make_holdoff_tracker(demo(), reset_loop=False)
    assert t(0, urgent=False) == 0
```

- [ ] **Step 7: Run to verify it fails**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_boot_guard.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'boot_guard'`

- [ ] **Step 8: Write the boot-guard module**

Create `edge_pump/boot_guard.py`:

```python
# -*- coding: utf-8 -*-
"""Pure reset-loop and boot-hold-off decisions (spec §5.6).

No NVS, no hardware — the counter is read by persist.py and passed in.
Kept pure so the tiering rules are exhaustively testable on the desktop.

The hold-off exists because `_off_since` lives in RAM: a WDT reset loop
(30s timeout) would restart a 2200W motor every 30 seconds, straight past
the min-off guard, because each boot believes the pump has never run.
"""


def is_reset_loop(boot_count, threshold):
    """threshold <= 0 disables detection (12V profile)."""
    return threshold > 0 and boot_count >= threshold


def holdoff_total_ms(profile, urgent, reset_loop):
    """Pick the hold-off duration for this boot.

    Three tiers, most-severe first:
      reset loop -> boot_loop_holdoff_ms  (urgency does NOT shorten it: a
                    node rebooting repeatedly cannot vouch for its sensors)
      urgent     -> boot_holdoff_urgent_ms
      otherwise  -> boot_holdoff_ms

    `urgent` is supplied by the MODE layer, not the profile: in DRAIN a
    high-water reading is a live flood, in COLLECT it means the container
    is full and there is nothing time-critical about starting.
    """
    if reset_loop:
        return profile["boot_loop_holdoff_ms"]
    if urgent:
        return profile["boot_holdoff_urgent_ms"]
    return profile["boot_holdoff_ms"]


def holdoff_remaining_ms(uptime_ms, total_ms):
    """Remaining hold-off, floored at 0. A null uptime means 'just booted'."""
    if total_ms <= 0:
        return 0
    if uptime_ms is None:
        return total_ms
    remaining = total_ms - uptime_ms
    return remaining if remaining > 0 else 0


def is_boot_healthy(uptime_ms, profile):
    """True once this boot has run long enough to disprove a reset loop."""
    window = profile["boot_healthy_ms"]
    if window <= 0:
        return False
    return uptime_ms is not None and uptime_ms >= window


def make_holdoff_tracker(profile, reset_loop):
    """Return a callable (uptime_ms, urgent) -> remaining_ms that LATCHES.

    holdoff_total_ms() is a function of `urgent`, and `urgent` can go from
    True back to False — a flood clears, which is the SUCCESS case, not an
    edge case. Recomputing the remaining time from a total that just grew
    would re-impose a hold-off on an already-running pump. Under
    SOCKET_220V that forced ON->OFF starts the 180s min-off lockout, so
    the pump would refuse to run for three minutes immediately after
    proving it works (spec §5.6, finding A5).

    So: the hold-off may SHORTEN while it is running, but once it reaches
    zero it is done for this boot. `machine.reset()` is what starts a new
    one — which is exactly right, because that is the event it guards.

    Kept here rather than as a closure inside main() so the latch is a pure
    thing that can be tested; main() only supplies the uptime.
    """
    state = {"released": False}

    def remaining(uptime_ms, urgent):
        if state["released"]:
            return 0
        total = holdoff_total_ms(profile, urgent, reset_loop)
        left = holdoff_remaining_ms(uptime_ms, total)
        if left <= 0:
            state["released"] = True
            return 0
        return left

    return remaining
```

- [ ] **Step 9: Run the boot-guard test**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_boot_guard.py -q`
Expected: PASS — 16 tests.

- [ ] **Step 10: Run the full suite**

Run: `cd edge_pump && /c/Python314/python -m pytest tests -q`
Expected: PASS — 121 tests (97 + 8 persist + 16 boot-guard).

- [ ] **Step 11: Confirm the spec already carries these parameters**

Nothing to write — this step is a check. `boot_holdoff_urgent_ms`, `boot_loop_holdoff_ms` and `contactor_service_ops` were folded into spec §5.2 on 2026-08-02, so the spec is authoritative and the profile table in Task 3 must match it.

Run: `cd .. && grep -c "boot_holdoff_urgent_ms\|boot_loop_holdoff_ms\|contactor_service_ops" docs/superpowers/specs/2026-08-02-pump-socket-switch-design.md`
Expected: `3` or more. If it reports 0, the spec has regressed — stop and reconcile before continuing, because Task 3's values would then have no authority behind them.

- [ ] **Step 12: Commit**

```bash
git add edge_pump/persist.py edge_pump/boot_guard.py \
        edge_pump/tests/test_persist.py edge_pump/tests/test_boot_guard.py \
        edge_pump/tests/fakes.py \
        docs/superpowers/specs/2026-08-02-pump-socket-switch-design.md
git commit -m "feat(pump): NVS boot counter and tiered boot hold-off

RTC memory is cleared by brownout reset -- the dominant reset cause in a
typhoon -- so an RTC-backed reset-loop counter reads zero exactly when the
loop is happening. NVS survives both power-on and brownout (spec 5.6, B2).

Hold-off is tiered: reset loop 300s > normal 60s > urgent 10s. Urgency is
supplied by the mode layer, not the profile, because high water means
'flood' in DRAIN and 'container full' in COLLECT.

Every persist.* call tolerates nvs=None: a node with unavailable flash
must run with degraded protection rather than boot-loop on its own
loop detector."
```

---

## Task 6: COLLECT mode trigger layer

**Files:**
- Modify: `edge_pump/control_logic.py` (reason codes, `_preamble` flags, `_trigger_collect`, `decide_collect`)
- Create: `edge_pump/tests/test_control_logic_collect.py`
- Regenerate: `edge_pump/tests/golden/decide_golden.jsonl`

**Interfaces:**
- Consumes: `_preamble`, `_safety_guards`, `_mk` (Task 2)
- Produces: `control_logic.decide_collect(readings, timing, ctrl_state, config)` — same signature and return shape as `decide()`; constants `CONTAINER_FULL`, `COLLECT_RAIN_ON`, `SOURCE_DRY`; flag `container_full`

- [ ] **Step 1: Write the failing test**

Create `edge_pump/tests/test_control_logic_collect.py`:

```python
from control_logic import (decide_collect, initial_state, DEFAULT_CONFIG,
                           CONTAINER_FULL, COLLECT_RAIN_ON, SOURCE_DRY)


def cfg(**over):
    c = dict(DEFAULT_CONFIG)
    c.update(over)
    return c


def timing(**over):
    t = {"pump_on_elapsed_ms": None, "rain_wet_elapsed_ms": None,
         "level_low_elapsed_ms": None, "burst_phase_elapsed_ms": None,
         "conflict_elapsed_ms": None, "rest_elapsed_ms": None,
         "min_off_elapsed_ms": None}
    t.update(over)
    return t


def readings(level=None, float_dry=None, high_water=None, raining=None):
    return {"level_pct": level, "float_dry": float_dry,
            "high_water": high_water, "raining": raining}


# ---- The inversion: high_water STOPS the pump here ----

def test_container_full_stops_the_pump():
    st = dict(initial_state(), pump_state="ON")
    d = decide_collect(readings(high_water=True, float_dry=False),
                       timing(), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == CONTAINER_FULL
    assert d["flags"]["container_full"] is True


def test_container_full_prevents_starting():
    d = decide_collect(readings(high_water=True, float_dry=False, raining=True),
                       timing(rain_wet_elapsed_ms=30000), initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == CONTAINER_FULL


def test_container_full_outranks_confirmed_rain():
    # Rain says collect, the container says stop. Overflow is water damage.
    d = decide_collect(readings(high_water=True, float_dry=False, raining=True),
                       timing(rain_wet_elapsed_ms=999999), initial_state(), cfg())
    assert d["reason"] == CONTAINER_FULL


def test_drain_and_collect_disagree_on_the_same_input():
    # The whole point of Mode C, asserted explicitly so nobody "fixes" it.
    from control_logic import decide
    r = readings(high_water=True, float_dry=False)
    assert decide(r, timing(), initial_state(), cfg())["action"] == "ON"
    assert decide_collect(r, timing(), initial_state(), cfg())["action"] == "OFF"


# ---- Rain starts collection ----

def test_confirmed_rain_starts_collection():
    d = decide_collect(readings(raining=True, float_dry=False),
                       timing(rain_wet_elapsed_ms=30000), initial_state(), cfg())
    assert d["action"] == "ON" and d["reason"] == COLLECT_RAIN_ON


def test_unconfirmed_rain_does_not_start_collection():
    d = decide_collect(readings(raining=True, float_dry=False),
                       timing(rain_wet_elapsed_ms=29999), initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == "STANDBY"


# ---- Source dry ----

def test_dry_source_stops_after_the_delay():
    st = dict(initial_state(), pump_state="ON")
    d = decide_collect(readings(level=10, float_dry=False),
                       timing(level_low_elapsed_ms=30000), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == SOURCE_DRY


def test_dry_source_holds_before_the_delay():
    st = dict(initial_state(), pump_state="ON")
    d = decide_collect(readings(level=10, float_dry=False),
                       timing(level_low_elapsed_ms=5000), st, cfg())
    assert d["action"] == "HOLD"


def test_dry_source_prevents_starting_immediately():
    d = decide_collect(readings(level=10, float_dry=False, raining=True),
                       timing(rain_wet_elapsed_ms=30000), initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == SOURCE_DRY


# ---- The shared safety core still governs ----

def test_dry_run_interlock_still_applies():
    d = decide_collect(readings(float_dry=True), timing(), initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == "DRY_RUN_OFF"


def test_max_runtime_rest_still_applies():
    st = dict(initial_state(), resting=True)
    d = decide_collect(readings(raining=True, float_dry=False),
                       timing(rain_wet_elapsed_ms=30000, rest_elapsed_ms=0),
                       st, cfg())
    assert d["reason"] == "MAX_RUNTIME_REST"


def test_min_off_guard_still_applies():
    from control_logic import MIN_OFF_WAIT
    d = decide_collect(readings(raining=True, float_dry=False),
                       timing(rain_wet_elapsed_ms=30000, min_off_elapsed_ms=1000),
                       initial_state(), cfg(min_off_ms=180000))
    assert d["reason"] == MIN_OFF_WAIT


def test_conflict_burst_still_applies():
    # float says dry + two wet votes -> the guarded override, unchanged.
    d = decide_collect(readings(level=90, float_dry=True, high_water=True),
                       timing(burst_phase_elapsed_ms=0, conflict_elapsed_ms=0),
                       initial_state(), cfg())
    assert d["flags"]["sensor_conflict"] is True
    assert d["reason"] in ("CONFLICT_BURST_ON", "CONFLICT_BURST_REST")


def test_pump_holds_while_collecting_mid_band():
    st = dict(initial_state(), pump_state="ON")
    d = decide_collect(readings(level=50, float_dry=False), timing(), st, cfg())
    assert d["action"] == "HOLD"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_control_logic_collect.py -q`
Expected: FAIL — `ImportError: cannot import name 'decide_collect'`

- [ ] **Step 3: Add the reason codes**

In `edge_pump/control_logic.py`, after the `MIN_OFF_WAIT` line:

```python
CONTAINER_FULL = "CONTAINER_FULL"
COLLECT_RAIN_ON = "COLLECT_RAIN_ON"
SOURCE_DRY = "SOURCE_DRY"
```

- [ ] **Step 4: Add the flag**

In `_preamble()`, after `"min_off_wait": False,`:

```python
        "container_full": False,
```

- [ ] **Step 5: Add the COLLECT trigger layer**

Append to `edge_pump/control_logic.py`:

```python
def _trigger_collect(readings, timing, state, config, flags, float_dry, rain_confirmed):
    """Layers 4-5 for COLLECT: the pump fills a container of finite size.

    high_water INVERTS relative to DRAIN. In DRAIN it means "flood, run
    hard"; here it means "container full, stop" — and it is also the alert
    a human must act on by emptying the container.
    """
    # Container full wins over everything below: overflow is water damage.
    if readings.get("high_water") is True:
        flags["container_full"] = True
        return _mk("OFF", state, flags, CONTAINER_FULL)

    # Source exhausted. Distinct from Layer 2's hard dry-run interlock:
    # that one protects the pump, this one just says there is nothing left
    # to collect.
    level = readings.get("level_pct")
    if level is not None and level <= config["low_threshold"]:
        if state.get("pump_state") == "ON":
            low_elapsed = timing.get("level_low_elapsed_ms")
            if low_elapsed is not None and low_elapsed >= config["dry_off_delay_ms"]:
                return _mk("OFF", state, flags, SOURCE_DRY)
            return _mk("HOLD", state, flags, HOLD)
        return _mk("OFF", state, flags, SOURCE_DRY)

    if rain_confirmed:
        return _mk("ON", state, flags, COLLECT_RAIN_ON)

    if state.get("pump_state") == "ON":
        return _mk("HOLD", state, flags, HOLD)
    return _mk("OFF", state, flags, STANDBY)


def decide_collect(readings, timing, ctrl_state, config):
    """COLLECT-mode counterpart to decide(). Same signature, same return
    shape, same safety core — only Layer 4/5 differs (spec §3, §5.4).

    The caller timing contract in decide()'s docstring applies unchanged.
    """
    state = dict(ctrl_state)
    float_dry, rain_confirmed, conflict_now, flags = _preamble(readings, timing, config)

    guarded = _safety_guards(state, timing, config, float_dry, conflict_now, flags)
    if guarded is not None:
        return guarded

    return _trigger_collect(readings, timing, state, config, flags,
                            float_dry, rain_confirmed)
```

- [ ] **Step 6: Run the COLLECT test**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_control_logic_collect.py -q`
Expected: PASS — 14 tests.

- [ ] **Step 7: Regenerate the baseline and confirm DRAIN is untouched**

The added `container_full` flag changes every record again; no *decision* may change.

Run: `cd edge_pump && /c/Python314/python tools/gen_decide_golden.py`
Then run the same unexpected-change check as Task 4 Step 9, extending the ignored-flag list:

Run: `cd edge_pump && /c/Python314/python -c "import json,subprocess; IGN={'min_off_wait','min_off_remaining_ms','container_full'}; old=[json.loads(l) for l in subprocess.run(['git','show','HEAD:edge_pump/tests/golden/decide_golden.jsonl'],capture_output=True,text=True,cwd='..').stdout.splitlines() if l.strip()]; new=[json.loads(l) for l in open('tests/golden/decide_golden.jsonl',encoding='utf-8')]; bad=[o['case'] for o,n in zip(old,new) if {k:v for k,v in n.items() if k!='flags'}!={k:v for k,v in o.items() if k!='flags'} or {k:v for k,v in n['flags'].items() if k not in IGN}!={k:v for k,v in o['flags'].items() if k not in IGN}]; print('unexpected changes:',len(bad), bad[:3])"`
Expected: `unexpected changes: 0 []`

- [ ] **Step 8: Run the full suite**

Run: `cd edge_pump && /c/Python314/python -m pytest tests -q`
Expected: PASS — 135 tests.

- [ ] **Step 9: Commit**

```bash
git add edge_pump/control_logic.py edge_pump/tests/test_control_logic_collect.py \
        edge_pump/tests/golden/decide_golden.jsonl
git commit -m "feat(pump): add COLLECT mode trigger layer

decide_collect() shares _safety_guards() with decide(), so Mode C adds
~45 lines of trigger logic rather than a second copy of the code that can
damage a pump (spec 5.4).

high_water inverts: 'flood, run hard' in DRAIN, 'container full, stop' in
COLLECT. A test asserts the two modes disagree on identical input so the
inversion cannot be 'fixed' by mistake.

DRAIN paths verified unchanged against the golden baseline."
```

---

## Task 7: Mode dispatch, profile wiring, and strict manual-ON rejection

**Files:**
- Modify: `edge_pump/main.py` (`build_config`, new `build_decider`, new `resolve_runtime`, new `_run_config_error_loop`, `apply_manual_override`, new `apply_boot_holdoff`, `run_iteration`, `main()` init)
- Modify: `edge_pump/control_logic.py` (`rest_remaining_ms` flag, `BOOT_HOLDOFF`, `CONFIG_ERROR`)
- Modify: `edge_pump/tests/test_manual_override.py`
- Modify: `edge_pump/tests/test_main_iteration.py`

**Interfaces:**
- Consumes: `profiles.get_profile`, `profiles.validate` (Task 3); `control_logic.decide_collect` (Task 6); `persist.open_nvs`, `persist.register_boot`, `persist.clear_boot_count` (Task 5); `boot_guard.is_reset_loop`, `boot_guard.holdoff_total_ms`, `boot_guard.holdoff_remaining_ms`, `boot_guard.is_boot_healthy` (Task 5)
- Produces: `main.build_decider(mode) -> callable`; `main.resolve_runtime() -> (profile, decider, error_message|None)`; `main.build_config(profile)` gains `min_off_ms`; `main.apply_manual_override(decision, manual_state, clock, strict=False)`; `manual_state["last_rejected"]`, `["last_rejected_flag"]` and `["last_rejected_remaining_ms"]`; `main.apply_boot_holdoff(decision, remaining_ms) -> decision dict`; `run_iteration(..., boot_holdoff=None)` where `boot_holdoff` is a callable `(readings) -> remaining_ms`; `control_logic.BOOT_HOLDOFF`; `control_logic.CONFIG_ERROR`

> **This task is where Task 5's `boot_guard` stops being dead code.** Task 5 builds and tests the module in isolation; nothing calls it until the wiring below. Do not mark Task 5 done and move on believing the hold-off is live — until Step 8 of this task lands, a reset loop restarts the motor on every boot exactly as if `boot_guard.py` had never been written.

- [ ] **Step 1: Write the failing test**

Append to `edge_pump/tests/test_manual_override.py`:

```python
def test_strict_mode_rejects_manual_on_during_max_runtime_rest():
    # Pre-existing gap (spec 5.7): an operator could restart a motor that
    # had just run for 10 minutes straight. Harmless at 12V, burns a
    # 2200W motor.
    from main import apply_manual_override
    from tests.fakes import FakeClock
    decision = {"action": "OFF", "next_state": {"pump_state": "OFF"},
                "flags": {"max_runtime_rest": True, "rest_remaining_ms": 45000},
                "reason": "MAX_RUNTIME_REST"}
    manual = {"action": "ON", "expires_ms": None}
    out, new_manual = apply_manual_override(decision, manual, FakeClock(), strict=True)
    assert out["action"] == "OFF"
    assert new_manual["action"] is None
    assert new_manual["last_rejected"] == "MANUAL_REJECTED"
    assert new_manual["last_rejected_remaining_ms"] == 45000


def test_strict_mode_rejects_manual_on_during_min_off():
    from main import apply_manual_override
    from tests.fakes import FakeClock
    decision = {"action": "OFF", "next_state": {"pump_state": "OFF"},
                "flags": {"min_off_wait": True, "min_off_remaining_ms": 120000},
                "reason": "MIN_OFF_WAIT"}
    manual = {"action": "ON", "expires_ms": None}
    out, new_manual = apply_manual_override(decision, manual, FakeClock(), strict=True)
    assert out["action"] == "OFF"
    assert new_manual["last_rejected_remaining_ms"] == 120000


def test_lenient_mode_preserves_legacy_behaviour():
    # The 12V profile must behave exactly as before this change.
    from main import apply_manual_override
    from tests.fakes import FakeClock
    decision = {"action": "OFF", "next_state": {"pump_state": "OFF"},
                "flags": {"max_runtime_rest": True},
                "reason": "MAX_RUNTIME_REST"}
    manual = {"action": "ON", "expires_ms": None}
    out, _ = apply_manual_override(decision, manual, FakeClock(), strict=False)
    assert out["action"] == "ON"


def test_manual_off_is_honoured_in_strict_mode():
    # Stopping is always safe, in every profile.
    from main import apply_manual_override
    from tests.fakes import FakeClock
    decision = {"action": "ON", "next_state": {"pump_state": "ON"},
                "flags": {"max_runtime_rest": True}, "reason": "HIGH_WATER"}
    manual = {"action": "OFF", "expires_ms": None}
    out, _ = apply_manual_override(decision, manual, FakeClock(), strict=True)
    assert out["action"] == "OFF"


def test_build_decider_selects_the_mode():
    import control_logic
    from main import build_decider
    assert build_decider("DRAIN") is control_logic.decide
    assert build_decider("COLLECT") is control_logic.decide_collect


def test_build_decider_rejects_an_unknown_mode():
    import pytest
    from main import build_decider
    with pytest.raises(ValueError):
        build_decider("SIPHON")


def test_apply_boot_holdoff_forces_off_while_the_holdoff_runs():
    import control_logic
    from main import apply_boot_holdoff
    decision = {"action": "ON", "next_state": {"pump_state": "ON"},
                "flags": {}, "reason": "HIGH_WATER"}
    out = apply_boot_holdoff(decision, 45000)
    assert out["action"] == "OFF"
    assert out["next_state"]["pump_state"] == "OFF"
    assert out["reason"] == control_logic.BOOT_HOLDOFF
    assert out["flags"]["boot_holdoff"] is True
    assert out["flags"]["boot_holdoff_remaining_ms"] == 45000


def test_apply_boot_holdoff_passes_through_once_expired():
    # No residue: the normal control path must resume untouched.
    from main import apply_boot_holdoff
    decision = {"action": "ON", "next_state": {"pump_state": "ON"},
                "flags": {}, "reason": "HIGH_WATER"}
    assert apply_boot_holdoff(decision, 0) is decision
    assert apply_boot_holdoff(decision, None) is decision


def test_apply_boot_holdoff_preserves_the_underlying_flags():
    # Telemetry must still report WHY the pump would otherwise be running.
    from main import apply_boot_holdoff
    decision = {"action": "ON", "next_state": {"pump_state": "ON"},
                "flags": {"rain_trigger": True}, "reason": "RAIN_TRIGGER"}
    out = apply_boot_holdoff(decision, 1000)
    assert out["flags"]["rain_trigger"] is True
    assert decision["flags"] == {"rain_trigger": True}   # input not mutated


def test_strict_mode_rejects_manual_on_during_boot_holdoff():
    # An operator clicking ON must not defeat the reset-loop protection.
    from main import apply_manual_override
    from tests.fakes import FakeClock
    decision = {"action": "OFF", "next_state": {"pump_state": "OFF"},
                "flags": {"boot_holdoff": True,
                          "boot_holdoff_remaining_ms": 30000},
                "reason": "BOOT_HOLDOFF"}
    manual = {"action": "ON", "expires_ms": None}
    out, new_manual = apply_manual_override(decision, manual, FakeClock(), strict=True)
    assert out["action"] == "OFF"
    assert new_manual["last_rejected_remaining_ms"] == 30000


# ---- Configuration errors must report, not reset ----

def test_resolve_runtime_returns_the_configured_profile():
    import main
    profile, decider, err = main.resolve_runtime()
    assert err is None
    assert profile["min_off_ms"] == 0        # PUMP_12V ships as the default
    assert decider is not None


def test_resolve_runtime_reports_an_unknown_mode_instead_of_raising(monkeypatch):
    # A one-character typo in PUMP_MODE must not become a boot loop. The
    # init block it used to raise inside ends in machine.reset(), and a
    # config error recurs on every boot, so the node would vanish rather
    # than report — before register_boot() ever runs, so without even a
    # boot counter to show for it.
    import config, main
    monkeypatch.setattr(config, "PUMP_MODE", "SIPHON")
    profile, decider, err = main.resolve_runtime()
    assert err is not None and "SIPHON" in err
    assert profile is not None and decider is not None   # inert stand-ins


def test_resolve_runtime_reports_a_missing_watchdog_instead_of_raising(monkeypatch):
    # WDT_ENABLED = False is a documented debugging step (config.py:60);
    # under SOCKET_220V it is forbidden, and it must fail loudly-and-online
    # rather than loudly-and-offline.
    import config, main
    monkeypatch.setattr(config, "ACTUATOR_PROFILE", "SOCKET_220V")
    monkeypatch.setattr(config, "WDT_ENABLED", False)
    profile, decider, err = main.resolve_runtime()
    assert err is not None and "WDT_ENABLED" in err
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_manual_override.py -q`
Expected: FAIL — `TypeError: apply_manual_override() got an unexpected keyword argument 'strict'`, plus `ImportError: cannot import name 'apply_boot_holdoff'` on the hold-off tests.

- [ ] **Step 3: Emit the remaining-time flag from Layer 3 and add the hold-off reason**

In `edge_pump/control_logic.py`, in `_safety_guards()`'s Layer 3 resting branch, add the remaining time alongside the flag:

```python
    if state.get("resting"):
        rest_elapsed = timing.get("rest_elapsed_ms") or 0
        if rest_elapsed < config["rest_ms"]:
            flags["max_runtime_rest"] = True
            flags["rest_remaining_ms"] = config["rest_ms"] - rest_elapsed
            return _mk("OFF", state, flags, MAX_RUNTIME_REST)
        state["resting"] = False  # rest complete -> resume normal control
```

Then add the reason constant alongside the existing ones (`MANUAL_REJECTED` and friends):

```python
BOOT_HOLDOFF = "BOOT_HOLDOFF"
```

> `decide()` never returns this reason — the hold-off is imposed above the
> pure core, exactly like `OVERLOAD_TRIP` in Task 9. The constant lives in
> `control_logic` only so every reason string the fleet can publish has one
> home. Adding it does not change any `decide()` output, so the golden
> baseline is unaffected by this step.

- [ ] **Step 4: Add mode dispatch and strict rejection**

In `edge_pump/main.py`, add after `build_config`:

```python
def build_decider(mode):
    """Pick the trigger layer for this node's PUMP_MODE.

    Resolved ONCE at boot rather than per iteration: a mode that could
    change mid-run would mean high_water flipping meaning between ticks.
    """
    if mode == "DRAIN":
        return control_logic.decide
    if mode == "COLLECT":
        return control_logic.decide_collect
    raise ValueError("unknown PUMP_MODE: %r" % (mode,))
```

Change `build_config` to accept the profile:

```python
def build_config(profile=None):
    cfg = {
        "high_threshold": float(config.HIGH_THRESHOLD),
        "low_threshold": float(config.LOW_THRESHOLD),
        "rain_on_threshold": float(config.RAIN_ON_THRESHOLD),
        "rain_confirm_ms": config.RAIN_CONFIRM_MS,
        "dry_off_delay_ms": config.DRY_OFF_DELAY_MS,
        "burst_on_ms": config.BURST_ON_MS,
        "burst_cooldown_ms": config.BURST_COOLDOWN_MS,
        "conflict_max_ms": config.CONFLICT_MAX_MS,
        "max_run_ms": config.MAX_RUN_MS,
        "rest_ms": config.REST_MS,
        "min_off_ms": 0,
    }
    if profile is not None:
        # Profile timings OVERRIDE the module defaults — config.py carries
        # the 12V-era values and the mains profile must win on a mains node.
        cfg["burst_cooldown_ms"] = profile["burst_cooldown_ms"]
        cfg["min_off_ms"] = profile["min_off_ms"]
    return cfg
```

Replace the manual-ON block in `apply_manual_override` (currently lines 114–128) and extend the signature:

```python
def apply_manual_override(decision, manual_state, clock, strict=False):
```

Add to the docstring's Contract section, after the "Manual ON is REJECTED" bullet:

```
      - Under `strict=True` (the SOCKET_220V profile) the rejection list
        also covers `max_runtime_rest` and `min_off_wait`. Restarting a
        motor that just ran for ten minutes is how you burn it; at 12V the
        same click is harmless, which is why this is profile-gated rather
        than unconditional (spec §5.7).
```

Add a module-level lookup above `apply_manual_override` (a chained conditional
expression would need a new branch per flag and is where the wrong remaining
time gets reported):

```python
# Flag -> the flag carrying its remaining time, for operator feedback.
_REMAINING_KEY = {
    "max_runtime_rest": "rest_remaining_ms",
    "min_off_wait": "min_off_remaining_ms",
    "boot_holdoff": "boot_holdoff_remaining_ms",
}
```

And the ON branch:

```python
    if action == "ON":
        # `boot_holdoff` blocks unconditionally rather than only under
        # strict: the flag exists only while a hold-off is actually
        # running, and a profile with no hold-off configured never sets
        # it. Gating it on `strict` would add a second way to be wrong
        # without adding any 12V freedom.
        blocking = ["dry_run_protect", "sensor_conflict", "boot_holdoff"]
        if strict:
            blocking += ["max_runtime_rest", "min_off_wait"]
        hit = next((f for f in blocking if flags.get(f)), None)
        if hit is not None:
            # Refuse and DROP the override so it doesn't retry every tick.
            # `last_rejected_remaining_ms` lets the dashboard say WHY the
            # click did nothing and when it will work, instead of silently
            # doing nothing.
            remaining_key = _REMAINING_KEY.get(hit)
            remaining = flags.get(remaining_key) if remaining_key else None
            return decision, {"action": None, "expires_ms": None,
                              "last_rejected": control_logic.MANUAL_REJECTED,
                              "last_rejected_flag": hit,
                              "last_rejected_remaining_ms": remaining}
        next_state = dict(decision["next_state"])
        next_state["pump_state"] = "ON"
        return {
            "action": "ON",
            "next_state": next_state,
            "flags": dict(flags, manual_override="ON"),
            "reason": control_logic.MANUAL_ON,
        }, manual_state
```

Then add `apply_boot_holdoff` immediately after `apply_manual_override`:

```python
def apply_boot_holdoff(decision, remaining_ms):
    """Force the pump OFF while the post-reset hold-off is still running.

    NOTE THE ASYMMETRY WITH ITS NAME: this does not merely veto a *start*,
    it drives the actuator OFF on any tick where time remains — including
    a tick where the pump is already running. During the boot window that
    is the same thing (the pump starts OFF), which is why the caller must
    never let the hold-off come back after it has expired. See
    `boot_guard.make_holdoff_tracker`.

    `_off_since` lives in RAM, so every reset makes the node believe the
    pump has never run and the min-off guard passes instantly. A WDT reset
    loop at the 30s timeout would therefore restart a 2200W motor every 30
    seconds, and each boot would consider that correct (spec §5.6).

    This sits ABOVE decide() for the same reason apply_overload_interlock()
    does: it is not a control decision, and a fresh decision must not be
    able to clear it. Returns the decision object UNCHANGED once the
    hold-off expires, so the normal path resumes with no residue.

    The `boot_holdoff` flag it sets is also read by apply_manual_override(),
    which is what stops an operator clicking ON straight through a hold-off.
    """
    if not remaining_ms or remaining_ms <= 0:
        return decision
    next_state = dict(decision["next_state"])
    next_state["pump_state"] = "OFF"
    return {
        "action": "OFF",
        "next_state": next_state,
        "flags": dict(decision["flags"],
                      boot_holdoff=True,
                      boot_holdoff_remaining_ms=remaining_ms),
        "reason": control_logic.BOOT_HOLDOFF,
    }
```

- [ ] **Step 5: Thread `strict` and `boot_holdoff` through `run_iteration`**

Change the signature and the decide call:

```python
def run_iteration(sensor_set, pump, mqtt, cfg, publish_cb,
                  manual_state=None, clock=None, decider=None, strict=False,
                  boot_holdoff=None):
    """One control-loop body. Pure of hardware except via injected objects.

    `decider` defaults to DRAIN so pre-existing callers and tests keep
    working; `main()` passes the mode-resolved function.

    `boot_holdoff` is an optional callable `(readings) -> remaining_ms`.
    It is re-evaluated EVERY tick, not resolved once at boot, because the
    hold-off shortens when the mode layer reports urgency — a live flood
    must not wait the full 60s (spec §5.6).
    """
    readings = sensor_set.read_all()
    timing = pump.snapshot_timing(readings)
    decide_fn = decider or control_logic.decide
    decision = decide_fn(readings, timing, pump.ctrl_state, cfg)

    # Post-reset hold-off sits directly above decide() and BEFORE the
    # manual override, so the flag it sets is visible to the override's
    # rejection list.
    if boot_holdoff is not None:
        decision = apply_boot_holdoff(decision, boot_holdoff(readings))

    if manual_state is not None and clock is not None:
        decision, new_manual = apply_manual_override(decision, manual_state, clock,
                                                     strict=strict)
```

(The rest of the function body is unchanged.)

- [ ] **Step 6: Wire it up in `main()`**

In `main()`, inside the init `try:` block, before `if config.WDT_ENABLED:`:

```python
        import profiles
        import persist
        import boot_guard
        profile = profiles.get_profile(config.ACTUATOR_PROFILE)
        profiles.validate(profile, config.WDT_ENABLED)
        decider = build_decider(config.PUMP_MODE)
        nvs = persist.open_nvs()
        # Only profiles that USE the counter pay for it. Under PUMP_12V,
        # boot_loop_threshold is 0 (detection off) and boot_healthy_ms is 0
        # (is_boot_healthy is never True, so clear_boot_count is never
        # called) — registering there would burn one flash erase cycle per
        # boot on a monotonic counter with no reset path, on exactly the
        # bench nodes that get power-cycled most.
        if profile["boot_loop_threshold"] > 0:
            boot_count = persist.register_boot(nvs)
        else:
            boot_count = 0
        reset_loop = boot_guard.is_reset_loop(boot_count,
                                              profile["boot_loop_threshold"])
        print("[MAIN] profile=%s mode=%s boot_count=%d reset_loop=%s"
              % (config.ACTUATOR_PROFILE, config.PUMP_MODE, boot_count, reset_loop))
```

> **Where this block goes matters for a reason the surrounding code makes
> non-obvious.** The init `try:` in `main()` ends with
> `except Exception: ... machine.reset()`. Anything raised in here therefore
> becomes a **reset**, and a *configuration* error raised on every boot
> becomes an unrecoverable boot loop that leaves no trace — `register_boot`
> has not run yet, so even the boot counter stays silent. `WDT_ENABLED =
> False` is a documented debugging step (`config.py:60`) and a `PUMP_MODE`
> typo is a single character, so this is a likely mistake, not a theoretical
> one; under the §4.8 split-bay decision the fix is a site visit. Step 6a
> below moves the two config checks out of that path.

Replace `cfg = build_config()` with:

```python
    cfg = build_config(profile)
```

- [ ] **Step 6a: Keep configuration errors out of the reset path**

Replace the two raising calls from Step 6 (`profiles.validate` and
`build_decider`) with a wrapper that reports instead of raising. Add above
`main()`:

```python
def resolve_runtime():
    """Resolve (profile, decider) from config WITHOUT raising.

    Returns (profile, decider, error_message|None).

    Why this exists: main()'s init block ends in
    `except Exception: ... machine.reset()`. A ValueError from
    profiles.validate() or build_decider() is a CONFIGURATION error, so it
    recurs on every boot — reset, re-raise, reset, forever. It also fires
    before register_boot(), so the boot counter never moves and the node
    leaves no trace of why it vanished. `WDT_ENABLED = False` is a
    documented debugging step and `PUMP_MODE` is one typo away from
    unknown, so this is a likely mistake; under the §4.8 split-bay decision
    recovering from it means a site visit.

    On error we return an INERT stand-in profile. It is not a fallback the
    node runs on — main() refuses to enter the control loop at all. It
    exists only so the rest of init can finish constructing the objects the
    error loop needs (a PUMP_12V profile touches no CT or HOA pins and
    no-ops the contactor counter), and so nothing downstream has to handle
    `profile is None`.
    """
    try:
        profile = profiles.get_profile(config.ACTUATOR_PROFILE)
        profiles.validate(profile, config.WDT_ENABLED)
        return profile, build_decider(config.PUMP_MODE), None
    except (ValueError, KeyError) as e:
        return profiles.get_profile("PUMP_12V"), control_logic.decide, str(e)
```

`profiles` must be importable at module level for this, so move
`import profiles` to the top of `main.py` alongside `import control_logic`
(it is pure — no `machine`, no `esp32` — so it is safe there). In the Step 6
block, replace these three lines:

```python
        profile = profiles.get_profile(config.ACTUATOR_PROFILE)
        profiles.validate(profile, config.WDT_ENABLED)
        decider = build_decider(config.PUMP_MODE)
```

with:

```python
        profile, decider, config_error = resolve_runtime()
```

Then add the refusal loop. After the init `try/except` block and before
`cfg = build_config(profile)`:

```python
    if config_error is not None:
        _run_config_error_loop(pump, mqtt, wdt, config_error)
        return
```

and define it above `main()`:

```python
def _run_config_error_loop(pump, mqtt, wdt, message):
    """Hold the pump OFF and keep saying why, without ever resetting.

    A configuration error is not transient — resetting reproduces it on the
    next boot and the node disappears instead of reporting. OFF is already
    the safe physical state (the coil is de-energised and the contactor
    drops out), so the useful thing left to do is stay reachable and keep
    publishing the reason. An operator seeing CONFIG_ERROR knows to reflash;
    an operator seeing nothing at all has to drive out and open the box.
    """
    print("[MAIN] CONFIG ERROR (refusing to run): %s" % message)
    off_state = dict(pump.ctrl_state)
    off_state["pump_state"] = "OFF"
    pump.apply({"action": "OFF", "next_state": off_state,
                "flags": {"config_error": True},
                "reason": control_logic.CONFIG_ERROR})
    while True:
        try:
            mqtt.publish_status("OFF", 0.0, {"config_error": True},
                                control_logic.CONFIG_ERROR)
            mqtt.check_msg()
        except Exception:
            pass          # network trouble must not turn this into a reset
        if wdt:
            wdt.feed()
        time.sleep(config.POLL_INTERVAL)
```

> The WDT is still fed here on purpose. A hung *loop* is what the watchdog
> is for; a node that has correctly diagnosed its own misconfiguration and
> parked the actuator OFF is not hung, and resetting it would only restart
> the cycle this step exists to break.

Add the reason constant in `control_logic.py` next to `BOOT_HOLDOFF`:

```python
CONFIG_ERROR = "CONFIG_ERROR"
```

- [ ] **Step 7: Build the hold-off tracker**

Still in `main()`, after `cfg = build_config(profile)` and before the main
loop. **This is the step that puts Task 5's `boot_guard` on the control path
— without it the module is written, tested, and never called.**

```python
    # Post-reset hold-off. Re-evaluated per tick because urgency can SHORTEN
    # it mid-run; the tracker is what stops it ever getting LONGER again.
    # `boot_started` is captured here rather than read from a timer the reset
    # cleared, which is the whole reason the counter had to move to NVS.
    boot_holdoff = None
    boot_cleared = False
    if profile["boot_holdoff_ms"] or profile["boot_loop_holdoff_ms"]:
        boot_started = clock.ticks_ms()
        holdoff_tracker = boot_guard.make_holdoff_tracker(profile, reset_loop)

        def boot_holdoff(readings):
            nonlocal boot_cleared
            uptime = clock.ticks_diff(clock.ticks_ms(), boot_started)
            # Urgency is a MODE question, not a profile one: high water is a
            # live flood in DRAIN and merely 'container full' in COLLECT,
            # where starting the pump is never the time-critical response.
            urgent = (config.PUMP_MODE == "DRAIN"
                      and readings.get("high_water") is True)
            if not boot_cleared and boot_guard.is_boot_healthy(uptime, profile):
                # This boot has run long enough to disprove a loop. Clearing
                # here rather than at boot is the point: a node that resets
                # before reaching the healthy window never clears, so the
                # count climbs and the loop is detected.
                persist.clear_boot_count(nvs)
                boot_cleared = True
            return holdoff_tracker(uptime, urgent)
```

> A reset loop deliberately ignores `urgent`: a node rebooting every 30
> seconds cannot vouch for the sensor reading that claims urgency
> (`boot_guard.holdoff_total_ms`, Task 5).
>
> **Go through `make_holdoff_tracker` rather than calling
> `holdoff_remaining_ms(uptime, holdoff_total_ms(...))` directly here.** The
> total is a function of `urgent`, and `urgent` goes back to False the moment
> the pump succeeds in clearing the high-water sensor. Recomputing from the
> grown total would force a *running* pump off partway through a drain, and
> under `SOCKET_220V` that ON→OFF transition immediately starts the 180s
> min-off lockout — three minutes of refusing to pump, triggered by the pump
> working. The tracker latches at zero so the hold-off can shorten but never
> return (Task 5, `test_tracker_does_not_reengage_when_urgency_ends`).

- [ ] **Step 8: Pass both into the loop**

The `run_iteration` call in the main loop:

```python
            run_iteration(sensor_set, pump, mqtt, cfg, publish_cb,
                          manual_state=manual_state, clock=clock,
                          decider=decider, strict=profile["ct_enabled"],
                          boot_holdoff=boot_holdoff)
```

> `ct_enabled` is the profile's "this is a mains node" marker; it is already
> True exactly when strict rejection is wanted. Do not add a second flag
> that can drift out of step with it.

- [ ] **Step 9: Prove the hold-off actually reaches the pump**

The unit tests above cover `apply_boot_holdoff` in isolation; this one is
what would have caught the wiring being absent. Append to
`edge_pump/tests/test_main_iteration.py`:

```python
def test_run_iteration_suppresses_the_pump_during_boot_holdoff():
    # Same flooded scenario as test_run_iteration_turns_pump_on_when_flooded,
    # which asserts the pump turns ON. The ONLY difference is the hold-off,
    # so a pump that turns on here means the wiring is missing.
    clk = FakeClock()
    cfg = main.build_config()
    config = {"level_enabled": True, "float_enabled": True, "rain_enabled": False,
              "high_water_enabled": False, "float_active_low": True,
              "rain_active_low": True, "high_water_active_low": False,
              "debounce_ms": 2500}
    readers = {"adc": make_reader(0), "float": make_reader(1),
               "rain": make_reader(1), "high_water": make_reader(0)}
    ss = sensors.SensorSet(config, readers, clk)
    pc = build_pc(clk)
    published = []
    d = main.run_iteration(ss, pc, None, cfg, lambda **kw: published.append(kw),
                           boot_holdoff=lambda readings: 30000)
    assert d["action"] == "OFF"
    assert pc.state == "OFF"
    assert d["reason"] == control_logic.BOOT_HOLDOFF
    assert published[0]["flags"]["boot_holdoff_remaining_ms"] == 30000


def test_run_iteration_resumes_when_the_holdoff_expires():
    clk = FakeClock()
    cfg = main.build_config()
    config = {"level_enabled": True, "float_enabled": True, "rain_enabled": False,
              "high_water_enabled": False, "float_active_low": True,
              "rain_active_low": True, "high_water_active_low": False,
              "debounce_ms": 2500}
    readers = {"adc": make_reader(0), "float": make_reader(1),
               "rain": make_reader(1), "high_water": make_reader(0)}
    ss = sensors.SensorSet(config, readers, clk)
    pc = build_pc(clk)
    d = main.run_iteration(ss, pc, None, cfg, lambda **kw: None,
                           boot_holdoff=lambda readings: 0)
    assert d["action"] == "ON"
    assert pc.state == "ON"
```

- [ ] **Step 10: Run the full suite**

Run: `cd edge_pump && /c/Python314/python -m pytest tests -q`
Expected: PASS — 150 tests (135 + 13 manual-override + 2 run_iteration).

- [ ] **Step 11: Regenerate the baseline and check**

`rest_remaining_ms` is a new flag on `MAX_RUNTIME_REST` records only.

Run: `cd edge_pump && /c/Python314/python tools/gen_decide_golden.py`
Then: `cd .. && git diff --stat edge_pump/tests/golden/decide_golden.jsonl`
Expected: only lines whose `reason` is `MAX_RUNTIME_REST` change.

Verify: `cd edge_pump && /c/Python314/python -c "import json,subprocess; old=[json.loads(l) for l in subprocess.run(['git','show','HEAD:edge_pump/tests/golden/decide_golden.jsonl'],capture_output=True,text=True,cwd='..').stdout.splitlines() if l.strip()]; new=[json.loads(l) for l in open('tests/golden/decide_golden.jsonl',encoding='utf-8')]; bad=[o['case'] for o,n in zip(old,new) if o!=n and o['reason']!='MAX_RUNTIME_REST']; print('unexpected changes:',len(bad), bad[:3])"`
Expected: `unexpected changes: 0 []`

- [ ] **Step 12: Compile-check for MicroPython syntax**

Run: `cd .. && /c/Python314/python -m py_compile edge_pump/main.py edge_pump/control_logic.py edge_pump/profiles.py edge_pump/persist.py edge_pump/boot_guard.py`
Expected: no output.

- [ ] **Step 13: Commit**

```bash
git add edge_pump/main.py edge_pump/control_logic.py \
        edge_pump/tests/test_manual_override.py edge_pump/tests/test_main_iteration.py \
        edge_pump/tests/golden/decide_golden.jsonl
git commit -m "feat(pump): mode dispatch, profile wiring, boot hold-off, strict manual ON

main.py resolves PUMP_MODE to a decider once at boot and validates the
profile before the relay is ever driven.

Wires Task 5's boot_guard onto the control path. Until this commit the
module was written and unit-tested but never called, so the NVS boot
counter it reads protected nothing. apply_boot_holdoff() now sits above
decide() -- the same shape as apply_manual_override() -- and the hold-off
is recomputed every tick because a live flood shortens it.

Manual ON now also refuses during max_runtime_rest and min_off_wait under
SOCKET_220V, and during boot_holdoff on every profile, reporting the
remaining time so the dashboard can say why the click did nothing (spec
5.7, finding H5). The 12V path keeps its existing lenient behaviour."
```

---

## Task 8: CT front-end — RMS and band classification

**Files:**
- Create: `edge_pump/current_sense.py`
- Create: `edge_pump/tests/test_current_sense.py`

**Interfaces:**
- Consumes: nothing
- Produces: `current_sense.rms_from_samples(samples) -> float`; `current_sense.classify_band(rms_counts, thresholds) -> str` (one of `"none"`, `"low"`, `"normal"`, `"high"`); `current_sense.sample_count_for_cycles(sample_rate_hz, cycles=3, mains_hz=50) -> int`; `current_sense.build_thresholds(low, normal, high) -> dict`

- [ ] **Step 1: Write the failing test**

Create `edge_pump/tests/test_current_sense.py`:

```python
import math

import current_sense as cs


def sine(n, amplitude, offset=2048.0, cycles=3):
    return [offset + amplitude * math.sin(2 * math.pi * cycles * i / n)
            for i in range(n)]


def test_rms_of_silence_is_zero():
    assert cs.rms_from_samples([2048] * 60) == 0.0


def test_rms_of_an_empty_buffer_is_zero():
    # A failed ADC read must read as "no current", not crash the loop.
    assert cs.rms_from_samples([]) == 0.0


def test_rms_of_a_sine_is_amplitude_over_root_two():
    samples = sine(600, 1000.0)
    assert abs(cs.rms_from_samples(samples) - 1000.0 / math.sqrt(2)) < 15.0


def test_rms_ignores_the_bias_level():
    # The bias network has component tolerance; a shifted mid-rail must not
    # read as phantom current.
    a = cs.rms_from_samples(sine(600, 800.0, offset=2048.0))
    b = cs.rms_from_samples(sine(600, 800.0, offset=1900.0))
    assert abs(a - b) < 1.0


def test_rms_scales_with_amplitude():
    small = cs.rms_from_samples(sine(600, 200.0))
    large = cs.rms_from_samples(sine(600, 800.0))
    assert large > small * 3.5


def test_band_boundaries():
    t = cs.build_thresholds(40, 120, 900)
    assert cs.classify_band(0, t) == "none"
    assert cs.classify_band(39, t) == "none"
    assert cs.classify_band(40, t) == "low"
    assert cs.classify_band(119, t) == "low"
    assert cs.classify_band(120, t) == "normal"
    assert cs.classify_band(899, t) == "normal"
    assert cs.classify_band(900, t) == "high"
    assert cs.classify_band(4095, t) == "high"


def test_build_thresholds_rejects_non_ascending_values():
    import pytest
    with pytest.raises(ValueError):
        cs.build_thresholds(120, 40, 900)


def test_sample_count_covers_whole_cycles():
    # 1000 Hz, 3 cycles at 50 Hz -> 60 samples == 60 ms.
    assert cs.sample_count_for_cycles(1000, cycles=3, mains_hz=50) == 60
    assert cs.sample_count_for_cycles(1000, cycles=1, mains_hz=50) == 20
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_current_sense.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'current_sense'`

- [ ] **Step 3: Write the module**

Create `edge_pump/current_sense.py`:

```python
# -*- coding: utf-8 -*-
"""CT front-end: raw ADC samples -> RMS -> coarse band (spec §4.6, §6.1).

The CT output is BIPOLAR and is biased to mid-rail in hardware before it
reaches the ADC, so the DC component in these samples is the bias, not
signal.

NEVER derive a published amp figure from this. An uncalibrated ESP32 ADC is
+-10-20% and non-monotonic near the rails; a payload field reading
`current_a: 3.7` gets believed by whoever sees it. Bands only.

Pure — no hardware, desktop-testable.
"""

_MAINS_HZ = 50


def rms_from_samples(samples):
    """True RMS of a bipolar waveform after removing its measured DC bias.

    The bias is taken from the samples rather than assumed to be exactly
    mid-rail, so tolerance in the bias network does not show up as phantom
    current on an idle socket.
    """
    n = len(samples)
    if n == 0:
        return 0.0
    mean = sum(samples) / float(n)
    acc = 0.0
    for s in samples:
        d = s - mean
        acc += d * d
    return (acc / n) ** 0.5


def build_thresholds(low, normal, high):
    """Validate and package the band edges, in ADC counts."""
    if not (low < normal < high):
        raise ValueError("CT band thresholds must ascend: %r < %r < %r"
                         % (low, normal, high))
    return {"low": low, "normal": normal, "high": high}


def classify_band(rms_counts, thresholds):
    """Map an RMS reading in ADC counts to a coarse band (spec §6.1)."""
    if rms_counts < thresholds["low"]:
        return "none"
    if rms_counts < thresholds["normal"]:
        return "low"
    if rms_counts < thresholds["high"]:
        return "normal"
    return "high"


def sample_count_for_cycles(sample_rate_hz, cycles=3, mains_hz=_MAINS_HZ):
    """Samples needed to cover a WHOLE number of mains cycles.

    Sampling a partial cycle leaves residual fundamental in the mean, which
    biases the RMS. 3 cycles at 50 Hz is 60 ms of blocking — the budget the
    spec flags as unverified against MQTT and the 30s WDT (§9.5).
    """
    return int(round(sample_rate_hz * cycles / float(mains_hz)))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_current_sense.py -q`
Expected: PASS — 8 tests.

- [ ] **Step 5: Commit**

```bash
git add edge_pump/current_sense.py edge_pump/tests/test_current_sense.py
git commit -m "feat(pump): CT RMS sampling and band classification

True RMS with the DC bias measured from the samples, so bias-network
tolerance does not read as phantom current on an idle socket.

Output is a coarse band (none/low/normal/high), never an amp figure: an
uncalibrated ESP32 ADC is +-10-20% and a published number gets believed
(spec 6.1)."
```

---

## Task 9: Actuation feedback and the overload interlock

**Files:**
- Modify: `edge_pump/current_sense.py` (add `diagnose`)
- Modify: `edge_pump/control_logic.py` (add `OVERLOAD_TRIP`)
- Modify: `edge_pump/main.py` (add `apply_overload_interlock`, extend `build_sensor_config`)
- Modify: `edge_pump/sensors.py` (CT + HOA readers)
- Modify: `edge_pump/tests/test_current_sense.py`
- Create: `edge_pump/tests/test_overload_interlock.py`

> **This task ends with the interlock built, tested, and called by nothing.**
> `apply_overload_interlock` is not wired into `run_iteration` here, and
> nothing samples the `readers["ct"]` that Step 9 creates — that happens in
> **Task 11 Step 4**. So a green suite at the end of this task is *not*
> evidence that overload protection works, in exactly the way Task 5's
> `boot_guard` was not evidence the hold-off worked. Say so in the handoff
> rather than letting the passing tests imply otherwise, and do not flash
> `SOCKET_220V` to a mains node believing the CT protects anything until
> Task 11 has landed.

**Interfaces:**
- Consumes: `current_sense.classify_band` (Task 8)
- Produces: `current_sense.diagnose(commanded_on, band, hoa_hand) -> str | None` (one of `"OVERLOAD"`, `"WELDED_CONTACT"`, `"PUMP_NOT_RUNNING"`, `None`); `control_logic.OVERLOAD_TRIP`; `main.apply_overload_interlock(decision, verdict) -> decision dict`; `sensors.build_readers()` gains optional `"ct"` and `"hoa_hand"` keys

- [ ] **Step 1: Write the failing diagnosis test**

Append to `edge_pump/tests/test_current_sense.py`:

```python
# ---- Actuation feedback truth table (spec §7) ----

def test_commanded_on_and_running_is_healthy():
    assert cs.diagnose(True, "normal", hoa_hand=False) is None
    assert cs.diagnose(True, "low", hoa_hand=False) is None


def test_commanded_on_with_no_current_is_not_running():
    # Unplugged pump, tripped MPCB, seized rotor, or a contactor that
    # failed to close — indistinguishable from here, diagnosis needs a
    # human on site.
    assert cs.diagnose(True, "none", hoa_hand=False) == "PUMP_NOT_RUNNING"


def test_commanded_on_with_excess_current_is_overload():
    assert cs.diagnose(True, "high", hoa_hand=False) == "OVERLOAD"


def test_commanded_off_with_no_current_is_healthy():
    assert cs.diagnose(False, "none", hoa_hand=False) is None


def test_commanded_off_with_current_is_a_welded_contact():
    assert cs.diagnose(False, "normal", hoa_hand=False) == "WELDED_CONTACT"
    assert cs.diagnose(False, "low", hoa_hand=False) == "WELDED_CONTACT"
    assert cs.diagnose(False, "high", hoa_hand=False) == "WELDED_CONTACT"


def test_hoa_hand_suppresses_the_welded_verdict():
    # With the panel switch in HAND the operator energises the coil, not
    # us. Without this suppression a technician trips a CRITICAL alarm on
    # every single maintenance visit (spec §4.5).
    assert cs.diagnose(False, "normal", hoa_hand=True) is None
    assert cs.diagnose(False, "high", hoa_hand=True) is None


def test_hoa_hand_does_not_suppress_overload_while_commanded_on():
    # HAND explains unexpected current, not excess current.
    assert cs.diagnose(True, "high", hoa_hand=True) == "OVERLOAD"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_current_sense.py -q`
Expected: FAIL — `AttributeError: module 'current_sense' has no attribute 'diagnose'`

- [ ] **Step 3: Add the truth table**

Append to `edge_pump/current_sense.py`:

```python
def diagnose(commanded_on, band, hoa_hand):
    """Compare what we commanded against what the wire actually reports.

    Returns "OVERLOAD", "WELDED_CONTACT", "PUMP_NOT_RUNNING", or None.

    `hoa_hand` suppresses the welded-contact verdict: with the panel switch
    in HAND the operator is energising the coil directly, so current while
    we command OFF is expected. Without this, every maintenance visit fires
    a CRITICAL weld alarm and the alarm stops meaning anything (spec §4.5).

    Note what this CANNOT tell apart: "pump unplugged", "MPCB tripped",
    "rotor seized" and "contactor failed to close" all read as no-current-
    while-commanded-on. They get one verdict and a human diagnoses on site
    (spec §7).
    """
    if commanded_on:
        if band == "high":
            return "OVERLOAD"
        if band == "none":
            return "PUMP_NOT_RUNNING"
        return None

    # Commanded OFF.
    if band == "none":
        return None
    if hoa_hand:
        return None
    return "WELDED_CONTACT"
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_current_sense.py -q`
Expected: PASS — 15 tests.

- [ ] **Step 5: Write the failing interlock test**

Create `edge_pump/tests/test_overload_interlock.py`:

```python
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
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_overload_interlock.py -q`
Expected: FAIL — `ImportError: cannot import name 'apply_overload_interlock'`

- [ ] **Step 7: Add the reason code**

In `edge_pump/control_logic.py`, after the `SOURCE_DRY` line:

```python
OVERLOAD_TRIP = "OVERLOAD_TRIP"
```

- [ ] **Step 8: Add the interlock**

In `edge_pump/main.py`, add after `apply_manual_override`:

```python
def apply_overload_interlock(decision, verdict):
    """Force OFF on an overload verdict, as a COMPLETE decision dict.

    This is the ONE place CT data touches the control path, and it is
    hardware protection sitting ABOVE decide() rather than a control
    decision inside it — the pure safety core stays CT-free (spec §5.5).

    NEVER drive the relay directly here. Poking the pin behind the state
    machine leaves ctrl_state saying ON while the contactor is open:
    _on_since keeps accumulating against a pump that is not running, the
    next decide() returns HOLD (which correctly leaves the relay alone, so
    everything LOOKS fine), and because no ON->OFF transition was recorded
    the rest timer never starts. apply_manual_override() already returns a
    decision rather than acting — this follows the same shape.
    """
    if verdict != "OVERLOAD":
        return decision
    next_state = dict(decision["next_state"])
    next_state["pump_state"] = "OFF"
    return {
        "action": "OFF",
        "next_state": next_state,
        "flags": dict(decision["flags"], overload_trip=True),
        "reason": control_logic.OVERLOAD_TRIP,
    }
```

- [ ] **Step 9: Add the CT and HOA readers**

In `edge_pump/sensors.py`, extend `build_readers()` before its `return readers`:

```python
    # Mains-box peripherals. Built only when the profile enables them, so a
    # 12V node never touches these pins.
    if config.get("ct_enabled"):
        ct = machine.ADC(machine.Pin(config["ct_adc_pin"]))
        ct.atten(machine.ADC.ATTN_11DB)
        ct.width(machine.ADC.WIDTH_12BIT)
        readers["ct"] = ct.read
    if config.get("hoa_enabled"):
        # Pull toward NOT-HAND: a broken auxiliary-contact wire then reads
        # AUTO, which leaves weld detection ARMED (false alarms). The
        # reverse would disable weld detection permanently on a wire fault
        # — a blind spot, which is worse (spec §4.5).
        pull = machine.Pin.PULL_UP if config["hoa_hand_active_low"] else machine.Pin.PULL_DOWN
        readers["hoa_hand"] = machine.Pin(config["hoa_hand_pin"], machine.Pin.IN, pull).value
```

In `edge_pump/main.py`, extend `build_sensor_config()` to accept the profile:

```python
def build_sensor_config(profile=None):
    cfg = {
        "level_enabled": config.LEVEL_ENABLED,
        "float_enabled": config.FLOAT_ENABLED,
        "rain_enabled": config.RAIN_ENABLED,
        "high_water_enabled": config.HIGH_WATER_ENABLED,
        "float_active_low": config.FLOAT_ACTIVE_LOW,
        "rain_active_low": config.RAIN_ACTIVE_LOW,
        "high_water_active_low": config.HIGH_WATER_ACTIVE_LOW,
        "debounce_ms": config.DEBOUNCE_MS,
        "adc_pin": config.ADC_PIN, "float_pin": config.FLOAT_PIN,
        "rain_pin": config.RAIN_PIN, "high_water_pin": config.HIGH_WATER_PIN,
        "ct_enabled": False, "hoa_enabled": False,
        "ct_adc_pin": config.CT_ADC_PIN, "hoa_hand_pin": config.HOA_HAND_PIN,
        "hoa_hand_active_low": config.HOA_HAND_ACTIVE_LOW,
    }
    if profile is not None:
        cfg["ct_enabled"] = profile["ct_enabled"]
        cfg["hoa_enabled"] = profile["hoa_enabled"]
    return cfg
```

Update both `build_sensor_config()` call sites in `main()` to `build_sensor_config(profile)`.

- [ ] **Step 10: Run the full suite**

Run: `cd edge_pump && /c/Python314/python -m pytest tests -q`
Expected: PASS — 171 tests (150 after Task 7, **+8 from Task 8** which has no
full-suite step of its own, +7 diagnose, +6 interlock).

- [ ] **Step 11: Compile-check**

Run: `cd .. && /c/Python314/python -m py_compile edge_pump/main.py edge_pump/sensors.py edge_pump/current_sense.py edge_pump/control_logic.py`
Expected: no output.

- [ ] **Step 12: Commit**

```bash
git add edge_pump/current_sense.py edge_pump/control_logic.py edge_pump/main.py \
        edge_pump/sensors.py edge_pump/tests/test_current_sense.py \
        edge_pump/tests/test_overload_interlock.py
git commit -m "feat(pump): CT actuation feedback and overload interlock

diagnose() compares commanded state against measured current: welded
contact, pump-not-running, overload. HOA HAND suppresses the weld verdict
so a maintenance visit does not fire a CRITICAL alarm every time.

The overload interlock returns a COMPLETE decision dict applied via
pump_controller.apply(), never a direct relay poke -- driving the pin
behind the state machine desynchronises ctrl_state from the actuator and
silently stops the rest timer from ever starting (spec 5.5, finding A1)."
```

---

## Task 10: Contactor operation counting

**Files:**
- Modify: `edge_pump/pump_controller.py` (count OFF→ON transitions)
- Modify: `edge_pump/tests/test_pump_controller.py`

**Interfaces:**
- Consumes: `persist.bump_contactor_ops` (Task 5)
- Produces: `PumpController(..., on_contactor_close=None)` optional callback; `PumpController.contactor_ops` (int, session count)

- [ ] **Step 1: Write the failing test**

Append to `edge_pump/tests/test_pump_controller.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_pump_controller.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'on_contactor_close'`

- [ ] **Step 3: Implement**

In `edge_pump/pump_controller.py`, extend `__init__`:

```python
    def __init__(self, relay, led_red, led_green, config, clock,
                 on_contactor_close=None):
```

and after `self._clock = clock`:

```python
        # Fired on each OFF->ON transition. The contactor is the only
        # wearing part in the mains box and its electrical life is counted
        # in operations, so this is how a service-due alert becomes
        # possible BEFORE the contacts weld (spec §5.8).
        self._on_contactor_close = on_contactor_close
        self.contactor_ops = 0
```

In `apply()`, inside the `if nxt["pump_state"] == "ON":` branch, extend the transition case:

```python
        if nxt["pump_state"] == "ON":
            if prev["pump_state"] != "ON":
                self._on_since = now
                self.contactor_ops += 1
                if self._on_contactor_close is not None:
                    try:
                        self._on_contactor_close()
                    except Exception:
                        pass   # a worn flash must never stop the pump
            self._off_since = None            # running -> not resting
            self._min_off_since = None
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_pump_controller.py -q`
Expected: PASS.

- [ ] **Step 5: Wire it into `main()`**

In `edge_pump/main.py`, replace the `PumpController(...)` construction:

```python
        def _count_contactor_close():
            if profile["contactor_service_ops"]:
                persist.bump_contactor_ops(nvs)

        pump = PumpController(relay, led_red, led_green,
                              {"low_threshold": float(config.LOW_THRESHOLD)}, clock,
                              on_contactor_close=_count_contactor_close)
```

- [ ] **Step 6: Run the full suite**

Run: `cd edge_pump && /c/Python314/python -m pytest tests -q`
Expected: PASS — 174 tests.

- [ ] **Step 7: Commit**

```bash
git add edge_pump/pump_controller.py edge_pump/main.py \
        edge_pump/tests/test_pump_controller.py
git commit -m "feat(pump): count contactor operations for service-due alerting

The contactor is the only wearing part and its life is rated in
operations, so welding is usually end-of-life rather than random failure.
Counting OFF->ON transitions in NVS makes replacement possible BEFORE the
contacts weld, instead of only detecting it afterwards (spec 5.8).

The callback swallows write failures: a worn flash partition must never
stop the pump from running."
```

---

## Task 11: Telemetry payload fields

> Device side only. The server and dashboard consuming these fields are Phase 2 (spec §6) and sequenced behind the in-flight SPA lane. Publishing them now is safe — `build_payload` is additive and the server ignores unknown keys.

**Files:**
- Modify: `edge_pump/mqtt_client.py:34-54` (`build_payload`)
- Modify: `edge_pump/main.py` (`publish_cb`, CT read in `run_iteration`)
- Modify: `edge_pump/profiles.py` (add `service_due`)
- Modify: `edge_pump/tests/test_mqtt_payload.py`
- Modify: `edge_pump/tests/test_profiles.py`

**Interfaces:**
- Consumes: `current_sense.classify_band`, `current_sense.diagnose` (Tasks 8–9); `persist.read_contactor_ops` (Task 5)
- Produces: `build_payload(..., extra=None)` — `extra` is a dict of additive fields merged last, plus a widened flag pass-through; payload keys `actuator_profile`, `pump_mode`, `current_band`, `hoa_hand`, `contactor_ops`, `contactor_service_due`, `boot_count`, `nvs_ok`, `ct_verdict`, `manual_rejected`, `manual_rejected_remaining_ms`, and the timing flags `min_off_wait` / `max_runtime_rest` / `container_full` / `overload_trip` / `boot_holdoff` / `config_error` with their `*_remaining_ms` companions; `profiles.service_due(profile, ops) -> bool`

> **`contactor_service_ops` becomes a real threshold here.** Task 3 puts the
> number in the profile table and Task 10 counts operations against it, but
> until this task nothing ever compares the two — the key is read only for
> its truthiness. The comparison is done ON-DEVICE rather than left to the
> Phase 2 server, so the threshold means something on a node whose telemetry
> nobody is watching yet.

- [ ] **Step 1: Write the failing test**

Append to `edge_pump/tests/test_mqtt_payload.py`:

```python
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
```

And append to `edge_pump/tests/test_profiles.py`:

```python
def test_service_due_compares_ops_against_the_profile_threshold():
    # The threshold is 60% of the contactor's rated AC3 electrical life
    # (spec §5.8) — the point is to replace it BEFORE the contacts weld.
    p = profiles.get_profile("SOCKET_220V")
    assert profiles.service_due(p, 59999) is False
    assert profiles.service_due(p, 60000) is True
    assert profiles.service_due(p, 250000) is True


def test_service_due_is_never_true_when_the_counter_is_disabled():
    # PUMP_12V has contactor_service_ops = 0: no contactor, nothing to wear.
    p = profiles.get_profile("PUMP_12V")
    assert profiles.service_due(p, 10 ** 9) is False


def test_service_due_tolerates_a_missing_count():
    # Defensive only: persist._read currently coerces every failure to 0,
    # so read_contactor_ops never actually returns None today. The guard is
    # here so that if that contract is ever tightened to distinguish
    # "unavailable" from "zero", service_due does not start comparing None.
    p = profiles.get_profile("SOCKET_220V")
    assert profiles.service_due(p, None) is False
```

> **Known limitation, deliberately not fixed here.** Because `persist._read`
> returns `0` for an unavailable or unreadable NVS, a node with a dead flash
> partition publishes `contactor_ops: 0` — indistinguishable from a
> brand-new contactor — and therefore never reports service-due. The wear
> tracking fails silent rather than loud. Fixing it properly means giving
> `persist` a way to signal "unavailable" separately from "zero", which
> changes the contract every caller shares; it is not worth doing inside a
> telemetry task. Logged in the deferrals table below.
>
> **The wear counter is the milder half of that.** The same coercion makes
> `read_boot_count()` return 0, so `is_reset_loop()` is False and the node
> falls back to the 60s hold-off instead of the 300s one — a *protection*
> failing silent, and failing in the same direction spec finding B2 rejected
> RTC memory for. The `nvs_ok` field added in Step 6 is the mitigation:
> it does not restore the protection, it makes its absence visible.

- [ ] **Step 2: Run to verify it fails**

Run: `cd edge_pump && /c/Python314/python -m pytest tests/test_mqtt_payload.py tests/test_profiles.py -q`
Expected: FAIL — `TypeError: build_payload() got an unexpected keyword argument 'extra'` and `AttributeError: module 'profiles' has no attribute 'service_due'`

- [ ] **Step 3: Implement**

In `edge_pump/mqtt_client.py`, change the signature and the tail of `build_payload`:

```python
def build_payload(node_id, timestamp, pump_state, water_level, flags, reason,
                  battery_voltage=None, power_source=None, extra=None):
    """Additive telemetry payload — never renames the original core fields."""
```

and before `return p`:

```python
    # Operator-facing flags added since the original six. Still an explicit
    # whitelist rather than `p.update(flags)` — flags also carries internal
    # bookkeeping, and a payload that grows whenever someone adds a flag is
    # how private state ends up on the wire. Included only when truthy: all
    # of these are False on the overwhelming majority of ticks, and this
    # payload goes out every 2 seconds.
    for k in ("min_off_wait", "max_runtime_rest", "container_full",
              "overload_trip", "boot_holdoff", "config_error",
              "rest_remaining_ms", "min_off_remaining_ms",
              "boot_holdoff_remaining_ms"):
        if flags.get(k):
            p[k] = flags[k]

    if extra:
        # Additive only. Core fields are re-asserted afterwards so a bug in
        # the extra-builder can never make telemetry misreport pump_state.
        for k, v in extra.items():
            if v is not None:
                p[k] = v
        p["node_id"] = node_id
        p["pump_state"] = pump_state
        p["timestamp"] = timestamp
    return p
```

Extend `publish_status`:

```python
    def publish_status(self, pump_state, water_level, flags, reason,
                       battery_voltage=None, power_source=None, extra=None):
```

and its `build_payload` call:

```python
            payload = build_payload(
                self._node_id, format_timestamp(), pump_state, water_level,
                flags, reason, battery_voltage, power_source, extra)
```

Then add to `edge_pump/profiles.py`, after `validate()`:

```python
def service_due(profile, ops):
    """True once the contactor has used up its service allowance.

    `contactor_service_ops` is 60% of the part's rated AC3 electrical life
    (spec §5.8), so this fires with margin left rather than at failure.

    Evaluated on-device rather than server-side: a node whose telemetry
    nobody is reading yet still needs the threshold to mean something, and
    the profile that owns the number is here. `ops=None` (NVS unavailable)
    is NOT service-due — an unreadable counter is a missing measurement,
    and raising a wear alarm on it would train operators to ignore it.
    """
    threshold = profile["contactor_service_ops"]
    if threshold <= 0 or ops is None:
        return False
    return ops >= threshold
```

- [ ] **Step 4: Read the CT in the control loop**

In `edge_pump/main.py`, extend `run_iteration` to read the CT and apply the interlock, after the `decide_fn` call and **before** `apply_manual_override`:

```python
def run_iteration(sensor_set, pump, mqtt, cfg, publish_cb,
                  manual_state=None, clock=None, decider=None, strict=False,
                  boot_holdoff=None, ct_read=None):
    readings = sensor_set.read_all()
    timing = pump.snapshot_timing(readings)
    decide_fn = decider or control_logic.decide
    decision = decide_fn(readings, timing, pump.ctrl_state, cfg)

    # Boot hold-off (Task 7) stays FIRST of the above-decide() layers.
    if boot_holdoff is not None:
        decision = apply_boot_holdoff(decision, boot_holdoff(readings))

    # CT feedback sits ABOVE decide() — hardware protection, not a control
    # decision, so the pure safety core stays CT-free (spec §5.5).
    # `pump.state` is the ACTUATOR's current state, not this tick's
    # decision, so the CT read is unaffected by the hold-off above it.
    ct_verdict = None
    if ct_read is not None:
        band, hoa_hand, ct_verdict = ct_read(pump.state == "ON")
        decision = apply_overload_interlock(decision, ct_verdict)
        readings["_current_band"] = band
        readings["_hoa_hand"] = hoa_hand

    if manual_state is not None and clock is not None:
        decision, new_manual = apply_manual_override(decision, manual_state, clock,
                                                     strict=strict)
```

> Both interlocks preserve the flags of the decision they replace, so a
> tick that is simultaneously in hold-off and overloaded still publishes
> `boot_holdoff` alongside `overload_trip` — the operator sees both
> reasons, not whichever ran last.

and extend the `publish_cb` call at the end of `run_iteration`:

```python
    pump.apply(decision)
    # `manual_state` has already been updated in place above, so a rejection
    # recorded this tick is visible here. Without these two keys the
    # remaining time computed by apply_manual_override never leaves the
    # device and spec §5.7's "tell the operator why the click did nothing"
    # is not delivered by anything.
    manual = manual_state or {}
    publish_cb(pump_state=pump.state,
               water_level=synthesize_display_level(readings),
               flags=decision["flags"], reason=decision["reason"],
               extra={"current_band": readings.get("_current_band"),
                      "hoa_hand": readings.get("_hoa_hand"),
                      "ct_verdict": ct_verdict,
                      "manual_rejected": manual.get("last_rejected_flag"),
                      "manual_rejected_remaining_ms":
                          manual.get("last_rejected_remaining_ms")})
    return decision
```

- [ ] **Step 5: Build the CT reader in `main()`**

In `edge_pump/main.py`, inside `main()` after the sensor set is built:

```python
    ct_read = None
    if profile["ct_enabled"]:
        import current_sense
        ct_thresholds = current_sense.build_thresholds(
            config.CT_BAND_LOW, config.CT_BAND_NORMAL, config.CT_BAND_HIGH)
        ct_samples = current_sense.sample_count_for_cycles(
            config.CT_SAMPLE_RATE_HZ, config.CT_SAMPLE_CYCLES)
        ct_reader = readers.get("ct")
        hoa_reader = readers.get("hoa_hand")

        def ct_read(commanded_on):
            """Blocking RMS burst. ~60ms at the shipped defaults — the
            budget spec §9.5 flags as unverified against MQTT + the 30s
            WDT. Measure on the bench before trusting it."""
            try:
                samples = [ct_reader() for _ in range(ct_samples)]
                rms = current_sense.rms_from_samples(samples)
                band = current_sense.classify_band(rms, ct_thresholds)
            except Exception as e:
                print("[CT] read failed: %s" % str(e))
                return None, None, None
            hoa_hand = None
            if hoa_reader is not None:
                raw = hoa_reader()
                hoa_hand = (raw == 0) if config.HOA_HAND_ACTIVE_LOW else (raw == 1)
            return band, hoa_hand, current_sense.diagnose(commanded_on, band,
                                                          bool(hoa_hand))
```

and pass it in the loop:

```python
            run_iteration(sensor_set, pump, mqtt, cfg, publish_cb,
                          manual_state=manual_state, clock=clock,
                          decider=decider, strict=profile["ct_enabled"],
                          boot_holdoff=boot_holdoff, ct_read=ct_read)
```

- [ ] **Step 6: Extend `publish_cb` to carry the static fields**

Replace `publish_cb` in `main()`:

```python
    def publish_cb(pump_state, water_level, flags, reason, extra=None):
        nonlocal last_publish, ntp_synced
        now = time.ticks_ms()
        if time.ticks_diff(now, last_publish) >= config.PUBLISH_INTERVAL * 1000:
            battery_voltage, power_source = _read_power(battery_adc, power_source_pin)
            # `nvs_ok` exists so "boot_count is 0" and "boot_count is
            # unreadable" are distinguishable. persist._read() coerces every
            # failure to 0, so without this a node with a dead flash
            # partition reports boot_count=0 forever — which reads as a
            # perfectly healthy node while reset-loop detection is silently
            # off, the same failure DIRECTION that got RTC memory rejected
            # in spec finding B2. This catches an unavailable namespace,
            # not a partially-worn one; it narrows the blind spot rather
            # than closing it.
            merged = {"actuator_profile": config.ACTUATOR_PROFILE,
                      "pump_mode": config.PUMP_MODE,
                      "boot_count": boot_count,
                      "nvs_ok": nvs is not None}
            if profile["contactor_service_ops"]:
                ops = persist.read_contactor_ops(nvs)
                merged["contactor_ops"] = ops
                # Compared on-device: the threshold has to mean something
                # before the Phase 2 server exists to evaluate it.
                merged["contactor_service_due"] = profiles.service_due(profile, ops)
            if extra:
                merged.update(extra)
            mqtt.publish_status(pump_state, water_level, flags, reason,
                                battery_voltage, power_source, extra=merged)
            last_publish = now
            if not ntp_synced and mqtt._wifi_connected:
                _sync_ntp(); ntp_synced = True
        mqtt.check_msg()
```

- [ ] **Step 7: Run the full suite**

Run: `cd edge_pump && /c/Python314/python -m pytest tests -q`
Expected: PASS — 183 tests (174 + 6 payload + 3 service-due).

- [ ] **Step 8: Compile-check**

Run: `cd .. && /c/Python314/python -m py_compile edge_pump/main.py edge_pump/mqtt_client.py edge_pump/profiles.py`
Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add edge_pump/mqtt_client.py edge_pump/main.py edge_pump/profiles.py \
        edge_pump/tests/test_mqtt_payload.py edge_pump/tests/test_profiles.py
git commit -m "feat(pump): publish profile, mode, CT band and wear telemetry

build_payload gains an additive `extra` dict; core fields are re-asserted
after the merge so telemetry can never misreport pump_state.

profiles.service_due() finally compares contactor_ops against
contactor_service_ops. Before this the threshold was only ever read for
its truthiness -- the number sat in the profile table and nothing tested
against it. Evaluated on-device so it means something on a node whose
telemetry the Phase 2 server is not yet reading.

Bands only, never amps (spec 6.1). A 12V node's payload shape is
unchanged. Server and dashboard consumption is Phase 2."
```

---

## Task 12: Commissioning, fleet-update, and handover documentation

> The spec makes these delivery requirements, not optional extras (§8.2, §8.3, §8.4, §12.2, §12.3). Mains equipment that ships without a signed acceptance record has no evidence trail if there is ever an incident.

**Files:**
- Modify: `docs/deployment/pump-bench-commissioning.md`
- Create: `docs/deployment/mains-box-commissioning.md`
- Create: `docs/deployment/pump-fleet-update.md`
- Create: `docs/deployment/pump-handover-checklist.md`

- [ ] **Step 1: Read the existing bench document**

Run: `cd .. && cat docs/deployment/pump-bench-commissioning.md`

Note its existing section lettering (especially **§A sensor-polarity verification**, which everything downstream inherits and which has never been executed) so the new matrix section follows the same convention.

- [ ] **Step 2: Add the four-configuration matrix**

Append a new section to `docs/deployment/pump-bench-commissioning.md`:

```markdown
## §H 組態矩陣 · Configuration matrix (Mode C)

`ACTUATOR_PROFILE` × `PUMP_MODE` = 四種合法部署。每一格都必須獨立驗收；
通過其中一格**不代表**其他格可用。

| | `DRAIN` | `COLLECT` |
|---|---|---|
| **`PUMP_12V`** | 現行台架節點（回歸基準） | 桌上展示收集情境 |
| **`SOCKET_220V`** | 淹水基坑，現場水泵 | 雨水收集，現場水泵 |

### 前置條件（不可跳過）

- [ ] **§A 感測器極性驗證已完成並簽署。** `FLOAT_ACTIVE_LOW` /
      `RAIN_ACTIVE_LOW` / `HIGH_WATER_ACTIVE_LOW` 目前是推測值
      （`config.py:81-83`）。四種組態全部繼承這個前提；極性錯誤會讓
      「水位到頂」讀成「水位見底」。
- [ ] `SOCKET_220V` 任一格開始前，NVS 開機計數器（規格 §5.6）必須已實作。

### 每一格的驗收項目

| # | 項目 | DRAIN 預期 | COLLECT 預期 |
|---|---|---|---|
| 1 | 觸發 `high_water` | 水泵**啟動** | 水泵**停止**，回報 `CONTAINER_FULL` |
| 2 | 觸發確認下雨（>30s） | 啟泵門檻降至 60% | 水泵**啟動**，回報 `COLLECT_RAIN_ON` |
| 3 | 浮球報乾 | 水泵停止，`DRY_RUN_OFF` | 同左（共用安全核心） |
| 4 | 連續運轉達 `MAX_RUN_MS` | 強制休息，`MAX_RUNTIME_REST` | 同左 |
| 5 | 休息期間點擊手動 ▶ | 12V：允許／220V：**拒絕**並回報剩餘秒數 | 同左 |
| 6 | 斷電後重新上電 | 220V：套用開機保留；`boot_count` 遞增 | 同左 |

### 僅 `SOCKET_220V` 需要

| # | 項目 | 預期 |
|---|---|---|
| 7 | HOA 切至 HAND | `hoa_hand=true`，**不得**發出熔接告警 |
| 8 | HOA 輔助接點拔線 | 讀為 AUTO，熔接偵測維持啟用（傾向誤報） |
| 9 | 命令 OFF 但插座有電流 | `WELDED_CONTACT` |
| 10 | 命令 ON 但無電流 | `PUMP_NOT_RUNNING` |
| 11 | 連續 3 次快速重開機 | 判定重置迴圈，保留延長至 300s |
| 12 | 60ms CT 取樣 vs 30s WDT | 一小時內無 WDT 重置（規格 §9.5 未驗證項） |
```

- [ ] **Step 3: Create the mains acceptance record**

Create `docs/deployment/mains-box-commissioning.md`:

```markdown
# 市電控制箱驗收紀錄 · Mains Box Commissioning Record

> 規格 §8.3。**本文件是萬一發生事故時唯一的憑據。** 每一項都必須有實測值、
> 測試者姓名與日期——打勾但沒有數值等於沒有測。
>
> 本設備為自行組裝，**未取得 BSMI/CNS 認證**（規格 §9.2）。組裝與檢查應由
> 合格電匠執行。

## 0. 基本資料

| 欄位 | 內容 |
|---|---|
| 箱體序號 | |
| 節點 ID | |
| 安裝地點 | |
| 組裝者 | |
| 檢查電匠（姓名／證號） | |
| 日期 | |

## 1. 採購前硬性關卡（規格 §10）

### 1.1 供電拓樸（**決定極數，必須在下單前量測**）

台灣民生 220V 多為**單相三線 110/220V**，負載跨 **L1–L2 兩條火線**，
對地各 110V。若如此，**單極切換會讓插座在接觸器正常斷開時仍有一支腳
帶 110V**，接觸器與 MPCB 都必須是雙極。若為三相四線 380/220V 則 L–N
成立，單極切換符合慣例。**不量測就無法選型。**

| 量測點 | 實測值 | 判定 |
|---|---|---|
| L1 – PE | ______ V | |
| L2（或 N）– PE | ______ V | ≈0V → L–N；≈110V → **L–L** |
| L1 – L2（或 L–N） | ______ V | 應為 ≈220V |

- [ ] 拓樸判定：☐ L–N（單極可）　☐ **L–L（接觸器與 MPCB 皆須雙極）**
- [ ] 若為 110V 單相：宣告最大值減半，BOM 全面重算，**本表作廢重來**

### 1.2 其餘關卡

- [ ] 宣告最大值已簽署核可：______ A / ______ W
- [ ] 所購接觸器**資料表 AC3 欄位**額定：______ A（不是型號上的數字）
- [ ] AC3 額定 ≥ 宣告最大電流
- [ ] 接觸器主接點極數 ______ P、MPCB 極數 ______ P，與 §1.1 判定相符
- [ ] 已納入突波保護器 SPD（規格 §4.2）
- [ ] **RCD 型式：☐ A 型（最低要求）　☐ B 型（現場水泵含變頻驅動時）　☐ AC 型＝不合格**
      現場水泵驅動型式：☐ 純感應電動機　☐ 含電子驅動　☐ **未知（→ 仍須 A 型）**（規格 §4.2.3）
- [ ] **MOSFET 為邏輯準位型**：型號 ______、`VGS(th)` ______ V ≤ 2V、
      **且 `RDS(on)` 標示於 `VGS = 3.3V`**（規格 §4.2.2；與 AC1／AC3 同類的資料表陷阱）
- [ ] **閘極下拉 `Rpd` 已納入採購**（10kΩ）——**這是規格 §7 唯一安全論證的實體依據**
- [ ] **SPD 後備保險絲 `F0`**：SPD 資料表最大後備值 ______ A、
      建物端上游斷路器實際額定 ______ A；上游 > 資料表值時 `F0` 為必要（規格 §4.1）
- [ ] **導線截面積**：市電路徑 ______ mm²、插座軟線 ______ mm²（規格 §4.2.3）
- [ ] **MPCB 遮斷容量** Icn／Icu ______ kA ≥ 現場預期短路電流 ______ kA（規格 §4.2.3）
- [ ] **插座本體 NEMA 6-15R 已列入採購**（規格 §4.2——第五輪 M6：舊版 BOM 漏了交付標的物本身）

## 2. 電氣安全（每項必測，記錄實測值）

| # | 項目 | 需求 | 實測值 | 通過 |
|---|---|---|---|---|
| 1 | 接地連續性：進線 PE → 插座 PE | < 0.5 Ω | | ☐ |
| 2 | 接地連續性：進線 PE → 金屬箱體 | < 0.5 Ω | | ☐ |
| 3 | RCD 動作電流 | ≤ 30 mA | | ☐ |
| 4 | RCD 動作時間 | < 300 ms | | ☐ |
| 5 | 絕緣電阻 L-PE | > 1 MΩ | | ☐ |
| 6 | 絕緣電阻 N-PE | > 1 MΩ | | ☐ |
| 7 | MPCB 過載設定值 | = 宣告最大電流 | | ☐ |
| 7a | **接觸器斷開時插座對地電壓**（每一極） | L–L 供電且單極切換時此值 ≈110V＝**不合格** | | ☐ |
| 7b | SPD 已安裝於 RCD 上游且指示窗正常 | 目視 | | ☐ |
| 7c | **SPD 後備保險絲 `F0`** | 上游斷路器 ≤ SPD 最大後備值，否則 `F0` 須已裝 | | ☐ |
| 7d | **導線截面積符合宣告電流** | 市電路徑與插座軟線皆記錄實際 mm² | | ☐ |
| 7e | **MPCB 遮斷容量 ≥ 預期短路電流** | 記錄 Icn／Icu 與現場預期值 | | ☐ |
| 7f | **端子鎖付扭力** | 依端子規格鎖付；記錄扭力值與本次日期（＝複緊起算點） | | ☐ |
| 7g | **RCD 型式為 A 型或 B 型** | 記錄實際型式；**AC 型＝不合格**（規格 §4.2.3） | | ☐ |

## 2.1 CT 二次側開路防護（規格 §4.6.1）〔安全要求〕

> **一次側帶電時二次側開路會產生數百伏至千伏高壓。** 分艙設計（§4.8 A 案）
> 使維修人員例行在一次側帶電時於低壓艙作業，因此以下三項為**強制**，
> 且箱體完成後無法補救。

| # | 項目 | 需求 | 通過 |
|---|---|---|---|
| 8 | 負擔電阻與 TVS 箝位**永久焊接**於 CT 二次線端 | 目視；不得位於可插拔接頭另一側 | ☐ |
| 9 | 維修連接器位於負擔電阻**下游** | 拔除接頭後量測二次側仍為閉迴路：______ Ω | ☐ |
| 10 | CT 線組已標示「一次側帶電時不得斷開」 | 目視 | ☐ |

## 2.2 分艙完整性（規格 §4.8.1）

| # | 項目 | 需求 | 通過 |
|---|---|---|---|
| 11 | 低壓艙可在**不隔離市電**的情況下開啟 | 實際操作確認 | ☐ |
| 12 | SMPS **一次側不在**低壓艙內 | 目視 | ☐ |
| 13 | 低壓艙內僅有：24V 二次側、ESP32、CT 前端 | 目視 | ☐ |
| 14 | 艙門標示「開啟本艙門並未隔離市電」 | 目視 | ☐ |
| 15 | 若裝有艙門開關：確認**僅上報遙測**，不影響水泵動作 | 開門後水泵仍可正常啟停 | ☐ |

> 第 15 項是刻意的方向：「箱門開啟 → 抑制水泵」會製造出「微動開關腐蝕 →
> 淹水時水泵拒絕啟動」的新失效路徑。告警可以，互鎖不行。

## 2.3 低壓介面：GPIO 與線圈之間（規格 §4.2.2）〔第五輪 C2／H5／H6〕

> **第 15a 項是本份紀錄中唯一能證明規格 §7 那條「唯一防線」成立的量測。**
> §7 宣稱 ESP32 當機時內部 WDT 重置會使線圈失電，並據此**否決了外部硬體看門狗**。
> 該保證由閘極下拉電阻提供——GPIO 33 重置後是**高阻抗**，而高阻抗的閘極不等於
> 關斷的閘極。**不得以目視代替量測。**

| # | 項目 | 需求 | 實測值 | 通過 |
|---|---|---|---|---|
| 15a | **拔除 ESP32（或持續按住重置）後的線圈端電壓** | **必須為 0 V** | | ☐ |
| 15b | 閘極下拉 `Rpd` 實體位置 | **緊鄰 MOSFET**，不在 ESP32 端（該線斷掉＝下拉消失） | | ☐ |
| 15c | MOSFET 為邏輯準位型 | 型號與 `VGS(th)` 已記錄於 §1.2 | | ☐ |
| 15d | **HOA 第二極為無電壓接點**：三段位分別量測 GPIO 13 對地 | **AUTO／STOP／HAND 皆須落在 0V–3.3V**；任一段出現 24V＝**不合格且 ESP32 已損毀** | | ☐ |

> 第 15d 項不是形式檢查。**同一顆 HOA 開關的第一極切的是 24VDC 線圈迴路**，
> 把第二極比照辦理是照圖施工者的合理讀法——後果是 24V 直入 3.3V GPIO。

## 3. 熱測試（規格 §4.7）

密閉箱體內約 10W 損耗，台灣夏季環境可達 40°C。SMPS 與接觸器多在 45-50°C
以上開始降額。

| # | 項目 | 實測值 | 通過 |
|---|---|---|---|
| 16 | 環境溫度 | | ☐ |
| 17 | 連續運轉 1 小時後箱內溫度 | | ☐ |
| 18 | 箱內溫度 < 所有元件降額起點 | | ☐ |

## 4. 功能互動

| # | 項目 | 預期 | 通過 |
|---|---|---|---|
| 19 | HOA → HAND | 水泵運轉，**無** CRITICAL 熔接告警 | ☐ |
| 20 | HOA → STOP | 水泵停止 | ☐ |
| 21 | HOA → AUTO | 恢復自動控制 | ☐ |
| 22 | HOA 輔助接點拔線 | 讀為 AUTO，熔接偵測**仍啟用** | ☐ |
| 23 | ESP32 斷電（模擬當機） | 30s 內線圈失電（WDT） | ☐ |

> **第 23 項的意義**：GPIO 在 MCU 當機時維持最後狀態，接觸器不會自行釋放。
> 內部看門狗是唯一防線，最長非預期運轉 30 秒（規格 §7）。

## 5. CT 校正（規格 §8.3）

以**已知電阻性負載**多點量測，鉤表對照。校正後把實測值寫回
`config.py` 的 `CT_BAND_LOW` / `CT_BAND_NORMAL` / `CT_BAND_HIGH`。

| 負載 (W) | 鉤表 (A) | RMS (ADC counts) | 分級 |
|---|---|---|---|
| 0（空載） | | | 應為 `none` |
| | | | |
| | | | |
| 宣告最大值 | | | 應為 `normal` |

> **突入電流無法以鉤表捕捉**（約 100ms 尖峰，需示波器加電流探棒）。
> 若無此設備，接觸器選型只能依賴資料表 AC3 額定——那就是唯一防線（規格 §9.3）。

## 6. 隔離點

- [ ] **MPCB 已標示為唯一上鎖點（lockout point）**
- [ ] 面板已標示「**STOP 不等於隔離**」：接觸器一旦熔接，無論線圈是否激磁
      都維持導通，STOP 位置不保證插座無電（規格 §4.5）

## 7. 簽署

| 角色 | 姓名 | 簽名 | 日期 |
|---|---|---|---|
| 組裝者 | | | |
| 檢查電匠 | | | |
| 驗收者 | | | |
```

- [ ] **Step 4: Create the fleet update runbook**

Create `docs/deployment/pump-fleet-update.md`:

```markdown
# 水泵節點更新與回復 · Pump Fleet Update & Rollback

> 規格 §12.2。現行 4 台已驗收節點共用 `control_logic.py`，因此更新策略是
> 交付的一部分，不是實作細節。
>
> **這是實體接觸更新**（`mpremote` / `esptool`），與 edge_glass 的 rsync
> 佈署不同。若日後採用規格 §4.8 的 OTA 方案，本文件即被取代。

## 設計上的保障

`ACTUATOR_PROFILE` 與 `PUMP_MODE` 的預設值就是現行行為
（`PUMP_12V` / `DRAIN`），所以重新燒錄既有節點在行為上是**中性**的。
這是刻意的：讓「更新」與「行為變更」兩件事分開發生、分開驗證。

## 步驟

### 1. 標記已知良好版本

```bash
git tag -a pump-fw-known-good-$(date +%Y%m%d) -m "pre-update baseline"
git push origin --tags
```

### 2. 演練回復（**先做這個**）

在**台架節點**上，先燒新版、再燒回舊版、確認節點恢復正常運轉。

> **未演練過的回復程序不算回復程序。** 颱風夜不是第一次執行它的時機。

- [ ] 台架節點燒錄新版 → 正常運轉
- [ ] 台架節點回燒 tag 版本 → 正常運轉
- [ ] 記錄整個回復耗時：______ 分鐘

### 3. 金絲雀

先更新**一台**現場節點，觀察**一個完整的乾濕循環**後再繼續。

- [ ] 節點 ID：______
- [ ] 更新時間：______
- [ ] 觀察至少一次啟泵 → 停泵完整循環
- [ ] `boot_count` 未持續遞增（無重置迴圈）
- [ ] 遙測欄位正常，無非預期告警

### 4. 其餘節點

**逐台更新，不同時。** 若第 2 台出現第 1 台沒有的問題，同時更新會讓你
分不清是韌體問題還是那台節點的問題。

### 5. 回復

```bash
git checkout pump-fw-known-good-YYYYMMDD -- edge_pump/
cd edge_pump && mpremote connect <port> fs cp *.py :
```

> **CH340 板的自動重置不可靠。** `mpremote` 需要 `resume`；`esptool` 需要
> `--before no-reset`。進燒錄模式要手動操作：按住 **BOOT** → 點一下
> **RST** → 放開 BOOT，然後才下指令。若跳過這步，指令會逾時，看起來像
> 節點壞了，其實只是沒進 bootloader。
```

- [ ] **Step 5: Create the handover checklist**

Create `docs/deployment/pump-handover-checklist.md`:

```markdown
# 水泵市電箱交接清單 · Handover Checklist

> 規格 §12.3。**市電設備的交接不能只交付一個箱子。** 以下九項缺一即不可
> 視為完成。

| # | 項目 | 規格 | 已交付 |
|---|---|---|---|
| 1 | 接線圖（**實際竣工版**，非設計版） | §4.1 | ☐ |
| 2 | 額定標示牌：電壓、電流、功率、未認證聲明 | §9.2、§9.7 | ☐ |
| 3 | HOA 操作說明，含「STOP 不等於隔離」警語 | §4.5 | ☐ |
| 4 | 隔離／上鎖點指定：**MPCB 為唯一上鎖點** | §4.5 | ☐ |
| 5 | 市電驗收紀錄（含測試者簽名與日期） | §8.3 | ☐ |
| 6 | 提供組態核對表（已簽署） | §8.4 | ☐ |
| 7 | 保養排程：接觸器操作次數門檻與檢查週期 | §5.8 | ☐ |
| 8 | 緊急程序：如何在**不開箱**的情況下停止水泵 | §4.5、§4.8 | ☐ |
| 9 | 專用插座使用限制聲明 | §9.6 | ☐ |

## 第 2 項：標示牌內容

面板標示必須同時寫明**電壓與最大功率**，不能只寫「水泵專用」——使用者
判斷相容性的依據就是這行字。

```
    220V ~  10A  2200W MAX
    專用插座 — 僅供水泵使用
    不得外接延長線或其他設備
    本設備未經 BSMI/CNS 認證
```

## 第 6 項：提供組態核對（規格 §8.4）

軟體**無法**偵測 XKC 探頭實體裝在哪裡。這兩項必須人工逐項確認並簽署：

- [ ] XKC 實體安裝位置與 `PUMP_MODE` 相符
- [ ] `ACTUATOR_PROFILE` 與實際安裝硬體相符

> 若 XKC 裝在收集容器上卻執行 DRAIN 韌體，容器滿時水泵會**啟動**——
> 溢流與水損，肇因只是一個設定字串。Mode C 的真正風險不在程式碼，
> 而在提供組態。

## 第 9 項：專用插座限制

CT 量測的是**插座的總電流**，不是水泵的電流。插上延長線再接其他設備會讓：

- 故障的水泵讀成「電流正常，運轉中」——且**不會告警**
- 正常的水泵誤判為 `OVERLOAD_TRIP`

這是**已接受的殘餘風險**（規格 §9.6），技術上無法強制，只能靠標示與交接說明。

## 第 8 項：緊急程序

```
水泵必須立即停止時：
  1. HOA 開關轉到 STOP        ← 一般情況足夠
  2. 若水泵仍在運轉（接點熔接）：
     切斷 MPCB                ← 唯一真正的隔離點
  3. 絕對不要在颱風中打開帶電的配電箱
```
```

- [ ] **Step 6: Verify the docs render and links resolve**

Run: `cd .. && ls -la docs/deployment/ && grep -c "^|" docs/deployment/mains-box-commissioning.md`
Expected: the three new files exist; the commissioning record has at least 30 table rows.

Then check that no relative link in the new docs dangles — a runbook that
points at a missing file is worse than one that inlines the instruction,
because the reader assumes the detail exists somewhere:

```bash
cd .. && for f in docs/deployment/pump-*.md docs/deployment/mains-box-commissioning.md; do
  grep -o '](\([^)h][^)]*\))' "$f" | sed 's/](\(.*\))/\1/' | while read -r l; do
    [ -e "docs/deployment/$l" ] || echo "DANGLING: $f -> $l"
  done
done
```
Expected: no output.

Finally confirm the item numbering in the commissioning record is a clean
run with no duplicates — the sections were renumbered once already and a
duplicate number makes a signed record ambiguous about what was tested:

```bash
cd .. && grep -o '^| [0-9]\+[a-z]\? |' docs/deployment/mains-box-commissioning.md | sort -V | uniq -d
```
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add docs/deployment/pump-bench-commissioning.md \
        docs/deployment/mains-box-commissioning.md \
        docs/deployment/pump-fleet-update.md \
        docs/deployment/pump-handover-checklist.md
git commit -m "docs: mains commissioning, fleet update, and handover deliverables

The 4-configuration bench matrix (spec 8.2), a signed mains acceptance
record with measured values rather than ticks (8.3), the canary-plus-
rehearsed-rollback runbook for the 4 commissioned nodes (12.2), and the
nine handover deliverables (12.3).

The bench matrix restates that Section A sensor-polarity verification has
never been executed and every configuration inherits it."
```

---

## Final Verification

- [ ] **Full edge_pump suite**

Run: `cd edge_pump && /c/Python314/python -m pytest tests -q`
Expected: PASS — 183 tests (70 baseline + 113 new).

Per-task checkpoints, for locating a drift rather than re-deriving it:
72 · 72 · 86 · 97 · 121 · 135 · 150 · (158) · 171 · 174 · 183.
Task 8 has no full-suite step, so its +8 lands inside Task 9's number.

- [ ] **Full-tree sweep** (confirms nothing else broke)

Run from `sdprs/`:
```bash
for f in $(find . -name "test_*.py" -not -path "*/node_modules/*" -not -path "*/.git/*" | sort); do /c/Python314/python -m pytest "$f" -q -p no:cacheprovider; done
```
Expected: no failures. Re-enumerate with `find` before quoting a total — a prior session reported a number that was an unlabelled subset.

- [ ] **MicroPython import-safety check** — no device-only module at import time

Run: `cd edge_pump && /c/Python314/python -c "import control_logic, profiles, boot_guard, persist, current_sense, sensors, pump_controller, main; print('all modules import on desktop')"`
Expected: `all modules import on desktop`. A failure means a `machine`/`esp32` import escaped to module level, which breaks every desktop test.

- [ ] **Confirm the defaults are still behaviour-neutral**

Run: `cd edge_pump && /c/Python314/python -c "import config, profiles; p=profiles.get_profile(config.ACTUATOR_PROFILE); print(config.ACTUATOR_PROFILE, config.PUMP_MODE, 'min_off_ms=%d' % p['min_off_ms'], 'ct=%s' % p['ct_enabled'])"`
Expected: `PUMP_12V DRAIN min_off_ms=0 ct=False`

- [ ] **Blocked-on-hardware items are documented, not silently skipped**

These cannot be closed at the desk. Confirm each is written down rather than assumed:

| Blocked item | Where it is recorded |
|---|---|
| §A sensor-polarity verification | Bench doc §H preconditions; spec §9.4, §10 item 2 |
| Site has a 220V circuit | Mains record §1.2; spec §10 item 1 |
| **Whether that 220V is L–N or L–L** — decides contactor and MPCB pole count, so it gates the purchase, not just the build | Mains record §1.1; spec §4.1.1, §10 item 1 |
| CT band thresholds are placeholder values | `config.py` comment; mains record §5 |
| `contactor_service_ops` pending the purchased part's AC3 life | `profiles.py` comment; spec §10 item 5 |
| 60ms CT sampling vs MQTT + 30s WDT | Bench doc §H item 12; spec §9.5 |

**Accepted design limitations** (not blocked on hardware — decided, and left as they are):

| Limitation | Consequence | Why it is accepted here |
|---|---|---|
| `persist._read` coerces an unavailable NVS to `0` | A node with a dead flash partition reports `contactor_ops: 0` forever and never reports service-due. Wear tracking fails silent. | Distinguishing "unavailable" from "zero" changes a contract shared by every `persist` caller. Out of scope for a telemetry task; revisit if flash failures are ever observed in the fleet. |
| No external watchdog | A hung ESP32 holds the contactor closed for up to 30s (`WDT_TIMEOUT`). The internal WDT is the only defence. | Spec §7, explicitly traded. `WDT_ENABLED = False` is forbidden under `SOCKET_220V` — `profiles.validate` enforces it, and `resolve_runtime` makes that refusal reportable instead of a boot loop. |
| `nvs_ok` detects an unavailable namespace, not a partially-worn one | A flash partition that opens but fails writes still reports `nvs_ok: true` while `register_boot` silently returns 0, so reset-loop detection is off and nothing says so. | Narrowing the blind spot was cheap; closing it means `persist` distinguishing "unavailable" from "zero" for every caller. Same deferral as the row above. |

---

## Notes for the Implementer

**The golden fixture is the spine of Tasks 1–7.** Every task that touches `control_logic.py` regenerates it and checks the diff against an explicit expectation. When a check reports unexpected changes, the answer is never "regenerate again" — it means the change did something you did not intend, and the fixture just told you so before a pump did.

**Two things in this codebase look like bugs and are not:**

1. `_safety_guards()` mutates `state` in place and returns `None` to mean "fall through". That is deliberate — the latch-clearing on the fall-through paths has to survive into the trigger layer.
2. `apply_manual_override()` returns a decision dict instead of driving the relay. Copy that shape for anything that wants to override the pump. `apply_overload_interlock()` and `apply_boot_holdoff()` both exist because a direct relay poke desynchronises `ctrl_state` from the actuator in a way that looks healthy from every angle: the relay is off, `decide()` returns `HOLD`, nothing errors — and the rest timer silently never starts.

**A module with passing tests is not a module that runs.** Tasks 5 and 7 are split so that `boot_guard` is built and tested before anything calls it. That gap is where this plan's own review found its worst defect: the hold-off was fully specified, fully unit-tested, and wired into nothing. If you finish a task whose deliverable is a pure module, the next question is always *what calls it* — and if the answer is "a later task", say so out loud in the handoff rather than letting a green suite imply the protection is live. The two `run_iteration` integration tests in Task 7 Step 9 exist specifically to fail if that wiring is ever removed.

**On `PUMP_12V` staying the default:** it is not caution for its own sake. Four commissioned nodes run this firmware, and the update path (Task 12) depends on reflashing being behaviour-neutral so that a failed update is distinguishable from a behaviour change.
