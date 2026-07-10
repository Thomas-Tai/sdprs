# Pump Node Merge & Reconstruction — Design Spec

**Date:** 2026-07-10
**Status:** Draft for review
**Scope:** Merge the student ESP32 water/rain pump demo concepts into the production MicroPython `edge_pump` node, refactor the pump controller into a testable layered architecture, and carry the required changes end-to-end into the central server and monitor-wall dashboard.

This spec is the implementable unit. The broader six-theme codebase reconstruction (from the full audit) is captured in the Appendix as decomposed follow-on projects, **not** built here.

---

## 1. Context

SDPRS is a typhoon-season disaster-monitoring system with three tiers: a FastAPI central server, Raspberry Pi glass-break edge nodes, and an ESP32 (MicroPython) pump node. The pump node today:

- Reads a single **analog** capacitive water-level sensor (ADC pin 34), 3-sample median, inverted to a 0–100% level.
- Drives a relay (26) + red/green status LEDs (27/25) with **80% ON / 20% OFF hysteresis**.
- Publishes status every 10s over MQTT; control runs every 1s.
- Is designed for **offline autonomy**: WiFi/MQTT failure must never affect local pump control.

A student team built a parallel ESP32 (Arduino) demo adding a **bottom float dry-run switch**, a **rain sensor**, and an optional **digital high-water sensor**, with a three-layer safety ladder and a "both wet → override the float" special case. Their firmware has good hardware instincts (dry-run interlock, rain linkage, hardware safety BOM) but production-hostile software (blocking reconnect on the control path, public broker, hardcoded credentials, no hysteresis/debounce, unbounded float bypass).

This project merges the **concepts**, not the code.

### 1.1 Confirmed defects in the current `edge_pump` (verified by direct code read)

- `main.py:82` uses `machine.Pin(...)` but `machine` is never imported → power-source monitoring silently dead (swallowed by a bare `except`).
- `mqtt_client.py:109` calls `self._client.connect()` with **no socket timeout**; when WiFi is up but the broker is unreachable, this blocks the 1s control loop on each 10s publish attempt.
- `mqtt_client.py:77-79` throttles WiFi retry on wall-clock `time.time()`, which the one-shot NTP step (`main.py`) shifts by ~26 years → throttle bypassed once after NTP.
- No `set_last_will`; `MQTT_TOPIC_HEARTBEAT` is defined but never published (the 10s `pump_status` is the de-facto heartbeat by coincidence).
- `config.py:52` ships `WDT_ENABLED=False`; the provisioning script never enables it, so the watchdog — the only recovery from a hang — is off in the field.
- `main.py` init (ADC/pump/MQTT) has **no top-level guard** and constructs the WDT *after* it, so any init fault bricks the node with no watchdog to reset it.

## 2. Goals / Non-Goals

**Goals**
1. Add float dry-run protection, rain-linked triggering, and optional digital high-water redundancy, each **individually enable/disable-able in `config.py`**, on a conflict-free pin map — one firmware runs on the old toolkit wiring, the student wiring, or the combined rig.
2. Replace the "both wet bypasses the float" override with a **guarded override**: bounded timed bursts + a `sensor_conflict` alarm, never an unbounded dry-run bypass.
3. Extract the safety decision into a **pure, hardware-free `control_logic.decide()`** with desktop `pytest` coverage of the full ladder.
4. Fix the confirmed offline-autonomy defects (bounded network I/O, WDT on by default, guarded init, `ticks_ms` throttle, LWT).
5. Extend the MQTT payload and carry it **end-to-end**: server payload parsing + node state + WebSocket push, and a monitor-wall pump card showing rain, dry-run protection, and sensor-conflict maintenance alerts.

**Non-Goals**
- The broader six-theme reconstruction (data-access unification, full auth hardening, glass-node fixes) — Appendix follow-ons.
- Any change to the analog hysteresis *values* (80/20 stays the backbone).
- Dual-float overflow architecture — noted as a future option, not built now.
- Downlink command/control to the pump (no cmd-topic subscription in this spec).

## 3. Deployment assumption

The pump publishes to the **project broker** (LAN Mosquitto or cloud EMQX), with credentials injected at flash time by `setup_esp32.sh` into `config.py` placeholders. The public `broker.emqx.io` and the student's hardcoded WiFi credentials (SSID `MSC-Person` / password `Msc@2333` — **leaked, must be rotated, must not enter production**) are explicitly excluded. TLS/auth posture follows whatever the project broker enforces; this node adds no new anonymous-publish surface.

## 4. Architecture — module decomposition

| Module | Responsibility | Desktop-testable |
|---|---|---|
| `config.py` (extend) | Per-sensor enable flags + polarity, conflict-free pin map, thresholds, rain/burst/max-runtime/debounce params. Creds stay as flash-time placeholders. | n/a |
| `sensors.py` (**new**) | HAL + unified read: analog level (ADC median+inversion+clamp) and debounced digital inputs (float, rain, high-water). Polarity config-declared. Exposes `read_all() → Readings` with a per-sensor `valid` flag. Holds **no** control policy. Pin access behind an injectable reader so tests run without hardware. | Yes |
| `control_logic.py` (**new**) | Pure `decide(readings, timing, ctrl_state, config) → Decision`. Implements the layered ladder, guarded-override burst state, max-runtime duty cycle, rain threshold-lowering, dry-off delay. No `machine`, no clock. | **Yes — the point** |
| `pump_controller.py` (extend) | Actuates relay + LEDs; owns `ticks_ms` bookkeeping (pump-on-since, last-off, phase timestamps) and passes them into `decide()` as `timing`. | Partial |
| `mqtt_client.py` (extend) | LWT, extended payload, **time-bounded** socket ops, `ticks_ms` retry throttle, `pump_status`-as-heartbeat made explicit. | Partial |
| `main.py` (rewrite thin) | Guarded init → loop: `sensors.read_all()` → `decide()` → actuate → publish (best-effort) → `wdt.feed()` **after a successful iteration**. | No |
| `boot.py` | Unchanged — inert, brownout-safe. | n/a |
| `tests/test_control_logic.py`, `tests/test_sensors.py`, `tests/fakes.py` (**new**) | Desktop `pytest`: full truth table, burst timing, debounce, degradation. | Yes |

**Invariant:** `sensors.read_all()` + `decide()` + actuate run every tick unconditionally; MQTT publish/reconnect is best-effort and time-bounded, so a broker outage changes nothing about pump control.

## 5. The decision ladder (`control_logic.decide`)

### 5.1 Interface

```
Readings   = { level_pct: float|None, float_dry: bool|None,
               high_water: bool|None, raining: bool|None }      # None = sensor disabled/invalid
Timing     = { now_ms, pump_on_since_ms|None, last_off_ms|None,
               rain_wet_since_ms|None, level_low_since_ms|None }
CtrlState  = { pump_state: "ON"|"OFF", conflict_latched: bool,
               burst_phase: "ON"|"REST"|None, burst_phase_since_ms|None,
               conflict_since_ms|None }
Config     = thresholds + enables + polarity + burst/max-runtime/delay/debounce params
Decision   = { action: "ON"|"OFF"|"HOLD", next_state: CtrlState,
               flags: {raining, float_safe, high_water, sensor_conflict,
                       dry_run_protect, max_runtime_rest},
               reason: str }
```

`decide()` is a pure function of its inputs — identical inputs yield identical output. All time enters via `Timing`.

### 5.2 Priority order (highest first)

1. **Guarded conflict override (bounded).** If `conflict` is latched, run the burst cycle: `action=ON` while `burst_phase=="ON"` and elapsed < `BURST_ON_SECONDS` (60); then `action=OFF`, `burst_phase="REST"` for `BURST_COOLDOWN_SECONDS` (30); on each REST→ON boundary, re-evaluate sensors. `flags.sensor_conflict=True`. If total conflict duration exceeds `CONFLICT_MAX_SECONDS` (900), latch **OFF** and keep alarming until sensors re-agree — never burst a stuck-dry float indefinitely. This is the *only* path that runs the pump while the float reads dry.
   - **Conflict definition:** `float_dry` (debounced, enabled) AND (count of independent wet votes ≥ 2), where wet votes ∈ {`high_water==True`, `raining` confirmed, `level_pct ≥ HIGH_THRESHOLD`}. Requires float + ≥2 wet-capable sensors enabled; otherwise conflict cannot form and dry-run (below) is absolute — the conservative, safe default.
   - Latch clears (with hysteresis) only when float reads safe OR the wet votes drop below 2.
2. **Dry-run protection.** Else if `float_dry` (enabled) → `action=OFF`, `flags.dry_run_protect=True`. Hard interlock.
3. **Max-runtime duty cycle.** Else if `pump_state=="ON"` and continuous on-time ≥ `MAX_RUN_SECONDS` (600) → `action=OFF`, `flags.max_runtime_rest=True`, rest `REST_SECONDS` (60), then re-evaluate (resumes immediately if still demanded — protects the motor without abandoning a real flood).
4. **Trigger + hysteresis (normal).**
   - Effective ON threshold = `HIGH_THRESHOLD` (80), lowered to `RAIN_ON_THRESHOLD` (60) when `raining` is confirmed (wet ≥ `RAIN_CONFIRM_SECONDS`, 30s).
   - Turn **ON** if `level_pct ≥ effective_ON` OR `high_water==True`.
   - Turn **OFF** only if `level_pct ≤ LOW_THRESHOLD` (20) AND not `high_water` AND the low condition + rain-clear have held for `DRY_OFF_DELAY` (30s) — the student's residual-water fix.
   - Otherwise `action=HOLD` (hysteresis band).
5. **Standby.** Else `action=OFF`.

### 5.3 Truth table (float + 2 wet sources enabled; analog primary)

| float | high_water | rain(confirmed) | level | Result |
|---|---|---|---|---|
| safe | — | — | ≥80 (or ≥60 if raining) | ON (hysteresis/rain trigger) |
| safe | true | — | any | ON (high-water) |
| safe | false | false | ≤20 for ≥30s | OFF (dry-off delay) |
| safe | false | false | 20–80 | HOLD |
| dry | false | false | any | OFF (dry-run protection) |
| dry | true | true | any | **Conflict burst** (60/30, alarm, 15-min ceiling) |
| dry | true | false | <80 | OFF (only 1 wet vote → dry-run absolute) |
| dry | false | true | ≥80 | **Conflict burst** (rain + analog-high = 2 votes) |

### 5.4 Config-disabled degradation

- **Float disabled:** no dry-run layer, no conflict path; analog + hysteresis only (current behavior). Boot logs `WARNING: dry-run protection DISABLED`.
- **Analog disabled:** triggering falls to `high_water` only; if `high_water` also disabled → boot `CRITICAL`, refuse to enter the loop (misconfiguration).
- **Rain disabled:** no threshold-lowering, no rain vote.
- **High-water disabled:** analog-only triggering.

## 6. Sensors, pin map, debounce

Conflict-free additions (existing relay=26, LED=27/25, ADC=34, battery-ADC=35, power-source=21 untouched):

```
FLOAT_PIN = 32 ; FLOAT_ENABLED = True  ; FLOAT_ACTIVE_LOW = True   # bottom switch, dry = LOW, internal PULL_UP
RAIN_PIN  = 33 ; RAIN_ENABLED  = True  ; RAIN_ACTIVE_LOW  = True   # rain DO, raining = LOW (power module from 3.3V)
HIGH_WATER_PIN = 13 ; HIGH_WATER_ENABLED = False ; HIGH_WATER_ACTIVE_LOW = False
LEVEL_ENABLED = True                                              # analog ADC primary
DEBOUNCE_MS = 2500   # digital state changes only after this window of agreement
```

- Polarity is config-declared per sensor, so wiring variants require no code change.
- Digital reads are debounced in `sensors.py` (state flips only after `DEBOUNCE_MS` of consecutive agreement, tracked via `ticks_ms`); analog keeps its 3-sample median.
- Each sensor reports a `valid` flag; a disabled sensor yields `None` (distinct from `False`) so `decide()` treats "absent" and "reads-safe" differently.
- **Hardware commissioning (mandatory, manual):** the student's sketch, wiring doc, and toolkit pinout disagree on pin assignments and wire colors. Before trusting the merge on real hardware, bench-verify each digital sensor's polarity and log raw ADC at known dry/full states. This is a rollout step, not an automated test.

## 7. MQTT payload + LWT

Extend `publish_status` — **add** fields, never rename existing ones (server-coordinated):

```json
{
  "node_id": "...", "timestamp": "...", "pump_state": "ON|OFF", "water_level": 0-100,
  "battery_voltage": 0.0, "power_source": "mains|battery",
  "raining": true, "float_safe": true, "high_water": false,
  "sensor_conflict": false, "dry_run_protect": false,
  "reason": "STANDBY|HYSTERESIS_ON|RAIN_TRIGGER|HIGH_WATER|CONFLICT_BURST|DRY_RUN_OFF|MAX_RUNTIME_REST"
}
```

**LWT:** `set_last_will` before `connect()` → retained message on the status topic `{"node_id":..., "pump_state":"UNKNOWN", "online":false}`, so an ungraceful disconnect (power loss, WDT reset) marks the node offline immediately instead of waiting for the 30s server timeout.

## 8. Server end-to-end changes

1. **`mqtt_service._handle_pump_status`** — parse the new fields into `node_states` + metadata; guard `isinstance(data, dict)`; **bump a last-seen timestamp even on malformed JSON** so glitchy-but-alive publishing doesn't trip the 30s false-offline.
2. **WebSocket push fix (in-scope, Theme 5).** Capture the asyncio loop once in the FastAPI lifespan and pass it into `init_mqtt_service(loop=...)`; use that stored loop in `_handle_pump_status`/`_mark_node_offline` instead of `get_event_loop()` on the paho thread (which currently raises → every pump broadcast is swallowed). Without this the pump card's live rain/dry-run/conflict display never updates.
3. **`database.py`** — add two nullable columns to `pump_readings`: `raining` and `sensor_conflict` (both chart-/alert-worthy), in **both** the SQLite and PostgreSQL create-table paths, via additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. The transient flags (`dry_run_protect`, `float_safe`, `high_water`) live in `node_states` + the live broadcast, not the time series.
4. **Monitor-wall pump card (SPA)** — add a rain indicator, a dry-run-protection state, and a **sensor-conflict maintenance alert** rendered as CRITICAL, driven by the live WebSocket `pump_status` payload.

## 9. Error handling, offline autonomy, WDT

- **Time-bounded network I/O:** set a socket timeout (`SOCKET_TIMEOUT`, 3s) so broker `connect()`/`publish()` raise `OSError` instead of hanging; the loop already treats network failure as best-effort and continues. No blocking call may exceed a couple of seconds.
- **WDT on by default** in the merged firmware; `setup_esp32.sh` forces `WDT_ENABLED=True` for production (warns loudly if shipping it off). `wdt.feed()` runs **only after** a successful read+decide+actuate iteration, so an error-spin still trips the dog and resets.
- **Guarded init:** wrap ADC/pump/sensor construction in `try/except: machine.reset()`, and construct + feed the WDT early so init-phase faults recover.
- **Throttle fix:** WiFi retry uses `ticks_ms`/`ticks_diff`, not `time.time()`.
- **Memory:** `gc.collect()` once per publish cycle.
- Fix the missing `machine` import.

## 10. Testing

- **`tests/test_control_logic.py`** (pure, desktop `pytest`): the full §5.3 truth table across every sensor-enable combination; ladder priority; guarded-override burst timing (ON ≤ 60s, cooldown 30s, latch/unlatch, 15-min ceiling → OFF-alarm); max-runtime duty cycle + resume; hysteresis-band HOLD; rain threshold-lowering; dry-off delay; degradation when sensors are `None`. Clock injected via `Timing`.
- **`tests/test_sensors.py`**: debounce (bounce sequences don't flip state until `DEBOUNCE_MS` of agreement) via an injected fake pin reader; polarity config; ADC median+inversion+clamp; per-sensor `valid` flag.
- **`tests/fakes.py`**: injectable clock + pin reader.
- `control_logic` imports nothing hardware-specific, so the safety core runs under CPython `pytest` in CI.

## 11. Rollout / de-risking

The refactor lands so the pure `decide()` + tests are validated on the desktop **before** any hardware change. New sensors default such that the exact current behavior is reproducible (`FLOAT_ENABLED`/`RAIN_ENABLED` can be turned off to fall back to analog-only). Sequence: (1) `control_logic` + tests; (2) `sensors` + tests; (3) thin `main.py` + `pump_controller`/`mqtt_client` extensions; (4) server + dashboard; (5) bench commissioning per §6; (6) field enable.

## 12. Security constraints (carried through implementation)

- No hardcoded WiFi/MQTT credentials in committed code — flash-time placeholders only.
- Rotate the leaked student WiFi password; never reference it.
- No public/anonymous broker in production.
- New payload fields are telemetry only; no downlink command surface is added here.

---

## Appendix — Reconstruction plan (follow-on projects, not built in this spec)

The full audit produced six themes. Each is its own spec → plan → implementation cycle. Severity is stated for the internet-exposed PostgreSQL/EMQX deployment; several drop a notch on an isolated LAN+SQLite Pi. Items marked ✓ were verified by direct code read.

1. **Unify the data-access layer.** ✓ In PostgreSQL mode `_db_connection` stays `None`, so `get_db()` raises and `acknowledge/resolve/bulk-resolve/handover/audit` all 500 (the cloud "respond to an alert" workflow is dead); inconsistent SQLite lock discipline causes `database is locked` races; a fresh engine is built per PG call. → One backend-agnostic access layer, single lock discipline, one pooled engine.
2. **Close the trust boundary.** ✓ Forgeable sessions (`SECRET_KEY` fallback `dev-secret-key-change-in-production`, warn-only), ✓ path traversal via unvalidated `node_id` (absolute path resets the storage prefix), ✓ public snapshot leak, ✓ anonymous MQTT (LAN by design; cloud EMQX exposure to re-confirm), shared static `EDGE_API_KEY`, non-constant-time compares, no login throttle, cookie missing `Secure`/`SameSite`, dead auth-policy helpers. → Fail-closed secrets, per-node credentials, MQTT ACL, node_id allowlist, authenticated snapshot/storage.
3. **Harden edge offline-autonomy.** Pump (this spec) + glass (blocking initial `connect()` kills reconnect; ✓ event data destroyed when MP4 cleanup deletes pending files then marks them `UPLOADED`; ✓ 4xx → infinite tight-retry).
4. **Data-lifecycle correctness.** Retention delimiter mismatch (T vs `CURRENT_TIMESTAMP` space — mechanism plausible; storage-side format unconfirmed; ~24h early on the boundary day → medium); `pump_readings` never pruned; ✓ `weather_config` wiped on every PG startup; orphaned MP4s; trusted client timestamps; backups with no verify/test-restore.
5. **Make observability real.** ✓ MQTT-thread WebSocket broadcasts are swallowed (loop-capture) — the pump-card fix in §8 is the first slice; broadcast head-of-line blocking; offline-mark TOCTOU false alarm; no LWT (addressed for the pump here).
6. **Detection correctness (glass).** ✓ Brightness filter can permanently, silently blind the visual detector; ✓ post-crack stuck-triggered state fuses storm noise into 30-60s of phantom events (medium-high, not critical — false-positive amplifier, not a miss); AND-only fusion with no single-sensor fallback; blocking encode; non-functional simulate paths; ~790MB baseline buffer (arithmetic unconfirmed against configured resolution).
