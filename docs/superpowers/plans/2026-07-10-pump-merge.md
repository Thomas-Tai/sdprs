# Pump Node Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the student ESP32 water/rain pump demo concepts (float dry-run interlock, rain-linked triggering, digital high-water redundancy, sensor-conflict handling) into the production MicroPython `edge_pump` node as a pure, testable layered controller, and carry the new telemetry end-to-end into the central server and monitor-wall dashboard.

**Architecture:** Extract the safety decision into a pure, hardware-free `control_logic.decide()` (portable across MicroPython on-device and CPython under `pytest`). A stateful `pump_controller` owns all `ticks_ms` bookkeeping and actuation; `sensors` provides debounced readings behind an injectable hardware reader; `main` is a thin orchestrator. The server parses the extended MQTT payload, the WebSocket loop-capture bug is fixed so the pump card updates live, and the SPA card renders rain / dry-run / sensor-conflict state.

**Tech Stack:** MicroPython (ESP32), `umqtt.simple`, CPython 3 + `pytest` (desktop tests), FastAPI + `paho-mqtt` + SQLite/PostgreSQL, React (in-browser Babel JSX).

**Spec:** `docs/superpowers/specs/2026-07-10-pump-merge-and-reconstruction-design.md`

## Global Constraints

- **Portability:** `control_logic.py` and the testable core of `sensors.py` MUST NOT `import machine`, `network`, or `umqtt` at module top level. Hardware access goes behind injectable callables / lazily-imported factories so `pytest` runs on the desktop.
- **Data structures are plain dicts** (Readings, Timing, CtrlState, Decision, Config) — portable, no `namedtuple`/dataclass dependency.
- **Time enters `decide()` only as pre-computed elapsed-millisecond durations** (never absolute timestamps) so the pure function is wrap-around-safe; `time.ticks_diff` lives only in `pump_controller`.
- **MQTT payload fields are additive only** — never rename or remove `node_id`, `timestamp`, `pump_state`, `water_level`, `battery_voltage`, `power_source`.
- **No hardcoded WiFi/MQTT credentials** in committed code — flash-time placeholders only. The leaked student WiFi password (`Msc@2333`) must never appear. No public broker (`broker.emqx.io`).
- **Parameter defaults (verbatim):** `HIGH_THRESHOLD=80`, `LOW_THRESHOLD=20`, `RAIN_ON_THRESHOLD=60`, `RAIN_CONFIRM_MS=30000`, `DRY_OFF_DELAY_MS=30000`, `BURST_ON_MS=60000`, `BURST_COOLDOWN_MS=30000`, `CONFLICT_MAX_MS=900000`, `MAX_RUN_MS=600000`, `REST_MS=60000`, `DEBOUNCE_MS=2500`, `SOCKET_TIMEOUT_S=3`.
- **Pin map (verbatim, conflict-free):** existing `RELAY_PIN=26`, `LED_RED_PIN=27`, `LED_GREEN_PIN=25`, `ADC_PIN=34`, `BATTERY_ADC_PIN=35`, `POWER_SOURCE_PIN=21`; new `FLOAT_PIN=32`, `RAIN_PIN=33`, `HIGH_WATER_PIN=13`.
- **Reason codes (verbatim):** `STANDBY`, `HYSTERESIS_ON`, `RAIN_TRIGGER`, `HIGH_WATER`, `HOLD`, `CONFLICT_BURST_ON`, `CONFLICT_BURST_REST`, `CONFLICT_LATCH_OFF`, `DRY_RUN_OFF`, `MAX_RUNTIME_REST`.
- **Run tests from** `sdprs/edge_pump/` (firmware) and `sdprs/central_server/` (server) with `pytest`.

---

## Phase A — Firmware pure core (desktop-testable)

### Task 1: Test harness scaffolding

**Files:**
- Create: `edge_pump/conftest.py`
- Create: `edge_pump/tests/__init__.py`
- Create: `edge_pump/tests/fakes.py`
- Test: `edge_pump/tests/test_fakes.py`

**Interfaces:**
- Produces: `FakeClock` with `ticks_ms() -> int`, `ticks_diff(a, b) -> int`, `advance(ms)`. `FakePin(value=1)` with `value(v=None)` get/set. `make_reader(seq_or_value)` returning a zero-arg callable yielding successive values (last value repeats).

- [ ] **Step 1: Create `edge_pump/conftest.py`** so `pytest` can import `control_logic`/`sensors` from the `edge_pump/` root.

```python
# conftest.py — makes edge_pump modules importable under pytest.
# Presence of this file at the edge_pump/ root puts that dir on sys.path
# (pytest "prepend" import mode), so `from control_logic import decide` works.
```

- [ ] **Step 2: Create `edge_pump/tests/__init__.py`** (empty file).

- [ ] **Step 3: Create `edge_pump/tests/fakes.py`**

```python
"""Desktop test doubles for the ESP32 hardware/clock. No `machine`/`time` deps."""


class FakeClock:
    """Deterministic monotonic clock in milliseconds."""

    def __init__(self, start_ms=0):
        self._t = start_ms

    def ticks_ms(self):
        return self._t

    def ticks_diff(self, a, b):
        # Desktop: plain subtraction. On-device the real clock uses
        # time.ticks_diff for wrap-around safety.
        return a - b

    def advance(self, ms):
        self._t += ms
        return self._t


class FakePin:
    """Mimics machine.Pin's value(v=None) get/set contract."""

    def __init__(self, value=1):
        self._v = value

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = v


def make_reader(seq_or_value):
    """Return a zero-arg callable. If given a list, yields successive items
    and repeats the last; if given a scalar, always returns it."""
    if isinstance(seq_or_value, (list, tuple)):
        box = {"i": 0, "seq": list(seq_or_value)}

        def rd():
            i = box["i"]
            seq = box["seq"]
            val = seq[i] if i < len(seq) else seq[-1]
            if i < len(seq) - 1:
                box["i"] = i + 1
            return val

        return rd
    return lambda: seq_or_value
```

- [ ] **Step 4: Create `edge_pump/tests/test_fakes.py`**

```python
from tests.fakes import FakeClock, FakePin, make_reader


def test_clock_advances_and_diffs():
    c = FakeClock(1000)
    assert c.ticks_ms() == 1000
    c.advance(250)
    assert c.ticks_ms() == 1250
    assert c.ticks_diff(c.ticks_ms(), 1000) == 250


def test_pin_get_set():
    p = FakePin(0)
    assert p.value() == 0
    p.value(1)
    assert p.value() == 1


def test_reader_sequence_repeats_last():
    rd = make_reader([1, 0, 0])
    assert [rd(), rd(), rd(), rd()] == [1, 0, 0, 0]
    assert make_reader(1)() == 1
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd sdprs/edge_pump && pytest tests/test_fakes.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add edge_pump/conftest.py edge_pump/tests/__init__.py edge_pump/tests/fakes.py edge_pump/tests/test_fakes.py
git commit -m "test(pump): add desktop test harness (fake clock/pin/reader)"
```

---

### Task 2: `control_logic.decide()` — pure layered safety ladder

**Files:**
- Create: `edge_pump/control_logic.py`
- Test: `edge_pump/tests/test_control_logic.py`

**Interfaces:**
- Consumes: nothing (pure module).
- Produces:
  - `DEFAULT_CONFIG: dict` — all parameter defaults (see Global Constraints).
  - `decide(readings: dict, timing: dict, ctrl_state: dict, config: dict) -> dict` returning `{"action": "ON"|"OFF"|"HOLD", "next_state": dict, "flags": dict, "reason": str}`.
  - `initial_state() -> dict` = `{"pump_state": "OFF", "conflict_latched": False, "conflict_holdoff": False, "burst_phase": None}`.
  - `readings` keys: `level_pct` (float|None), `float_dry` (bool|None — True=dry/danger), `high_water` (bool|None), `raining` (bool|None). `None` means the sensor is disabled/invalid.
  - `timing` keys (all int ms or None): `pump_on_elapsed_ms`, `rain_wet_elapsed_ms`, `level_low_elapsed_ms`, `burst_phase_elapsed_ms`, `conflict_elapsed_ms`.
  - `flags` keys: `raining`, `float_safe`, `high_water`, `sensor_conflict`, `dry_run_protect`, `max_runtime_rest`.

- [ ] **Step 1: Write the failing tests** (`edge_pump/tests/test_control_logic.py`)

```python
from control_logic import decide, initial_state, DEFAULT_CONFIG


def cfg(**over):
    c = dict(DEFAULT_CONFIG)
    c.update(over)
    return c


def timing(**over):
    t = {
        "pump_on_elapsed_ms": None, "rain_wet_elapsed_ms": None,
        "level_low_elapsed_ms": None, "burst_phase_elapsed_ms": None,
        "conflict_elapsed_ms": None,
    }
    t.update(over)
    return t


def readings(level=None, float_dry=None, high_water=None, raining=None):
    return {"level_pct": level, "float_dry": float_dry,
            "high_water": high_water, "raining": raining}


# ---- Layer 4/5: hysteresis + standby ----

def test_off_turns_on_above_high_threshold():
    d = decide(readings(level=85, float_dry=False), timing(),
               initial_state(), cfg())
    assert d["action"] == "ON" and d["reason"] == "HYSTERESIS_ON"
    assert d["next_state"]["pump_state"] == "ON"


def test_on_holds_inside_hysteresis_band():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=50, float_dry=False), timing(), st, cfg())
    assert d["action"] == "HOLD" and d["next_state"]["pump_state"] == "ON"


def test_off_stays_off_inside_band():
    d = decide(readings(level=50, float_dry=False), timing(),
               initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == "STANDBY"


def test_on_turns_off_below_low_after_dry_off_delay():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=15, float_dry=False),
               timing(level_low_elapsed_ms=30000), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "STANDBY"


def test_on_holds_below_low_before_dry_off_delay():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=15, float_dry=False),
               timing(level_low_elapsed_ms=5000), st, cfg())
    assert d["action"] == "HOLD"


# ---- Rain trigger ----

def test_rain_confirmed_lowers_on_threshold():
    d = decide(readings(level=65, float_dry=False, raining=True),
               timing(rain_wet_elapsed_ms=30000), initial_state(), cfg())
    assert d["action"] == "ON" and d["reason"] == "RAIN_TRIGGER"


def test_rain_not_yet_confirmed_does_not_lower_threshold():
    d = decide(readings(level=65, float_dry=False, raining=True),
               timing(rain_wet_elapsed_ms=1000), initial_state(), cfg())
    assert d["action"] == "OFF"


# ---- High-water digital trigger ----

def test_high_water_forces_on():
    d = decide(readings(level=10, float_dry=False, high_water=True),
               timing(), initial_state(), cfg())
    assert d["action"] == "ON" and d["reason"] == "HIGH_WATER"


def test_digital_only_off_when_high_water_clears():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=None, float_dry=False, high_water=False),
               timing(), st, cfg())
    assert d["action"] == "OFF"


# ---- Layer 2: dry-run protection ----

def test_dry_float_forces_off():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=90, float_dry=True), timing(), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "DRY_RUN_OFF"
    assert d["flags"]["dry_run_protect"] is True


def test_dry_float_absolute_with_single_wet_vote():
    # float dry + only high_water true (1 vote) -> dry-run wins, no conflict
    d = decide(readings(level=10, float_dry=True, high_water=True, raining=False),
               timing(), initial_state(), cfg())
    assert d["action"] == "OFF" and d["reason"] == "DRY_RUN_OFF"


# ---- Layer 3: max-runtime duty cycle ----

def test_max_runtime_forces_rest():
    st = dict(initial_state(), pump_state="ON")
    d = decide(readings(level=90, float_dry=False),
               timing(pump_on_elapsed_ms=600000), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "MAX_RUNTIME_REST"
    assert d["flags"]["max_runtime_rest"] is True


# ---- Layer 1: guarded conflict override ----

def test_conflict_two_wet_votes_starts_burst_on():
    # float dry BUT high_water true AND rain confirmed => 2 votes => burst
    d = decide(readings(level=10, float_dry=True, high_water=True, raining=True),
               timing(rain_wet_elapsed_ms=30000), initial_state(), cfg())
    assert d["action"] == "ON" and d["reason"] == "CONFLICT_BURST_ON"
    assert d["flags"]["sensor_conflict"] is True
    assert d["next_state"]["conflict_latched"] is True
    assert d["next_state"]["burst_phase"] == "ON"


def test_conflict_rain_plus_analog_high_is_two_votes():
    d = decide(readings(level=85, float_dry=True, high_water=False, raining=True),
               timing(rain_wet_elapsed_ms=30000), initial_state(), cfg())
    assert d["reason"] == "CONFLICT_BURST_ON"


def test_conflict_burst_on_expires_to_rest():
    st = dict(initial_state(), conflict_latched=True, burst_phase="ON")
    d = decide(readings(float_dry=True, high_water=True, raining=True),
               timing(rain_wet_elapsed_ms=30000, burst_phase_elapsed_ms=60000,
                      conflict_elapsed_ms=60000), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "CONFLICT_BURST_REST"
    assert d["next_state"]["burst_phase"] == "REST"


def test_conflict_burst_rest_expires_back_to_on():
    st = dict(initial_state(), conflict_latched=True, burst_phase="REST")
    d = decide(readings(float_dry=True, high_water=True, raining=True),
               timing(rain_wet_elapsed_ms=30000, burst_phase_elapsed_ms=30000,
                      conflict_elapsed_ms=120000), st, cfg())
    assert d["action"] == "ON" and d["reason"] == "CONFLICT_BURST_ON"


def test_conflict_ceiling_latches_off_alarm():
    st = dict(initial_state(), conflict_latched=True, burst_phase="ON")
    d = decide(readings(float_dry=True, high_water=True, raining=True),
               timing(rain_wet_elapsed_ms=30000, conflict_elapsed_ms=900000), st, cfg())
    assert d["action"] == "OFF" and d["reason"] == "CONFLICT_LATCH_OFF"
    assert d["next_state"]["conflict_holdoff"] is True
    assert d["flags"]["sensor_conflict"] is True


def test_holdoff_clears_when_sensors_reagree():
    st = dict(initial_state(), conflict_latched=True, conflict_holdoff=True)
    d = decide(readings(float_dry=False, high_water=False, raining=False),
               timing(), st, cfg())
    # sensors agree again -> holdoff cleared, normal ladder resumes
    assert d["next_state"]["conflict_holdoff"] is False
    assert d["next_state"]["conflict_latched"] is False


def test_conflict_clears_when_float_recovers():
    st = dict(initial_state(), conflict_latched=True, burst_phase="ON")
    d = decide(readings(level=90, float_dry=False, high_water=True, raining=True),
               timing(rain_wet_elapsed_ms=30000, conflict_elapsed_ms=5000), st, cfg())
    # float safe now -> conflict clears, normal ladder (high water -> ON)
    assert d["next_state"]["conflict_latched"] is False
    assert d["action"] == "ON" and d["reason"] == "HIGH_WATER"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sdprs/edge_pump && pytest tests/test_control_logic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'control_logic'`.

- [ ] **Step 3: Write `edge_pump/control_logic.py`**

```python
# -*- coding: utf-8 -*-
"""Pure pump control decision logic — the safety core.

No hardware, no wall clock. Portable across MicroPython (device) and
CPython (desktop pytest). All time enters as pre-computed elapsed-ms
durations so the function is wrap-around safe.
"""

# ----- Reason codes -----
STANDBY = "STANDBY"
HYSTERESIS_ON = "HYSTERESIS_ON"
RAIN_TRIGGER = "RAIN_TRIGGER"
HIGH_WATER = "HIGH_WATER"
HOLD = "HOLD"
CONFLICT_BURST_ON = "CONFLICT_BURST_ON"
CONFLICT_BURST_REST = "CONFLICT_BURST_REST"
CONFLICT_LATCH_OFF = "CONFLICT_LATCH_OFF"
DRY_RUN_OFF = "DRY_RUN_OFF"
MAX_RUNTIME_REST = "MAX_RUNTIME_REST"

DEFAULT_CONFIG = {
    "high_threshold": 80.0,
    "low_threshold": 20.0,
    "rain_on_threshold": 60.0,
    "rain_confirm_ms": 30000,
    "dry_off_delay_ms": 30000,
    "burst_on_ms": 60000,
    "burst_cooldown_ms": 30000,
    "conflict_max_ms": 900000,
    "max_run_ms": 600000,
    "rest_ms": 60000,
}


def initial_state():
    return {"pump_state": "OFF", "conflict_latched": False,
            "conflict_holdoff": False, "burst_phase": None}


def _rain_confirmed(readings, timing, config):
    if readings.get("raining") is not True:
        return False
    e = timing.get("rain_wet_elapsed_ms")
    return e is not None and e >= config["rain_confirm_ms"]


def _wet_votes(readings, config, rain_confirmed):
    votes = 0
    if readings.get("high_water") is True:
        votes += 1
    if rain_confirmed:
        votes += 1
    level = readings.get("level_pct")
    if level is not None and level >= config["high_threshold"]:
        votes += 1
    return votes


def _mk(action, state, flags, reason):
    if action != "HOLD":
        state["pump_state"] = action
    return {"action": action, "next_state": state, "flags": flags, "reason": reason}


def decide(readings, timing, ctrl_state, config):
    state = dict(ctrl_state)
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

    # ---- Layer 3: max-runtime duty cycle ----
    on_elapsed = timing.get("pump_on_elapsed_ms")
    if state.get("pump_state") == "ON" and on_elapsed is not None \
            and on_elapsed >= config["max_run_ms"]:
        flags["max_runtime_rest"] = True
        return _mk("OFF", state, flags, MAX_RUNTIME_REST)

    # ---- Layer 4: trigger + hysteresis ----
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
        if level <= config["low_threshold"] and not high_water \
                and not rain_confirmed and low_elapsed is not None \
                and low_elapsed >= config["dry_off_delay_ms"]:
            return _mk("OFF", state, flags, STANDBY)
        return _mk("HOLD", state, flags, HOLD)

    # ---- Layer 5: standby ----
    return _mk("OFF", state, flags, STANDBY)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sdprs/edge_pump && pytest tests/test_control_logic.py -v`
Expected: all passed (20 tests).

- [ ] **Step 5: Commit**

```bash
git add edge_pump/control_logic.py edge_pump/tests/test_control_logic.py
git commit -m "feat(pump): add pure layered control_logic.decide with full test suite"
```

---

## Phase B — Firmware sensors, controller, MQTT, orchestrator

### Task 3: `sensors.py` — debounced readings behind an injectable reader

**Files:**
- Create: `edge_pump/sensors.py`
- Test: `edge_pump/tests/test_sensors.py`

**Interfaces:**
- Consumes: `FakeClock` (tests).
- Produces:
  - `analog_to_level(raw_samples: list) -> float` — median of samples, inverted `100 - median/4095*100`, clamped 0–100.
  - `DigitalSensor(read_raw, active_low, clock, debounce_ms)` with `.update() -> bool|None` (returns debounced logical state: `True`=asserted). Logical assertion = `raw == 0` when `active_low` else `raw == 1`.
  - `SensorSet(config, readers, clock)` with `read_all() -> dict` producing the `readings` dict (`level_pct`, `float_dry`, `high_water`, `raining`) with `None` for disabled sensors. `readers` is a dict of zero-arg callables: `adc`, `float`, `rain`, `high_water`.

- [ ] **Step 1: Write the failing tests** (`edge_pump/tests/test_sensors.py`)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sdprs/edge_pump && pytest tests/test_sensors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sensors'`.

- [ ] **Step 3: Write `edge_pump/sensors.py`**

```python
# -*- coding: utf-8 -*-
"""Sensor HAL + debounced readings. The debounce/conversion logic is pure
and desktop-testable; real hardware wiring is behind `build_readers()`
which imports `machine` lazily (never at module top level)."""

_ADC_SAMPLES = 3
_ADC_SAMPLE_GAP_MS = 10


def analog_to_level(raw_samples):
    """Median of raw ADC samples -> inverted level percent, clamped 0..100."""
    s = sorted(raw_samples)
    median = s[len(s) // 2]
    level = 100.0 - (median / 4095.0) * 100.0
    if level < 0.0:
        level = 0.0
    elif level > 100.0:
        level = 100.0
    return level


class DigitalSensor:
    """Debounced digital input. `read_raw()` returns 0/1. Logical assertion is
    raw==0 for active-low, raw==1 otherwise. State flips only after the new
    logical value has been stable for `debounce_ms`."""

    def __init__(self, read_raw, active_low, clock, debounce_ms):
        self._read_raw = read_raw
        self._active_low = active_low
        self._clock = clock
        self._debounce_ms = debounce_ms
        self._stable = None       # last committed logical state
        self._candidate = None
        self._since = None

    def _logical(self, raw):
        return (raw == 0) if self._active_low else (raw == 1)

    def update(self):
        now = self._clock.ticks_ms()
        val = self._logical(self._read_raw())
        if self._stable is None:
            self._stable = val
            self._candidate = val
            self._since = now
            return self._stable
        if val != self._candidate:
            self._candidate = val
            self._since = now
        elif val != self._stable and self._clock.ticks_diff(now, self._since) >= self._debounce_ms:
            self._stable = val
        return self._stable


class SensorSet:
    """Assembles the `readings` dict decide() consumes."""

    def __init__(self, config, readers, clock):
        self._config = config
        self._readers = readers
        self._clock = clock
        c = config
        self._float = DigitalSensor(readers["float"], c["float_active_low"], clock, c["debounce_ms"]) \
            if c["float_enabled"] else None
        self._rain = DigitalSensor(readers["rain"], c["rain_active_low"], clock, c["debounce_ms"]) \
            if c["rain_enabled"] else None
        self._high = DigitalSensor(readers["high_water"], c["high_water_active_low"], clock, c["debounce_ms"]) \
            if c["high_water_enabled"] else None

    def _read_level(self):
        if not self._config["level_enabled"]:
            return None
        adc = self._readers["adc"]
        return analog_to_level([adc() for _ in range(_ADC_SAMPLES)])

    def read_all(self):
        return {
            "level_pct": self._read_level(),
            "float_dry": self._float.update() if self._float else None,
            "raining": self._rain.update() if self._rain else None,
            "high_water": self._high.update() if self._high else None,
        }


def build_readers(config):
    """MicroPython-only: wire zero-arg readers to real GPIO/ADC.
    Imports `machine` lazily so this module stays desktop-importable."""
    import machine
    readers = {}
    adc = machine.ADC(machine.Pin(config["adc_pin"]))
    adc.atten(machine.ADC.ATTN_11DB)
    adc.width(machine.ADC.WIDTH_12BIT)
    readers["adc"] = adc.read
    for key, pin_key in (("float", "float_pin"), ("rain", "rain_pin"),
                         ("high_water", "high_water_pin")):
        pin = machine.Pin(config[pin_key], machine.Pin.IN, machine.Pin.PULL_UP)
        readers[key] = pin.value
    return readers
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sdprs/edge_pump && pytest tests/test_sensors.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add edge_pump/sensors.py edge_pump/tests/test_sensors.py
git commit -m "feat(pump): add debounced sensors HAL with desktop tests"
```

> **Hardware note (manual, not automated):** `build_readers()` polarity/pins must be bench-verified per spec §6 before field use — the student sketch, wiring doc, and toolkit pinout disagree. Log raw ADC at known dry/full and toggle each digital sensor to confirm `active_low` settings.

---

### Task 4: `pump_controller.py` — actuation + timing/state manager

**Files:**
- Modify: `edge_pump/pump_controller.py`
- Test: `edge_pump/tests/test_pump_controller.py`

**Interfaces:**
- Consumes: `control_logic.decide` (indirectly), `FakeClock`, `FakePin` (tests).
- Produces: `PumpController(relay, led_red, led_green, config, clock)` where `relay`/`led_*` are objects with `.value(v)` (real `machine.Pin` on device, `FakePin` in tests). Methods:
  - `.ctrl_state -> dict` (current CtrlState, seeded from `control_logic.initial_state()`).
  - `.snapshot_timing(readings) -> dict` — updates sensor-condition timers from `readings`, returns the `timing` dict for `decide()`.
  - `.apply(decision)` — actuates relay/LEDs per `decision["action"]`, stores `decision["next_state"]`, and resets timing stamps on transitions.
  - `.state -> "ON"|"OFF"`.

- [ ] **Step 1: Write the failing tests** (`edge_pump/tests/test_pump_controller.py`)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sdprs/edge_pump && pytest tests/test_pump_controller.py -v`
Expected: FAIL — `PumpController.__init__` signature mismatch / missing methods.

- [ ] **Step 3: Rewrite `edge_pump/pump_controller.py`**

```python
# -*- coding: utf-8 -*-
"""Pump relay + LED actuation, and the stateful timing/CtrlState manager
that feeds control_logic.decide(). All ticks bookkeeping lives here."""

import control_logic


class _RealClock:
    def ticks_ms(self):
        import time
        return time.ticks_ms()

    def ticks_diff(self, a, b):
        import time
        return time.ticks_diff(a, b)


class PumpController:
    def __init__(self, relay, led_red, led_green, config, clock=None):
        self._relay = relay
        self._led_red = led_red
        self._led_green = led_green
        self._config = config
        self._clock = clock or _RealClock()

        self.ctrl_state = control_logic.initial_state()
        self._on_since = None
        self._off_since = None      # start of the current continuous-OFF period
        self._rain_wet_since = None
        self._level_low_since = None
        self._burst_phase_since = None
        self._conflict_since = None

        self._apply_relay("OFF")  # safe initial state

    @property
    def state(self):
        return self.ctrl_state["pump_state"]

    def _apply_relay(self, action):
        if action == "ON":
            self._relay.value(1)
            self._led_red.value(1)
            self._led_green.value(0)
        else:  # OFF
            self._relay.value(0)
            self._led_red.value(0)
            self._led_green.value(1)

    def _elapsed(self, since, now):
        return None if since is None else self._clock.ticks_diff(now, since)

    def snapshot_timing(self, readings):
        now = self._clock.ticks_ms()

        if readings.get("raining") is True:
            if self._rain_wet_since is None:
                self._rain_wet_since = now
        else:
            self._rain_wet_since = None

        level = readings.get("level_pct")
        low = self._config["low_threshold"]
        if level is not None and level <= low:
            if self._level_low_since is None:
                self._level_low_since = now
        else:
            self._level_low_since = None

        return {
            "pump_on_elapsed_ms": self._elapsed(self._on_since, now),
            "rain_wet_elapsed_ms": self._elapsed(self._rain_wet_since, now),
            "level_low_elapsed_ms": self._elapsed(self._level_low_since, now),
            "burst_phase_elapsed_ms": self._elapsed(self._burst_phase_since, now),
            "conflict_elapsed_ms": self._elapsed(self._conflict_since, now),
            # rest_elapsed_ms = continuous-OFF duration, so decide()'s max-runtime
            # rest (Layer 3) measures ACTUAL rest: a conflict burst that runs the
            # pump mid-rest clears _off_since and restarts the rest.
            "rest_elapsed_ms": self._elapsed(self._off_since, now),
        }

    def apply(self, decision):
        now = self._clock.ticks_ms()
        prev = self.ctrl_state
        nxt = decision["next_state"]
        action = decision["action"]

        # actuation
        if action == "ON":
            self._apply_relay("ON")
        elif action == "OFF":
            self._apply_relay("OFF")
        # HOLD: leave relay as-is

        # pump-on / continuous-off timers
        if nxt["pump_state"] == "ON":
            if prev["pump_state"] != "ON":
                self._on_since = now
            self._off_since = None            # running -> not resting
        else:  # nxt OFF
            if prev["pump_state"] == "ON":
                self._off_since = now          # ON->OFF: start the rest/off clock
            self._on_since = None

        # burst-phase timer
        if nxt.get("burst_phase") != prev.get("burst_phase"):
            self._burst_phase_since = now if nxt.get("burst_phase") is not None else None

        # conflict timer
        if nxt.get("conflict_latched") and not prev.get("conflict_latched"):
            self._conflict_since = now
        elif not nxt.get("conflict_latched"):
            self._conflict_since = None

        self.ctrl_state = nxt
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sdprs/edge_pump && pytest tests/test_pump_controller.py -v`
Expected: 7 passed.

- [ ] **Step 5: Run the whole firmware suite**

Run: `cd sdprs/edge_pump && pytest -v`
Expected: all passed (Tasks 1–4).

- [ ] **Step 6: Commit**

```bash
git add edge_pump/pump_controller.py edge_pump/tests/test_pump_controller.py
git commit -m "feat(pump): pump_controller manages actuation + ticks timing/state"
```

---

### Task 5: `mqtt_client.py` — extended payload, LWT, bounded socket, ticks throttle

**Files:**
- Modify: `edge_pump/mqtt_client.py`
- Test: `edge_pump/tests/test_mqtt_payload.py`

**Interfaces:**
- Produces: module-level pure `build_payload(node_id, timestamp, pump_state, water_level, flags, reason, battery_voltage=None, power_source=None) -> dict` — assembles the additive telemetry dict. `PumpMQTTClient.publish_status(pump_state, water_level, flags, reason, battery_voltage=None, power_source=None)`.

- [ ] **Step 1: Write the failing test** (`edge_pump/tests/test_mqtt_payload.py`)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdprs/edge_pump && pytest tests/test_mqtt_payload.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_payload'`.

- [ ] **Step 3: Edit `edge_pump/mqtt_client.py`** — add `build_payload`, rewrite `publish_status`, add LWT + bounded socket + ticks throttle.

First, make the device-only imports desktop-tolerant so the pure `build_payload` can be imported under CPython pytest (`network`/`umqtt` do not exist on the desktop). Replace the top-level import block:

```python
import network
import time
import json
from umqtt.simple import MQTTClient
```

with:

```python
import time
import json

# Device-only imports, guarded so this module still imports under desktop
# CPython (pytest) for the pure build_payload(). network/umqtt are used only
# inside methods that run on the ESP32.
try:
    import network
except ImportError:
    network = None
try:
    from umqtt.simple import MQTTClient
except ImportError:
    MQTTClient = None
```

Add at module level (after `format_timestamp`):

```python
def build_payload(node_id, timestamp, pump_state, water_level, flags, reason,
                  battery_voltage=None, power_source=None):
    """Additive telemetry payload — never renames the original core fields."""
    p = {
        "node_id": node_id,
        "timestamp": timestamp,
        "pump_state": pump_state,
        "water_level": round(water_level, 1),
        "raining": flags.get("raining"),
        "float_safe": flags.get("float_safe"),
        "high_water": flags.get("high_water"),
        "sensor_conflict": flags.get("sensor_conflict"),
        "dry_run_protect": flags.get("dry_run_protect"),
        "reason": reason,
    }
    if battery_voltage is not None:
        p["battery_voltage"] = round(battery_voltage, 2)
    if power_source is not None:
        p["power_source"] = power_source
    return p
```

Replace `publish_status` with:

```python
    def publish_status(self, pump_state, water_level, flags, reason,
                       battery_voltage=None, power_source=None):
        if not self.ensure_connection():
            return
        if self._client is None:
            return
        try:
            payload = build_payload(
                self._node_id, format_timestamp(), pump_state, water_level,
                flags, reason, battery_voltage, power_source)
            self._client.publish(self._topic, json.dumps(payload))
        except OSError as e:
            print("[MQTT] Publish error: %s" % str(e))
            self._mqtt_connected = False
            self._client = None
```

In `__init__`, change the retry-attempt seed to ticks and store socket timeout:

```python
        self._socket_timeout_s = 3
        self._last_wifi_attempt = None  # ticks_ms of last attempt; None = never
```

In `ensure_connection`, replace the `now_sec = time.time()` throttle block with a ticks-based one:

```python
            now = time.ticks_ms()
            if self._last_wifi_attempt is None or \
                    time.ticks_diff(now, self._last_wifi_attempt) > self._retry_interval * 1000:
                self._last_wifi_attempt = now
                # ... existing WiFi connect body unchanged ...
```

In the broker-connect block, set a socket timeout before `connect()` so a dead broker can't hang the control loop:

```python
                self._client = MQTTClient(
                    client_id=self._node_id, server=self._broker, port=self._port,
                    user=self._mqtt_user if self._mqtt_user else None,
                    password=self._mqtt_pass if self._mqtt_pass else None)
                # LWT: broker publishes this if we drop ungracefully
                self._client.set_last_will(
                    self._topic,
                    json.dumps({"node_id": self._node_id, "pump_state": "UNKNOWN", "online": False}),
                    retain=True, qos=0)
                self._client.connect()
                try:
                    self._client.sock.settimeout(self._socket_timeout_s)
                except Exception:
                    pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sdprs/edge_pump && pytest tests/test_mqtt_payload.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add edge_pump/mqtt_client.py edge_pump/tests/test_mqtt_payload.py
git commit -m "feat(pump): extended MQTT payload, LWT, bounded socket, ticks throttle"
```

> **Device note:** `set_last_will` must be called before `connect()` (enforced above). `sock.settimeout` bounds subsequent `publish`/`check_msg` so a stuck broker socket raises `OSError` instead of blocking the 1s control loop.

---

### Task 6: `config.py` — sensor enables, polarity, pins, parameters

**Files:**
- Modify: `edge_pump/config.py`
- Test: `edge_pump/tests/test_config.py`

**Interfaces:**
- Produces: new module constants consumed by `main.py` (see Global Constraints for exact values).

- [ ] **Step 1: Write the failing test** (`edge_pump/tests/test_config.py`)

```python
import config


def test_thresholds_ordered():
    assert config.HIGH_THRESHOLD > config.LOW_THRESHOLD
    assert config.RAIN_ON_THRESHOLD >= config.LOW_THRESHOLD
    assert config.RAIN_ON_THRESHOLD <= config.HIGH_THRESHOLD


def test_new_sensor_pins_distinct_from_existing():
    used = {config.RELAY_PIN, config.LED_RED_PIN, config.LED_GREEN_PIN,
            config.ADC_PIN, config.BATTERY_ADC_PIN, config.POWER_SOURCE_PIN}
    for pin in (config.FLOAT_PIN, config.RAIN_PIN, config.HIGH_WATER_PIN):
        assert pin not in used


def test_enable_flags_exist():
    for name in ("LEVEL_ENABLED", "FLOAT_ENABLED", "RAIN_ENABLED", "HIGH_WATER_ENABLED"):
        assert isinstance(getattr(config, name), bool)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdprs/edge_pump && pytest tests/test_config.py -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'RAIN_ON_THRESHOLD'`.

- [ ] **Step 3: Append to `edge_pump/config.py`** (after the existing GPIO section):

```python
# ============ 新增數位感測器（學生示範合併） ============
FLOAT_PIN = 32          # 底部防干燒浮球開關（dry = LOW，內部上拉）
RAIN_PIN = 33           # 雨水模組 DO（下雨 = LOW；模組供電 3.3V）
HIGH_WATER_PIN = 13     # 選用數位高水位感測器

LEVEL_ENABLED = True
FLOAT_ENABLED = True
RAIN_ENABLED = True
HIGH_WATER_ENABLED = False

FLOAT_ACTIVE_LOW = True
RAIN_ACTIVE_LOW = True
HIGH_WATER_ACTIVE_LOW = False

# ============ 控制參數 ============
RAIN_ON_THRESHOLD = 60      # 確認下雨後降低開泵門檻（80 -> 60）
RAIN_CONFIRM_MS = 30000
DRY_OFF_DELAY_MS = 30000
BURST_ON_MS = 60000
BURST_COOLDOWN_MS = 30000
CONFLICT_MAX_MS = 900000    # 15 分鐘後鎖定 OFF 並持續告警
MAX_RUN_MS = 600000
REST_MS = 60000
DEBOUNCE_MS = 2500
SOCKET_TIMEOUT_S = 3
```

Change `WDT_ENABLED = False` to `WDT_ENABLED = True` (production default; the provisioning script may still override).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sdprs/edge_pump && pytest tests/test_config.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add edge_pump/config.py edge_pump/tests/test_config.py
git commit -m "feat(pump): config for merged sensors, control params, WDT on by default"
```

---

### Task 7: `main.py` — thin guarded orchestrator

**Files:**
- Modify: `edge_pump/main.py`
- Test: `edge_pump/tests/test_main_iteration.py`

**Interfaces:**
- Consumes: `sensors.SensorSet`, `control_logic.decide`, `pump_controller.PumpController`, `mqtt_client.PumpMQTTClient`, `config`.
- Produces: `build_config() -> dict` (assembles the `decide()`/`SensorSet` config from `config.py`), `run_iteration(sensor_set, pump, mqtt, cfg, publish_cb) -> dict` (one loop body, returns the decision — network via `publish_cb` injected for testability).

- [ ] **Step 1: Write the failing test** (`edge_pump/tests/test_main_iteration.py`)

```python
from tests.fakes import FakeClock, FakePin, make_reader
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdprs/edge_pump && pytest tests/test_main_iteration.py -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute 'build_config'`.

- [ ] **Step 3: Rewrite `edge_pump/main.py`** as a thin orchestrator.

```python
# -*- coding: utf-8 -*-
"""SDPRS 水泵節點主程式 — 精簡協調層。
讀感測器 -> decide() -> 執行 -> 發布(盡力) -> 餵狗(僅在成功迭代後)。
離線自治：所有網路操作為盡力而為且有時限，永不影響控制。"""

import time
import config
import control_logic
from sensors import SensorSet
from pump_controller import PumpController
from mqtt_client import PumpMQTTClient


def build_config():
    return {
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
    }


def build_sensor_config():
    return {
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
    }


def run_iteration(sensor_set, pump, mqtt, cfg, publish_cb):
    """One control-loop body. Pure of hardware except via injected objects."""
    readings = sensor_set.read_all()
    timing = pump.snapshot_timing(readings)
    decision = control_logic.decide(readings, timing, pump.ctrl_state, cfg)
    pump.apply(decision)
    publish_cb(pump_state=pump.state, water_level=readings.get("level_pct") or 0.0,
               flags=decision["flags"], reason=decision["reason"])
    return decision


def main():
    print("[MAIN] SDPRS Pump Node starting (merged firmware)...")
    wdt = None
    try:
        import machine
        if config.WDT_ENABLED:
            from machine import WDT
            wdt = WDT(timeout=config.WDT_TIMEOUT)
        from sensors import build_readers
        readers = build_readers(build_sensor_config())
        relay = machine.Pin(config.RELAY_PIN, machine.Pin.OUT)
        led_red = machine.Pin(config.LED_RED_PIN, machine.Pin.OUT)
        led_green = machine.Pin(config.LED_GREEN_PIN, machine.Pin.OUT)
        sensor_set = SensorSet(build_sensor_config(), readers, _RealClockShim())
        pump = PumpController(relay, led_red, led_green, {"low_threshold": float(config.LOW_THRESHOLD)})
        mqtt = PumpMQTTClient(
            ssid=config.SSID, password=config.WIFI_PASS, broker=config.MQTT_BROKER,
            port=config.MQTT_PORT, node_id=config.NODE_ID, topic=config.MQTT_TOPIC_STATUS,
            retry_interval=config.WIFI_RETRY_INTERVAL,
            username=config.MQTT_USERNAME, mqtt_password=config.MQTT_PASSWORD)
        # Item 12: battery/power monitoring (optional — preserve existing telemetry;
        # inner try so an unwired pin disables it without boot-looping the node).
        battery_adc = None
        power_source_pin = None
        try:
            if config.BATTERY_ADC_PIN:
                battery_adc = machine.ADC(machine.Pin(config.BATTERY_ADC_PIN))
                battery_adc.atten(machine.ADC.ATTN_11DB)
                battery_adc.width(machine.ADC.WIDTH_12BIT)
            if config.POWER_SOURCE_PIN:
                power_source_pin = machine.Pin(config.POWER_SOURCE_PIN, machine.Pin.IN)
        except Exception as e:
            print("[MAIN] Battery/power pins unavailable: %s (continuing)" % str(e))
    except Exception as e:
        print("[MAIN] Init failed, resetting: %s" % str(e))
        import machine
        machine.reset()
        return

    cfg = build_config()
    last_publish = time.ticks_ms()
    ntp_synced = False

    def publish_cb(pump_state, water_level, flags, reason):
        nonlocal last_publish, ntp_synced
        now = time.ticks_ms()
        if time.ticks_diff(now, last_publish) >= config.PUBLISH_INTERVAL * 1000:
            battery_voltage, power_source = _read_power(battery_adc, power_source_pin)
            mqtt.publish_status(pump_state, water_level, flags, reason,
                                battery_voltage, power_source)
            last_publish = now
            if not ntp_synced and mqtt._wifi_connected:
                _sync_ntp(); ntp_synced = True
        mqtt.check_msg()

    while True:
        try:
            run_iteration(sensor_set, pump, mqtt, cfg, publish_cb)
            if wdt:
                wdt.feed()           # feed ONLY after a full successful iteration
            import gc
            gc.collect()
            time.sleep(config.POLL_INTERVAL)
        except KeyboardInterrupt:
            off_state = dict(pump.ctrl_state)
            off_state["pump_state"] = "OFF"
            pump.apply({"action": "OFF", "next_state": off_state,
                        "flags": {}, "reason": "STANDBY"})
            mqtt.disconnect()
            break
        except Exception as e:
            print("[ERROR] %s" % str(e))
            time.sleep(config.POLL_INTERVAL)


class _RealClockShim:
    def ticks_ms(self):
        return time.ticks_ms()

    def ticks_diff(self, a, b):
        return time.ticks_diff(a, b)


def _sync_ntp():
    import ntptime
    ntptime.timeout = 5
    for srv in ("pool.ntp.org", "time.cloudflare.com", "216.239.35.0"):
        try:
            ntptime.host = srv
            ntptime.settime()
            return True
        except Exception as e:
            print("[MAIN] NTP %s failed: %s" % (srv, str(e)))
    return False


def _read_power(battery_adc, power_source_pin):
    """Best-effort battery voltage + power source. Device-only; returns
    (None, None) when the pins are unwired so build_payload omits the fields."""
    battery_voltage = None
    power_source = None
    if battery_adc is not None:
        raw = battery_adc.read()
        battery_voltage = raw * 3.3 / 4095.0 * 2.0  # 1:2 divider — tune per wiring
    if power_source_pin is not None:
        power_source = "mains" if power_source_pin.value() else "battery"
    return battery_voltage, power_source


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sdprs/edge_pump && pytest tests/test_main_iteration.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full firmware suite**

Run: `cd sdprs/edge_pump && pytest -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add edge_pump/main.py edge_pump/tests/test_main_iteration.py
git commit -m "feat(pump): thin guarded orchestrator with WDT-after-success + gc"
```

> **Device note:** `machine`/`WDT`/`ntptime` import inside `main()` only, so the module stays desktop-importable for `run_iteration`/`build_config` tests. Field verification: flash to ESP32, confirm dry-run OFF with float lifted, rain-lowered threshold, and a `sensor_conflict` burst when float is held dry while rain+high-water assert.

---

## Phase C — Central server end-to-end

### Task 8: `database.py` — persist rain + sensor_conflict on pump readings

**Files:**
- Modify: `central_server/database.py`
- Test: `central_server/tests/test_pump_readings_columns.py`

**Interfaces:**
- Produces: `insert_pump_reading(node_id, timestamp, water_level, pump_state, raining=None, sensor_conflict=None)` (extended, backward-compatible signature).

- [ ] **Step 1: Write the failing test** (`central_server/tests/test_pump_readings_columns.py`)

```python
# This project's tests import via the `central_server.` package prefix with
# the sdprs repo root on sys.path (matches tests/test_alerts_api.py and
# tests/test_retention.py — there is no conftest.py). A bare `import database`
# does NOT resolve under pytest here.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server import database


def test_insert_pump_reading_accepts_new_flags(tmp_path, monkeypatch):
    db_file = str(tmp_path / "t.db")
    monkeypatch.setenv("DATABASE_URL", "")  # force SQLite mode
    database.init_db(db_file)
    database.insert_pump_reading("pump_node_01", "2026-07-10T00:00:00Z",
                                 82.4, "ON", raining=True, sensor_conflict=False)
    rows = database.get_pump_readings("pump_node_01",
                                      "2026-07-09T00:00:00Z", "2026-07-11T00:00:00Z")
    assert rows and rows[-1]["raining"] in (1, True)
    assert rows[-1]["sensor_conflict"] in (0, False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdprs/central_server && pytest tests/test_pump_readings_columns.py -v`
Expected: FAIL — `insert_pump_reading()` rejects `raining` / column missing.

- [ ] **Step 3: Edit `central_server/database.py`.**

In the SQLite `pump_readings` `CREATE TABLE` block, add the two columns; also add idempotent migrations right after the table is created (both backends):

```sql
-- SQLite CREATE TABLE pump_readings ( ... existing ...,
    raining         INTEGER,
    sensor_conflict INTEGER
);
```

Add, in both the SQLite and PostgreSQL create paths, after the pump_readings table:

```python
# SQLite migration (idempotent)
try:
    cursor.execute("ALTER TABLE pump_readings ADD COLUMN raining INTEGER")
except Exception:
    pass
try:
    cursor.execute("ALTER TABLE pump_readings ADD COLUMN sensor_conflict INTEGER")
except Exception:
    pass
```

```python
# PostgreSQL migration (idempotent)
conn.execute(sqlalchemy.text("ALTER TABLE pump_readings ADD COLUMN IF NOT EXISTS raining INTEGER;"))
conn.execute(sqlalchemy.text("ALTER TABLE pump_readings ADD COLUMN IF NOT EXISTS sensor_conflict INTEGER;"))
```

Extend `insert_pump_reading` — add `raining=None, sensor_conflict=None` params and include them in the INSERT column list and values for both backends (convert bool→int: `int(x) if x is not None else None`). Extend `get_pump_readings`' SELECT to include `raining, sensor_conflict`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sdprs/central_server && pytest tests/test_pump_readings_columns.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add central_server/database.py central_server/tests/test_pump_readings_columns.py
git commit -m "feat(server): persist raining + sensor_conflict on pump_readings (both backends)"
```

---

### Task 9: `mqtt_service.py` — parse new fields, guard malformed, keep-alive on garble

**Files:**
- Modify: `central_server/services/mqtt_service.py:259-321`
- Test: `central_server/tests/test_handle_pump_status.py`

**Interfaces:**
- Consumes: `insert_pump_reading(..., raining, sensor_conflict)` from Task 8.
- Produces: `MQTTService._handle_pump_status` stores the new flags into `node_states[node_id]` and bumps `last_heartbeat` even when JSON is malformed.

- [ ] **Step 1: Write the failing test** (`central_server/tests/test_handle_pump_status.py`)

```python
# mqtt_service.py uses package-relative imports (`from ..config import ...`),
# so it MUST be imported as `central_server.services.mqtt_service` with the
# sdprs repo root on sys.path — a top-level `from services...` import raises
# "attempted relative import beyond top-level package". Monkeypatch targets
# must use the same fully-qualified module path. (No conftest.py in the repo;
# matches tests/test_alerts_api.py's self-insert pattern.)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json
from central_server.services.mqtt_service import MQTTService


def make_service():
    svc = MQTTService.__new__(MQTTService)
    import threading
    svc._lock = threading.Lock()
    svc.node_states = {}
    svc.db = None
    svc._loop = None
    return svc


def test_pump_status_stores_new_flags(monkeypatch):
    svc = make_service()
    monkeypatch.setattr("central_server.services.mqtt_service.upsert_node", lambda *a, **k: None)
    monkeypatch.setattr("central_server.services.mqtt_service.insert_pump_reading", lambda *a, **k: None)
    payload = json.dumps({"node_id": "pump_node_01", "pump_state": "ON",
                          "water_level": 82.4, "raining": True,
                          "sensor_conflict": True, "dry_run_protect": False})
    svc._handle_pump_status("pump_node_01", payload)
    st = svc.node_states["pump_node_01"]
    assert st["raining"] is True and st["sensor_conflict"] is True


def test_malformed_payload_still_bumps_last_seen():
    svc = make_service()
    svc._handle_pump_status("pump_node_01", "{not json")
    assert "pump_node_01" in svc.node_states
    assert svc.node_states["pump_node_01"]["last_heartbeat"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdprs/central_server && pytest tests/test_handle_pump_status.py -v`
Expected: FAIL — new flags absent / malformed payload creates no node entry.

- [ ] **Step 3: Rewrite `_handle_pump_status`** (`central_server/services/mqtt_service.py`).

```python
    def _handle_pump_status(self, node_id: str, payload: str):
        from datetime import datetime
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Invalid pump_status JSON from {node_id}: {e}")
            with self._lock:
                st = self.node_states.get(node_id, {"type": "pump", "status": "ONLINE"})
                st["last_heartbeat"] = datetime.utcnow()  # garbled-but-alive != offline
                self.node_states[node_id] = st
            return
        if not isinstance(data, dict):
            logger.error(f"pump_status payload not an object from {node_id}")
            return

        with self._lock:
            self.node_states[node_id] = {
                "type": "pump", "status": "ONLINE",
                "last_heartbeat": datetime.utcnow(),
                "pump_state": data.get("pump_state", "UNKNOWN"),
                "water_level": data.get("water_level"),
                "raining": data.get("raining"),
                "float_safe": data.get("float_safe"),
                "high_water": data.get("high_water"),
                "sensor_conflict": data.get("sensor_conflict"),
                "dry_run_protect": data.get("dry_run_protect"),
                "reason": data.get("reason"),
            }

        metadata = {"pump_state": data.get("pump_state"), "water_level": data.get("water_level"),
                    "raining": data.get("raining"), "sensor_conflict": data.get("sensor_conflict")}
        if self.db:
            self.db.upsert_node(node_id, "pump", "ONLINE", metadata)
        else:
            upsert_node(node_id, "pump", "ONLINE", metadata)

        try:
            ts = data.get("timestamp") or datetime.utcnow().isoformat()
            insert_pump_reading(node_id, ts, data.get("water_level"), data.get("pump_state"),
                                raining=data.get("raining"), sensor_conflict=data.get("sensor_conflict"))
        except Exception as ts_err:
            logger.warning(f"Failed to persist pump reading for {node_id}: {ts_err}")

        self._broadcast_pump_status(node_id, data)  # see Task 10
```

Add a stub `_broadcast_pump_status` for now (Task 10 fills it):

```python
    def _broadcast_pump_status(self, node_id, data):
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sdprs/central_server && pytest tests/test_handle_pump_status.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add central_server/services/mqtt_service.py central_server/tests/test_handle_pump_status.py
git commit -m "feat(server): parse pump sensor flags, keep-alive on malformed payload"
```

---

### Task 10: WebSocket loop capture — make the pump card update live

**Files:**
- Modify: `central_server/main.py:55-79` (lifespan)
- Modify: `central_server/services/mqtt_service.py` (`__init__`/`init_mqtt_service`, `_broadcast_pump_status`, `_mark_node_offline`)
- Test: `central_server/tests/test_ws_loop_capture.py`

**Interfaces:**
- Consumes: `broadcast_from_sync(loop, message)` from `services/websocket_service.py`.
- Produces: `init_mqtt_service(loop=None)` stores the loop; `_broadcast_pump_status` uses the stored loop instead of `asyncio.get_event_loop()`.

- [ ] **Step 1: Write the failing test** (`central_server/tests/test_ws_loop_capture.py`)

```python
# mqtt_service.py uses package-relative imports (`from ..config import ...`),
# so import it as a central_server submodule with the sdprs repo root on
# sys.path. A top-level `from services...` import raises "attempted relative
# import beyond top-level package". Monkeypatch targets use the same FQ path.
# (No conftest.py in the repo — matches tests/test_alerts_api.py.)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server.services.mqtt_service import MQTTService


def test_broadcast_uses_stored_loop(monkeypatch):
    svc = MQTTService.__new__(MQTTService)
    sentinel = object()
    svc._loop = sentinel
    captured = {}
    monkeypatch.setattr("central_server.services.websocket_service.broadcast_from_sync",
                        lambda loop, msg: captured.update(loop=loop, msg=msg))
    svc._broadcast_pump_status("pump_node_01", {"pump_state": "ON", "water_level": 80,
                                                "raining": True, "sensor_conflict": False})
    assert captured["loop"] is sentinel
    assert captured["msg"]["type"] == "pump_status"
    assert captured["msg"]["data"]["raining"] is True


def test_broadcast_noop_without_loop(monkeypatch):
    svc = MQTTService.__new__(MQTTService)
    svc._loop = None
    called = {"n": 0}
    monkeypatch.setattr("central_server.services.websocket_service.broadcast_from_sync",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    svc._broadcast_pump_status("n", {})
    assert called["n"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd sdprs/central_server && python -m pytest tests/test_ws_loop_capture.py -v`
Expected: FAIL — `_broadcast_pump_status` is a no-op / `_loop` unused.

- [ ] **Step 3a: Edit `MQTTService.__init__`** to accept and store a loop, and update `init_mqtt_service`:

```python
    def __init__(self, ..., loop=None):   # keep existing params, add loop
        ...
        self._loop = loop
```

```python
def init_mqtt_service(..., loop=None):
    global _mqtt_service
    _mqtt_service = MQTTService(..., loop=loop)
    return _mqtt_service
```

- [ ] **Step 3b: Replace the `_broadcast_pump_status` stub:**

```python
    def _broadcast_pump_status(self, node_id, data):
        if self._loop is None:
            return
        try:
            from datetime import datetime
            from .websocket_service import broadcast_from_sync
            broadcast_from_sync(self._loop, {
                "type": "pump_status",
                "data": {
                    "node_id": node_id,
                    "pump_state": data.get("pump_state"),
                    "water_level": data.get("water_level"),
                    "raining": data.get("raining"),
                    "sensor_conflict": data.get("sensor_conflict"),
                    "dry_run_protect": data.get("dry_run_protect"),
                    "timestamp": data.get("timestamp", datetime.utcnow().isoformat()),
                },
            })
        except Exception as ws_error:
            logger.warning(f"WebSocket broadcast failed: {ws_error}")
```

Apply the same `self._loop`-based pattern to `_mark_node_offline`'s broadcast (replace its `asyncio.get_event_loop()` block).

- [ ] **Step 3c: Capture the loop in the lifespan** (`central_server/main.py`), replacing the `init_mqtt_service()` call:

```python
    import asyncio
    loop = asyncio.get_running_loop()
    mqtt_svc = init_mqtt_service(loop=loop)
    mqtt_svc.start()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd sdprs/central_server && python -m pytest tests/test_ws_loop_capture.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add central_server/main.py central_server/services/mqtt_service.py central_server/tests/test_ws_loop_capture.py
git commit -m "fix(server): capture event loop for MQTT-thread WS broadcasts (pump card live)"
```

---

## Phase D — Dashboard

### Task 11: Monitor-wall pump card — rain / dry-run / sensor-conflict

**Files:**
- Modify: the SPA pump-card component in `central_server/static/spa/` (locate first — likely `components.jsx` or `pages.jsx`)
- Verify: manual (the SPA transpiles JSX in-browser via Babel; no automated test harness exists)

**Interfaces:**
- Consumes: the WebSocket `{"type":"pump_status","data":{...raining, sensor_conflict, dry_run_protect...}}` message from Task 10, and `pump_state`/`water_level` already handled today.

- [ ] **Step 1: Locate the pump card.** Search the SPA for the existing pump rendering:

Run: `cd sdprs/central_server && grep -rn "pump_state\|water_level\|pump" static/spa/*.jsx`
Identify the component that renders a pump node card and the WebSocket message handler that updates node state.

- [ ] **Step 2: Extend the WS handler** so `pump_status` messages merge `raining`, `sensor_conflict`, `dry_run_protect` into the node's state object (alongside the existing `pump_state`/`water_level` merge).

- [ ] **Step 3: Add three indicators to the pump card**, driven by that state:
  - Rain: a small "🌧 Raining" / "Dry" badge from `raining`.
  - Dry-run protection: a "Dry-run protect (pump held OFF)" badge when `dry_run_protect` is true.
  - Sensor conflict: a prominent CRITICAL maintenance banner ("⚠ Sensor conflict — inspect float switch") when `sensor_conflict` is true, styled like existing critical alerts.

Use the codebase's existing badge/alert classes (match the glass-node critical styling already present) — do not introduce a new styling system.

- [ ] **Step 4: Manual verification.** Start the server, publish a synthetic pump_status via the broker with `sensor_conflict:true` and confirm the card shows the banner live (no page reload); then `raining:true`/`dry_run_protect:true` and confirm the badges. Example:

```bash
mosquitto_pub -h <broker> -t "sdprs/edge/pump_node_01/pump_status" \
  -m '{"node_id":"pump_node_01","pump_state":"OFF","water_level":10,"raining":true,"sensor_conflict":true,"dry_run_protect":false,"reason":"CONFLICT_LATCH_OFF"}'
```

- [ ] **Step 5: Commit**

```bash
git add central_server/static/spa/
git commit -m "feat(dashboard): pump card shows rain, dry-run protection, sensor-conflict alert"
```

---

## Self-Review

**1. Spec coverage:**
- §4 modules → Tasks 2 (control_logic), 3 (sensors), 4 (pump_controller), 5 (mqtt_client), 6 (config), 7 (main). ✓
- §5 decision ladder + truth table + degradation → Task 2 test suite (all layers, conflict burst/ceiling/holdoff, None handling). ✓
- §6 pin map + debounce + polarity → Tasks 3, 6. ✓ (bench commissioning noted as manual.)
- §7 payload + LWT → Task 5. ✓
- §8 server: parse (Task 9), WS loop-capture (Task 10), pump_readings columns (Task 8), pump card (Task 11). ✓
- §9 offline autonomy / WDT / bounded IO / ticks throttle / gc / init guard → Tasks 5, 7. ✓
- §10 testing (control_logic, sensors) → Tasks 2, 3, plus controller/payload/main/server tests. ✓
- §11 rollout ordering → task order (firmware core → integration → server → dashboard). ✓
- §12 security constraints → Global Constraints + no creds touched. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to". Task 11 is inherently manual (in-browser JSX, no test harness) and says so with concrete field names + a verification command — not a placeholder.

**3. Type consistency:** `decide()` dict contract (readings/timing/ctrl_state/decision keys, reason codes) is identical across Tasks 2, 4, 5, 7; `insert_pump_reading(..., raining, sensor_conflict)` matches between Tasks 8 and 9; `_broadcast_pump_status(node_id, data)` matches between Tasks 9 (stub) and 10 (impl); `build_config()` keys match `DEFAULT_CONFIG`.

**Known follow-ups not in this plan** (spec Appendix): the broader six-theme reconstruction (data-access unification, full auth hardening, glass-node detection fixes). Each is its own spec → plan.
