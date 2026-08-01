# Wall-Mode / Tray UX — Verified Fix Plan (for GLM execution)

**Author:** verification+planning pass, 2026-08-01 (Opus).
**Source audit:** `docs/audits/2026-07-26-wall-mode-tray-ux-audit.md` (23 findings)
**Source handoff:** `docs/audits/2026-07-26-wall-mode-tray-ux-audit_HANDOFF.md`

This plan is the output of an **independent re-verification of every wall-mode /
tray finding against current `main`** (commit `0ea6349`). It tells GLM exactly
what is still real, what is already fixed, what is a feature gap to leave alone,
and — for the genuine bugs — the precise current location, fix approach, the
testable helper to extract, and the acceptance test to write.

**Do not trust the audit's line numbers** — they are from the 2026-07-26 checkout
and have drifted (app.jsx grew ~25 lines). All line numbers below were
re-confirmed on 2026-08-01. Locate by surrounding identifiers if they drift again.

---

## 0. Operating manual — READ BEFORE TOUCHING CODE

**Environment (the `[Cloud]` path trap is real):**
- Git root is `sdprs/` (the workspace root has a vestigial `.git` stub — ignore it).
- No-build React 18 SPA in `central_server/static/spa/`. JSX is transpiled
  in-browser by Babel; **modules communicate via `window.*` globals**. Script load
  order (from `index.html`): `icons.jsx → data.jsx → api.jsx → components.jsx →
  pages/*.jsx → tweaks-panel.jsx → app.jsx`.
- Python interpreter for tests: `/c/Python314/python` (via the Bash tool). The
  bracket in the path breaks bare `pytest`; always `cd` into the suite dir first.

**Test commands (copy exactly):**
- SPA render tests: `cd "<repo>/tools/spa" && node render_tests.js` (per-suite),
  and the full gate `node run_all.js` (compiles + renders every JSX file — this is
  the ONLY thing that catches an app.jsx syntax error, since app.jsx is not a
  render-test target).
- Backend per-suite: `cd "<repo>/central_server" && /c/Python314/python -m pytest tests/<file> -q -p no:cacheprovider`.
- Webcam client: `cd "<repo>/webcam_client" && /c/Python314/python -m pytest tests/<file> -q -p no:cacheprovider`.

**Hard constraints (non-negotiable):**
1. **TDD every fix: write the failing test FIRST, watch it fail (RED), then implement (GREEN).** No exceptions.
2. **Never merge or push without the user's explicit "approved"/"merge".** Local commits on a feature branch are fine; merging to `main` or `git push` is NOT until the user says so.
3. **Verify agent/edit CODE, not reports.** If you delegate, read the diff.
4. Datetime in Python: `from ...timeutil import utcnow` (naive-UTC). **Never** `datetime.utcnow()` or tz-aware.
5. Literal strings `Msc@2333` / `MSC-Person` must NEVER appear anywhere. No hardcoded credentials of any kind.
6. Do NOT add any new command/downlink surface to edge/webcam devices beyond what exists.
7. zh-TW UI — all operator-facing strings in Traditional Chinese.

**The testability reality for WallView (critical — read twice):**
`WallView` is an internal function inside `app.jsx`. It is **NOT** exported to
`window`, and `app.jsx` is **NOT** a render-test target (it bootstraps the whole
app on load and cannot be mounted in the jsdom harness). Therefore you **cannot**
mount WallView in `render_tests.js`.

**The proven pattern (used for FLOW-001, AUTH-003, OPS-002 already on main):**
extract each fix's decision LOGIC into a **pure helper in `data.jsx`** (a real
render-test target), unit-test the helper in the `data.jsx` suite, then wire
`app.jsx`/WallView to call it. The wiring itself is verified by `run_all.js`
(compile + render) + reading the diff. Be honest in commit messages that the
wiring is compile-verified, not behaviorally unit-tested.

**Branch:** create `fix/wall-mode-audit-2026-08-01` off `main` (`0ea6349`). A stale
empty branch `fix/dashboard-medium-2026-08-01` exists at the same commit — ignore
or delete it.

---

## 1. Verdict table (all 23 findings, re-verified 2026-08-01)

Legend: **FIX** = real bug, in scope, fix it. **FEATURE** = confirmed gap but a
new feature — do NOT build without explicit user design sign-off. **REFUTED** =
already fixed / no longer reproduces. **LOW** = cosmetic, optional.

| ID | Audit sev | Verdict | Current location | Note |
|----|-----------|---------|------------------|------|
| WAL-C1 | Critical | **FEATURE** | `webcam_client/gui/tray_app.py`, `main.py` | No tray→dashboard path. `webbrowser` appears only in PyInstaller build artifacts, not source. Feature, not a bug. |
| WAL-C2 | Critical | **FEATURE** | `app.jsx:1826,1830,1875`; `styles.css:206-207` | No node/section selection; `.wall-hide`/`.wall-scale-up` defined in styles.css but wired nowhere in JSX (confirmed). Feature. |
| WAL-H1 | High | **FEATURE/DESIGN** | `app.jsx:715-744` (keep-alive), expiry modal | Unattended wall logs out. Needs a security decision (read-only wall token). Do not hack around SEC-001. Flag for design. |
| WAL-H2 | High | **DESIGN** | WallView typography | 4K type-scale pass. Ergonomic; needs a real display. Defer to a design pass. |
| WAL-H3 | High | **FEATURE** | `app.jsx:1858-1865, 1889-1896` | "+N more" pills are non-interactive. Real, but the fix (paging/rotation) is a feature. |
| WAL-H4 | High | **REFUTED/SUPERSEDED** | `webcam_client/` | `TrayApp.set_status` was REMOVED (`tests/test_tray_app.py::test_set_status_is_gone`). The guard-UX refactor changed tray status handling. **Re-verify against the new tray architecture before any action; the finding as written no longer applies.** |
| WAL-H5 | High | **FIX** | `app.jsx:1794` | Live pill text is the literal `Live · Ns` even during an outage. **See §2.1.** |
| WAL-H6 | High | **FIX** | `app.jsx:1561` (WallView call), `1609-1613` (banner, shell-only) | Wall never shows the partial-load-failure banner. **See §2.2.** |
| WAL-M1 | Medium | **FEATURE** | `app.jsx` RESTORED_STATE, toggle | No `?wall=1` direct entry. Feature (prerequisite for a tray launcher). |
| WAL-M2 | Medium | **FEATURE** | `tweaks-panel.jsx:219` | Gear hidden in wall mode → settings unreachable. Only matters once in-wall controls exist. Feature. |
| WAL-M3 | Medium | **FIX** | `app.jsx:1767-1771` + context use at `1763` | Wall clock (+ liveSec context) re-renders the whole WallView every second. **See §2.3.** |
| WAL-M4 | Medium | **LOW/FIX** | `app.jsx:1926` | Rain tile permanent `即時雨率 — 資料來源未提供`. Optional soften. **See §2.7.** |
| WAL-M5 | Medium | **FEATURE** | `webcam_client/gui/tray_app.py`, `app_controller.py` (`pause_all`) | Tray pause is all-or-nothing. Per-node pause is a feature. |
| WAL-M6 | Medium | **REFUTED** | `webcam_client/main.py:46,442`, `single_instance.py` | **Already fixed** — `_acquire_single_instance()` + `SingleInstance` mutex guard now exist. Do nothing. |
| WAL-M7 | Medium | **FEATURE** | `app.jsx` toggle, `tweaks-panel.jsx` | 3-step hidden wall entry; no "set this machine as a wall" action. Feature (overlaps WAL-M1). |
| WAL-M8 | Medium | **FIX** | `app.jsx:1832` | Wall freezes a tile only on `offline`, not on a stale frame → green dot + icon for a stale-online camera. **See §2.4.** |
| WAL-M9 | Medium | **FIX (scoped)** | `app.jsx:1872, 1875` | Ticker counts acknowledged in `{alerts.length} 筆` and slices newest-12 with no severity sort → a burst can bury an unacked critical. **See §2.5.** Note handoff caveat: only bites at >12 active alerts. |
| WAL-M10 | Medium | **FIX** | `app.jsx:438-443` | Theme toggle ignores `wallMode`; light theme → white panels on the black wall. **See §2.6.** |
| WAL-L1 | Low | LOW | `app.jsx:1571-1575` | Exit button always visible. Optional fade-after-idle. Skip unless asked. |
| WAL-L2 | Low | LOW | `app.jsx:1790` | `NOC WALL · v2.4` hardcoded version. Skip or read a real version. |
| WAL-L3 | Low | LOW | `app.jsx:1874-1875` | Ticker `overflow-y-auto` but capped at 12 → vestigial scroll. Resolves naturally if WAL-M9/paging changes the cap. |
| WAL-L4 | Low | LOW | `app.jsx:1817, 1910` | Wind direction shown twice in different formats. Cosmetic. |
| WAL-L5 | Low | REFUTED-ish | `app.jsx` WallSnapshot; `components.jsx` (`window.SnapshotImage = SnapshotImage`) | Dead-code comment only; SnapshotImage is exported. Optional comment cleanup. |

**Summary:** 7 genuine bugs to FIX (H5, H6, M3, M4, M8, M9, M10), 2 REFUTED/already-fixed (H4 superseded, M6 fixed), the rest are FEATURE/DESIGN gaps (C1, C2, H1, H2, H3, M1, M2, M5, M7) or LOW cosmetics — **do not build features or touch REFUTED items without explicit user sign-off.**

---

## 2. Fix specifications (the 7 real bugs)

Do these in the order below; commit per finding (or per 2-3 closely related), TDD each.

### 2.1 WAL-H5 — Live pill must degrade its label during an outage

**Current (`app.jsx:1794`):**
```jsx
<window.Pill tone={liveState} dot pulse={liveState==='ok'} className="!h-8 !text-sm !px-3">{`Live · ${liveSec}s`}</window.Pill>
```
`liveState` (line 1764) already goes ok→warn→critical, so the pill changes color,
but the text is always "Live". `StatusStrip` already solved this — `components.jsx:484`:
```js
const liveLabel = liveSec < 10 ? `Live · ${liveSec}s` : liveSec < 30 ? `重新連線中… ${liveSec}s` : `連線中斷 ${liveSec}s`;
```
**Fix:** extract that inline expression into a **pure helper `liveClockLabel(liveSec)` in `data.jsx`**, export via `window.liveClockLabel`, use it in BOTH `components.jsx:484` (replace the inline) and `app.jsx:1794` (replace `` `Live · ${liveSec}s` ``). DRY + fixes the wall.
**Test (data.jsx suite in `render_tests.js`):** `liveClockLabel(3) === 'Live · 3s'`; `liveClockLabel(15)` contains `重新連線中`; `liveClockLabel(45)` contains `連線中斷`; boundaries at 10 and 30.
**Acceptance:** wall pill reads `連線中斷 120s` (not `Live · 120s`) when liveSec≥30, and `components.jsx` behavior is unchanged (its existing StatusStrip tests still pass).

### 2.2 WAL-H6 — Wall must surface partial-load-failure warnings

**Current:** the `dataWarnings` amber banner is rendered only in the shell arm of
the ternary — `app.jsx:1609-1613` (`{dataWarnings.length > 0 && (...)}` inside the
`: (...)` non-wall branch at the `1543` ternary). WallView is invoked at
`app.jsx:1561` as `<WallView alerts={alerts} nodes={nodes} unackCount={unackCount}/>`
— it never receives `dataWarnings`.
**Fix:**
1. Pass the prop: `<WallView ... dataWarnings={dataWarnings}/>` at line 1561.
2. Add `dataWarnings` to the `WallView({ ... })` signature (line 1762).
3. Render a compact wall banner (top strip or a thin bar) when `dataWarnings.length > 0`, reusing the same label mapping the shell uses (`_FAILURE_LABELS[k] || k`, see line 1613) — e.g. `{labels} 無法載入 — 顯示快取資料`. Style it high-contrast (amber on dark) so it reads across a room.
**Testability:** presentational. Extract nothing new; verify via `run_all.js` (compile+render) and by reading the diff. If you want a unit anchor, `_FAILURE_LABELS` could be moved to `data.jsx` and tested, but that's optional.
**Acceptance:** with `dataWarnings=['nodes']`, the wall shows an amber "…無法載入…" banner; with `[]`, nothing. Shell banner unchanged.

### 2.3 WAL-M3 — Isolate the per-second wall re-render

**Current:** `WallView` holds `const [wallClock, setWallClock] = useStateA(...)` +
a 1s `setInterval` (`app.jsx:1767-1771`) AND consumes `liveSec` from
`LiveClockContext` at the top (`1763`). BOTH cause the entire WallView subtree
(9 snapshot tiles + 12-row ticker + weather) to re-render every second.
**Fix:** move the per-second reads into **leaf components** so only the time
string / pill re-renders:
- `WallClock` leaf: owns the `wallClock` state + interval, renders only the
  `<div>{new Date(wallClock).toLocaleTimeString(...)}</div>` (currently line 1822).
- `WallLivePill` leaf: consumes `LiveClockContext`, renders only the `<window.Pill>`
  (line 1794), using `liveClockLabel` from §2.1.
- Remove `liveSec`/`wallClock` usage from `WallView`'s top scope so WallView itself
  no longer re-renders on the tick.
**Testability:** structural/perf — no behavior change. Verify via `run_all.js` and
by confirming (read) that WallView's body no longer references `liveSec`/`wallClock`
except through the two leaves. This is the one fix with no unit test; say so in the commit.
**Acceptance:** clock still ticks; live pill still degrades; `run_all.js` green.

### 2.4 WAL-M8 — Freeze stale-online tiles on the wall

**Current (`app.jsx:1832`):** `${n.status === 'offline' ? 'snapshot-frozen' : ''}`
— only offline gets the grayscale/dim. A node that is `online` but whose frame is
stale (`upload > 60`) shows a green dot + icon, reading as "camera fine."
**Fix:** extract a pure helper **`wallTileFrozen(node)` in `data.jsx`** matching the
`monitor.jsx` NodeCard convention (`monitor.jsx:83`: `frozen = status==='offline' || upload > 60`;
note `upload == null` is NOT frozen — null means "never had a snapshot", and
`null > 60` is false, so it stays un-grayscaled but the offline branch already
covers never-reported nodes). Use `wallTileFrozen(n)` for the `snapshot-frozen`
class at line 1832.
**Test (data.jsx suite):** `wallTileFrozen({status:'offline'})===true`;
`wallTileFrozen({status:'online', upload:120})===true`;
`wallTileFrozen({status:'online', upload:5})===false`;
`wallTileFrozen({status:'online', upload:null})===false`.
**Acceptance:** a stale-online tile is grayscale on the wall.

### 2.5 WAL-M9 — Ticker: honest count + severity/state ordering (scoped)

**Handoff caveat:** only bites at >12 active alerts. Keep the fix minimal and safe.
**Current:** `{alerts.length} 筆` (line 1872) counts ACKNOWLEDGED too; `alerts.slice(0, 12)`
(line 1875) is newest-first with no severity re-sort.
**Fix:** two pure helpers in `data.jsx`:
- `activeAlertCount(alerts)` → count of alerts whose `state !== 'acknowledged'`
  (the wall fetches only PENDING_VIDEO/PENDING/ACKNOWLEDGED, so this ≈ unacked).
  Confirm the exact `state` values `mapAlert` emits before finalizing the predicate.
- `orderWallAlerts(alerts)` → returns a NEW array sorted so unacked + higher
  severity float up before the `.slice(0,12)`: primary key state (pending before
  acknowledged), secondary severity (critical > warn > info), tertiary recency
  (existing newest-first). Do NOT drop acknowledged rows entirely (operators still
  want to see them) — just make sure an unacked critical can't be pushed out of 12.
Use `activeAlertCount(alerts)` for the header count and `orderWallAlerts(alerts).slice(0,12)`
for the list.
**Test (data.jsx suite):** an array of 13 alerts where the ONLY critical is the
oldest → after `orderWallAlerts(...).slice(0,12)` the critical is still present;
`activeAlertCount` excludes acknowledged.
**Acceptance:** unacked critical never buried; header shows active (not total) count.

### 2.6 WAL-M10 — Force the dark palette in wall mode

**Current (`app.jsx:438-443`):** the effect toggles `dark`/`light` from
`tweaks.theme` regardless of `tweaks.wallMode`. With theme=light, `styles.css:122-138`
`html.light .bg-surface-panel{#FFFFFF!important}` turns the wall into white panels.
**Fix:** extract a pure helper **`effectiveTheme(theme, wallMode)` in `data.jsx`**
returning `wallMode ? 'dark' : theme`. In the effect, compute
`const t = window.effectiveTheme(tweaks.theme, tweaks.wallMode)` and toggle
`dark`/`light` from `t` instead of `tweaks.theme` (leave the `wall-mode` and
`focus-mode` toggles as-is; add `tweaks.theme` is already in deps).
**Test (data.jsx suite):** `effectiveTheme('light', true)==='dark'`;
`effectiveTheme('light', false)==='light'`; `effectiveTheme('dark', true)==='dark'`.
**Acceptance:** entering wall mode with theme=light shows dark panels; exiting
restores light.

### 2.7 WAL-M4 — Rain tile permanent "unavailable" (LOW, optional)

**Current (`app.jsx:1926`):** `即時雨率 — 資料來源未提供` renders unconditionally.
It is honest but permanent noise. **Lowest priority.** If done: only render the
line when it adds information, or subdue it (smaller/dimmer) so it doesn't compete
with the 24h figure. Do NOT fabricate a rate. Skip if time-constrained.

---

## 3. Do-NOT-touch list (and why)

- **WAL-M6 (REFUTED):** single-instance guard already exists (`webcam_client/single_instance.py`, `main.py:46,442`). Touching it risks regressing the guard-UX work.
- **WAL-H4 (SUPERSEDED):** `TrayApp.set_status` was deliberately removed; a test asserts its absence. The tray status model changed. If the user still wants tray status honesty, that is a NEW design task against the current tray/guard code — not this finding. Re-verify first.
- **WAL-C1, C2, H1, H2, H3, M1, M2, M5, M7:** feature/design gaps. The audit itself defers them to "Recommended direction (for a follow-up design, not implemented here)." Building a tray launcher, wall node-selection, `?wall=1` entry, a read-only wall session token, or a 4K type-scale system are **features that need the user's explicit design sign-off** — do not start them from this plan.
- **WAL-L1..L5:** cosmetic; skip unless the user asks.

---

## 4. Suggested execution order & commits

1. Branch `fix/wall-mode-audit-2026-08-01` off `main`.
2. **Commit A — data-honesty on the wall (highest value):** WAL-H5 + WAL-M8 + WAL-M10 (three pure helpers `liveClockLabel`, `wallTileFrozen`, `effectiveTheme` in data.jsx + wiring). One data.jsx render-test suite covering all three. Also update `components.jsx` StatusStrip to use `liveClockLabel` (verify its existing tests still pass).
3. **Commit B — WAL-H6** wall failure banner (prop threading + banner JSX).
4. **Commit C — WAL-M9** ticker ordering + honest count (`orderWallAlerts`, `activeAlertCount` helpers + tests).
5. **Commit D — WAL-M3** clock/pill leaf isolation (perf; compile-verified).
6. (Optional) **Commit E — WAL-M4** rain-tile soften.
7. After each commit: `node run_all.js` MUST be green (this is the only guard for app.jsx syntax). Run the data.jsx render suite for the helper tests.
8. **Do NOT merge/push.** Report to the user with the commit list and the verdict table, and ask for the finish decision + confirm the feature-gap items are out of scope.

---

## 5. Record verdicts back into the audit

Per the handoff's expected deliverable, after (or as part of) the work, append a
"Verification 2026-08-01" verdict column/section to
`docs/audits/2026-07-26-wall-mode-tray-ux-audit.md` using the verdict table in §1,
so the disagreements (M6 fixed, H4 superseded) are recorded and not silently lost.

---

## 6. Broader backlog (context, not part of this plan)

The dashboard **Medium tier (~60 findings)** across `docs/audits/full_dashboard_audit_2026-07-26.md`
(SHELL-*, DATA-003..011, COMP-002..008, OPS-006..026, ALR/WXA/HDO/AUD-*, AUTH-005/006,
FLOW-002..005, UX-001, SEC-002/003) and the **delta audit's 35 new findings**
(`docs/audits/delta_audit_2026-07-26_HANDOFF.md`) remain open. The Critical + all 13
High findings from the full dashboard audit are already DONE and on `main`
(`5733c0e`→`0ea6349`). If GLM continues into the Mediums, apply the SAME operating
manual (§0): TDD, pure-helper extraction for app.jsx-internal logic, `run_all.js`
gate, per-suite backend pytest, no merge/push without approval. Note: **COMP-005**
(HlsPlayer no onFallback) was already resolved by the OPS-003 fix on main.
