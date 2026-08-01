# SDPRS — 4K 牆面模式 & System Tray UX Audit (2026-07-26)

**Scope:** Full UI/UX audit of the 4K Wall Mode dashboard display and the
system-tray launch path. Read-only — no code modified. Every finding is backed
by file:line evidence.

**Auditor perspective:** UI/UX engineering review of the operator-facing wall
display and the tray-to-dashboard workflow requested by the user ("open from
the system tray, select which nodes to show, temp-close unwanted sections").

**Totals:** 2 Critical (feature gaps matching the reported bug) · 6 High ·
10 Medium · 5 Low. (First pass: 2C/4H/7M/5L. A second pass — triggered by the
question "is every problem found?" — re-examined the data pipeline feeding the
wall, theme interaction, and liveness signalling, and added WAL-H5, WAL-H6,
WAL-M8, WAL-M9, WAL-M10 below. This is still a static read-only review, not a
guarantee of exhaustiveness — see "Coverage caveats" at the end.)

---

## Executive summary

The reported "bug when opening from the system tray" is **not a defect in
existing code — it is a feature that does not exist yet.** Two independent
gaps produce the symptom:

1. **There is no tray-to-dashboard path at all.** The only tray app in the
   codebase is the webcam client (`webcam_client/gui/tray_app.py`), whose menu
   is 開啟設定 / 暫停推送 / 離開. Nothing anywhere opens the dashboard, lets a
   user pick nodes, or toggles sections.
2. **Wall mode has zero user control over content.** `WallView` hardcodes the
   top-9 nodes by severity and a fixed 3-column layout. There is no node
   selection, no section visibility toggle, no paging. The `wall-hide` /
   `wall-scale-up` CSS utilities that were clearly intended for this were
   defined and never wired up.

Separately, the wall display has real UX problems for its actual operating
context (a 4K screen read from across a room, unattended): session expiry
kicks the wall to a login screen, typography is sized for a desktop at arm's
length, and overflow content is a dead "+N more" pill.

The webcam tray also has a confirmed status-honesty bug (icon never reflects
disconnects) that is relevant if the tray is to become a launch surface.

---

## Priority 0 — Critical (the reported "bug")

### WAL-C1 — No tray-to-dashboard launch path exists
`webcam_client/gui/tray_app.py:62-68` (entire menu) · `webcam_client/main.py:81-88`

The tray menu contains exactly three items: 開啟設定, 暫停推送/恢復推送, 離開.
There is no "開啟監控牆" / "Open dashboard" item, no `webbrowser.open()` call,
no URL construction, and no second tray app for the dashboard. A grep across
the whole repo for tray/dashboard-launch wiring returns nothing.

The user's expectation — right-click tray → dashboard opens, choose which
nodes/sections appear — has no implementation to be buggy. **This is the
primary gap to close.**

### WAL-C2 — Wall mode offers no node selection or section visibility control
`central_server/static/spa/app.jsx:1755-1759, 1813, 1858` ·
`central_server/static/spa/styles.css:206-207`

- Node grid is `sorted.slice(0, 9)` — the nine highest-severity nodes, sorted
  `offline → critical → warn → online` (`app.jsx:1755-1759`). The operator
  cannot pin, reorder, include, or exclude any node.
- Alert ticker is `alerts.slice(0, 12)` (`app.jsx:1858`) — fixed cap, no
  filter, no rotation.
- The 3-column layout (monitor wall / 即時警報 / 風速+雨量+系統健康) is a
  hardcoded `grid-cols-[2fr_1fr_1fr]` (`app.jsx:1809`). No section can be
  hidden or "temp-closed."
- `styles.css:206-207` defines `html.wall-mode .wall-hide { display:none }`
  and `html.wall-mode .wall-scale-up { font-size:1.5em }` — **neither class is
  applied anywhere in any JSX** (grep-confirmed). The mechanism for
  per-section visibility was stubbed and abandoned.

Together WAL-C1 + WAL-C2 fully explain the user's report: they reached for a
tray-driven node/section picker that was designed (CSS hooks exist) but never
built.

---

## Priority 1 — High

### WAL-H1 — Wall display gets logged out by session expiry (unattended = broken)
`central_server/static/spa/app.jsx:698-701, 1662-1714`

Session keep-alive is deliberately activity-gated (`pointerdown`/`keydown`),
and the code comment acknowledges: *"A NOC wall display counts as abandoned by
this rule and WILL expire."* The blocking session-expiry modal (`z-[100]`,
rendered outside the wallMode branch) then paints a login prompt over the wall.

For the exact deployment this mode exists for — a wall screen nobody touches —
the display silently dies after the session TTL and stays dead until someone
walks over and re-authenticates. During a typhoon this is the worst possible
moment. The security rationale in the comment is real, but the current design
offers no middle ground (e.g. a long-lived read-only wall token, or a
wall-scoped session that shows data but no controls).

### WAL-H2 — Typography and chrome are desktop-sized, not 4K-viewing-distance-sized
`central_server/static/spa/app.jsx:1766-1806 (h-16 strip, text-xl badge, text-base clock), 1834-1835 (text-base node id, text-xs name), 1924 (h-8 footer, text-xs)`

The wall layout is proportional (`2fr 1fr 1fr` grid) but every font size is a
fixed Tailwind utility tuned for ~60cm viewing: node IDs at `text-base`,
names at `text-xs`, the entire footer (sparkline + handover note) at `text-xs`
in an `h-8` bar, status dots at `w-4 h-4`. On a 3840×2160 panel read from 3m,
most of this is illegible. The unused `wall-scale-up` (1.5em) hints the
original intent was a scale pass that never happened. There is **no
viewport-relative sizing and no wall-mode media query** anywhere.

### WAL-H3 — Overflow content is a dead affordance
`central_server/static/spa/app.jsx:1841-1848, 1872-1879`

Beyond 9 nodes / 12 alerts the wall shows a "+N more" pill that is not
interactive — no click handler, no rotation, no auto-cycle, no tooltip. On a
deployment with 12+ cameras (the normal case for a building with multiple
glass facades) the wall permanently hides a third of the fleet with no way to
see them short of exiting wall mode and opening the monitor page. A NOC wall
that can't show the whole fleet defeats its purpose.

### WAL-H4 — Webcam tray status icon never reflects reality
`webcam_client/main.py:88` · `webcam_client/gui/tray_app.py:50-56`

`tray.set_status(True)` is called exactly once at startup and never again.
`TrayApp.set_status()` and `_refresh_icon()` are correctly implemented, but
nothing ever calls `set_status(False)`: server down, API key revoked (the
control channel stops on 401), or a camera unplugged all leave the icon
permanently green. `PushEngine.run()` also returns silently when a camera
fails to open. The tray lies about system health — directly relevant if the
tray is to become the dashboard's launch surface, because operators will read
its color as "everything is fine." (Already documented as a Phase-2 item in
`docs/superpowers/specs/2026-07-26-webcam-startup-and-guard-ux-design.md`.)

### WAL-H5 — Wall "Live" pill asserts liveness during a connection outage (second pass)
`central_server/static/spa/app.jsx:1777` vs `components.jsx:484`

The wall's freshness pill always renders the literal string
`` `Live · ${liveSec}s` ``. When the WebSocket drops, `liveSec` climbs and the
pill correctly turns amber then red — but the text still says **"Live · 120s"**.
The StatusStrip solved this exact problem: its label degrades to
`重新連線中… Ns` / `連線中斷 Ns` (`components.jsx:484`). The wall — the display
a room reads at a glance during an outage — shows a red pill labelled "Live",
a direct contradiction that under-reports exactly the failure mode (lost
telemetry during a typhoon) it exists to catch.

### WAL-H6 — Wall mode never surfaces partial load failures (second pass)
`central_server/static/spa/app.jsx:1509-1544 (wall branch) vs 1575-1583 (banner)`

The amber "X 無法載入 — 顯示快取資料" data-warning banner is rendered only
inside the normal-shell branch. The wall branch has no equivalent. When
`loadNodes` / `loadWeather` / `loadHandover` fail (startup `allSettled` or a
later refresh), the wall silently shows stale cached data — or an empty node
grid that reads as an all-clear — with zero indication. The operator shell
warns; the wall lies by omission. This is the same "data honesty" seam the
2026-07-20 audit flagged elsewhere, surviving in the one view where nobody can
click anything to discover the truth.

---

## Priority 2 — Medium

### WAL-M1 — No direct-entry path to wall mode (URL param / kiosk)
`central_server/static/spa/app.jsx:20-34 (RESTORED_STATE only), 1642 (toggle)`

The only way into wall mode is: gear trigger → TweaksPanel → 檢視模式 → toggle.
There is no `?wall=1` query parameter, no dedicated route, and no fullscreen
API call. A tray launcher or a NOC machine's boot shortcut has no way to open
the wall directly — it would have to open the dashboard and simulate three
clicks. `RESTORED_STATE` (`app.jsx:20-34`) round-trips `{page, selectedId,
hadDraft}` across login but knows nothing about wall mode. Any tray-launch
feature (WAL-C1) needs a direct-entry mechanism first.

### WAL-M2 — Wall settings unreachable from within wall mode
`central_server/static/spa/tweaks-panel.jsx:219`

`html.wall-mode .twk-trigger{display:none}` hides the gear while in wall mode
(correctly, to avoid overlapping the exit button). Consequence: once in wall
mode there is no way to adjust *anything* — theme, density, or (future) node
selection — without first exiting to the operator shell. If node/section
selection is added (WAL-C2), its controls must be reachable from inside wall
mode or the feature is unusable on a dedicated wall box.

### WAL-M3 — Wall clock forces a full WallView re-render every second
`central_server/static/spa/app.jsx:1750-1754`

`setWallClock(Date.now())` on a 1s interval re-renders the entire WallView —
all 9 snapshot tiles, the 12-item ticker, weather tiles — every second, just
to tick the clock. `SnapshotImage` tiles each also poll their JPEG at 1Hz
(`components.jsx:267 SNAPSHOT_REFRESH_MS`). The clock should be isolated in a
leaf component so the per-second render touches only the time string.

### WAL-M4 — Rain tile permanently advertises missing data
`central_server/static/spa/app.jsx:1909`

`即時雨率 — 資料來源未提供` renders on every wall, forever, because the backend
exposes only a 24h total. Honest (good), but a permanent "data not available"
line on the wall's largest rain tile is noise that a room learns to ignore —
and ignored "unavailable" labels are exactly what get ignored when they flip
to meaningful. Consider collapsing it when there is genuinely nothing to show.

### WAL-M5 — Tray pause is all-or-nothing; no per-node control
`webcam_client/gui/tray_app.py:73-81` · `webcam_client/app_controller.py` (pause_all/resume_all)

`_toggle_pause` calls `controller.pause_all()` / `resume_all()`. There is no
per-camera pause and no submenu listing cameras. The user's "select which one
to show up" mental model implies per-node granularity that the tray cannot
express today.

### WAL-M6 — No single-instance guard on the tray app
`webcam_client/main.py:59-88`

Nothing prevents a second `SDPRS_Webcam.exe` from launching and fighting the
first for the same DSHOW camera devices. Combined with the 39–60s silent
startup (no splash screen — documented as S4 in the startup design doc),
double-launch is the natural user behaviour, and the result is a broken
camera feed with no explanation. A tray-launched dashboard would inherit the
same risk if it ships as a second tray process.

### WAL-M7 — Wall mode entry is a 3-step hidden flow on a machine meant to boot into it
`central_server/static/spa/app.jsx:1642` · `tweaks-panel.jsx:397-433`

`wallMode` persists in localStorage, so a dedicated wall box does re-enter the
wall after reload — but the *first* entry requires discovering the small gear
trigger (bottom-right, 36px, easily missed on a large screen), opening the
panel, and finding the toggle under 檢視模式. For a NOC machine there is no
"set this machine as a wall" one-time action.

### WAL-M8 — Online-but-stale camera tiles are not visually frozen on the wall (second pass)
`central_server/static/spa/app.jsx:1815` vs `components.jsx:275` · `styles.css:104-106`

WallView applies the `snapshot-frozen` class (grayscale + dim) only when
`n.status === 'offline'`. But `SnapshotImage` independently treats
`upload == null || upload > 60` as frozen and swaps the live frame for the
icon fallback. A node that is still reporting heartbeats but whose frames
stopped 2 minutes ago therefore renders on the wall as: **green status dot +
camera icon, no grayscale, no 離線 overlay** — indistinguishable from "camera
fine, feed just not shown." The wall under-reports degraded feeds precisely
when a feed going stale is the signal (a camera about to be lost to the storm).

### WAL-M9 — Alert ticker mixes acknowledged into the live count and can bury critical alerts (second pass)
`central_server/static/spa/api.jsx:596-603` · `app.jsx:1855, 1858, 1861`

`loadAlerts` fetches `PENDING_VIDEO,PENDING,ACKNOWLEDGED`, and the backend
orders `timestamp DESC` (newest first). The wall ticker then: (a) shows
`{alerts.length} 筆` — counting already-acknowledged alerts the room is no
longer responsible for; and (b) slices the 12 newest regardless of severity,
so a burst of fresh info-level alerts can push an older unacked critical out
of the visible 12 entirely. The only state styling is a faint background on
pending+critical rows. A wall ticker should lead with unacked severity, not
recency-with-handled-items-mixed-in.

### WAL-M10 — Light theme renders white panels on the black wall (second pass)
`central_server/static/spa/app.jsx:434-439` · `styles.css:122-138`

The `dark`/`light` class toggle runs regardless of `wallMode`, and WallView
uses theme tokens (`bg-surface-panel`, `text-ink-primary`). With the theme set
to light, `html.light .bg-surface-panel { #FFFFFF !important }` turns the wall
into white panels on a black field — readable, but a wall of bright white
cards in a dim NOC room is glare, and it silently breaks the mode's design
intent (dark glass on black). Wall mode should force the dark palette (or at
minimum not render white surfaces); today nothing prevents this state.

---

## Priority 3 — Low

### WAL-L1 — Exit button is always visible on the wall
`central_server/static/spa/app.jsx:1534-1543`

The 「離開牆面模式」 button sits at `bottom-10 right-3` permanently. Correct for
escapability (the WHA-H4 fix), but on a wall display it is constant visual
noise and an accidental-click target for anyone with mouse access. A
fade-after-idle treatment would preserve both goals.

### WAL-L2 — Top-strip "NOC WALL · v2.4" is a hardcoded version string
`central_server/static/spa/app.jsx:1773`

Cosmetic, but a manually maintained version label drifts from the actual
release and means nothing to the operator.

### WAL-L3 — Alert ticker container scrolls but content is capped at 12
`central_server/static/spa/app.jsx:1857-1858`

The ticker body is `overflow-y-auto` yet `slice(0, 12)` bounds the items, so
on most screens the scroll is vestigial. Either drop the cap and let the wall
scroll, or drop the scroll affordance.

### WAL-L4 — Wind direction rendered twice with different formats
`central_server/static/spa/app.jsx:1800, 1893`

Top strip shows `dir + speed km/h`; the hero tile shows `dir + degree°`.
Two representations of the same measurement in different formats on one wall
invites "which one is right?" confusion.

### WAL-L5 — `WallSnapshot` defensive fallback is now mostly dead code
`central_server/static/spa/app.jsx:1736-1743` · `components.jsx:333`

`SnapshotImage` is now deliberately exported (`window.SnapshotImage =
SnapshotImage`), so the accidental-global fallback branch rarely fires. Harmless,
but the long comment describing the fragility is stale — the primary path is
now a real export.

---

## What already works well (do not regress)

- **Escapability (SHL-2 / WHA-H4):** Escape handler + exit button rendered
  outside the ErrorBoundary (`app.jsx:1398-1409, 1530-1543`). The inescapable-
  wall trap is genuinely fixed.
- **ErrorBoundary around WallView (SHL-17):** a render crash shows a fallback
  instead of blanking the whole wall (`app.jsx:1517-1529`).
- **Data honesty on offline tiles:** `離線 · 從未回報` vs a real duration
  (`app.jsx:1820-1830`); the bare-unit weather bugs (WHA-M6) are fixed.
- **Live freshness pill:** `Live · Ns` driven by WS age with ok/warn/critical
  thresholds (`app.jsx:1746-1747`).
- **IME-safe hotkey handling** and wall-mode hotkey lockdown (`app.jsx:1069-1083`).
- **Tweaks persistence** via a committed-state `useEffect` (WHA-M13 fix,
  `tweaks-panel.jsx:243-256`) — no stale-write risk.

---

## Recommended direction (for a follow-up design, not implemented here)

1. **Direct wall entry first (unblocks everything):** a `?wall=1` (or
   `/wall`) entry point that boots straight into WallView, survives login
   round-trips, and optionally requests fullscreen. This is the substrate the
   tray launcher needs (fixes WAL-M1, WAL-M7).
2. **Tray launcher:** a tray item (either in the existing webcam tray or a
   thin dedicated launcher) that opens the wall URL. Decide single vs dual
   tray process given WAL-M6.
3. **Wall content configuration:** persist a wall config (selected node ids,
   visible sections, tile order) — settable from a small in-wall control layer
   (fixes WAL-M2) or from the operator shell before entering. Wire up the
   existing `wall-hide` utility rather than inventing a new mechanism
   (fixes WAL-C2, WAL-H3).
4. **Wall-session policy:** a read-only, long-lived wall session or token so
   an unattended wall doesn't die (fixes WAL-H1) — needs an explicit security
   decision, not just a frontend change.
5. **4K type scale:** viewport-relative sizing or a wall-mode scale pass
   (fixes WAL-H2); isolate the wall clock render (WAL-M3).
6. **Tray status honesty (WAL-H4)** before the tray becomes a launch surface
   operators trust.

---

## Coverage caveats — what this audit did and did not verify

This was a **static, read-only code review** (two passes). It is not a
guarantee that every problem has been found. Specifically:

**Verified by reading the code:** wall layout & truncation, exit/escape flow,
session-expiry interaction, tweaks persistence & reachability, tray menu &
status wiring, the data pipeline feeding the wall (loadInitial/refreshLive →
window.* → WallView props), alert ordering & status filter, theme-token
interaction, SnapshotImage frozen logic, liveness-pill labelling.

**NOT verified (would need runtime / a real 4K display):**
- Actual rendering at 3840×2160 (real legibility, overflow, scrollbar
  behaviour, Tailwind CDN behaviour offline on the wall box).
- WebSocket reconnect behaviour as observed from the wall (reconnect backoff
  was read in api.jsx but not exercised; the `liveSec` degradation path is
  inferred, not run).
- Performance under load (9 tiles × 1Hz JPEG + per-second re-render is flagged
  as WAL-M3 from code shape, not measured).
- Browser kiosk/autoplay policies on a wall box (audio, fullscreen).
- Multi-display setups (wall on a second monitor while operators use the
  first) — no code path exists for this, but none was requested either.
- The backend API contract beyond alert ordering (snapshot endpoint, weather
  provider failover) — out of the UI/UX scope requested.
- Anything in the edge/pump firmware that affects what the wall displays.

If completeness matters for sign-off, the next step would be a runtime pass:
run the wall on a 4K output, kill the WS and each API in turn, and observe.
Every finding above, however, is evidenced in code and reproducible by
inspection.

---

## Verification 2026-08-01 (branch `fix/wall-mode-audit-2026-08-01`)

Independent re-verification against `main` (56dc0e7) confirmed, refuted, or
reclassified every finding. Plan: `docs/audits/2026-08-01-wall-mode-fix-plan-for-glm.md`.

| ID | Verdict | Resolution |
|----|---------|------------|
| WAL-H5 | **FIXED** | `liveClockLabel` helper in data.jsx; wall pill degrades text. Commit 860504b. |
| WAL-H6 | **FIXED** | `dataWarnings` prop threaded to WallView; amber banner renders. Commit 253d764. |
| WAL-M3 | **FIXED** | WallClock + WallLivePill leaf components isolate the 1s re-render. Commit 5f9ea30. |
| WAL-M8 | **FIXED** | `wallTileFrozen` helper; stale-online tiles get snapshot-frozen. Commit 860504b. |
| WAL-M9 | **FIXED** | `activeAlertCount` + `orderWallAlerts` helpers; honest count + severity sort. Commit 4c98445. |
| WAL-M10 | **FIXED** | `effectiveTheme` helper; wall forces dark palette. Commit 860504b. |
| WAL-M4 | Deferred | Low priority rain-tile cosmetic; skipped per plan §2.7. |
| WAL-M6 | **REFUTED** | Single-instance guard already exists on main. No action. |
| WAL-H4 | **SUPERSEDED** | `TrayApp.set_status` deliberately removed; finding no longer applies. |
| WAL-C1, C2, H1, H2, H3, M1, M2, M5, M7 | FEATURE/DESIGN | Confirmed gaps requiring explicit design sign-off. Not built. |
| WAL-L1..L5 | LOW/cosmetic | Skipped per plan. |
