# SDPRS Dashboard — Full UI/UX & Engineering Audit (2026-07-20)

**Method:** 6 parallel read-only audit agents, each with a disjoint scope, every finding verified
against actual code with file:line evidence. No code was modified. Slices:

| ID prefix | Scope |
|---|---|
| `CMP` | components.jsx, styles.css, icons.jsx (shared component library) |
| `API` | api.jsx ↔ central_server/api/* (frontend↔backend contract, WS events, auth, datetimes) |
| `ALR` | pages/alerts.jsx |
| `MSP` | pages/monitor.jsx, pages/status.jsx, pages/pumps.jsx |
| `WHA` | pages/weather.jsx, pages/handover.jsx, pages/audit.jsx, tweaks-panel.jsx |
| `SHL` | app.jsx, api.jsx (WS/state plumbing), data.jsx, index.html |

**Totals after cross-agent dedup: 3 Critical · 16 High · ~40 Medium · ~50 Low.**
Findings independently confirmed by two agents are marked ✦.

---

## Executive summary

The codebase is visibly post-remediation — double-submit ref guards, PENDING_VIDEO gating,
focus traps, parseTs naive-UTC normalizer, soft-401 flow, WS reconnect/backoff are all
correctly built. The remaining defects cluster at **five systemic seams**, and these —
not the individual button handlers — explain the reported "buttons and UI interaction" bugs:

1. **Promises that lie about success.** app.jsx's `onAck/onResolve/onSnooze` swallow errors
   (toast + normal return), so child components' carefully written failure branches are dead
   code: resolve notes are wiped on failure, the snooze menu closes on failure looking like
   success (ALR-H1, ALR-M8). The same lie-by-200 pattern exists on weather refresh (API-F4 ✦)
   and pump commands report "sent" for commands the device silently drops (MSP-F1).
2. **State that moves without operator consent.** Overlay lifecycle effects keyed on an
   unstable `onClose` prop steal focus from every open modal on every data tick (CMP-F1);
   WS refresh silently swaps the selected alert under the operator (ALR-H3); the monitor
   grid re-sorts under the cursor (MSP-F10); the handover textarea reverts to the old note
   right after a successful save (WHA-C1).
3. **Two views of the same list.** App-level keyboard nav walks the unfiltered alert list
   while the page renders a filtered one — arrow triage freezes and auto-advance picks
   hidden/stale targets (ALR-H2, SHL-8).
4. **Data honesty gaps.** Stale/never-reported values rendered as live: offline pump gauges
   (MSP-F3), fabricated "16m" heartbeats from a 999 sentinel (MSP-F8), a hardcoded-flat trend
   arrow (MSP-F14), forecast hours shifted +8h (API-F1), always-0 bitrate (API-F3).
5. **One-side-only features.** Lightning auto-mute handled but never emitted (SHL-1 ✦),
   presence/carry-over badges with no data source (ALR-L1, SHL-15), an unreachable tweaks
   panel guarding the only wall-mode toggle (WHA-H4 ✦), `/api/session/extend` and
   `/api/stream/health` built but never called (API-F7, API-F3).

**Highest-leverage fixes:** (a) rethrow from app.jsx action handlers; (b) one shared
`useModalBehavior` hook replacing six copy-pasted overlay implementations; (c) a
"sent → device confirmed" feedback loop on pump commands; (d) sync `window.HANDOVER.current`
after save; (e) pick one datetime serialization rule.

---

## Priority 0 — Critical

### MSP-F1 — Pump UI is an open loop: actual pump state is never shown
`pumps.jsx` (whole page) + `api.jsx:246-286`. Backend serves `pump_state` (nodes.py:57,190) and
documents that the device **silently drops** ON commands under dry-run/sensor-conflict protection
(nodes.py:497-499) — but `mapNode` never maps `pump_state` and no page renders a 運轉中/停止
indicator. Operator clicks ▶30s during a flood, sees 「已送出」, and never learns the pump never ran.
**Fix:** map `pump_state`, render a prominent state indicator on every pump card, driven by the
existing `pump_status` WS→refresh path.

### WHA-C1 — Handover note reverts to the OLD note after a successful save; in-flight keystrokes destroyed
`handover.jsx:127-132` + `:232-253`. `performSave` sets `dirty=false` but never updates
`window.HANDOVER.current`, so the silent-adoption effect immediately overwrites the textarea with
the stale pre-save server copy (until the next 20s poll). Typing during a slow save → `dirty`
cleared over the trailing keystrokes → localStorage draft deleted → text overwritten = permanent
loss; can also fire a phantom conflict banner against the operator's own save.
**Fix:** sync `window.HANDOVER.current = savedText` on success and only clear `dirty` if the
current text still equals the saved snapshot.

### CMP-F1 — Every overlay loses focus on every data tick (unstable `onClose` in effect deps)
`components.jsx:853, 973, 1435, 1616` + `app.jsx:1137-1143`. Overlay open/close effects depend on
`onClose`, which app.jsx recreates every render — so every ~20s poll / WS alert tears down and
re-runs the effect: cleanup "restores" focus behind the modal, the re-run re-grabs it (or not, for
CommandPalette/ShortcutsModal). Stray keystrokes then hit global single-key hotkeys (`A`=ack,
`1-7`=page switch, `M`=mute). Makes every modal/drawer/palette unreliable exactly when alerts flow.
**Fix:** drop `onClose` from deps (ref pattern) or `useCallback` in app.jsx — the logout dialog
(deps `[logoutConfirmOpen]`) already does it right.

---

## Priority 1 — High

### Alerts interaction integrity
- **ALR-H1 — Resolve note wiped even when the API call fails.** `alerts.jsx:889-897` +
  `app.jsx:672-697`: parent catches, toasts, returns normally → child's keep-the-note catch is
  dead. Same root kills the snooze menu's retry path (**ALR-M8**, `alerts.jsx:543-550` +
  `app.jsx:699-719`). **Fix:** rethrow from `onAck/onResolve/onSnooze` after toasting.
- **ALR-H2 — Keyboard ↑/↓ triage broken under any filter/search.** `alerts.jsx:297-301` vs
  `app.jsx:928-937`: nav walks the unfiltered list, page snaps selection back to `filtered[0]` —
  stuck selection; `findNextUnack` ignores filters. **Fix:** expose the page's `filtered` list to
  the app keyboard layer.
- **ALR-H3 — Selected alert silently replaced under the operator, draft note destroyed.**
  `alerts.jsx:288-301`: peer resolve via WS or the operator's own filter keystroke swaps the
  detail pane to a different alert (fast click acks the wrong one) and wipes `resolveNote`.
  **Fix:** tombstone banner instead of auto-reselect; never discard a non-empty note silently.
- **SHL-8 — Ack/resolve auto-advance picks "next" from pre-refresh data** (`app.jsx:663-665,
  690-692`): selects an alert a peer may have just resolved → dead selection, keyboard inert.
  **Fix:** choose the next target after `await refresh()` from the fresh list.

### Pump command safety (pages/pumps.jsx)
- **MSP-F2 — No confirmation before commanding hardware.** `pumps.jsx:35-55`: single bare click
  fires ▶10s/▶30s/⏹停機; ⏹ is an **indefinite** OFF hold. Compounded by **MSP-F16**: ~22px-tall
  buttons, ⏹ 6px from ▶30s. **Fix:** confirm/two-step arm at minimum for ⏹; ≥32px targets.
- **MSP-F4 — Command errors collapse to 「網路或權限問題」.** `pumps.jsx:24-26` discards
  `e.detail/e.message` (broker down 502, not-a-pump 400, 503) and misreports timeouts — the POST
  may still execute server-side while the UI says "failed" → operator re-sends → double command.
  **Fix:** surface detail; distinct 「逾時—指令可能已送出」 message.
- **MSP-F5 — In-flight ends at HTTP ack, not device ack; no cross-operator visibility.**
  `pumps.jsx:14,27-28`: buttons re-enable in ~100ms; nothing shows "last command: OFF by alice
  14:32" although every command is audit-logged. Operator B overrides A's OFF hold unknowingly.
  **Fix:** hold card in 「等待裝置回報…」 until next `pump_status`; render last audit-logged command.
- **MSP-F6 — Manual OFF hold has no release-to-automatic control** (`pumps.jsx:49-55`,
  nodes.py:479-481): forgot-to-restore = pump held OFF when rain arrives. **Fix:** 「恢復自動」
  action + visible 「手動停機中」 banner.

### Monitor wall data honesty
- **MSP-F3 — Offline pump renders stale last-known level as a live gauge.** `monitor.jsx:346-356`:
  the offline guard already shipped in pumps.jsx:133-137 was never ported to monitor's PumpCard;
  offline is color-only (blinking dot identical to critical-level). **Fix:** port the guard + add
  a textual 「離線」 badge.

### Shared components / interaction layer
- **CMP-F2 — No IME guard in CommandPalette** (`components.jsx:1466-1470`): Enter committing a
  zh-TW composition fires the highlighted command and closes the palette; arrows hijacked during
  candidate selection. **Fix:** bail on `isComposing || keyCode === 229`.
- **CMP-F3 — All six focus traps leak when first/last focusable is disabled**
  (`components.jsx:334, 605, 833, 950, 1415, 1593`): Tab escapes the aria-modal (MuteDrawer's
  last button is usually disabled). **Fix:** `:not([disabled])` in the shared selector — ideally
  one `useFocusTrap` hook.
- **CMP-F4 — Light theme: selected row is dark-on-dark.** `styles.css:80-83` hardcodes
  `#111A2E !important` with no `html.light` override — the selected alert row becomes unreadable.
  (See also CMP-F9: `sev-warn` amber ≈1.9:1 contrast on white; opacity-variant surface classes
  keep dark colors.) **Fix:** light-theme overrides for `.row-selected`, `sev-warn`, `/NN` variants.

### Weather / time correctness
- **API-F1 — Forecast hour labels shifted +8h.** `weather_service.py:469,489` produces naive
  *Asia/Macau local* datetimes; `api.jsx:95` appends 'Z' (assumes UTC) → every forecast hour reads
  8h late on a Macau browser ("peak gusts 16:00" = actually 08:00). Adjacent verified backend bug:
  `weather_service.py:765-767` compares aware vs naive → `TypeError` whenever forecast data exists.
  **Fix:** serialize forecast times as UTC (or request `timezone=UTC`).
- **API-F4 ✦ (= WHA-H1) — Weather refresh failure returns 200 `{ok:false}`; SPA never checks.**
  `weather.py:189-193` vs `weather.jsx:174-197, 396-409`: upstreams down mid-typhoon → toast says
  「天氣資料已重新載入」 while tiles stay stale. **Fix:** check `r.ok !== true` in both call sites.

### Other High
- **WHA-H2 — Audit log rows show HH:MM:SS only, no date**, under a 7-day filter
  (`audit.jsx:265`, `api.jsx:389`): multi-day events indistinguishable. **Fix:** render
  `MM-DD HH:MM:SS` from the (correctly parsed) `a.ts`.
- **WHA-H3 — Handover 2000-char server cap with zero client validation** (`handover.py:32` vs
  `handover.jsx:334-339`): long typhoon-night note → cryptic `HTTP 422` toast forever (422 list
  detail is dropped by `api.jsx:65-67`). **Fix:** `maxLength` + live counter + explicit message.
- **WHA-H4 ✦ (= SHL-2) — Wall mode is an exit-less trap; tweaks panel unreachable in prod.**
  Nothing ever posts `__activate_edit_mode`, so the panel — sole `wallMode` toggle — is dead UI;
  a persisted `wallMode:true` boots into WallView with all hotkeys disabled
  (`app.jsx:778, 1079, 1172`) and no in-app exit (recovery = devtools). **Fix:** in-app activator
  + an exit affordance inside WallView.
- **SHL-1 ✦ (= API-F2) — Lightning auto-mute is dead code.** `app.jsx:535-547` handles a
  `weather` WS event that is (a) not in the `_WS_EVENT_TYPES` whitelist (`api.jsx:834-838`) and
  (b) never emitted by any backend code. The toggle does nothing, forever. **Fix:** implement the
  emit + whitelist it, or delete the branch and the toggle.

---

## Priority 2 — Medium (by area)

### Alerts page
- **ALR-M1** `alerts.jsx:151-153,326` — 作用中 tab badge shows the *history* count while on the 歷史 tab.
- **ALR-M2** `alerts.jsx:379` — advertised "(Esc)" clear-bulk-selection shortcut doesn't exist.
- **ALR-M3** `app.jsx:895-901` — number-key template *replaces* a hand-typed note; chip click path appends.
- **ALR-M4** `app.jsx:672-675` — whitespace-only note passes keyboard-R resolve (`!note` vs `.trim()`).
- **ALR-M5** `alerts.jsx:372-374` — bulk-resolve of up to 200 alerts: no confirmation, no undo endpoint.
- **ALR-M6** `alerts.jsx:216-227,249-261` — partial bulk failure clears the selection of the rows that FAILED.
- **ALR-M7** `api.jsx:424` — active list capped at 200, oldest (longest-waiting) alerts silently invisible; no truncation notice.
- **ALR-M8** — snooze menu closes on failure (see ALR-H1 root cause).

### Monitor / Status / Pumps
- **MSP-F7** `status.jsx:52-63` — stream toggle state inferred from cached bitrate → desync + double-fire.
- **MSP-F8** `api.jsx:253-254` — `999` sentinel renders fabricated 「心跳 16m」/「999s」 for never-reported nodes.
- **MSP-F9** `api.jsx:240-243` — camera 上傳 age silently substitutes heartbeat age when no snapshot exists; broken-pipeline detector can never fire.
- **MSP-F10** `monitor.jsx:175-178` — status-sorted grid re-sorts under the cursor on every refresh (click targets teleport).
- **MSP-F11** `status.jsx:115-122` — in-flow toast shifts the whole table mid-interaction (next click lands on the wrong row's device button). Fix: overlay like app.jsx's global toast.
- **MSP-F12** `status.jsx:224-226 vs 248` — amber badge still labeled 「正常」 when pump level missing.
- **MSP-F13** `status.jsx:9-34,302-308` — snooze invisible & irreversible from the table; repeat clicks silently reset the 30-min window.
- **MSP-F14** `api.jsx:270-271` — `trend/flow/cycleHistory` hardcoded null but render as definite claims (permanent 「→/平」 trend, dead 流量 metric, never-rendered cycle chart).
- **MSP-F15** `pumps.jsx:119-127` etc. — safety badges English-only (`Dry-run protect (pump held OFF)`) in a zh-TW UI — the very flag that explains "why didn't my ON run".
- **MSP-F16** — hit targets (see MSP-F2).

### Weather / Handover / Audit / tweaks
- **WHA-M1** `audit.jsx:17-21,215` — CSV export double-submit guard self-disarms after 3s while export may run 10s.
- **WHA-M2** `audit.jsx:270` — `sev-muted` token doesn't exist → most action badges render unstyled.
- **WHA-M3** (+ **API-F5**) `weather.jsx:127` + `weather.py:76-85` — failed config GET silently shows defaults; saving then NULLs the real stored config. Independently: SPA never round-trips `station_name`, so **every** save clears it.
- **WHA-M4** `weather.jsx:603-613` — titled 「36 小時預報」 but data layer slices to 16 buckets.
- **WHA-M5** `weather.jsx:641-691` — chart gridlines geometrically meaningless (fixed-top lines over bottom-aligned downward-growing bars).
- **WHA-M6** (+ **SHL-14**) `api.jsx:350-352` — rain hero tile headline permanently 「—」 (`now/hour` hardcoded null); WallView header permanently shows bare " mm/h".
- **WHA-M7** `weather.jsx:445-452` — weather-unavailable early return hides settings + refresh; dead end exactly when needed.
- **WHA-M8** `handover.jsx:254-272` — concurrent-edit check diffs against a ≤20s-stale copy; silent last-write-wins clobber. Fix: `updated_at` precondition → 409.
- **WHA-M9** `handover.jsx:65-69` — destructive confirm autofocuses the destructive button (Enter destroys draft). Same pattern: **CMP-F8** logout confirm autofocuses 登出.
- **WHA-M10** `api.jsx:501` — 歷史備註 pane permanently dead (`history: []` hardcoded, no backend endpoint).
- **WHA-M11** `handover.jsx:78` — draft key not user-scoped; drafts leak across logins on a shared console and publish under the wrong name.
- **WHA-M12** `handover.jsx:177` — header date uses UTC day: wrong for the entire 00:00–08:00 Macau night shift.
- **WHA-M13** `tweaks-panel.jsx:182-191` — persistence reads the setState updater's result outside the updater; rapid changes can store `"undefined"` → all tweaks silently reset on reload.
- **WHA-M14** `api.jsx:532` — audit page silently caps at newest 200 rows; filters imply full history; CSV and screen disagree.

### Shared components
- **CMP-F5** `components.jsx:16` — `safeSevMeta` fallback calls nonexistent `Icon.HelpCircle` — the crash-guard itself throws.
- **CMP-F6** `components.jsx:540,860,1041,1473,1655` — backdrop closes on `click`: drag-select ending outside dismisses the overlay and discards edits. Fix: mousedown-target check.
- **CMP-F7** `components.jsx:1160-1163,1201-1207` — 解除/解除所有 no in-flight disable → double-submit, stale errors.
- **CMP-F9** — light-theme opacity-variant + `sev-warn` contrast (see CMP-F4).
- **CMP-F10** `components.jsx:1671,1529,1289` — color-only node status dots (no text/aria).
- **CMP-F11** `components.jsx:1461` — palette node results discard the chosen id → dead-end navigation to generic status page.

### Contract / shell
- **API-F3** — bitrate/drops always 0: UI reads fields the edge never publishes; `/api/stream/health` (which computes them) has no caller.
- **API-F6** — WS auth is connect-time only; expired/logged-out sessions keep receiving live telemetry until the socket drops.
- **SHL-3** `api.jsx:279` — `snoozedAt` bypasses `parseTs` → snooze provenance chip shows time 8h off (「由 alice 於 02:14 設定」 for a 10:14 snooze).
- **SHL-4** `app.jsx:338-341` — popstate treats null-state entries (skip-link hash) as "go to Alerts"; can wedge the history-push guard so Back desyncs.
- **SHL-5** `app.jsx:448-454,712` — `muteState.nodes` hydrates from server snooze state once, then drifts all session (expired snoozes stay muted; peer snoozes never appear).
- **SHL-6 ✦ (= WHA-L3)** `index.html:25` — theme anti-flash script reads `localStorage['theme']`, which nothing writes (theme lives in `sdprs.tweaks`); light-theme users get a dark flash every reload.
- **SHL-7** `index.html:113` — boot spinner never spins: `spin-slow` keyframes are never generated (no `animate-spin-slow` utility anywhere). Slow load looks like a hung tab.

---

## Priority 3 — Low (compact)

**Alerts:** ALR-L1 dead 正在查看/↶上班 presence badges (fields never mapped; also ambiguous copy) ·
ALR-L2 SystemOKState claims 「WebSocket 連線中」 unconditionally with render-time as 最後檢查 ·
ALR-L3 bulk guard uses async state not the ref pattern · ALR-L4 bulk note no maxLength → opaque 422 ·
ALR-L5 `ackedIds` never pruned → red flash replays on every remount, set grows unbounded ·
ALR-L6 清除篩選 also yanks the tab back to 作用中 · ALR-L7 snapshot branches dead (fields never
mapped) + index keys on history carousel · ALR-L8 sibling "+N" counts resolved alerts on 歷史 tab.

**Monitor/Status/Pumps:** MSP-F17 dead `r.ok===false` branch (✦ API-F11) · MSP-F18 `pumpCommand`
called without the existence guard status.jsx has · MSP-F19 raw-seconds ages (「畫面凍結 86400s」)
· MSP-F20 heartbeat alarm thresholds differ between status (5s/60s) and monitor (10s/30s) ·
MSP-F21 temp `0` renders as 「—」 (falsy check) · MSP-F22 click-to-cycle "dropdowns" with fake
chevron; nested-interactive clear-×; raw slate colors invisible on light theme · MSP-F23 24px
buttons at 60% opacity until row hover · MSP-F24 `exitFullscreen()` promise unhandled ·
MSP-F25 ticking wall-clock inside snapshot strip reads as frame timestamp · MSP-F26 setState in
`.finally` after potential unmount · MSP-F27 pump success toast tone `info` not `ok`.

**Weather/Handover/Audit/tweaks:** WHA-L1 meOnly+今日 CSV/screen mismatch early in a calendar day ·
WHA-L2 peak badge 「—」 when true peak = 1 (axis-floor conflated) · WHA-L4 `__omelette_rail_enabled`
listener missing origin gate; `'*'` targetOrigin · WHA-L5 tweaks panel hardcoded light-glass on the
dark NOC UI and z-index 2147483646 floats above the blocking session-expiry modal · WHA-L6 index
keys on refreshing audit rows · WHA-L7 「資料較舊」 stale marker dropped in the typhoon-active
header branch (highest-stakes moment) · WHA-L8 `TweakNumber` coerces emptied field to 0 instantly.

**Shell/API:** SHL-9 session-expiry modal doesn't block single-key shortcuts (`1-7` change the
page the re-login roundtrip restores) · SHL-10 first paint blocks on all seven loaders (~20s
worst case behind the frozen spinner; mount-then-load is already safe) · SHL-11 401 errors carry
no `.status`, leak "unauthorized" into zh-TW toasts · SHL-12 `refreshLive` catch reports
`failures: []`, clearing the stale-data banner on total failure · SHL-13 data.jsx WEATHER
placeholder drifted from `mapWeather` shape (latent crash) · SHL-15 `OPERATORS_ONLINE` never
populated — presence cluster always empty · SHL-16 audit CSV filename stamped with UTC date ·
SHL-17 `SnapshotImage` resolves cross-file only via Babel preset-env `const`→global-`var`
accident (not exported; breaks under any build change; WallView is outside the ErrorBoundary) ·
SHL-18 ✦ `mapAuditRow.ts` returns a Date, not the documented ms (dead NaN guard) ·
SHL-19 `__SDPRS_USER__` try/catch can't catch the bad-escaping SyntaxError path ·
SHL-20 `fetchedAt` bypasses `parseTs` — safe today only because the weather service breaks the
naive-UTC convention (latent +8h if "fixed") · API-F7 `/api/session/extend` never called — active
operators hard-expire mid-shift at 24h · API-F8 `GET /api/nodes/{id}` missing 8 fields the list
sends (latent) · API-F9 pump-cycle `alert` threshold flag computed server-side, never surfaced ·
API-F10 422 list details dropped by the toast extractor · API-F12 three datetime conventions
coexist (naive-UTC, manual-'Z', tz-aware) — one refactor from an API-F1-class bug; stale comments
(`apiFetch` "redirects", ping "~10s").

**Components:** CMP-F12 modal state not reset between opens (stale search filter, stale error
banners) · CMP-F13 test-audio feedback via direct DOM + untracked timers · CMP-F14 `SnapshotImage`
no `onError` fallback (broken-image glyph) · CMP-F15 location edit: no Enter-to-save, Escape
discards draft instead of exiting edit mode · CMP-F16 index keys on live history list ·
CMP-F17 English strings in core zh-TW UI (`Reconnecting…`, `Disconnected`) · CMP-F18 assorted:
24px hit targets, `role="alert"` on a button, hardcoded `aria-expanded`, `Pill` undefined tone,
two identical Moon icons, missing `type="button"`, `100vh` vs `100dvh`, unsnoozeAll no-API
fallback clears unconfirmed state.

---

## Verified clean (checked, no defect)

- WS whitelist covers 100% of backend-emitted event types (9/9); the historical
  "silently dropped event" trap is not currently biting (only the phantom `weather` type — SHL-1).
- Reconnect/backoff (1s→15s, 30s-stable reset), missed-event resync via safety-net poll, no
  duplicate WS registration, all listeners/intervals cleaned up.
- Route contract: all 23 SPA fetches match backend routes exactly (method, path, params);
  frozen bulk-op and audit-export contracts hold; attribution always server-derived.
- Error flows: soft-401 → modal → `next`-preserving re-login roundtrip; 409 conflict details
  reach toasts; 403 audit gate; CSV preflight; 10s abort timeout.
- naive-UTC parsing via `parseTs` correct on all paths except the flagged ones
  (API-F1 forecast, SHL-3 snoozedAt, SHL-20 fetchedAt-latent).
- `AlertsPage.onRefresh` contract correctly wired both ends; alert rows id-keyed; single-action
  double-submit ref guard solid; PENDING_VIDEO ack gate matches backend 409 exactly.
- App-level keyboard shortcut handler correctly guards `isComposing`/229 (the gap is only in
  CommandPalette's own handler — CMP-F2).

---

## Recommended fix sequence

**Phase 1 — Safety-critical correctness (pump loop + alerts integrity)**
MSP-F1 pump_state end-to-end · MSP-F2/F16 confirm + hit targets · MSP-F4 error detail/timeout
copy · MSP-F5 pending-until-device-ack + last-command display · MSP-F6 release-hold control ·
MSP-F3 port offline guard to monitor · ALR-H1/M8 rethrow from app.jsx handlers · ALR-H2 shared
filtered list · ALR-H3 tombstone + note protection · SHL-8 post-refresh auto-advance.

**Phase 2 — Systemic interaction layer (one refactor, many fixes)**
Extract `useModalBehavior` (stable close ref, disabled-aware trap, mousedown backdrop, state
reset, IME guard) → closes CMP-F1, F3, F6, F12, half of F15 · CMP-F2 palette IME · WHA-C1
handover save sync · WHA-M9/CMP-F8 stop autofocusing destructive buttons.

**Phase 3 — Data honesty & time**
API-F1 forecast TZ (+ backend TypeError) · API-F4/WHA-H1 check `ok` · WHA-M3/API-F5 config
round-trip · SHL-3 snoozedAt parseTs · WHA-H2 audit dates · WHA-M12/SHL-16 local dates ·
MSP-F8 999 sentinel · MSP-F9 upload-age substitution · MSP-F14 null trend rendering ·
API-F3 stream health wiring · SHL-5 muteState reconciliation · pick ONE datetime rule (API-F12).

**Phase 4 — Theme, a11y, polish**
CMP-F4/F9 light theme · WHA-H4/SHL-2 wall-mode exit + tweaks activator · SHL-6 theme flash key ·
SHL-7 boot spinner · MSP-F15/CMP-F17 localization · MSP-F11 toast overlay · hit-target and
remaining Low batch.

**Regression guard suggestion:** extend `test_ws_event_contract.py` to assert every SPA-handled
WS type is both whitelisted and backend-emitted (would have caught SHL-1 and keeps that seam
honest); add a datetime-serialization contract test pinning "all wire timestamps are UTC with
explicit zone or 'Z'-repairable naive-UTC" (would have caught API-F1).
