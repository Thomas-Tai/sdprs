# SDPRS — Codebase Audit & Progress Ledger

**Originally audited:** 2026-07-13 (evidence-based — every claim re-verified against the working tree)
**Last updated:** 2026-07-13 — *after* the pump-merge merge/push, the Theme-6 glass-hardening + detector-health slices, the Theme-4 data-lifecycle slice, the Theme-2 auth-hardening slice, the Theme-5 observability slice, **and the Theme-3 glass-autonomy slice**.
**Repo:** `sdprs/` (its own git repo; parent folder is not versioned) · **Remote:** `github.com/Thomas-Tai/sdprs`
**Current branch/commit:** `main` @ `73b5af0` (**1 ahead of `origin/main` @ `4f0dd9d`** — T3 glass-autonomy slice `73b5af0` not yet pushed; T5 slice `9d3f063` + its docs `4f0dd9d` already pushed)

> Canonical, living progress tracker for the `docs/superpowers` workstream.
> Task-by-task execution detail for the pump-merge effort lives in
> `.superpowers/sdd/progress.md` (SDD ledger). This file is the higher-altitude
> project-state view and the reconstruction-theme tracker.

---

## 0. Current status (headline)

**The pump-merge reconstruction + the entire V2 SPA are merged to `main` and pushed to origin.** `main` had been idle since 2026-05-09; it is now the live line again. The two security defects that rode in with the SPA baseline have been fixed. **The Theme-6 glass-hardening slice is pushed, and its detector-health telemetry is now wired end-to-end** — edge heartbeat → server `/api/nodes` → SPA — so a blinded-but-online camera shows 視覺/音訊 health + degraded status to operators. **Theme-4 data-lifecycle correctness (retention delimiter, pump_readings pruning, orphan-MP4 sweep, weather_config persistence) is pushed** (`dddda76`). **Theme-2 auth-hardening (UTF-8-safe constant-time credential compares, per-IP login throttle, hardened session-cookie flags, edge node_id allowlist, MQTT auth+ACL deploy template) is pushed** (`c4660d8`). **Theme-5 observability (dashboard request-storm coalescing, WS broadcast head-of-line-blocking fix, offline-mark TOCTOU false-alarm fix) is pushed** (`9d3f063`). **Theme-3 glass offline-autonomy (upload gives up on 4xx / after max retries instead of retrying forever, MP4-missing marks FAILED not false-UPLOADED, non-blocking MQTT connect at boot) is now committed** (`73b5af0`, local-only). **236 tests pass** (edge_pump 48 · central_server 81 · edge_glass 107).

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
10. **Theme-2 auth-hardening — 5 parallel subagents** (file-disjoint: `auth.py` · `main.py` · `config.py` · `alerts.py`+`snapshots.py` · `deploy/`), coordinating via a frozen contract (4 new config settings + `auth.verify_node_id()` + `authenticate_user()`), then orchestrator-run full suite + review of the two highest-risk files. — commit `c4660d8` (local-only).
    - **Timing-safe credential compares:** every secret compare (`EDGE_API_KEY` ×3 sites, dashboard user+password) went through plain `==`/`!=`, leaking length/prefix timing. All routed through a new `_ct_equal()` that compares **UTF-8 bytes** via `secrets.compare_digest` — bytes form is deliberate: `compare_digest` on non-ASCII `str` raises `TypeError`, and this is a Traditional-Chinese deployment where `DASHBOARD_PASS` may be non-ASCII (would otherwise 500 a valid login). `authenticate_user` evaluates both fields unconditionally (no short-circuit) so timing can't reveal which was wrong.
    - **Login brute-force throttle:** per-client-IP monotonic-clock failure counter (`LOGIN_MAX_ATTEMPTS`/`LOGIN_LOCKOUT_SECONDS`); on lockout returns 429 **without** checking creds; success clears the counter. Per-process/in-memory (documented; fine for the single-worker MVP). Login now flows through the constant-time `authenticate_user` instead of an inline `==`.
    - **Session-cookie flags:** `same_site="lax"` (CSRF) + `https_only=COOKIE_SECURE` (default False keeps HTTP-LAN working; flip once TLS-fronted). `httponly` is Starlette's default.
    - **Edge node_id allowlist (trust boundary):** `verify_node_id()` gates `POST /api/alerts` (client-supplied node_id) and `POST /api/edge/{node_id}/snapshot` (path). `ALLOWED_NODE_IDS` empty by default ⇒ allow-all ⇒ **backward compatible** with the current single-node deploy; set it to lock ingest to known nodes. Video-upload path left alone (node_id there is server-derived, already trusted).
    - **MQTT broker hardening (deploy):** `mosquitto.conf` gains a loud secure-mode block (password_file + acl_file + `allow_anonymous false`, commented so the running anonymous-LAN MVP isn't broken); new `mosquitto_acl.conf` least-privilege template (server `readwrite sdprs/edge/#`; per-node write-own / read-cmd) + `MQTT_SECURITY.md` operator guide. **No secrets committed**; ACL topics grounded on `shared/mqtt_topics.py` but flagged as reconcile-before-enable.
    - **Test-isolation catch (integration):** `test_node_allowlist.py` initially set `ALLOWED_NODE_IDS` at *import* scope, which pytest's collection phase leaked into sibling modules (broke `test_config_auth_settings`'s default-assert + an alerts test). Moved the env mutation into a module-scoped setup/teardown fixture (cache-cleared both ends) → isolation restored. Production code was never at fault.
    - Tests: central_server **54 → 72** (+18: auth-hardening 6, login-throttle 4, config-settings 2, node-allowlist 6). Full repo **216 pass**.
11. **Theme-5 observability — 3 parallel subagents** (file-disjoint: SPA `app.jsx` · `websocket_service.py` · `mqtt_service.py`), then orchestrator-run full suite + close read of the SPA change (no JS harness → review-verified). — commit `9d3f063` (local-only).
    - **Dashboard request-storm (headline, deployed-today):** the SPA called a full `refreshLive()` (4 GETs + one `/cycles` per pump, N+1) on **every** inbound WS message, with no debounce and no overlap guard → an alert burst multiplied into a request storm against the 24/7 dashboard. Introduced a shared **in-flight guard** + **300ms trailing-debounce**: WS events and the 20s safety-poll now funnel through one guarded runner so `refreshLive()` never runs concurrently and a burst collapses to a single refresh; exactly one trailing run fires if events arrive mid-flight (no missed state). User-action `refresh()` (ack/resolve/snooze) stays forced + awaitable + post-mutation-fresh.
    - **WS broadcast head-of-line blocking:** `broadcast()` awaited `send_json` **serially** per client → one slow/stalled client delayed (or with backpressure, stalled) delivery to all others. Now sends concurrently via `asyncio.gather` with a **5s per-client `wait_for` timeout**; a timed-out/failed client is isolated and removed instead of blocking the pool.
    - **Offline-mark TOCTOU false alarm:** `_check_offline_nodes` selected stale nodes under the lock, released it, then `_mark_node_offline` set OFFLINE **unconditionally** — a heartbeat arriving in the gap produced a false OFFLINE (spurious pump CRITICAL logs, operator noise, WS churn), flipped back ONLINE on the next beat (flapping). `_mark_node_offline` now **re-validates staleness under the lock** before committing; a fresh heartbeat aborts the transition. (Confirmed all `last_heartbeat` writers hold `self._lock`, so the re-check is race-free.)
    - Tests: central_server **72 → 81** (+9: `test_ws_broadcast` 4 — slow/failing/timed-out client isolation; `test_offline_detection` 5 — genuine-offline + fresh-beat-in-gap for glass & pump + already-offline/removed guards). SPA verified by review. Full repo **225 pass**.
12. **Theme-3 glass offline-autonomy — 2 parallel subagents** (file-disjoint: `comms/api_uploader.py` · `comms/mqtt_client.py`), then orchestrator-run edge_glass suite + review of the uploader branch ordering. — commit `73b5af0` (local-only).
    - **Upload retried forever on 4xx (headline):** on a 4xx the worker returned WITHOUT `increment_retry` and WITHOUT changing status, so `get_pending()` re-served the row **every 1s forever** (no backoff, never drains) — pure log-spam + a queue that never empties. Now: 4xx (non-429) → terminal `FAILED` immediately (won't self-heal); 429 → treated as transient (backoff); 5xx / timeout / connect-error → backoff **and give up to `FAILED` after `MAX_RETRIES=10`** (there was no cap at all before). **Interacts with the T2 node_id allowlist:** a 403 for an unlisted node no longer spins endlessly. `FAILED` is excluded by `get_pending` so it's terminal with no schema change.
    - **MP4-missing falsely marked UPLOADED:** when the local clip was gone, the worker set status `UPLOADED` — signalling success though nothing uploaded (server alert stuck `PENDING_VIDEO`, clip silently lost). Now marks `FAILED` (honest terminal).
    - **Blocking MQTT connect at boot:** `start()` used the blocking `connect()` and **early-returned on failure before `loop_start()`** — a broker momentarily down at boot left MQTT permanently dead (paho's configured `reconnect_delay_set(1..60s)` auto-reconnect never engaged). Now uses non-blocking `connect_async()` and **always** starts the loop, so the node recovers automatically when the broker returns.
    - **Deliberately deferred (NOT rushed into this batch):** the blocking **MP4 encode** on the capture loop + **buffer arithmetic**. The correct fix (post-trigger frames from the circular buffer + off-thread encode) is coupled to the unconfirmed buffer sizing AND to camera-thread-safety (`record_post_trigger` reads the same `VideoCapture` as the main loop), and can't be validated without hardware — it needs a dedicated design pass, not an unvalidatable parallel edit.
    - Tests: edge_glass **96 → 107** (+11: `test_api_uploader` 8 — 4xx/429/5xx-max/mp4-missing/happy; `test_mqtt_client_start` 3 — connect_async-not-blocking, loop-starts-even-when-connect-raises, idempotent). Full repo **236 pass**.

---

## 2. Executive summary (current)

| Dimension | State |
|---|---|
| **Production MVP** | Deployed on LAN (Pi 5 central + Pi 5 glass edge), SQLite/WAL. See root `MEMORY.md`. |
| **`main`** | @ `dddda76`, **1 ahead of `origin`** (T4 slice unpushed). Carries pump reconstruction + V2 SPA + all 2026-07-13 fixes + T6 hardening + detector-health end-to-end + T4 data-lifecycle. |
| **Pump-merge SDD effort** | ✅ **Complete, reviewed, merged, pushed.** ⚠️ **Not bench-commissioned on hardware** (spec §6). |
| **V2 SPA dashboard** | ✅ On `main` (~4,900 LOC `.jsx`). Two security items fixed; detector-health pills added; perf (blanket-refresh) still open (Theme 5). |
| **Theme-6 glass slice** | ✅ Silent-blinding, dead-simulate, stale-pairing, detector-health telemetry (`22e6084`, pushed) + **health now surfaced end-to-end in server/dashboard** (`3c110a2`, local). Remainder open (single-sensor fallback, blocking encode, buffer arithmetic). |
| **Theme-4 data-lifecycle** | ✅ Retention delimiter (~24h boundary), `pump_readings` pruning, orphan-MP4 sweep, `weather_config` PG-startup wipe all fixed (`dddda76`, pushed). Remainder: retention PG-portability, unverified backups. |
| **Theme-2 auth-hardening** | ✅ UTF-8-safe constant-time compares, per-IP login throttle, session-cookie flags, edge node_id allowlist, MQTT auth+ACL deploy template (`c4660d8`, pushed). Remainder: per-node API keys, auth'd snapshot/storage endpoints, WS-session tightening. |
| **Theme-5 observability** | ✅ Dashboard request-storm coalescing (debounce + in-flight guard), WS broadcast HOL-blocking fix (concurrent gather + per-client timeout), offline-mark TOCTOU false-alarm fix (`9d3f063`, pushed). Remainder: glass LWT, live node-health WS push (`broadcast_node_status` dead code), N+1 `/cycles` batch endpoint. |
| **Theme-3 glass autonomy** | ✅ Upload gives up on 4xx / after max retries (was infinite 1s retry), MP4-missing → FAILED not false-UPLOADED, non-blocking MQTT connect at boot (`73b5af0`, local). Remainder: **blocking MP4 encode + buffer arithmetic** (deferred — camera-thread-safety + hardware-timing, needs design pass). |
| **Tests** | **236 passing** (48 pump + 81 server + 107 glass). Zero failures. |
| **Biggest remaining risk** | **Hardware commissioning gate** for pump sensors (spec §6) + **Theme 1** (PG data-access 500s) if/when cloud cutover happens. Neither affects the deployed SQLite LAN MVP. |

**One-line status:** Pump-merge is shipped and green on `main`; the next work is the open reconstruction themes and the pump hardware bench pass — not fixing what shipped.

---

## 3. Verified test status (run 2026-07-13, on `main`)

| Suite | Command | Result |
|---|---|---|
| edge_pump | `cd edge_pump && python -m pytest tests -q` | **48 passed** |
| central_server | `cd central_server && python -m pytest tests -q` | **81 passed** (deprecation warnings) |
| edge_glass | `cd edge_glass && python -m pytest tests -q` | **107 passed** |
| **Total** | | **236 passed, 0 failed** |

Environment: Python **3.14.3**, pytest **9.0.2**, FastAPI **0.135.1**. (edge_glass +11 from the T3 slice: uploader 4xx/max-retry give-up + non-blocking MQTT connect; +33 earlier from T6. central_server +9 from T5, +18 from T2 auth, +7 from T4. The SPA `app.jsx` change has no automated test — verified by review.)

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
| 2 | **Close the trust boundary** | 🟡 **Advancing** (`c4660d8`) | ✅ `SECRET_KEY`/`EDGE_API_KEY`/dashboard creds `required=True` (fail-closed). ✅ SPA inline-script injection fixed (`55b351a`), spoofable `resolved_by` fixed (`4a8bdc2`). ✅ **NEW 2026-07-13 (`c4660d8`):** UTF-8-safe **constant-time** compares (`_ct_equal`) for API key + dashboard creds; **login throttle** (per-IP, 429 lockout); cookie **`SameSite=Lax` + `Secure` (COOKIE_SECURE)**; **`node_id` allowlist** on alert/snapshot ingest (`ALLOWED_NODE_IDS`, empty=allow-all); **MQTT auth+ACL** deploy template (`mosquitto_acl.conf` + `MQTT_SECURITY.md`, secure-mode block in `mosquitto.conf`). 🔴 Still open: placeholder detection warn-only, shared static `EDGE_API_KEY` (no **per-node** creds), authenticated snapshot/storage endpoints (snapshot GET still public for `<img>`), storage-path traversal review, WS-session tightening. |
| 3 | **Harden edge offline-autonomy** | 🟡 **Pump done, glass advancing** (`73b5af0`) | ✅ Pump slice delivered (WDT, bounded I/O, guarded init, LWT). ✅ **NEW 2026-07-13 (`73b5af0`):** glass upload gives up on 4xx / after `MAX_RETRIES` (was infinite 1s retry — now interacts safely with the T2 403 allowlist); MP4-missing → terminal `FAILED` instead of a false `UPLOADED` (silent clip loss); glass MQTT `start()` uses non-blocking `connect_async()` + always `loop_start()` so a broker down at boot self-recovers (was: early-return defeated paho auto-reconnect). 🔴 Remaining: **blocking MP4 encode on the capture loop + buffer arithmetic** (deferred — post-frames-from-buffer redesign needs camera-thread-safety + hardware validation); ⚠️ bounding `umqtt connect()` on the *pump* still needs a socket-factory/library change + hardware. |
| 4 | **Data-lifecycle correctness** | 🟡 **Advancing** (`dddda76`) | ✅ **Fixed 2026-07-13:** retention delimiter ~24h boundary error (`datetime()`-normalized both sides in retention_service + event_service helpers); `pump_readings` now pruned (was unbounded); orphaned-MP4 fail-safe sweep added; `weather_config` no longer wiped on every PG startup. 🔴 Remaining: retention is SQLite-only (`datetime()` — PG portability, ties to T1); "trusted client timestamps" = non-issue for event retention (uses server `created_at`) but `pump_readings` prunes by edge-supplied `timestamp` (edge-clock trust, accepted); unverified backups. |
| 5 | **Make observability real** | 🟡 **Advancing** (`9d3f063`) | ✅ MQTT-thread WS loop-capture fixed (pump card live). ✅ glass heartbeat emits `visual_health`/`audio_health` + throttled can-never-alert WARNING (`22e6084`), **server/dashboard consume + surface them** (`3c110a2`). ✅ **NEW 2026-07-13 (`9d3f063`):** SPA request-storm fixed (300ms debounce + in-flight guard — burst of WS events → one `refreshLive()`, never concurrent); WS broadcast **head-of-line blocking** fixed (concurrent `gather` + 5s per-client timeout, slow client isolated/removed); offline-mark **TOCTOU false alarm** fixed (re-validate staleness under lock). 🔴 Remaining: N+1 `/cycles` batch endpoint (secondary to the debounce); LWT for glass; `broadcast_node_status` is dead code (live node-health push not wired — REST-refresh only). |
| 6 | **Detection correctness (glass)** | 🟡 **Advancing** (`22e6084`) | ✅ **Fixed 2026-07-13:** silent permanent visual blinding (sustained-anomaly re-baseline + `blinded` flag); dead `--simulate`/`simulate_trigger` path (now fires a processed, cooldown-bypassing event); stale-pairing fusion (both-within-window-of-now + reset-after-fire); detector-health telemetry + audio ring-buffer vectorization + camera-reopen settings + audio staleness; **detector health now surfaced operator-facing end-to-end** (`3c110a2` — see T5). 🔴 Remaining: AND-only fusion w/ no single-sensor fallback (design decision); post-crack persistent-visual + storm-audio phantom hardening beyond cooldown; **blocking MP4 encode on the main loop + buffer-size arithmetic** (tracked under T3 — deferred as a coupled redesign needing hardware validation). |

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
3. ~~Theme 6 silent detector-blinding~~ ✅ **DONE** (`22e6084`+`3c110a2`). ~~Theme 4 retention/pruning~~ ✅ **DONE** (`dddda76`). ~~Theme 2 auth-hardening (bulk)~~ ✅ **DONE** (`c4660d8`). ~~Theme 5 request-storm + WS HOL + offline TOCTOU~~ ✅ **DONE** (`9d3f063`). ~~Theme 3 glass 4xx-retry + MQTT-connect-at-boot~~ ✅ **DONE 2026-07-13** (`73b5af0`). **Next candidate (needs a design pass, NOT a quick parallel edit): the blocking MP4 encode + buffer-window redesign** — decouple post-trigger frames from the camera (feed the encode worker from the circular buffer) + confirm buffer sizing (pre+post ≤ duration); best done with hardware in the loop. After that, the remaining items are cloud-gated (T1) or design decisions (single-sensor fallback).
4. **Theme 2 remainder** — bulk auth-hardening shipped (`c4660d8`); what's left is the heavier/coupled work: **per-node** API keys (registry + edge coordination, breaks single-key deploy), authenticated snapshot/storage endpoints (snapshot GET is still public for `<img>` tags), storage-path traversal review, WS-session tightening.
5. **Theme 5 remainder** — glass LWT, live node-health WS push (`broadcast_node_status` is dead code), N+1 `/cycles` batch endpoint. All secondary to the shipped debounce/HOL/TOCTOU fixes.
6. **Gate any PostgreSQL/cloud cutover on Theme 1.** Fine to defer on SQLite LAN; must be first if cloud is on the roadmap.
7. **Housekeeping:** normalize the test harness (edge_glass imports → one repo-root `pytest`); migrate `datetime.utcnow()` before a Python bump.

---

*Method: directory + LOC census, live `pytest` runs, `git` divergence analysis, direct source re-verification, and a 9-subagent final whole-branch review + file-disjoint parallel-subagent slices (T2 security ×2, T6 glass ×5, detector-health ×2, T4 lifecycle ×2, T2 auth-hardening ×5, T5 observability ×3, T3 glass-autonomy ×2). Findings marked ✓ were confirmed by reading current code.*
