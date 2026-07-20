# Dashboard audit fix phase — ROUND 2 LANDED + VERIFIED, tree is green

**Round 1: 100 fixed / 11 partial / 2 correctly deferred / 10 not fixed** (table below).
**Round 2 (closing the 21 open findings) is now FULLY LANDED and per-finding VERIFIED.**

Nothing committed. Branch `fix/dashboard-audit-2026-07-20`.

---

# ROUND 2 — COMPLETE 2026-07-20 (resumed + finished). READ THIS FIRST.

Six file-disjoint agents closed the 21 open findings across two dispatch waves.
The first wave was paused mid-flight (3 landed, 3 landed nothing); the second
wave re-dispatched the three that landed nothing plus the not-started backend,
and the merge engineer landed the two `app.jsx`-owned halves by hand. **All 21
open findings are now landed and code-verified. The tree is no longer half-wired
— every new backend/data-layer field is now consumed by UI.**

## Verification state — ALL GREEN (authoritative, full consolidated tree)

| Gate | Result |
|---|---|
| SPA syntax (13 files) | PASS — all compile |
| Undefined references | PASS — only the 5 known false positives (`SIZE`, `useMemo`/`useCallback`, `bootstrap`×2) |
| SPA classes (advisory) | 18 suspect — all pre-existing, none new |
| `test_nodes_api.py` | 26 passed |
| `test_handle_pump_status.py` | 7 passed |
| `edge_pump/tests/test_manual_override.py` | 10 passed |
| `test_dual_backend_dispatch.py` (handover DB layer) | 23 passed |
| `test_ws_broadcast.py` / `test_ws_loop_capture.py` | 4 / 2 passed |

Two suites still fail **pre-existing** (proven at HEAD): `test_ws_event_contract`
(2 — `node_deleted` drift in the test's own `EXPECTED_ALL_TYPES`) and
`test_node_allowlist` (4 — `EDGE_API_KEY` fixture). Not regressions.

Gates live in the session scratchpad
(`check_spa_syntax.js`, `check_spa_refs.js`, `check_spa_classes.js`). Recreate
from the recipe below only if actually missing.

## The half-wiring is now CLOSED (was the thing to watch)

Every field the backend/data layer exposed is now consumed:

- `nodes.py` `manual_override` / `api.jsx` `manualOverride` → pumps.jsx MSP-F6
  banner + 「恢復自動」 release, MSP-F5 last-command line renders `lastPumpCommand`.
- `api.jsx` `getStreamHealth()` → status.jsx `StreamRowButton` (MSP-F7) is its caller.
- `node.cyclesAlert` (the clean mapped boolean, NOT `n._cycles.alert`) → pumps.jsx
  API-F9 short-cycle banner.

## LANDED and verified — ROUND 2 (do not redo)

Every item below was independently code-verified by the merge engineer (diff
read, contract cross-check, or grep of the actual behaviour — not the agent's
report alone), plus the gates + backend suites above.

- **A (backend):** WHA-M8 handover 409 (`handover.py` — optional
  `expected_updated_at`, flat `{detail,current,updated_at}` 409, backward
  compatible, returns new `updated_at`); API-F6 WS session re-validation
  (`websocket_service.py` — `_session_revalidation_loop` every 45s, reuses
  `_get_session_user`, sends existing `auth_expired` + 1008 close, independent task).
- **B (pumps/status):** MSP-F6 `releaseAuto()`+banner (sev-critical/warn render
  confirmed via config DEFAULT key), MSP-F5 `lastPumpCommand` line, MSP-F18
  API/`pumpId` existence guard (status.jsx has no `pumpCommand` call — mirrored
  SnoozeRowButton's guard instead), MSP-F7 `getStreamHealth` in an effect +
  `inFlightRef` double-fire latch, MSP-F22 `FilterChip.onClear` (non-interactive
  span + `stopPropagation`), API-F9 `cyclesAlert` banner.
- **C (alerts/components):** API-F2 honest manual-mute relabel, ALR-L1 dead
  presence badges removed, ALR-L5 flash-dedupe hoisted to
  `window.__SDPRS_FLASHED_ALERT_IDS`, ALR-L7 dead snapshot branches removed,
  CMP-F11 palette emits `onCmd(`node:${it.id}`)` (`components.jsx:1729`),
  CMP-F17 `已認領`/`由` localized.
- **D (handover client):** WHA-M8 client — `loadHandover` exposes raw `updatedAt`,
  `saveHandover(note, expectedUpdatedAt)` lifts the 409 body (via `apiFetch`'s
  pre-existing `err.body`) to `e.conflict/e.current/e.updatedAt`, `handover.jsx`
  ConflictDialog (keep-server / overwrite-with-new-token / cancel).
- **E (weather/audit/tweaks):** WHA-M3 `configLoadFailed` banner + `canSave` gate,
  WHA-L1 CSV export cutoffs = `Math.max` of the same `_shiftFloor`/`inDateRange`
  cutoffs the on-screen `records` predicate uses, WHA-L8 `TweakNumber` local text
  state tolerating empty mid-edit.
- **app.jsx (merge engineer):** CMP-F11 `node:` branch in `onCmdkCommand`
  (`setNodePanelNodeId(id.slice(5))`); ALR-L5 effect pruning BOTH `ackedIds` and
  `window.__SDPRS_FLASHED_ALERT_IDS` to the live-alert id set.

**Still hardware-day gated:** MSP-F6's `AUTO` release path has still never run
against a physical pump — validate on hardware day. See [[todo_hardware_day]].
**Open question the user never answered:** the API-F7 keep-alive is
activity-gated, so a NOC wall display counts as abandoned and WILL expire.

## LANDED and verified — pre-pause wave (do not redo)

- **MSP-F6 (pump hold release) — backend + firmware.** `PumpCommandRequest.action`
  now `^(ON|OFF|AUTO)$` (`nodes.py:638`); `manual_override` ingested via
  `mqtt_service.py` and exposed on `NodeStatus` (`nodes.py:87`) in **both**
  `list_nodes` (`:326`) and `get_node` (`:523`); `edge_pump/main.py` handles
  `AUTO` explicitly. Tests green.
- **MSP-F5 (cross-operator visibility) — backend.** `last_pump_command`
  (`nodes.py:94`), batched via `_last_pump_commands()` (`:132`) — one query, not
  one per node. Populated in both endpoints (`:327`, `:524`).
- **API-F1 latent landmine — FIXED.** `weather_service.py:386` is now
  `"timezone": "UTC"` (was `Asia/Macau`). This was the trap found during
  verification, not in the original audit.
- **API-F12 (narrow).** `weather_service.py` tz-aware timestamps converted to
  naive UTC. `nodes.py`/`alerts.py` manual-`'Z'` sites deliberately untouched.
- **api.jsx data layer:** SHL-18 (`ts` is now real ms — all four `audit.jsx`
  consumers verified compatible, no change needed there), API-F3
  (`getStreamHealth`, `:942`), API-F7 (`extendSession`, `:1094`), API-F9
  (`_cycles.alert`), MSP-F5/F6 field mapping (`:345`, `:353`).
- **API-F7 scheduling half — app.jsx, done by hand.** Activity-gated, not a bare
  timer: extends only if the operator interacted since the last extend
  (`pointerdown`/`keydown`, not `mousemove`). A plain interval would keep an
  abandoned browser authenticated forever and defeat the expiry entirely.
  **Open question for the user:** a NOC wall display counts as abandoned under
  this rule and WILL expire. Judged correct (a login screen on the wall is
  visible and fixable; an eternally-authenticated wall panel is an unwatched
  credential) — but the user was asked and has not answered.

## NOT STARTED — ALL NOW COMPLETE (kept for the re-dispatch record)

Everything in this section was closed in the resumed second wave (2026-07-20).
Left here only as the record of what was re-dispatched; see the ROUND 2 LANDED
list above for the verified result.

- **Backend:** WHA-M8 (`handover.py`) and API-F6 (`websocket_service.py`) — DONE.
- **Frontend:** pumps/status (MSP-F6/F5/F18/F7/F22/API-F9), alerts/components
  (API-F2/ALR-L1/L5/L7/CMP-F11/F17), weather/audit/handover/tweaks
  (WHA-M8 UI/M3/L1/L8) — DONE.
- **app.jsx (merge engineer):** CMP-F11 `node:` branch and ALR-L5 prune — DONE
  (the ALR-L5 prune covers BOTH `ackedIds` and the hoisted
  `window.__SDPRS_FLASHED_ALERT_IDS`).

## Frozen contract — reuse VERBATIM when re-dispatching

```
PumpCommandRequest.action      : "ON" | "OFF" | "AUTO"     # AUTO rejects duration_s
NodeStatus.manual_override     : "ON" | "OFF" | null
NodeStatus.last_pump_command   : {action, by, at} | null    # at = naive UTC ISO
mapNode → manualOverride       : 'ON'|'OFF'|null
mapNode → lastPumpCommand      : {action, by, at:Date|null}|null
window.SDPRS_API.pumpCommand(nodeId, 'AUTO')
window.SDPRS_API.getStreamHealth(nodeId)
window.SDPRS_API.extendSession()
node._cycles.alert             : boolean
mapAuditRow.ts                 : number (ms)  — NOT a Date
handover GET → updated_at ; PUT ← expected_updated_at ; 409 on mismatch
```

## Why MSP-F6 was cheaper than expected (keep this — it is non-obvious)

It had been filed as "needs backend work + a hardware day." It did not.
`edge_pump/main.py:125-126` already documented **"Unknown action — ignore, clear
the slot to avoid a stuck state"** — clearing the slot IS return-to-automatic.
So sending `AUTO` releases a hold on **already-deployed firmware with no
reflash**; `send_pump_command` passes `action` through verbatim and the only
blocker was the Pydantic pattern. The firmware now also handles `AUTO`
explicitly, backward-compatibly. **Still validate on hardware day** — the
release path has not been exercised against a physical pump.

---

# ROUND 1 — verification results (complete)

2026-07-20. Branch `fix/dashboard-audit-2026-07-20`. **Nothing committed.**

Findings source: `dashboard_ui_audit_2026-07-20.md` (same directory).
18 files changed, +2768 / -606.

> **Correction (2026-07-20).** This document previously said "COMPLETE — all
> green." That claim was wrong, and the way it was wrong matters more than the
> claim itself. The four gates below verify that the code *runs*. They verify
> nothing about whether any of the **123 audit findings** was actually
> addressed. Per-finding verification had never been performed when completion
> was declared. It is running now; see "Per-finding verification" below.

## Gate state — green, but narrow

These prove the tree is loadable and the backend suites pass. Necessary, not
sufficient. Do not read a green gate as a fixed finding.

| Gate | Result |
|---|---|
| SPA syntax (all 13 files compile under vendored Babel) | PASS |
| Undefined references (runtime ReferenceError class) | PASS |
| Tailwind token check | PASS (only hit is inside a comment) |
| Backend pytest, one suite at a time | 43 passed across 6 suites |

## Per-finding verification — COMPLETE

All 123 findings independently scored by six read-only agents (one per finding
domain), each required to cite file:line **behaviour**, never a comment naming
the finding ID.

| Domain | Findings | FIXED | PARTIAL | DEFERRED-OK | NOT-FIXED |
|---|---|---|---|---|---|
| MSP (monitor/status/pumps) | 27 | 22 | 3 | 0 | 2 |
| WHA (weather/handover/audit/tweaks) | 27 | 22 | 2 | 1 | 2 |
| SHL (shell/app/index) | 20 | 18 | 0 | 1 | 1 |
| ALR (alerts) | 19 | 16 | 2 | 0 | 1 |
| CMP (shared components) | 18 | 16 | 2 | 0 | 0 |
| API (contract/api) | 12 | 6 | 2 | 0 | 4 |
| **Total** | **123** | **100** | **11** | **2** | **10** |

**Do not commit as "audit complete."** 21 findings are open (11 PARTIAL +
10 NOT-FIXED), several safety-relevant. The branch is a large genuine
improvement, not a finished remediation.

### Open findings

NOT-FIXED (10): MSP-F6, MSP-F18, WHA-L1, WHA-L8, ALR-L1, SHL-18,
API-F3, API-F6, API-F7, API-F9.

PARTIAL (11): MSP-F5, MSP-F7, MSP-F22, WHA-M3, WHA-M8, ALR-L5, ALR-L7,
CMP-F11, CMP-F17, API-F2, API-F12.

DEFERRED-OK (2): WHA-M10, SHL-15 — genuinely no backend/data source AND
documented in code as such. These are correctly resolved.

### Safety-ranked open items

1. **MSP-F6 — NOT-FIXED, nothing exists.** No 「恢復自動」 control, no
   「手動停機中」 banner, no backend resume-to-automatic path. Grep of
   `pumps.jsx`/`nodes.py`/`mqtt_service.py` for
   `恢復自動|手動停機中|resume_auto|release_hold` → zero hits. An operator who
   holds a pump OFF to service it has no way to release it and no on-screen
   reminder it is still held. If rain returns after they go off-shift the pump
   stays commanded OFF. Audit put this in Phase 1, safety-critical.
2. **MSP-F5 — PARTIAL.** Device-ack hold is real (`pumps.jsx:72-83,151-164`).
   Cross-operator half absent: commands are audit-logged (`nodes.py:529,555`)
   but never read back for display. `lastOutcome` is local React state — invisible
   to a peer, lost on reload. Operator B can still override Operator A's hold.
3. **WHA-M8 — PARTIAL.** Handover clobber window narrowed ~20s → sub-second by
   awaiting `refreshLive()` before the conflict diff, but still last-write-wins
   with no 409. `central_server/api/handover.py` is untouched in this diff.
4. **API-F6 — NOT-FIXED, zero lines changed.** `websocket_service.py` is
   byte-identical to base; `session.get("user")` is checked once at connect
   (`:200`) and never re-validated. The only thing bounding exposure is a
   **pre-existing** client-side 2s poll of `__SDPRS_SESSION_EXPIRED`
   (`app.jsx:644-649`) that predates this branch.
5. **WHA-M3 — PARTIAL.** Backend merge fixed (`weather.py:86-107`). Frontend
   half untouched: `getWeatherConfig().catch(() => ({}))` (`weather.jsx:126`)
   silently shows defaults when the config GET fails, with no operator signal.

### Latent landmine found during verification (NOT in the original audit)

`weather_service.py:371` — `_fetch_openmeteo_current` still requests
`"timezone": "Asia/Macau"`, the exact bug class API-F1 just fixed, one function
away in the same file. Dormant **only** because no frontend file reads
`obs_time` (repo-wide grep confirms). The next person to wire "observed at" onto
the current-conditions tile silently reintroduces a +8h error. Fix or comment
it as a trap.

### Corrections to earlier claims in this document's own history

Recorded because each was asserted confidently and was wrong:
- **API-F6 was reported "fixed but untagged"** on the strength of an
  `auth_expired` grep. Wrong — that path is pre-existing and the server-side
  finding got zero changes. Grepping for a symptom's vocabulary is not
  verification.
- **MSP-F6 was filed as a documented deferral.** Nothing in the code documents
  it. A future reader finds silence, not a known gap.
- **WHA-M3 was reported fixed.** Only the backend half is.
- **The tweaks-trigger / wall-mode-exit overlap was reported as a caught
  re-break of SHL-2.** It was structurally impossible — `<TweaksPanel>` mounts
  only in the non-wall-mode branch (`app.jsx:1550`), so the trigger is never in
  the DOM during wall mode. The suppression rule added is valid but dead code.
  Keep it: it is the only guard if a future refactor hoists the panel out of
  that ternary.

### Confirmed solid (independently re-verified, high confidence)

- **Forecast timezone.** Exactly ONE correction
  (`weather_service.py:480`, `timezone: "UTC"`). No compensating offset anywhere
  — verified by grep for `28800`/`8*3600`/offset math, none outside comments.
  Hour labels correct, not double-corrected. Two agents concurred independently.
- **ALR-H1 / ALR-M8** (silent-success on a failed ack/snooze) — rethrow traced
  through to a catch that actually acts on it, not a no-op.
- **CMP-F1** — `grep` for `onClose` in any effect dep array in `components.jsx`
  returns zero hits; fixed via real `useLatestRef`, not a reordered array.
- **Wall-mode escapability (SHL-2)** — two independent IME-guarded exits; the
  button renders outside the `ErrorBoundary` so it survives a WallView crash.
- **Alert truncation contract** — `.truncated`/`.totalAvailable` survive
  `markSeen` (`app.jsx:751-756`) and the audit path (`app.jsx:1369`).
- **No invalid Tailwind classes** across `components.jsx`, `app.jsx`,
  `index.html`, `tweaks-panel.jsx`. Interpolated `bg-${m.color}` is safe —
  constrained to the five `sev-*` literals plus an `ink-muted` fallback.

Two suites fail and are **pre-existing**, proven by `git stash`ing the changed
files and reproducing identical failures at HEAD:
- `test_ws_event_contract.py` (2 failed) — `node_deleted` missing from a
  hardcoded `EXPECTED_ALL_TYPES` list in the test itself.
- `test_node_allowlist.py` (4 failed) — `EDGE_API_KEY` env/fixture issue in
  ingest endpoints.

Neither is in a file this work touched. Fix separately; do not block on them.

## The three verification gates (scratchpad is session-scoped — recreate if gone)

1. **`check_spa_syntax.js`** — compiles all 13 files with the vendored Babel.
   Catches parse errors only.
2. **`check_spa_refs.js`** — parses each file, unions all top-level bindings
   (they share ONE global scope, as classic scripts), flags free identifiers
   that aren't host globals. **Also reports declared-but-never-referenced
   bindings.** Both additions exist because real bugs slipped past gate 1.
3. **`check_spa_classes.js`** — flags color/animation utilities outside the
   Tailwind config. Advisory; expect false positives (directional border
   widths, font sizes, CSS property names in strings).

**Why gates 2 and 3 exist.** Gate 1 passes green on:
- a call to a function that was never defined (ReferenceError at render —
  a command-palette edit shipped calling three nonexistent helpers);
- a Tailwind class absent from config (renders as NOTHING — this shipped three
  times: `spin-slow` froze the boot spinner, `bg-brand-primary` made the only
  button on a blocking modal invisible, and an interpolated `bg-sev-${...}`
  fell through to green 「正常」 for any unmapped status).

## The dominant failure mode of the resumed work

**Four separate files had fixes that were written, commented against a finding
ID, and never wired up.** A finding ID appearing in a diff proves nothing.

- `monitor.jsx` — `fmtAgeOrDash` and `useStableSort` defined, never called
  (MSP-F19, MSP-F10 both claimed done).
- `alerts.jsx` — `bulkBusyRef`, `flashedIdsRef`, `flashScheduledRef` declared,
  never referenced (ALR-L3, ALR-L5 both claimed done).
- `weather.jsx` — `rainColorClass` orphaned by the WHA-M6 hero rewrite, leaving
  the rain tile the only uncoloured hero beside a coloured temperature tile.

Gate 2's unused-binding report now catches this class mechanically.

## Cross-file items resolved at merge (not by any single agent)

- **Forecast timezone — VERIFIED SAFE.** Exactly ONE correction exists: the
  Open-Meteo request param went `Asia/Macau` → `UTC` (`weather_service.py`).
  `api.jsx` does a plain `parseTsMs` with no offset arithmetic. Both stale
  comments that described the bug as unfixed were rewritten — they were
  themselves the hazard, since they invited a compensating ±8h offset.
- `refreshLive()`'s outer catch returned `failures: []`, so a mapper crash
  silently CLEARED the stale-data banner — a frozen board reading as live.
  Now reports all keys failed.
- Audit CSV filename used the UTC date; the whole 00:00–08:00 Macau night shift
  exported logs stamped with the previous day. Now local date parts.
- `markSeen` did `prev.map(...)`, which drops the `.truncated`/`.totalAvailable`
  properties Contract A attaches to the alerts array — the truncation banner
  vanished the first time an operator viewed any alert. Metadata now carried.
- `window.SnapshotImage` published, so the NOC wall stops depending on Babel's
  `const`→global-`var` accident.
- Audit truncation banner quoted `totalAvailable` as BOTH "at least N on the
  server" and "only N loaded" — same number, two meanings, no information.
  Reworded to state the cap honestly.
- Tweaks trigger (36px at right/bottom 16px, z-90) overlapped the WallView exit
  button (z-50) and sat on top of it — partially re-breaking SHL-2, whose whole
  purpose is making a 4K wall display escapable. Now hidden in wall mode.

## Known deferred — SUPERSEDED by the per-finding table above

This list was written before per-finding verification and overstated how many
gaps were genuine blockers. Only WHA-M10 and SHL-15 survive as DEFERRED-OK.
MSP-F6 and API-F3 are plain NOT-FIXED (nothing documents them in code);
WHA-M8 and WHA-M3 are PARTIAL. Kept for history:

### Original (inaccurate) deferral list

- **WHA-M6** — no 10-min/1-hour rainfall field exists in the backend; only
  `rainfall_24h_mm`. Hero now shows the 24h total honestly instead of a
  permanently dead placeholder.
- **API-F3** — `/api/stream/health` needs a 3s Prometheus scrape, a different
  payload shape, and derivative bitrate math. Follow-up ticket.
- **WHA-M8** — handover conflict race only narrowed; robust fix needs a backend
  `updated_at` precondition returning 409.
- **MSP-F6** — pump manual-override banner. `flags.manual_override` is never
  ingested or exposed by `central_server`, and `PumpCommandRequest.action` is
  pattern-locked to `^(ON|OFF)$`. Needs backend work first.
- **SHL-15** — `OPERATORS_ONLINE` has no feed anywhere; documented as
  permanently empty rather than faked.
- `CurrentWeather.fetched_at` still uses tz-aware `datetime.now(timezone.utc)`
  rather than naive `utcnow()`. Currently harmless (`parseTs` tolerates both
  shapes) but inconsistent with the wire contract. Follow-up.

## Runtime verification (2026-07-20)

Static review + the SPA gates prove a file *compiles* and *reads* correctly.
They do not prove it *renders and behaves* correctly. A jsdom render harness
now closes that gap: everything in `static/spa/vendor/` is local, so the SPA
runs offline in Node. Mock `window.SDPRS_API` with spies, render the real
component, dispatch real clicks, assert the real DOM.

**36 runtime assertions, all passing**, over the highest-risk findings:

| Finding | What was executed, not just read |
|---|---|
| MSP-F6 (safety) | `手動停機中` / `手動強制運行中` banners render on `manualOverride` OFF/ON; **clicking 恢復自動 actually calls `pumpCommand(id,'AUTO')`**; absent when null |
| MSP-F5 | `上次指令` line renders operator + time from `lastPumpCommand` |
| MSP-F7 | stale cached `bitrate>0` with `health.reachable=false` still shows 開始串流 and clicking calls `startStream` — health, not cached bitrate, decides the command; two clicks in one tick fire exactly one command; missing API renders disabled |
| API-F9 | `短循環警告` banner renders iff `node.cyclesAlert` |
| CMP-F11 | picking a node in the palette calls `onNav('status')` **and** `onCmd('node:<id>')` |
| WHA-M8 | a 409 opens the 儲存衝突 dialog showing **both** server text and the operator's draft; 覆蓋伺服器版本 re-issues with the server's new token from the 409 body |
| WHA-L8 | number input tolerates an empty field (no snap-to-0, no `onChange(0)`) |

Still static-only (relabels, deletions, disabled-state guards — low risk):
MSP-F18/F22, WHA-M3, WHA-L1, API-F2, ALR-L1/L5/L7, CMP-F17.

The harness and all three gates now live in the repo at **`tools/spa/`**
(`cd tools/spa && npm install && npm run check`). Developer tooling only — the
SPA still has no build step. `node_modules/` is gitignored; React, ReactDOM,
Babel and Tailwind all come from `static/spa/vendor/`, so the checks run with
no network access. See `tools/spa/README.md`.

### Script scope is ISOLATED per file — corrects an earlier assumption

Earlier notes (and the first version of the undefined-reference gate) assumed
every `<script type="text/babel">` shares ONE global lexical scope. **That is
wrong.** `@babel/standalone`'s `transformScriptTags` runs each script tag in
its own top-level scope — verified against the real vendored `babel.min.js`,
and corroborated by the fact that `pages/alerts|audit|handover|monitor|status.jsx`
each declare `const useState_p` at line 3 without colliding.

What this changes:

- A top-level `const`/`function` in one file is **invisible** to another. Bare
  cross-file identifiers resolve only because the symbol was published
  (`window.X = …` / `Object.assign(window, {…})`), making it a global-object
  property. This is why every page ends with `Object.assign(window, { …Page })`
  and `app.jsx` renders `<window.StatusPage/>`.
- A refs gate that unions all files' top-level bindings cannot see a cross-file
  reference that was never published — it would report OK on a runtime
  `ReferenceError`. Correct per-file allowed set is: host/library globals ∪
  names published to `window` by any file ∪ that file's own top-level bindings.
  **Re-checked under the corrected model: all 13 files clean**, so the wrong
  model was not masking a real defect here.
- A render harness must load each dependency as its own script; concatenating
  all files is over-permissive and can green-light a reference the browser
  would throw on.

## Before committing

Re-run all three gates plus the backend suites — **and** confirm the
per-finding verification table above is complete with no unresolved NOT-FIXED
or PARTIAL entry that affects pump state, alert acknowledgement, or data
freshness. Gates alone are not a release criterion.

Run `cd tools/spa && npm run check` — it runs the scope invariant, syntax,
the **corrected** per-file refs gate, the 36 render assertions, and the
advisory Tailwind token check, and exits non-zero if any blocking one fails.

Two Tailwind-token hits are known false positives, already checked by hand:
`bg-brand-primary` (app.jsx:1681, inside a JSX comment describing the old fix)
and `animate-in` (a hand-written CSS class at index.html:46).
