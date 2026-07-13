# SDPRS — Codebase Audit & Progress Ledger

**Originally audited:** 2026-07-13 (evidence-based — every claim re-verified against the working tree)
**Last updated:** 2026-07-13 — *after* the pump-merge merge/push, the Theme-2 security slice, **and the Theme-6 glass-detection hardening slice**.
**Repo:** `sdprs/` (its own git repo; parent folder is not versioned) · **Remote:** `github.com/Thomas-Tai/sdprs`
**Current branch/commit:** `main` @ `dddda76` (**1 ahead of `origin/main` @ `b3d3360`** — T4 data-lifecycle slice `dddda76` not yet pushed; everything through the detector-health consume-side already pushed)

> Canonical, living progress tracker for the `docs/superpowers` workstream.
> Task-by-task execution detail for the pump-merge effort lives in
> `.superpowers/sdd/progress.md` (SDD ledger). This file is the higher-altitude
> project-state view and the reconstruction-theme tracker.

---

## 0. Current status (headline)

**The pump-merge reconstruction + the entire V2 SPA are merged to `main` and pushed to origin.** `main` had been idle since 2026-05-09; it is now the live line again. The two security defects that rode in with the SPA baseline have been fixed. **The Theme-6 glass-hardening slice is pushed, and its detector-health telemetry is now wired end-to-end** — edge heartbeat → server `/api/nodes` → SPA — so a blinded-but-online camera shows 視覺/音訊 health + degraded status to operators. **Theme-4 data-lifecycle correctness (retention delimiter, pump_readings pruning, orphan-MP4 sweep, weather_config persistence) is now committed** (`dddda76`, local-only). **198 tests pass** (edge_pump 48 · central_server 54 · edge_glass 96).

The remaining work is the (now-advancing) open reconstruction themes plus one **hardware** gate: the pump sensors are **not yet bench-commissioned**, so they ship OFF / analog-only until spec §6 is done. No coding blocker remains on what shipped.

---

## 1. What happened 2026-07-13 (chronological)

1. **Full codebase audit** (this document's original content, preserved in §4–§8). Established that a large, review-clean body of work was stranded on the unmerged `spec/pump-merge` branch.
2. **Final whole-branch review — 9 parallel subagents** (3 review · 3 fix · 3 verify), scoped pump-firmware / server-backend / V2-SPA over `0ceeb22..0595060`:
   - **Biggest catch, from triage rather than the scoped reviewers:** `scripts/setup_esp32.sh`'s hardcoded on-device copy list was **missing `control_logic.py` and `sensors.py`** — the refactor's new required modules. The `-f` guard silently skipped them, so a provisioned node would **ImportError on boot**. Fixed (deploy script + README manual command + module tree); removed the dead `water_sensor.py`. — commit `6c099f0`
   - **PostgreSQL `/api/nodes` 500:** `snoozed_until` was passed raw to a Pydantic `Optional[str]` → `datetime` rejected → HTTP 500 for the whole list whenever a node was snoozed. Wrapped in `_ts_to_iso()` at both sites + REST regression test. — commit `65dc906`
   - **Dashboard:** `60/cycles` rendered "每 Infinity 分" for idle pumps; guarded both cards + added `role="alert"` to the sensor-conflict banner. — commit `a2a8bd0`
   - All three fix areas independently re-reviewed → **SAFE TO COMMIT** (incl. confirming the regression tests fail if the fix is reverted, and that the deploy list now covers every module `main.py` imports).
3. **Merged to `main`** via `git merge --no-ff spec/pump-merge` → merge commit `1c39b9e`. Re-verified 151 tests on the merged result.
4. **Theme-2 security slice — 2 parallel fixers:**
   - **SPA inline-`<script>` injection:** `dashboard_page` injected the username via `json.dumps(user)`, which does not neutralize a `</script>` breakout. Added `_js_safe_json()` (escapes `<`, `>`, `&`, U+2028/9). — commit `55b351a`
   - **Spoofable audit attribution:** single-alert-resolve trusted a client-sent `resolved_by` for the DB write, WS broadcast, and tamper-evident audit log. Now derives it from the authenticated session (matching bulk-resolve); SPA stops sending it. — commit `4a8bdc2`
5. **Pushed** `main` → `origin/main` @ `4a8bdc2`. Deleted the merged `spec/pump-merge` label. Final sweep: **155 tests pass**.
6. **Docs commit** `d7424bd` (this file rewritten to post-merge state) — **pushed** → `origin/main`.
7. **Theme-6 glass-detection hardening slice — 5 parallel subagents** (one per source file, file-disjoint, coding against frozen interfaces `AudioDetector.is_stale` / `VisualDetector.blinded` / `MQTTClient.set_detector_health`), then orchestrator-run full suite + runtime cross-file integration smoke test. — commit `22e6084` (local-only):
   - **Silent permanent blinding (headline T6 risk):** a sustained brightness shift (day/night, lights, camera move) made every frame an "anomaly" forever → visual detector dead with only a debug log. Now re-baselines after `>fps*3` consecutive anomalies and exposes a public `blinded` flag. Single-frame anomalies still return None (unchanged).
   - **Dead simulation path:** `--simulate` / MQTT `simulate_trigger` built the forced event in dead code *after* the trigger block → never encoded/enqueued (the only end-to-end node self-test was silently broken). Rewired to fire a processed event that bypasses cooldown.
   - **Stale-pairing correlation:** trigger fusion now requires BOTH detectors triggered within the window of *now* and resets timestamps after firing (a stale visual could previously pair with a fresh audio).
   - **Observability (also feeds T5):** heartbeat now carries `visual_health`/`audio_health` (telemetry-only) via `set_detector_health()`; main loop emits it + a throttled WARNING when the node is in a can-never-alert state (audio disabled/stale, visual paused/blinded).
   - **Reliability:** camera reopen re-applies resolution/fps (was silently degrading detection); real-time audio ring-buffer write vectorized (overrun→dropped-audio risk) + `is_stale()` liveness.
   - Tests: edge_glass **63 → 96** (new `test_trigger_engine`, `test_mqtt_heartbeat`, `test_main_helpers`, + audio/visual recovery & staleness). Full repo **188 pass**.
8. **Detector-health consume-side — 2 parallel subagents** (file-disjoint: backend Python vs SPA `.jsx`, coordinating via a frozen field contract), then orchestrator-run server suite + SPA review. — commit `3c110a2` (local-only). Closes the T6 observability loop: the edge was emitting `visual_health`/`audio_health` into the void.
   - **Server:** `mqtt_service._handle_heartbeat` stores the two fields (node_states + `metadata` JSON — **no migration**); `NodeStatus` + both `/api/nodes` construction sites expose them. Mirrors the existing `buffer_health` REST path. (`broadcast_node_status` is dead code → WS left untouched.)
   - **SPA:** `mapNode` normalizes + **folds detector health into node status** (online camera that is blinded/paused/disabled/stale → `warn`); new shared `<DetectorHealth>` atom renders 視覺/音訊 pills (camera-only) in NodeCard + NodeSidePanel; `detectorHealthMeta` labels (正常/已暫停/已致盲/訊號停滯/未啟用/未知).
   - Tests: central_server **44 → 47** (heartbeat stores fields; `/api/nodes` surfaces them incl. a blinded/disabled node). Full repo **191 pass**. SPA has no JS harness → verified by review.
9. **Theme-4 data-lifecycle — 2 parallel subagents** (file-disjoint: retention engine vs `weather_config`), then orchestrator-run server suite + review of the destructive orphan-sweep + delimiter regression test. — commit `dddda76` (local-only).
   - **Retention delimiter ~24h boundary bug:** `events.created_at` is stored space-delimited (SQLite `CURRENT_TIMESTAMP`) but was compared lexicographically against a `T`-delimited `isoformat()` cutoff → up to a day of boundary events mis-classified. Normalized both sides with SQLite `datetime()` in `retention_service.py` (SELECT+DELETE) and the two exported-but-unused `event_service.py` helpers.
   - **`pump_readings` never pruned** (unbounded on a 24/7 pump) → now pruned by `datetime(timestamp)`, guarded for older DBs.
   - **Orphaned MP4s** → fail-safe sweep (surviving-ref set built post-DELETE, path-normalized, mtime guard, any uncertainty keeps the file).
   - **`weather_config` wiped every PG startup** (`database.py` unconditional `UPDATE … NULL`) → removed; fresh installs still default empty; SQLite schema-guarded path untouched.
   - Tests: central_server **47 → 54** (delimiter boundary regression that fails on revert, pump prune + missing-table, orphan sweep, weather_config re-init persistence + PG-source guard). Full repo **198 pass**.

---

## 2. Executive summary (current)

| Dimension | State |
|---|---|
| **Production MVP** | Deployed on LAN (Pi 5 central + Pi 5 glass edge), SQLite/WAL. See root `MEMORY.md`. |
| **`main`** | @ `dddda76`, **1 ahead of `origin`** (T4 slice unpushed). Carries pump reconstruction + V2 SPA + all 2026-07-13 fixes + T6 hardening + detector-health end-to-end + T4 data-lifecycle. |
| **Pump-merge SDD effort** | ✅ **Complete, reviewed, merged, pushed.** ⚠️ **Not bench-commissioned on hardware** (spec §6). |
| **V2 SPA dashboard** | ✅ On `main` (~4,900 LOC `.jsx`). Two security items fixed; detector-health pills added; perf (blanket-refresh) still open (Theme 5). |
| **Theme-6 glass slice** | ✅ Silent-blinding, dead-simulate, stale-pairing, detector-health telemetry (`22e6084`, pushed) + **health now surfaced end-to-end in server/dashboard** (`3c110a2`, local). Remainder open (single-sensor fallback, blocking encode, buffer arithmetic). |
| **Theme-4 data-lifecycle** | ✅ Retention delimiter (~24h boundary), `pump_readings` pruning, orphan-MP4 sweep, `weather_config` PG-startup wipe all fixed (`dddda76`, local). Remainder: retention PG-portability, unverified backups. |
| **Tests** | **198 passing** (48 pump + 54 server + 96 glass). Zero failures. |
| **Biggest remaining risk** | **Hardware commissioning gate** for pump sensors (spec §6) + **Theme 1** (PG data-access 500s) if/when cloud cutover happens. Neither affects the deployed SQLite LAN MVP. |

**One-line status:** Pump-merge is shipped and green on `main`; the next work is the open reconstruction themes and the pump hardware bench pass — not fixing what shipped.

---

## 3. Verified test status (run 2026-07-13, on `main`)

| Suite | Command | Result |
|---|---|---|
| edge_pump | `cd edge_pump && python -m pytest tests -q` | **48 passed** |
| central_server | `cd central_server && python -m pytest tests -q` | **54 passed** (deprecation warnings) |
| edge_glass | `cd edge_glass && python -m pytest tests -q` | **96 passed** |
| **Total** | | **198 passed, 0 failed** |

Environment: Python **3.14.3**, pytest **9.0.2**, FastAPI **0.135.1**. (+33 edge_glass tests from the T6 slice: vectorized-ring + staleness, anomaly recovery, trigger_engine correlation/reset, heartbeat detector-health, main-loop health helpers.)

> **pytest + `[Cloud]` path trap:** a bare `pytest` from `edge_glass/` fails with *"path cannot contain [] parametrization"* — pytest parses the `[Cloud]` in the rootdir as a test-id. Pass an explicit **relative** test path instead: `python -m pytest tests -q`.

**Caveats worth fixing (unchanged):**
1. **edge_glass import convention diverges** — bare imports (`from utils…`, `from detectors…`) only collect when `edge_glass/` is the CWD; `pytest` from repo root fails collection. CI/portability trap; normalize so one command runs all three suites.
2. **`datetime.utcnow()` deprecation** — 29 call sites in `central_server`; scheduled for removal. Migrate to `datetime.now(datetime.UTC)` before a Python bump.

---

## 4. Module inventory (verified line counts)

| Module | .py files | LOC (src+tests) | Test result | Notes |
|---|---|---|---|---|
| `edge_pump` | ~19 | ~1,500 | 48 ✅ | Reconstructed: pure `control_logic.decide()`, `sensors` HAL, thin guarded `main`. `water_sensor.py` removed. Desktop-testable. |
| `central_server` | ~28 | ~7,600 | 44 ✅ | FastAPI + SQLite/WAL (PG path present but see Theme 1). 8 API routers, 6 services. |
| `edge_glass` | 25 | ~5,550 | 63 ✅ | Detection (audio/visual/trigger), buffer, comms, MP4, snapshot, thermal, RTSP. Not touched by pump-merge. |
| SPA (`static/spa`) | 8 `.jsx` + vendor | ~4,900 | no automated test | React/Babel/Tailwind in-browser. **No headless JSX test harness** — a real gap; SPA-side fixes are verified by reasoning + server-side tests only. |

---

## 5. Pump-Merge SDD effort — ✅ COMPLETE, MERGED, PUSHED (un-commissioned)

Spec: `docs/superpowers/specs/2026-07-10-pump-merge-and-reconstruction-design.md`
Plan: `docs/superpowers/plans/2026-07-10-pump-merge.md`
Detail ledger: `.superpowers/sdd/progress.md` (11 tasks + final-review record + merge outcome)

All 11 tasks landed review-clean; the final whole-branch review + fixes are done; merged and pushed. Delivered:
- Pure, hardware-free `control_logic.decide()` — full safety ladder (guarded conflict override w/ bounded bursts + 15-min ceiling, dry-run interlock, max-runtime duty cycle, rain threshold-lowering, dry-off delay) with exhaustive desktop tests.
- New `sensors.py` HAL (debounced digital + analog median, per-sensor `valid`/`None` degradation, injectable reader).
- Thin **guarded** `main.py`: WDT-on-by-default (fed only after a successful iteration), guarded init, fixed missing `machine` import, `ticks_ms` throttle, LWT before connect, bounded socket I/O.
- Extended MQTT payload end-to-end: server parsing, 2 new `pump_readings` columns (SQLite + PG), WebSocket push, SPA pump card (rain / dry-run / **sensor-conflict CRITICAL**).
- **Theme-5 first slice:** asyncio loop captured in the FastAPI lifespan → pump broadcasts from the paho thread no longer swallowed.
- **Deploy path corrected** (final review): provisioning now copies the new modules; node boots.

**Remaining gates before FIELD-ENABLING the new sensors:**
- ⚠️ **Hardware bench commissioning (spec §6)** — student sketch, wiring doc, and toolkit pinout disagree on pin/polarity. Bench-verify per-sensor polarity + raw ADC at known dry/full states. Manual rollout step, **not done**.
- ⚠️ **No automated frontend test** for the pump-card indicators.
- ✅ Safe incremental rollout is possible: sensors default OFF (`FLOAT_ENABLED`/`RAIN_ENABLED`), reproducing current analog-only behavior.

---

## 6. Six-theme reconstruction tracker

The pump-merge spec's Appendix decomposed a full audit into six themes. Pump-merge shipped the pump slice + one observability slice; the Theme-2 security slice (2 items) shipped 2026-07-13. Current status (✓ = re-verified by code read):

| # | Theme | Status | Detail |
|---|---|---|---|
| 1 | **Unify data-access layer** | 🔴 **Open** (latent) | ✓ `_init_postgresql()` sets `_pg_database` but never `_db_connection`; `get_db()` raises when `None`. **16 call sites** → cloud alert-response (ack/resolve/bulk/handover/audit) **500s in PG mode**. **SQLite LAN MVP unaffected.** Gate any PG/cloud cutover on this. |
| 2 | **Close the trust boundary** | 🟡 **Advancing** | ✅ `SECRET_KEY`/`EDGE_API_KEY`/dashboard creds `required=True` (fail-closed). ✅ **NEW 2026-07-13:** SPA inline-script injection fixed (`55b351a`), spoofable `resolved_by` fixed (`4a8bdc2`). 🔴 Still open: placeholder detection warn-only, shared static `EDGE_API_KEY` (no per-node creds), MQTT ACL, path-traversal via unvalidated `node_id`, public snapshot leak, login throttle, cookie `Secure`/`SameSite`, constant-time compare. |
| 3 | **Harden edge offline-autonomy** | 🟡 **Pump done, glass open** | ✅ Pump slice delivered (WDT, bounded I/O, guarded init, LWT). ⚠️ Truly bounding `umqtt connect()` needs a socket-factory/library change + hardware (deferred). 🔴 Glass slice open: blocking initial `connect()`; event data destroyed when MP4 cleanup deletes pending files then marks `UPLOADED`; 4xx → infinite tight-retry. |
| 4 | **Data-lifecycle correctness** | 🟡 **Advancing** (`dddda76`) | ✅ **Fixed 2026-07-13:** retention delimiter ~24h boundary error (`datetime()`-normalized both sides in retention_service + event_service helpers); `pump_readings` now pruned (was unbounded); orphaned-MP4 fail-safe sweep added; `weather_config` no longer wiped on every PG startup. 🔴 Remaining: retention is SQLite-only (`datetime()` — PG portability, ties to T1); "trusted client timestamps" = non-issue for event retention (uses server `created_at`) but `pump_readings` prunes by edge-supplied `timestamp` (edge-clock trust, accepted); unverified backups. |
| 5 | **Make observability real** | 🟡 **Advancing** | ✅ MQTT-thread WS loop-capture fixed (pump card live). ✅ **2026-07-13:** glass heartbeat emits `visual_health`/`audio_health` + throttled can-never-alert WARNING (`22e6084`), and the **server/dashboard now consume + surface them** (`3c110a2`) — a blinded-but-online camera shows 視覺/音訊 pills + `warn` status. 🔴 Remaining: SPA blanket-`refresh()` + N+1 fetch request storm; broadcast head-of-line blocking; offline-mark TOCTOU false alarm; LWT for glass; `broadcast_node_status` is dead code (live node-health push not wired — REST-refresh only). |
| 6 | **Detection correctness (glass)** | 🟡 **Advancing** (`22e6084`) | ✅ **Fixed 2026-07-13:** silent permanent visual blinding (sustained-anomaly re-baseline + `blinded` flag); dead `--simulate`/`simulate_trigger` path (now fires a processed, cooldown-bypassing event); stale-pairing fusion (both-within-window-of-now + reset-after-fire); detector-health telemetry + audio ring-buffer vectorization + camera-reopen settings + audio staleness; **detector health now surfaced operator-facing end-to-end** (`3c110a2` — see T5). 🔴 Remaining: AND-only fusion w/ no single-sensor fallback (design); post-crack persistent-visual + storm-audio phantom hardening beyond cooldown; blocking MP4 encode on the main loop; buffer-size arithmetic unconfirmed. |

**Each open theme is its own spec → plan → implementation cycle.** Appendix severities assume the internet-exposed PostgreSQL/EMQX deployment; several drop a notch on the LAN + SQLite Pi deployed today.

---

## 7. Cross-cutting tech debt

1. **`datetime.utcnow()` × 29** in `central_server` — deprecated; migrate to `datetime.now(datetime.UTC)`. (§3)
2. **edge_glass test portability** — bare imports break `pytest` from repo root; no single top-level command runs all suites. (§3)
3. **No headless JSX test harness** — SPA changes are verified by reasoning + server-side tests only. Consider a minimal DOM/JSDOM harness before further SPA work.
4. **Ledgers are load-bearing** — debt is tracked in docs (this file + `.superpowers/sdd/progress.md`) rather than inline TODOs; keep them current.

---

## 8. Deferred / needs explicit go-ahead

| Item | Note |
|---|---|
| **Pump hardware bench commissioning** | Spec §6. **Blocks field-enabling** the new sensors. Not started. Ship analog-only until done. |
| Weather UI | Backend shipped 2026-05-03 (gated by `CWA_API_KEY`); SPA already references weather (36 `.jsx` hits). Confirm remaining scope vs "done". |
| ESP32 battery firmware | Battery/power telemetry preserved in payload (`_read_power`); on-device sensing firmware pending. |
| MQTT downlink of snooze config to edge | Server-side snooze exists; downlink not built (spec excludes a cmd topic). |
| Untracked docs | `docs/sdpr_UI_V2.zip`, `docs/sdpr_UI_V2/`, `docs/ui_redesign_v2_prompt.md` — decide: commit or `.gitignore`. |

---

## 9. Recommended next actions (prioritized)

1. ~~Merge `spec/pump-merge → main`~~ ✅ **DONE 2026-07-13** (merged + pushed).
2. **Bench-commission the pump sensors** (spec §6) before enabling `FLOAT_ENABLED`/`RAIN_ENABLED`. Highest-value remaining pump action.
3. ~~Theme 6 silent detector-blinding~~ ✅ **DONE** (`22e6084`+`3c110a2`). ~~Theme 4 retention/pruning~~ ✅ **DONE 2026-07-13** (`dddda76` — delimiter, pump_readings, orphans, weather_config). **Next deployed-today candidates:** Theme 5 remainder (SPA blanket-`refresh()` + N+1 fetch request storm on the 24/7 dashboard) and Theme 6 remainder (single-sensor fallback design, blocking MP4 encode, buffer arithmetic). Theme 2 auth-hardening remains for a security-focused cycle.
4. **Theme 2 remainder** — the 2 highest-risk SPA/auth items are fixed; the rest (per-node creds, MQTT ACL, `node_id` allowlist, authenticated snapshot/storage, login throttle, cookie flags) as a focused auth-hardening cycle.
5. **Theme 5 remainder** — SPA blanket-refresh request storm (24/7 dashboard load).
6. **Gate any PostgreSQL/cloud cutover on Theme 1.** Fine to defer on SQLite LAN; must be first if cloud is on the roadmap.
7. **Housekeeping:** normalize the test harness (edge_glass imports → one repo-root `pytest`); migrate `datetime.utcnow()` before a Python bump.

---

*Method: directory + LOC census, live `pytest` runs, `git` divergence analysis, direct source re-verification, and a 9-subagent final whole-branch review + a 2-subagent security slice. Findings marked ✓ were confirmed by reading current code.*
