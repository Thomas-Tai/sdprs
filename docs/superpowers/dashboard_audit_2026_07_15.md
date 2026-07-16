# SDPRS Dashboard Full Audit — 2026-07-15

**Method:** 4 parallel read-only audit agents, each owning a slice of the SPA/backend, reporting structured findings. **72 findings** total across data layer, app shell, UI, and backend contract. This doc keeps the raw agent output for reference; the consolidated punch list is delivered inline in chat.

## Scope

- SPA: `central_server/static/spa/{api,data,app,pages,components,tweaks-panel,icons}.jsx` + `index.html` (~4,300 LOC)
- Backend: `central_server/api/*.py` + `services/websocket_service.py` (WebSocket + REST contract)

## Findings by tranche

### Tranche 1: Data layer (`api.jsx`, `data.jsx`) — 16 findings

Owner: SPA data mapping, REST fetch, WebSocket reconnect, timestamp handling.

Highlights:
- **P0** `api.jsx:238-240` — `mapWeather` binds `rain.now`, `rain.hour`, `rain.day` all to `rainfall_24h_mm`. UI displays same number under 3 different unit labels.
- **P0** `api.jsx:151-190` — Pump with dead water-level sensor: `null` → coerced to `0` → renders green "online" tile. No visual cue of sensor failure.
- **P0** `api.jsx:242,232,238-240,243` — `round(x) || 0` swallows legitimate `0°C`/`0 mm` AND `null` (sensor down) into the same display.
- **P1** `api.jsx:409-427` — No in-flight guard on `refreshLive`; concurrent poll + WS event can race, older response wins `window.NODES = nodes`.
- **P1** `api.jsx:130,170-172,183-184,200` — All `ageSec`/`heartbeat`/`upload`/`snoozeMin` computed at map-time; clock doesn't tick between refreshes.
- **P1** `api.jsx:311-317` — `loadWeather` sequential await — total = sum of 3 RTTs instead of max.
- **P1** `api.jsx:14-28` — No fetch timeout / AbortController; slow FastAPI worker hangs the poll loop.
- **P1** `api.jsx:44` — `parseTs` regex misses 2-digit UTC offsets (`+00`) — appends `Z` to already-tz-aware string → NaN.
- **P1** `api.jsx:410` vs `403-404` — `refreshLive` swallows rejections silently (no console.warn like `loadInitial`).
- **P1** `api.jsx:233` — `wind.gust: 0` hardcoded (no backend field) — "0 km/h gust" during typhoon.
- **P2** `api.jsx:210-213` — `mapWeather(null)` returns stale `{ ...window.WEATHER, available: false }` — consumers reading `.wind.speed` without guarding get stale values.
- **P2** `api.jsx:66,429` — `_seen` Set is process-lifetime; grows unbounded in 24/7 SPA.
- **P2** `api.jsx:454,460,466-469` — WS `retry` resets on `onopen`; a server that accepts-then-closes triggers 1 req/s reconnect flood.
- **P2** `api.jsx:186-189` — Nested ternary for bitrate/drops assumes fallback field is same unit (Mbps vs Kbps risk).
- **P2** `api.jsx:312,316,317,336,346` — Silent `/* ignore */` catches; no distinction 503-disabled vs 500-broken.
- **P2** `data.jsx:141,84` — `detectorHealthMeta.unknown.tone: 'muted'` may not be a valid Pill tone; `OPERATOR.role='op'` hardcoded (admin UI branches never activate).

### Tranche 2: App shell (`app.jsx`, `index.html`) — 18 findings

Owner: React shell, state model, keyboard handler, WallView, bootstrap.

Highlights:
- **P0** `index.html:68` — `window.SDPRS_USER = __SDPRS_USER__;` — unsubstituted placeholder is a bare identifier → `ReferenceError` → dashboard blank.
- **P0** `app.jsx:606-613 + 20,72,410,501-583` — `loadInitial()` failure only logs; App mounts anyway; mount-time `.filter` / `.pinned.by` on undefined globals → white-screen with no error UI.
- **P1** `app.jsx:19,124` (write) never-read — `nodes` state is dead code whose only function is triggering re-renders so global `window.NODES` reads pick up changes. Deletion (looks unused) silently breaks the whole live-node UI.
- **P1** `app.jsx:72 vs 71,73` — `offlineCount` reads `window.NODES` directly while neighbors use React state — fragile ordering dep.
- **P1** `app.jsx:339-341` — `useEffect(() => setTweak('muted', muteState.global))` fires on mount with default `false` → clobbers persisted mute preference every session start.
- **P1** `app.jsx:253-337 + 376-378` — Keyboard handler stays mounted in wall mode; hotkeys mutate hidden state (an ack goes to an alert operator can't see selected).
- **P1** `app.jsx:28,164,413-419` — `newAlertBannerCount` never decays: not on ack/resolve, not on page-away, not on queue clear. Number becomes lies.
- **P1** `app.jsx:289` — `document.querySelector('input[placeholder*="搜尋"]')?.focus()` matches the first such input anywhere in DOM — may focus wrong one, or silently no-op.
- **P1** `app.jsx:294-306` — `'7'` bypass in resolve-template intercept teleports operator to Audit mid-resolve.
- **P1** `app.jsx:92-95` — `setToast(); setTimeout(3000, null)` with no `clearTimeout` — rapid toasts blank each other prematurely.
- **P1** `app.jsx:475-478` — `WallView` sort `rank[a.status] - rank[b.status]`; unknown status → `undefined - undefined = NaN`; engine-dependent sort chaos.
- **P2** `app.jsx:431` — `animate-in` class not in Tailwind standalone or inline keyframes; toast has no animation.
- **P2** `app.jsx:73` — `window.STALE_ACK_THRESHOLD` undefined → `> undefined` always false; silent metric stop.
- **P2** `app.jsx:40-54` — Setters do side effects in updater callbacks; StrictMode double-invocation → double history push.
- **P2** `app.jsx:37,81-90` — Audio-replay counter skips 0 (30→…→1→30). Any consumer awaiting `=== 0` never fires.
- **P2** `app.jsx:187-190 + 237-239` — `markSeen(id)` returned promise never `.catch`-ed → UnhandledPromiseRejection on stale IDs.
- **P2** `index.html:2 vs app.jsx:57-62` — `<html class="dark">` hardcoded → ~200ms dark-flash if operator's persisted theme is light.
- **P2** `app.jsx:475,519,545` — Wall silently truncates to 9 tiles + 12 alerts; growing fleet loses visibility.

### Tranche 3: UI components (`pages.jsx`, `components.jsx`, `tweaks-panel.jsx`, `icons.jsx`) — 26 findings

Owner: Rendered UI, page components, dialogs, snapshot slot.

Highlights:
- **P0** `pages.jsx:566-591` — In `NodeCard`, `SnapshotImage` renders AFTER 4 status badges inside `relative` container — no z-index → JPEG covers status dot, alert count, CAM/PUMP tag, snooze pill. Wall becomes uninformative once images arrive.
- **P0** `pages.jsx:1129-1130` — Handover page `useState(window.HANDOVER.current)` captures ONCE at mount; `refreshLive` never propagates; saves clobber peer edits.
- **P0** `app.jsx:343-354 + components.jsx:788-792` — NodeSidePanel: name/floor/area typed into local state but backend PATCH only sends `location`; subsequent `await refresh()` reverts non-location fields. Silent data loss.
- **P0** `components.jsx:633-671` — `ShiftBanner` ignores its `shiftSummary` prop; renders literal hardcoded numbers ("2 / 1 / 5", "alice.chen", specific incident text). Active misinformation.
- **P0** `pages.jsx` — **~20 dead buttons** with no `onClick`/`onChange` across every page:
  - AlertsPage: bulk ack/resolve, note textarea, undo-ack, takeover, Runbook step buttons, "check all history"
  - MonitorPage: 分組 / 1s / 全螢幕
  - StatusPage: 類型/狀態/位置 filters, row 靜音/配置/重啟
  - WeatherPage: source tag, "雷擊自動靜音" (`defaultChecked` no `onChange`)
  - AuditPage: 操作者/動作/日期 filters, 匯出 CSV
  - ShiftBanner: "檢視完整交接紀錄"
  - Alert detail: 3 "tabs" 事件時間軸/節點資訊/處理紀錄 — no tab state, tabs #2 and #3 never render.
- **P0** `components.jsx:505-524` — `MuteDrawer` snooze row hardcodes `alice 於 02:14 設定`, fallback `剩餘 22 分鐘`, lightning `最近雷擊 18km` — every snoozed node looks identical.
- **P1** `pages.jsx:285-315` — Alert detail "video preview" — Camera icon + hardcoded `HLS · {alert.node} · 1920×1080 · 4.2s clip` + fake progress `1.4s/4.2s` + non-functional Play/Download buttons. Resolution string is wrong (actual is 720p).
- **P1** `pages.jsx:329-334` — History thumbnails always show Camera icon fallback; alert's actual snapshot filename ignored.
- **P1** `pages.jsx:794,1342` — Only English sentence in whole zh-TW UI: `⚠ Sensor conflict — inspect float switch`. Highest-severity pump condition.
- **P1** `pages.jsx:1069,1072` — Weather UI displays `w.rain.now mm/h` and `日累計 {w.rain.day}mm` — same number under contradictory units (root fix in api.jsx).
- **P1** `pages.jsx:717,938-1000,1278-1300,1319-1408` — Monitor/Status/Audit/Pumps pages have NO empty state — headers + zero rows. `EmptyState` component exists but never used on these 4 pages. Especially bad on Audit where non-admin 403 also produces `[]`.
- **P1** `components.jsx:9-11,22-24 + callers` — `sevMeta[a.sev]` unguarded in AlertRow/AlertDetail/NodeSidePanel — unknown sev value → crash render.
- **P1** `components.jsx:322-329, app.jsx:410` — `Footer` calls `handover.ageMin` — throws if `handover` prop is null (initial `window.HANDOVER.pinned` is null).
- **P1** `components.jsx:505-524, 686-689` — `MuteDrawer.window.NODES.find`, `CommandPalette.window.NODES.map` — direct global reads; stale when open during refresh.
- **P1** `components.jsx:459, 551` — `VolumeSlider` writes to `muteState.volume` but nothing reads it — placebo control.
- **P1** `components.jsx:797-806,432-559,675-770,377-428` — Dialogs miss `role="dialog"` / `aria-modal`; no focus trap.
- **P1** `pages.jsx:35,36,565,773,942` — Row/card click targets are `<div>` with `onClick`, no `role="button"`, no `Enter`/`Space` handling — keyboard-only operators can't select.
- **P2** `components.jsx:303-320, pages.jsx:1103` — `Sparkline` divide-by-zero on empty data (Infinity/NaN).
- **P2** `data.jsx fmtAge`, sites in pages/components — no day unit; multi-day typhoon shows `72h`.
- **P2** `components.jsx:255, app.jsx:407` — `NavRail w-56 fixed` + main `ml-56` unconditionally → mobile portrait crushed.
- **P2** `components.jsx:582-587` — Snooze pill count doesn't tick; static server value, no client countdown.
- **P2** `components.jsx:74` — Every camera tile spawns its own 1Hz interval — 30 cameras = 30 timers; wasteful.
- **P2** `pages.jsx:1080` — Lightning "警戒" text always shown when `nearest` set — cries wolf at 47km strikes.

### Tranche 4: Backend API contract — 12 findings

Owner: SPA↔backend contract verification.

Highlights:
- **P0** `services/websocket_service.py:78,161 + api/alerts.py:*` — Backend broadcasts **7 WS event types**: `ping`, `new_alert`, `alert_updated`, `alert_acknowledged`, `alert_resolved`, `node_status`, `pump_status`. SPA handles only `ping` and `new_alert`. Ack/resolve by peers, node up/down, pump state changes never live-update — operator sees stale until next 20s poll.
- **P1** `api/alerts.py:531` — `int(t.timestamp()) % bucket_s` on naive datetime uses OS LOCAL tz, not UTC. Sparkline bars misaligned off UTC hosts.
- **P1** `api/alerts.py:502-561` — Alert rate returns 15 buckets for `window=4h,bucket=15m` (snap-forward off-by-one).
- **P1** `api/alerts.py:642-674` — `list_alerts` with `status_filter=A,B,C` fires 3 separate queries × (limit+offset) then merges in Python. 600 rows per SPA poll.
- **P1** `api/alerts.py:666` — Post-merge sort mixes `datetime` (PG) with `""` fallback (missing timestamp) → `TypeError` → 500 → SPA alerts page dies.
- **P1** `services/websocket_service.py:179-198` — `/ws` upgrade returns `close(1008)` on session expiry with no signal type; SPA reconnect-loops forever.
- **P1** `api/snapshots.py:167-215` — Snapshot GET now requires auth (hardened in `e73718d`); when dashboard session expires, all `<img>` cache-buster fetches 401 → broken-image icons across the whole wall until next `apiFetch` triggers `/login`.
- **P2** `api/nodes.py:332-356` — `updateNodeLocation` with whitespace `" "` clears the label silently; `PATCH /api/nodes/{id}` auto-upserts a phantom `glass` node for any typo (no delete endpoint).
- **P2** `api/alerts.py:73-85` — `ResolveRequest.resolved_by` accepted in schema but ignored — brittle contract, easy to accidentally re-enable.
- **P2** `api/nodes.py:510-543 pump_cycles_batch` — "batched" only at HTTP level; still M queries × 50000-row scans internally.
- **P2** `api/audit.py:23-44` — Audit response timestamps rely on naive-UTC contract; TIMESTAMPTZ migration would silently break the client-side `Z`-append.
- **P2** `services/websocket_service.py:98-147` — Broadcast is fire-and-forget with no sequence numbers; dropped `new_alert` during reconnect window is invisible until poll.

## Bugs the operator will notice IMMEDIATELY (grouped by fix cost)

### 30-min surgical fixes (biggest UX ROI)

1. NodeCard z-index — `SnapshotImage` needs `-z-10` or badges need `z-10` (P0 UI #1)
2. Rain fields — split `rainfall_1h_mm` vs `_24h_mm` in api.jsx map (P0 data #1 + P1 UI #15)
3. `WallView` sort NaN — add `?? 99` (P1 shell #11)
4. `Footer` null crash — guard `handover?.ageMin` (P1 UI #14)
5. `sevMeta[sev]` guards on 3 callers (P1 UI #13)
6. Toast timer collision — `clearTimeout(prev)` on new toast (P1 shell #10)
7. `parseTs` regex fix — accept 2-digit offsets (P1 data #8)
8. `refreshLive` in-flight guard (P1 data #4)

### 1-2 hour cleanup PRs

9. **Dead buttons sweep** — wire all ~20 button/checkbox/tab handlers OR mark visibly "TODO" (P0 UI #5)
10. **Empty-state sweep** — apply `<EmptyState>` to Monitor/Status/Audit/Pumps (P1 UI #12)
11. **ShiftBanner** — pass `shiftSummary` prop through, delete hardcoded strings (P0 UI #4)
12. **MuteDrawer** — drive from real snooze reason/author/expiry (P0 UI #7)
13. **WS handler expansion** — handle `alert_updated`/`alert_acknowledged`/`alert_resolved`/`node_status`/`pump_status` → targeted state update, no full refetch (P0 backend #1)
14. **Handover page state sync** — read from `window.HANDOVER.current` reactively, warn on edit conflict (P0 UI #2)
15. **NodeSidePanel** — either send all patch fields to backend or make name/floor/area read-only (P0 UI #3)

### Bigger design decisions

16. Alert-detail video preview — either implement snapshot-at-time-of-event or replace with honest placeholder (not a fake progress bar). Same for history thumbnails.
17. Silent init failure — add error boundary + retry UI (P0 shell #2)
18. Wall-mode keyboard handling — decide whether hotkeys should work / different set
19. Session-expiry UX — surface a "session expired" modal instead of WS-loop + broken images
20. Accessibility pass — role="dialog", focus trap, keyboard row selection

## Non-findings worth noting (agents verified OK)

- `apiFetch` 401 redirect flow, `refresh()` post-mutation semantics, WS cleanup on unmount, `findNextUnack` sort mutation, `markSeen` effect deps, script load order determinism, `resolve_alert` server-side attribution, `verify_api_key_or_session` for snapshot endpoint, `/api/audit` returns 403 not 500 for non-admin, `get_event_created_ats` PG portability, `stream_status` shape flexibility, icons.jsx completeness.
