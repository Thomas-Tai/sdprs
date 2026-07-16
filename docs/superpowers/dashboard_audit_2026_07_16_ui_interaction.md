# SDPRS Dashboard — UI/UX + Button-Interaction Audit (2026-07-16)

**Trigger:** operator report — "current dashboard still has quite a lot of bugs, especially when using the buttons and UI interaction"; user asked for a senior-engineer full audit.
**Method:** 5 file-disjoint parallel Claude subagents (~6,100 LOC), read-only, structured-JSON findings with `file:line + failure_scenario`. No fixes during the audit phase — this file is the deliverable.
**Related:** [Dashboard Audit 2026-07-15](dashboard_audit_2026_07_15.md) (72 findings, entries 22–27 in `PROGRESS.md` closed the vast majority). This audit is a NEW pass, focused specifically on interaction / button / keyboard flows that survived the earlier rounds.

## Distribution

| Severity | Count |
|---|---:|
| BLOCKER | 2 |
| CRITICAL | 6 |
| HIGH | 25 |
| MEDIUM | 24 |
| LOW | 7 |
| **Total** | **64** |

## Agent scopes (file-disjoint)

| Agent | Files | LOC | Findings |
|---|---|---:|---:|
| A | `pages/alerts.jsx` | 779 | 11 (1C · 5H · 4M · 1L) |
| B | `pages/monitor.jsx` + `status.jsx` + `pumps.jsx` | 715 | 11 (4H · 5M · 2L) |
| C | `pages/weather.jsx` + `handover.jsx` + `audit.jsx` | 654 | 13 (2B · 2C · 4H · 4M · 1L) |
| D | `app.jsx` + `tweaks-panel.jsx` | 1496 | 12 (1C · 5H · 5M · 1L) |
| E | `components.jsx` + `api.jsx` + `data.jsx` | 2393 | 17 (2C · 7H · 6M · 2L) |

---

## Cross-cutting patterns (durable)

1. **Lifted state without `key={alert.id}`** — the top offender. `AlertDetail` + `SnoozeMenu` + `resolveNote` all persist state across `selectedId` change. Four of the "wrong alert actioned" bugs (A2, A5, A9) collapse into this one root cause.
2. **In-flight guard applied only to bulk operations.** Single-alert Ack/Resolve/Snooze all lack the same protection — double-tap on a laggy VPN = duplicate mutation. Same gap on `pages/status.jsx` snooze button.
3. **Preferences persistence is broken by design.** `useTweaks()` never reads localStorage; the intended persistence protocol is a dead artifact-host `postMessage(window.parent, ...)`. Only `sdprs.volume` gets manually persisted in `app.jsx`.
4. **Null-guard drift.** Pages that fixed one field-access leave siblings unguarded — weather ×3, handover ×2, monitor stats ×4. Same "silent partial fix" pattern as the earlier `WeatherPage 資料缺失時不再閃退` change.
5. **Missing focus restore on modal close** across MuteDrawer, NodeSidePanel, ShortcutsModal, CommandPalette, mobile-nav overlay. WCAG 2.4.3.
6. **Silent unknown-branch handling.** WS unknown event type, apiFetch non-2xx body, tweaks-panel postMessage from any origin — all drop information the operator or engineer would benefit from.

---

## BLOCKER — 2

| ID | File:line | Title |
|---|---|---|
| **B1** | `pages/handover.jsx:121` | `window.SHIFT_SUMMARY` accessed with no null guard — `HandoverPage` crashes on cold-start race with the summary API. Every other code path guards `window.HANDOVER && window.HANDOVER.current`; this variable is used bare. |
| **B2** | `pages/handover.jsx:254` | `window.HANDOVER.history.map(...)` unguarded. Same crash surface: first paint with unhydrated HANDOVER → `TypeError: Cannot read properties of undefined (reading 'map')` → operator cannot draft/save the shift note. |

## CRITICAL — 6

| ID | File:line | Title |
|---|---|---|
| **C1** | `pages/alerts.jsx:129` | **Bulk-select IDs leak across filter/tab/search change** — the `checked` Set is only mutated on toggle / explicit clear / successful API call, never intersected with the currently-visible filter. Operator ticks 5 CRITICALs, flips severity chip to `info`, types a note, clicks 批次解決 — 5 invisible criticals get resolved with the wrong note. |
| **C2** | `pages/audit.jsx:86` | CSV export silently ignores `operatorFilter`/`dateFilter`/`meOnly` — only `type` (action filter) is forwarded. Compliance receives every SNOOZE row across all operators/history (up to the 1000-row cap) instead of the "today · my actions · SNOOZE" shown on screen. |
| **C3** | `pages/audit.jsx:73` | `本班·我的動作` button has **no shift/time window at all** — filters only on `a.by === _sessionUser`. Returns every action the operator ever took, not "this shift". Handover-note builder built on this view double-counts across shifts. |
| **C4** | `tweaks-panel.jsx:163` | **Theme/density/wallMode/muted never persist across reloads.** `useTweaks()` reads DEFAULTS only. `postMessage(window.parent, ...)` is the intended persistence path — dead code in production (SDPRS is not embedded under the artifact host). `app.jsx` manually persists only `sdprs.volume`; everything else resets on every refresh. |
| **C5** | `data.jsx:70` | `window.WEATHER` initial state seeds `wind:{speed:0, gust:0}`, `rain:{now:0, hour:0, day:0}`, `temp:0`, `available:false`. `api.jsx:300` correctly warns "do NOT default gust to 0 — reads as 'no gust during a typhoon'", but `data.jsx` does exactly that. Any panel that renders these fields without gating on `available` shows a calm-weather snapshot during a real event. |
| **C6** | `components.jsx:1233` | `NodeSidePanel.saveEdits` calls `onUpdateNode(...)` then **immediately** `setEditing(false)` — no await, no pending state, no error path. Rejected PATCH silently reverts on next poll; operator moves on thinking the edit stuck; responder dispatched to wrong floor. |

## HIGH — 25

Grouped by cluster for readability. Each entry: `file:line — title`.

### Alerts workflow state leaks (5)

- `pages/alerts.jsx:262` — **Snooze menu `open` state persists across `selectedId` change**; menu bound to alert A can be clicked after ArrowDown flipped selection to B, snoozing the wrong node. `mousedown` click-outside listener misses pure-keyboard selection changes.
- `pages/alerts.jsx:662` — **`resolveNote` not reset on `selectedId` change**. Draft for A survives selection flip; operator hits R on B with A's note text → audit log lies.
- `pages/alerts.jsx:385` — **`applyTemplate` unconditionally sets `noteEdited=false` after apply.** First template append works; second template REPLACES freeform text + first template silently (comment on `:384` claims otherwise but is wrong once two templates are involved).
- `pages/alerts.jsx:672` — Single-alert Ack / Resolve / Snooze have **no in-flight busy flag** (bulk correctly uses `bulkBusy`). Slow network + operator impatience → duplicate submissions.
- `pages/alerts.jsx:262` — `AlertDetail` rendered without `key={alert.id}`; local `noteEdited` + `detailTab` state leaks between alerts, making template append-vs-replace behavior non-deterministic from the operator's viewpoint.

### Buttons that lie about state or do nothing (4)

- `pages/status.jsx:130` — **Snooze `<button>` inside status row: keyboard Enter/Space bubbles to row's `role="button"` and opens side panel instead of snoozing.** The button's `stopPropagation` is only on `onClick`, not `onKeyDown`.
- `pages/pumps.jsx:36` — Offline pump renders green "正常". Only `p.status === 'critical'` and level thresholds are checked; `offline` and `warn` fall through to the green treatment.
- `pages/status.jsx:183` — Snooze `<button>` has no `disabled` state or in-flight guard → double-click fires two POSTs.
- `components.jsx:707` — MuteDrawer test buttons flash "▶ 播放測試" but `SDPRS_AUDIO.beep` short-circuits on `muted` — zero sound with visual feedback. Operator concludes "audio broken" instead of "muted".

### Selection / bubbling / state-desync (4)

- `pages/alerts.jsx:99` — Selected alert filtered out of the list stays shown in `AlertDetail` (ghost selection). A/R shortcuts still target it.
- `pages/alerts.jsx:305` — SnoozeMenu Arrow keys call `preventDefault` but **not `stopPropagation`** — page-level alert-nav shortcuts fire underneath while menu is open, changing `selectedId` invisibly.
- `pages/alerts.jsx:30` — `AlertRow` uses `role="button"` while nesting an interactive checkbox — invalid ARIA. Screen readers collapse the announcement; bulk workflow becomes SR-inaccessible.
- `components.jsx:749` — MuteDrawer `unsnoozeAll` (label "全部解除") silently flips **`global: false, lightning: false`** alongside clearing per-node snoozes. No confirmation. Operator clearing stale snoozes accidentally unmutes globally during a planned drill.

### Session / auth flow (4)

- `app.jsx:752` — Session-expiry `next=` only carries `pathname` — page/`selectedId`/`resolveNote` all lost after login roundtrip. Operator was drafting a Chinese note on alert #4127 → lands on default alerts page with a different selection, note gone.
- `app.jsx:395` — **Input-focus shortcut guard is `tag === 'INPUT' || tag === 'TEXTAREA'`** — misses `contenteditable`, `<select>`, shadow-DOM inputs. A '7' typed in a future rich-text handover field teleports operator to Audit page.
- `app.jsx:399` — **Ctrl+K opens palette on top of session-expiry modal** — bypasses the "blocking modal" contract; palette-driven navigation fires 401s against a dead session, misleading operator into thinking app still works.
- `app.jsx:387` — **IME composition (Bopomofo/Zhuyin/Cangjie) not guarded** (`e.isComposing` never checked). In a zh-TW deployment Chinese input keys fire shortcuts during composition (Firefox delivers keydown for consumed keys before the IME) — combined with the `contenteditable` gap, IME becomes randomly unusable.

### API / WS / fetch layer (7)

- `api.jsx:41` — **`apiFetch` discards response body on non-2xx** — `throw new Error('HTTP ' + res.status + ' on ' + path)` never reads `res.body`. FastAPI's structured `{ detail: 'already resolved by user X at HH:MM' }` never reaches the toast. `loadAudit` special-cases 403 by regex-matching the message string — fragile contract.
- `api.jsx:25` — `fetch(path, { credentials, signal: ac.signal, ...opts })` — `...opts` spread AFTER `signal`, so if any caller passes their own AbortController, the 10s timeout is defeated. Currently no caller, but a landmine for anyone adding page-switch cancellation.
- `api.jsx:653` — Legacy `openSocket(fn)` positional form assigns `onEvent = null` → **all 5 whitelisted telemetry types silently dropped.** No console warning; legacy pages get NEW-alert notifications but no ack/resolve echo, no node-status flip. Also `auth_expired` dropped → mysterious 401 → /login redirects.
- `components.jsx:228` — **SnapshotImage cache-buster uses `Date.now()`** — 30 req/s per operator on the monitor wall regardless of whether the underlying JPEG changed. `node.snapshotTimestamp` is already carried through `mapNode` (`api.jsx:256`) exactly to enable browser 304s. Fights the browser cache; also flaps broken frames on an offline tab.
- `pages/monitor.jsx:69` — Card timestamp `{new Date().toLocaleTimeString(...)}` evaluated at render only. `NodeCard` doesn't subscribe to the shared 1Hz ticker (only `SnapshotImage` does) → timestamp freezes for minutes while the snapshot underneath keeps updating.
- `components.jsx:679` — MuteDrawer 30s interval calls `setNow(Date.now())` but the displayed `remain = n?.snoozeMin` is frozen from the last full nodes fetch. Tick is a no-op; countdown jumps by ~20s at each poll, not smoothly.
- `components.jsx:1354` — `NodeSidePanel` pump-cycles renders `每 {(60/node.cycles).toFixed(1)}m`. When cycles = 0 (dry season / new node / 404), result is `每 Infinity 分` — reads as either a bug or an alarming "infinite cycle time".

### Data-render edge cases + auth surface (5)

- `pages/weather.jsx:13` — Forecast bar heights render as `NaNpx` if any hour has `null` wind/rain (`Math.max(1, ...fc.map(f => f.wind))` propagates NaN). Backend can return `null` for partial future hours; entire 36h chart collapses.
- `pages/handover.jsx:83` — No `beforeunload` handler and no in-app nav interception — operator drafting a 4-minute narrative loses everything on tab-switch/reload. `dirty` is tracked and displayed but not enforced.
- `pages/handover.jsx:245` — 24h TTL expiry never surfaced. Note saved at 08:00 evicts at next 08:00; incoming operator reads what appears to be a current note that vanishes 30 seconds later.
- `pages/audit.jsx:169` — CSV export button not disabled during in-flight; stressed operator hammers it → multiple concurrent exports, multiple downloads, multiple audit rows.
- `tweaks-panel.jsx:253` — **Message listener reads `e?.data?.type` without validating `e.origin`.** Any parent frame, opener, or extension can force `__activate_edit_mode`. `__edit_mode_available` also announced unconditionally to `window.parent` — fingerprints SDPRS to any embedder.

## MEDIUM — 24

Grouped by area; each entry brief.

**Alerts (4)**: `alerts.jsx:139` bulk error is native `alert()` blocking dialog with generic string, drops keyboard focus; `alerts.jsx:99` selected-alert-out-of-filter allows keyboard-shortcut actions on hidden alert; `alerts.jsx:305` SnoozeMenu Arrow keys leak to document; `alerts.jsx:135` bulk handler clears `checked`+`bulkNote` before refresh completes — on refresh failure, selection is gone with no undo.

**Monitor/Status/Pumps (5)**: `status.jsx:187` snooze silently no-ops when `SDPRS_API` missing (no fallback toast); `monitor.jsx:175` fullscreen button labels `[F]` shortcut with no listener anywhere; `pumps.jsx:19` null/NaN water level renders `null%` in centered readout; `pumps.jsx:43` sensor-conflict banner has no dismiss/ack; `monitor.jsx:112` tab state resets on unmount when navigating away and back.

**Weather/Handover/Audit (4)**: `weather.jsx:17` `autoMuteLightning` checkbox is inert UI (backend not wired, tooltip easy to miss); `weather.jsx:18` empty state conflates "loading" with "backend unavailable"; `weather.jsx:71` rain unit inconsistency — hero says "mm (10min)", chart legend "mm/h"; `handover.jsx:253` history has no empty state — bare heading with nothing below.

**app.jsx + tweaks-panel (5)**: `app.jsx:421` `Alt+←` unconditionally `preventDefault`s → kills browser back when app history empty; `app.jsx:401` Ctrl+K not idempotent (won't close on second press); `app.jsx:737` session-expiry modal has no focus trap — Tab escapes to controls behind backdrop; `app.jsx:38` focus mode not persisted; `app.jsx:258` no `refresh()` on WS reconnect, no "may have missed events" indicator.

**components/api/data (6)**: `api.jsx:524` in-flight guard collapses concurrent events but no trailing debounce despite claim — burst of 10 node_status events after idle spawns 10 back-to-back refetches; `api.jsx:690` `auth_expired` reconnect loop never stops (every 15s indefinitely); `components.jsx:691` MuteDrawer + NodeSidePanel lack focus restore on close (WCAG 2.4.3); `api.jsx:597` `exportAuditCsv` HEAD-preflight breaks on servers/CDNs that don't auto-serve HEAD (Zeabur edge, corp proxies); `pages/status.jsx:64` nested `<button>` inside FilterChip for filter clear × (invalid HTML — browsers unwrap or split); `pages/monitor.jsx:78` missing null-guards on heartbeat/upload/cycles/level → renders `nulls`/`null×`.

## LOW — 7

- `pages/audit.jsx:87` CSV limit hardcoded 1000 (docs say 10000); truncation never communicated to operator
- `pages/alerts.jsx:135` — bulk handler clears `checked` before refresh completes (also in MEDIUM cluster); typed bulk note is lost if refresh fails
- `pages/monitor.jsx:78` — missing null-guards produce `nulls` / `null×` cosmetic strings on partial-row nodes
- `pages/status.jsx:64` — nested `<button>` inside FilterChip (invalid HTML)
- `app.jsx:637` — mobile nav doesn't return focus to hamburger; CSS selector `nav.hidden.md\\:flex` fragile to Tailwind refactor
- `components.jsx:1146` — CommandPalette missing `role="listbox"`/`role="option"`/`aria-activedescendant`
- `components.jsx:932` — FilterChip has no × affordance nor Delete/Backspace keyboard alt

---

## Follow-up commit slice — pointer

*This section will be filled in as fixes ship. Initial slice targets B1+B2 + C1–C6 + the 5 HIGH alerts-workflow state leaks (13 items).*
