# HANDOFF — Verification of 4K Wall Mode & Tray UX Audit (2026-07-26)

**Purpose:** A prior session produced
`docs/audits/2026-07-26-wall-mode-tray-ux-audit.md` (2 Critical · 6 High ·
10 Medium · 5 Low). This document lets an independent session **verify or
falsify each finding from scratch** without trusting the original auditor.

Read this first, then re-check each finding against the code. Do NOT assume
the audit is correct — several of its claims are inferences, and it explicitly
did not cover runtime behaviour.

---

## 0. Environment facts (verify these before anything else)

- **The git repo and all code live in `sdprs/`, NOT the workspace root.**
  The root (`TyphoneCrackDetect_waterRemove/`) is a planning wrapper.
  `cd sdprs && git log` works; at the root it does not.
- **Stack:** FastAPI backend (`sdprs/central_server/`) + a **no-build-step
  React 18 SPA** in `sdprs/central_server/static/spa/`. JSX is transpiled
  in-browser by Babel; files communicate via `window.*` globals. There is no
  bundler, no npm build, no TS.
- **Script load order matters:** `icons.jsx → data.jsx → api.jsx →
  components.jsx → pages/*.jsx → tweaks-panel.jsx → app.jsx` (see
  `static/spa/index.html`).
- **The "system tray" in this codebase is the webcam client**
  (`sdprs/webcam_client/gui/tray_app.py`, pystray). There is **no** tray for
  the dashboard. The user's reported "tray bug" was resolved by the audit as a
  *missing feature*, not a defect — challenge this conclusion if you think a
  tray-to-dashboard path exists somewhere.
- Prior audits exist and some findings reference them:
  `docs/audits/dashboard_ui_audit_2026-07-20.md` (WHA-H4 = inescapable wall,
  marked fixed) and `docs/superpowers/specs/2026-07-26-webcam-startup-and-guard-ux-design.md`.

---

## 1. How to verify each finding

For every finding below: open the cited file at the cited line, confirm the
code matches the claim, and decide whether the *conclusion* (not just the
code shape) is a real UX problem. Statuses used:
**CONFIRMED** = code read directly matches claim. **INFERRED** = code shape
suggests it but behaviour was not executed.

### Critical

| ID | Claim | Evidence to check | Verify by | Status |
|---|---|---|---|---|
| WAL-C1 | No tray-to-dashboard launch path exists | `webcam_client/gui/tray_app.py:62-68` (menu = 開啟設定/暫停推送/離開); `webcam_client/main.py:81-88` | `grep -ri "webbrowser\|開啟監控\|open.*dashboard\|wall=1" sdprs/` — expect NO dashboard-launch hit. Also confirm no second tray app exists. | CONFIRMED |
| WAL-C2 | Wall has no node selection / section visibility | `app.jsx:1755-1759` (sort), `:1813` (`slice(0,9)`), `:1858` (`slice(0,12)`), `:1809` (fixed `grid-cols-[2fr_1fr_1fr]`) | Confirm no state/prop controls which nodes render. Then `grep -rn "wall-hide\|wall-scale-up" sdprs/central_server/static/spa/` — expect ONLY `styles.css:206-207` (defined, never applied in JSX). | CONFIRMED |

### High

| ID | Claim | Evidence to check | Verify by | Status |
|---|---|---|---|---|
| WAL-H1 | Unattended wall gets logged out by session expiry | `app.jsx:698-701` (comment admits it), `:702-732` (activity-gated keep-alive), `:1662-1714` (expiry modal `z-[100]`, rendered outside wall branch) | Confirm keep-alive requires `pointerdown`/`keydown` and wall generates neither. Modal renders over wall. | CONFIRMED |
| WAL-H2 | Typography is desktop-sized, no 4K scaling | `app.jsx:1766` (`h-16` strip), `:1834-1835` (`text-base` id / `text-xs` name), `:1924` (`h-8` footer, `text-xs`) | `grep -rn "wall-scale-up\|vw\|clamp(" static/spa/` for any wall-mode responsive sizing — expect none applied. | CONFIRMED |
| WAL-H3 | "+N more" overflow is non-interactive | `app.jsx:1841-1848` (nodes pill), `:1872-1879` (alerts pill) | Confirm both `<span>`s have no `onClick`/handler. | CONFIRMED |
| WAL-H4 | Webcam tray icon never reflects disconnect | `webcam_client/main.py:88` (`set_status(True)` once); `tray_app.py:50-56` (set_status/_refresh_icon exist) | `grep -rn "set_status" sdprs/webcam_client/` — expect only the definition + the single `main.py:88` call, never `False`. Also check `push_engine.py` silent-return on camera-open failure (~`:77-79`). | CONFIRMED |
| WAL-H5 | Wall Live pill says "Live" during outage | `app.jsx:1777` (literal `` `Live · ${liveSec}s` ``) vs `components.jsx:484` (StatusStrip degrades label to 重新連線中/連線中斷) | Confirm pill `tone` changes with `liveState` but the text string is constant. | CONFIRMED |
| WAL-H6 | Wall never shows partial-load-failure banner | `app.jsx:1509-1544` (wall branch) vs `:1575-1583` (dataWarnings banner) | Confirm the banner JSX sits inside the **non-wall** `:` branch of the ternary at `:1509`, so wall mode has no equivalent. Check `dataWarnings` is never rendered in the wall subtree. | CONFIRMED |

### Medium

| ID | Claim | Evidence | Verify by | Status |
|---|---|---|---|---|
| WAL-M1 | No direct wall entry (URL param/kiosk) | `app.jsx:20-34` (RESTORED_STATE carries only `{page,selectedId,hadDraft}`), `:1642` (toggle is only entry) | `grep -rni "wall" static/spa/app.jsx` for any query-param/route handling — expect none. | CONFIRMED |
| WAL-M2 | Wall settings unreachable from within wall mode | `tweaks-panel.jsx:219` (`html.wall-mode .twk-trigger{display:none}`) | Confirm gear hidden in wall mode; only exit is Esc/button, so no settings reachable without leaving wall. | CONFIRMED |
| WAL-M3 | Wall clock forces full WallView re-render every 1s | `app.jsx:1750-1754` (`setWallClock(Date.now())` interval) | Confirm the interval state lives in WallView itself, so each tick re-renders the whole subtree. Also note WallView consumes `LiveClockContext` (`:1746`) = a second per-second render source. | CONFIRMED (perf impact INFERRED, not measured) |
| WAL-M4 | Rain tile permanently shows "資料來源未提供" | `app.jsx:1909`; `api.jsx:482-494` (`rain.now`/`hour` hardcoded null) | Confirm backend exposes only 24h total; the "no instantaneous rate" line renders unconditionally. | CONFIRMED |
| WAL-M5 | Tray pause is all-or-nothing | `tray_app.py:73-81` → `controller.pause_all/resume_all` | Confirm no per-camera pause or submenu. | CONFIRMED |
| WAL-M6 | No single-instance guard on tray app | `webcam_client/main.py:59-88` | `grep -rni "mutex\|single.instance\|lockfile\|CreateMutex" sdprs/webcam_client/` — expect none. | CONFIRMED |
| WAL-M7 | Wall entry is a 3-step hidden flow | `app.jsx:1642`, `tweaks-panel.jsx:397-433` | Trace the only path: gear trigger → panel → 檢視模式 → toggle. | CONFIRMED |
| WAL-M8 | Online-but-stale tiles not visually frozen on wall | `app.jsx:1815` (`snapshot-frozen` only when `offline`) vs `components.jsx:275` (`frozen = offline \|\| upload==null \|\| upload>60`), `styles.css:104-106` | Confirm the wall applies the class on a narrower condition than SnapshotImage's own frozen definition → green dot + icon, no grayscale, for a stale-online node. | CONFIRMED |
| WAL-M9 | Ticker counts acked alerts; newest-first can bury critical | `api.jsx:596-603` (statuses incl. ACKNOWLEDGED), backend `ORDER BY timestamp DESC` (`central_server/api/alerts.py:~767-778`), `app.jsx:1855` (`{alerts.length} 筆`), `:1858` (`slice(0,12)`) | Confirm no severity/state re-sort before the slice in WallView. | CONFIRMED |
| WAL-M10 | Light theme renders white panels on black wall | `app.jsx:434-439` (dark/light toggle ignores wallMode), `styles.css:122-138` (`html.light .bg-surface-panel{#FFFFFF!important}`) | Confirm WallView uses `bg-surface-panel`/`text-ink-primary` tokens and nothing forces dark in wall mode. | CONFIRMED |

### Low
WAL-L1..L5 — see audit report `Priority 3`. Each is a single-location claim;
verify the cited line directly. L5 (WallSnapshot fallback "mostly dead") —
confirm `components.jsx:333` now does `window.SnapshotImage = SnapshotImage`.

---

## 2. Claims the next session should specifically challenge

These are the audit's *interpretations*, most worth an independent eye:

1. **"The tray bug is a missing feature, not a defect" (WAL-C1/C2).** If you
   find ANY existing tray→dashboard wiring, or evidence the user meant the
   *webcam* tray, this framing is wrong. The user's own words: "opening from
   the system tray (user can select which node to show up... temp close
   unwanted sections)". They confirmed "Dashboard from tray" when asked.
2. **WAL-H1 severity.** The code comment argues expiring a wall session is
   *correct* security. Whether that's acceptable UX is a judgment call — the
   audit rated it High; a security reviewer might disagree.
3. **WAL-M9 "can bury critical".** Depends on real alert volume. With ≤12
   active alerts it's a non-issue. Check typical `ALERTS` cardinality if data
   is available.
4. **WAL-H2 legibility.** "Illegible at 3m on 4K" is an ergonomic claim, not
   proven in code. Needs a real display to confirm.

---

## 3. Known coverage gaps (where new findings are most likely)

The audit was **static only**. Not examined / not executable:

- **Runtime rendering at 3840×2160** — overflow, scrollbars, actual font px.
- **WebSocket reconnect as observed from the wall** — backoff read in
  `api.jsx` but never exercised; `liveSec` degradation path is inferred.
- **Performance** — 9 tiles × 1Hz JPEG + per-second re-render flagged from
  code shape, never profiled.
- **Kiosk/browser policies** — fullscreen API, audio autoplay on a wall box.
- **Backend contract beyond alert ordering** — snapshot endpoint, weather
  provider failover, `/api/session/extend` behaviour.
- **Multi-monitor setups** — no code path exists; none was requested.
- **Edge/pump firmware** effects on displayed data.
- The audit did **not** re-verify the 2026-07-20 audit's other (non-wall)
  findings; it only relied on its WHA-H4 "fixed" status.

Suggested runtime pass: run the wall on a 4K output, then kill (a) the WS,
(b) `/api/nodes`, (c) `/api/weather` in turn and observe what the wall shows.
This directly tests WAL-H5, WAL-H6, WAL-M8.

---

## 4. Gotchas for the verifying session

- **Don't trust `grep` at the workspace root** for code questions — search
  inside `sdprs/`.
- **`window.*` globals are the module system.** A symbol that looks "missing"
  may be a Babel `const`→global accident (see `SnapshotImage`, now fixed at
  `components.jsx:333`). Check `window.X = X` assignments before concluding
  something is undefined.
- **`styles.css` light-theme overrides use `!important`** and beat Tailwind
  utilities — relevant to WAL-M10.
- **The wall branch and the normal shell are two arms of one ternary**
  (`app.jsx:1509`). Anything rendered only in the `:` arm is invisible in wall
  mode — this is the root of WAL-H6. Re-scan that ternary for other
  shell-only affordances the wall silently loses.
- **Line numbers are from the 2026-07-26 checkout.** If the code has moved,
  locate by surrounding identifiers, not line number.

---

## 5. Deliverable expected from the verifying session

A short verdict per finding: **Confirmed / Refuted / Partial / Cannot-confirm
(needs runtime)**, plus any NEW findings discovered while re-reading. Update
or supersede `docs/audits/2026-07-26-wall-mode-tray-ux-audit.md` accordingly —
do not silently leave disagreements unrecorded.
