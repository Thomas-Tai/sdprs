# SDPRS Dashboard — Full UI/UX & Engineering Audit Report

**Date:** 2026-07-26
**Scope:** Entire SPA (`static/spa/`), backend API (`central_server/`), login flow, cross-cutting UX
**Method:** 6 parallel static-analysis audit agents covering: app shell/navigation, API/data layer + backend, shared components, operational pages (status/monitor/pumps), secondary pages (alerts/weather/handover/audit), and auth flow/cross-cutting UX
**Codebase size:** ~11,858 lines frontend JSX/CSS + Python backend

---

## Executive Summary

| Severity | Count |
|----------|-------|
| Critical | 3 |
| High | 13 |
| Medium | 61 |
| Low | 107 |
| **Total** | **184** |

**Overall assessment:** The codebase has clearly been through prior audit passes (SHL-/CMP-/ALR-/MSP-/WHA- fix annotations) and the fundamentals are strong — interval cleanup, focus traps, IME guards, double-submit ref gates, honest degradation states. However, **3 critical issues** remain that directly impact operational safety and security, plus 13 high-severity issues concentrated in: broken logout, stream health display lies, silent live-view failures, keyboard-inaccessible controls, session lifetime enforcement, and toast feedback loss.

### Top 10 Issues to Fix Immediately

| Priority | ID | Issue | Why it matters |
|----------|----|-------|----------------|
| 1 | AUTH-001 | Logout button GETs a POST-only route → 405, session never ends | Security hazard on shared consoles; one-line fix |
| 2 | DATA-001 | Snooze flag stored & displayed but **never enforced** anywhere | UI actively lies about alert suppression during typhoons |
| 3 | OPS-001 | Backdrop click destroys shown-once API key | Unrecoverable credential loss; trivial fix |
| 4 | SEC-001 | No absolute session lifetime (`login_at` never enforced) | Sessions are immortal; contradicts documented 24h design |
| 5 | DATA-002 | Ack/resolve TOCTOU race — concurrent operators silently overwrite attribution | Defeats the core purpose of the acknowledge feature |
| 6 | OPS-002 | "串流健康" column permanently 0.0 Mbps / red for every camera | Alarm fatigue; trains operators to ignore the table |
| 7 | AUTH-002 | WebSocket session re-validation reads frozen scope — can never fire | Live data keeps flowing after logout server-side |
| 8 | OPS-003/004 | Black tile forever if hls.js fails + silent 30s live-start timeout | Operator believes stream is up (`● LIVE`) while seeing nothing |
| 9 | FLOW-001 | Toasts don't stack/dismiss; action-relevant failure toasts overwritten | Operator walks away believing an ack landed when it failed |
| 10 | DATA-004 | Video upload 100MB limit bypassable via chunked transfer | Disk exhaustion DoS during typhoon |

---

## Independent Verification Pass (same day, 4 parallel agents)

All 3 Critical + all 13 High + 8 representative Medium findings were independently re-verified by fresh agents re-reading the actual code:

| Result | Count | Notes |
|--------|-------|-------|
| CONFIRMED | 22 / 24 | Code matches cited evidence exactly; exhaustive greps found no mitigating code |
| PARTIALLY CORRECT | 1 | **COMP-008** downgraded Medium → Low: unguarded `.wind` access exists, but `mapWeather` always constructs the full object shape — crash not producible by current code paths |
| CONFIRMED (narrow scope) | 1 | **HDO-001**: state-only guard is real, but React 18 synchronous flush + `disabled` attr + server token make exploitation very narrow in practice |
| REFUTED | 0 | — |

**Verification caveats added:**
- DATA-004: `deploy/nginx.conf` has `client_max_body_size 100M` for docker-compose deployments — mitigates at proxy level, but direct uvicorn/Zeabur deployments remain unprotected at the application level.
- AUTH-002: The backend test file can only exercise the WS close path by monkeypatching `_get_session_user` — corroborating that no production code path mutates the scope session.
- OPS-002: Edge firmware source (`edge_glass/stream/rtsp_server.py`) confirmed to publish only `{status, tunnel_port, format, cloud_mode}` — never bitrate keys.

---

## Section 1: App Shell, Navigation & Layout (28 findings)

**Files:** `app.jsx`, `index.html`, `styles.css`
**Counts:** 0 Critical · 0 High · 4 Medium · 24 Low

### Medium

| ID | Category | Location | Issue |
|----|----------|----------|-------|
| SHELL-001 | Bug | app.jsx:160-164, 420-426, 1448-1457 | Unvalidated `page` value (from sessionStorage/RESTORED_STATE/popstate) → blank content area with no error or redirect. Fix: validate against `VALID_PAGES` set at every entry point. |
| SHELL-002 | Accessibility | app.jsx:1644-1657 | Tweaks-panel page-jump buttons use hardcoded light-theme inline colors; in default dark theme text is ~1.2:1 contrast (effectively invisible). Fails WCAG 1.4.3. |
| SHELL-003 | Bug (responsive) | app.jsx:1584 | `<main>` uses `h-[calc(100vh-88px)]` but `#root` already migrated to `100dvh` (CMP-F18). On mobile, bottom 50-90px of every page is unreachable. |
| SHELL-004 | Bug (error states) | app.jsx:1465-1486 | Bootstrap-error retry path never reads back `__SDPRS_LOAD_FAILURES` — partial retry success presented as full success with no warning banner. |

### Low (24 findings)

| ID | Category | Issue (one-liner) |
|----|----------|-------------------|
| SHELL-005 | UX | URL never reflects current page — no deep-linking or shareable URLs |
| SHELL-006 | UX | Browser Back pollutes Alt+← in-app history stack — Alt+← acts as "forward" |
| SHELL-007 | Bug | RESTORED_STATE.selectedId adopted without validation → dead selection after login roundtrip |
| SHELL-008 | Performance | `markSeen` POSTs on every selection change even when alert already seen |
| SHELL-009 | UX | Missing resolve templates for keys 1-6 swallowed silently (key 7 gets a toast) |
| SHELL-010 | UX | `/` search shortcut is a silent no-op outside the Alerts page |
| SHELL-011 | UX | `M` opens mute drawer but doesn't toggle (inconsistent with Cmd+K fix B2) |
| SHELL-012 | Accessibility | Warning-banner dismiss button lacks aria-label |
| SHELL-013 | Accessibility | Skip-link target `<main>` lacks `tabIndex={-1}` (Safari focus bug) |
| SHELL-014 | Accessibility | Tweaks page buttons lack `aria-pressed` active state |
| SHELL-015 | Accessibility | Session-expiry modal lacks `aria-describedby` |
| SHELL-016 | Performance | WallView driven by two independent 1Hz timers; full re-render + sort every second |
| SHELL-017 | Bug | Wall-mode Escape exits even while blocking session-expiry modal is up |
| SHELL-018 | Bug | Toast lacks `key` — no re-animation on replace; timer not cleared on unmount |
| SHELL-019 | Bug (responsive) | `h-screen w-screen` roots contradict the documented `100dvh`/`100%` fix |
| SHELL-020 | UX | Wall footer handover note not truncated — overflows 32px footer |
| SHELL-021 | UX | Wall top strip has no wrap/overflow strategy below 4K |
| SHELL-022 | UX | ErrorBoundary retry can infinite-loop on deterministic crash; no nav escape hatch |
| SHELL-023 | Performance | Refresh replaces arrays every 20s even when unchanged → idle full re-renders |
| SHELL-024 | Accessibility | Light-theme `text-ink-dim` (#94A3B8 on white ≈2.5:1) fails WCAG AA |
| SHELL-025 | Visual | Slim scrollbars are WebKit-only; Firefox gets default chrome |
| SHELL-026 | UX | Fixed failure banner permanently overlays top of page content while failures persist |
| SHELL-027 | Bug | `ageSec` comparator has no NaN guard, contradicting the file's own severity-rank policy |
| SHELL-028 | Performance | In-browser Babel + Tailwind JIT on every load (deliberate offline trade-off, informational) |

---

## Section 2: API/Data Layer & Backend (27 findings)

**Files:** `api.jsx`, `data.jsx`, `central_server/api/*.py`, `central_server/services/*.py`
**Counts:** 1 Critical · 2 High · 8 Medium · 16 Low

### Critical

**DATA-001 — Snooze is never enforced anywhere (dead safety feature)**
- **Location:** `api/nodes.py:1005-1038`, `services/mqtt_service.py:543-572`, `api/alerts.py:144-202`
- The snooze endpoint stores `snoozed_until` in the DB and pushes MQTT config. Two docstrings claim enforcement in `event_service.py`. **No such check exists anywhere.** `create_alert` inserts unconditionally. Edge firmware doesn't subscribe to the snooze topic either.
- **Impact:** Operator snoozes a node (UI shows "已延期" chip), believes false positives are suppressed. They are not — alerts keep arriving. Worse, operator may ignore a genuine alert on the "snoozed" node. The dashboard actively misrepresents system state.
- **Fix:** Enforce in `create_alert`: read `nodes.snoozed_until`, suppress audio-only alerts during snooze window. At minimum, remove false docstrings and show caveat in UI.

### High

**DATA-002 — Ack/resolve TOCTOU race: concurrent operators silently overwrite attribution**
- **Location:** `api/alerts.py:362-426, 429-522`, `services/event_service.py:155-204, 239-292`
- UPDATE's WHERE clause matches only `id` — no status predicate. Two operators acking the same PENDING alert concurrently both succeed; last writer silently overwrites `acknowledged_by`. The bulk endpoints correctly include `AND status = 'PENDING'`; single-row paths do not.
- **Fix:** Add status predicate to UPDATE + check rowcount; return 409 when rowcount == 0.

**DATA-004 — Video upload size limit bypassable via chunked transfer**
- **Location:** `api/alerts.py:223-296`
- `file.size` is `None` for chunked uploads → 100MB check skipped; streaming write loop has no cumulative counter → arbitrary disk write.
- **Fix:** Track bytes written in loop; abort + unlink when exceeding MAX_VIDEO_SIZE.

### Medium

| ID | Category | Issue |
|----|----------|-------|
| DATA-003 | Bug | Backend `Content-Disposition` (UTC date) overrides frontend's local-date filename fix (SHL-16 dead code) |
| DATA-005 | Performance | `GET /api/alerts` has no upper bound on `limit` (audit clamps 500; alerts unclamped) |
| DATA-006 | Bug | No alert dedup/idempotency — edge retries on degraded WiFi create duplicate alerts |
| DATA-007 | Bug | Heartbeat handler hard-codes `type:"glass"`, clobbering pump state if topic shared |
| DATA-008 | UX | Pump command returns success (`queued:true`) for offline nodes; inconsistent with stream start's 503 |
| DATA-009 | Bug | Snooze endpoint auto-creates phantom nodes on typo'd node_id (PATCH was hardened; this wasn't) |
| DATA-010 | Performance | Throwaway SQLAlchemy engine created per query on 20s poll hot path (PostgreSQL) |
| DATA-011 | Bug | `\|\| 0` coerces null timestamp age to "0s ago" (fresh) instead of "—" in mapAlert |

### Low (16 findings)

| ID | Category | Issue (one-liner) |
|----|----------|-------------------|
| DATA-012 | Bug | Caller AbortSignal silently dropped when `AbortSignal.any` unavailable |
| DATA-013 | Bug | CSV export auth preflight TOCTOU — error body can still download as .csv |
| DATA-014 | UX | `/api/pumps/cycles` failure silently zeros all pump counts, no warning banner |
| DATA-015 | Bug | `openSocket` reconnects forever after 1008 auth-expiry close unless caller tears down |
| DATA-016 | Bug | `GET /api/audit` response echoes un-clamped `limit` |
| DATA-017 | Bug | Same-node same-second alerts overwrite each other's MP4 evidence (filename collision) |
| DATA-018 | Bug | Webcam mutation helpers skip `encodeURIComponent` (inconsistent with siblings) |
| DATA-019 | Bug | `DELETE /nodes/{id}/snooze` ignores DB failure — reports success while snooze persists |
| DATA-020 | UX | `mapNode.bitrate`/`drops` default to fabricated `0` instead of null (violates own convention) |
| DATA-021 | Bug | `loadAudit` 401 fallback returns array lacking `forbidden`/`truncated` metadata |
| DATA-022 | UX | `buildShiftSummary` ships permanent placeholder metrics ('—') despite data existing |
| DATA-023 | UX | Presence roster (`OPERATORS_ONLINE`) permanently empty but rendered as truth |
| DATA-024 | Security | Login throttle keyed by `request.client.host` breaks behind reverse proxy |
| DATA-025 | Bug | `apiFetch` success-path JSON parse unguarded; rejects without `.status` |
| DATA-026 | Bug | `stream_health` derivative cache is per-process; breaks under multi-worker |
| DATA-027 | Bug | Batch pump-cycle 50k row cap shared across all pumps; silently undercounts fast cyclers |

---

## Section 3: Shared Components & Tweaks Panel (30 findings)

**Files:** `components.jsx`, `tweaks-panel.jsx`, `icons.jsx`
**Counts:** 0 Critical · 1 High · 7 Medium · 22 Low

### High

**COMP-001 — TweakRadio segmented control completely inoperable by keyboard**
- **Location:** `tweaks-panel.jsx:503-573`
- Selection wired exclusively to track's `onPointerDown`. Segment `<button role="radio">` elements have no `onClick`/`onKeyDown`. Enter/Space fires `click` that nothing handles. Used for **主題 (theme)** and **密度 (density)** — the panel's primary settings.
- **Fix:** Add `onClick={() => onChange(o.value)}` to each button + implement roving tabindex arrow-key pattern.

### Medium

| ID | Category | Issue |
|----|----------|-------|
| COMP-002 | Accessibility/Bug | CommandPalette & ShortcutsModal restore focus to detached autoFocus input → focus lands on `<body>` → next keystrokes hit global hotkeys (A=ack!) |
| COMP-003 | Accessibility | Shift+Tab immediately after opening MuteDrawer/NodeSidePanel escapes the modal (heading not in trap set) |
| COMP-004 | Bug | HlsPlayer retry budget not reset on `nodeId` change → premature permanent fallback on first error |
| COMP-005 | Bug/UX | HlsPlayer: no `onFallback()` when hls.js fails to load → permanent unexplained black tile |
| COMP-006 | Accessibility | All tweaks-panel form controls lack programmatically associated labels (sliders, toggles, selects) |
| COMP-007 | Bug (contract) | NodeSidePanel `onUpdateNode?.()` silent fake-success when prop missing |
| COMP-008 | Bug (robustness) | StatusStrip weather chip crashes entire strip on partial WEATHER payload (no null guard on `.wind`) |

### Low (22 findings)

| ID | Category | Issue (one-liner) |
|----|----------|-------------------|
| COMP-009 | UX | CommandPalette highlight index stale when live results shrink |
| COMP-010 | Bug | Sparkline NaNpx bar heights on non-numeric buckets |
| COMP-011 | Dead code | StatusStrip accepts dead props `page`, `setMuted` |
| COMP-012 | Accessibility | Mute-drawer trigger misuses `aria-pressed` (should be `aria-haspopup`) |
| COMP-013 | Dead code | NavRail unused `hamburgerRef` |
| COMP-014 | Performance | Ineffective `React.memo` on Sparkline/Pill (unstable props) |
| COMP-015 | Bug | AudioController overlap-guard timestamps advance while muted |
| COMP-016 | Bug | AgeCell renders "0s" calm-colored for `null` ages |
| COMP-017 | Props contract | NodeSidePanel `openAlerts` unguarded |
| COMP-018 | Dead code | Unused `useMemo`/`useCallback` destructures |
| COMP-019 | UX | TweakToggle: label text not clickable; 32x18px-only hit area |
| COMP-020 | UX/Accessibility | TweaksPanel: no Escape close, no dialog role, no focus management |
| COMP-021 | UX | TweaksPanel z-90 floats above logout modal z-[80] |
| COMP-022 | UX | TweaksPanel drag is mouse-only, not touch-draggable |
| COMP-023 | UX | TweakNumber leading-zero input snaps mid-typing |
| COMP-024 | Props contract | TweakSelect emits strings; numeric consumers break |
| COMP-025 | Robustness | useTweaks persisted values not type-validated against defaults |
| COMP-026 | UX (i18n) | English "Tweaks"/"Close tweaks" in zh-TW UI |
| COMP-027 | Bug | TweakColor native fallback mishandles undefined value |
| COMP-028 | Performance | TweakSlider writes localStorage + postMessage per drag pixel |
| COMP-029 | Dead code | icons.jsx unused `SIZE` map |
| COMP-030 | Bug | HlsPlayer `onFallback` missing from effect deps (stale closure) |

---

## Section 4: Operational Pages — Status / Monitor / Pumps (40 findings)

**Files:** `pages/status.jsx`, `pages/monitor.jsx`, `pages/pumps.jsx`
**Counts:** 1 Critical · 5 High · 21 Medium · 13 Low

### Critical

**OPS-001 — Backdrop click permanently destroys a shown-once API key**
- **Location:** `status.jsx:604, 668`
- Both "create webcam client" and "revoke key" modals display a credential shown **exactly once** ("⚠ API Key 僅顯示一次，請立即複製") yet dismiss on any backdrop click. A stray click/trackpad drag loses the key with no recovery.
- **Fix:** Disable backdrop dismissal while `createdKey`/`revokedKey` is displayed.

### High

| ID | Category | Issue |
|----|----------|-------|
| OPS-002 | Bug | "串流健康" column permanently `0.0Mbps` / critical-red for every camera — edge never publishes `bitrate_mbps`; column never re-wired to `getStreamHealth` |
| OPS-003 | Bug | Live tile permanent black frame if vendored hls.js fails to load or platform unsupported — no `onFallback()`, no `Hls.isSupported()` check, UI still claims `● LIVE` |
| OPS-004 | UX | Webcam live-start failures and 30s warm-up timeout completely silent — no toast, no error text; tile just quietly reverts |
| OPS-005 | Accessibility/Bug | Revoke-key (🔑) button keyboard-inaccessible: Enter bubbles to `<tr>` handler which `preventDefault()`s the click — keyboard users can never rotate credentials |

### Medium (21 findings)

| ID | Category | Issue |
|----|----------|-------|
| OPS-006 | Bug | HLS fatal-error recovery uses `recoverMediaError()` for ALL error types (network errors need `startLoad()`) |
| OPS-007 | Performance | Navigating away while stream is live never sends `stopWebcamStream` — encoder runs until lease lapses (~90s) |
| OPS-008 | Bug | Pump command confirm race: device confirms via WS before `awaitRef` armed → false 25s "outcome unknown" on the fast path |
| OPS-009 | Bug | "恢復自動" success toast contradicted by stale manual-override banner for up to 20s (no `onRefresh` prop) |
| OPS-010 | Bug | "畫面凍結" overlay (from snapshot age) painted over perfectly live HLS video |
| OPS-011 | Bug | Playlist readiness probe only recognizes `.ts` segments — fMP4/CMAF config silently breaks live view |
| OPS-012 | Bug (layout) | Source badge (`top-1 left-1`) overlaps status dot + alert-count badge on every camera tile |
| OPS-013 | Performance | N identical `/api/stream/health` scrapes on Status page mount (one per camera row; endpoint scrapes ALL streams) |
| OPS-014 | Bug | StreamRowButton state stale: no periodic re-probe; errors pin label to "開始串流" permanently |
| OPS-015 | UX | Raw English `err.message` leaks into zh-TW toasts (3 handlers bypass `actionErrorText`) |
| OPS-016 | UX/Bug | Revoke flow: native `confirm()`, no busy latch (double-fire → 404 toast), no post-success refresh |
| OPS-017 | Bug | Copy-success toast (z-40) renders UNDER the open modal (z-50) — feedback invisible |
| OPS-018 | UX | Grid order-freeze is mouse-only (`onMouseEnter`) — touch/keyboard operators get cards reshuffled mid-interaction |
| OPS-019 | Accessibility | Interactive buttons nested inside `role="button"` cards/rows (WCAG 4.1.2); `<tr role="button">` destroys table semantics |
| OPS-020 | Accessibility | Status conveyed by color only (node dots, gauge fills) — no text/aria alternative |
| OPS-021 | Accessibility | Icon-only action buttons (snooze, stream, 🔑) lack `aria-label` |
| OPS-022 | Accessibility | Create/revoke modals: no `role="dialog"`, no focus trap, no Escape handling |
| OPS-023 | Bug | Missing `power_source` defaults to `'mains'` — never-reported pump shows confident green 市電 badge |
| OPS-024 | UX | Water-level reading has no "as of" timestamp; no page-level staleness banner |
| OPS-025 | Bug | Pumps control wrapper swallows ALL keydowns → global shortcuts (Esc, ⌘K) dead while control focused |
| OPS-026 | Bug | `▶ 即時` double-click has no synchronous in-flight latch (same-tick double-fire) |

### Low (13 findings)

| ID | Category | Issue (one-liner) |
|----|----------|-------------------|
| OPS-027 | UX | Error toasts auto-dismiss in 3s (same as success) |
| OPS-028 | Bug | Battery voltage rendered unrounded (12.734999V) |
| OPS-029 | UX | lastPumpCommand time shows HH:MM:SS with no date/age |
| OPS-030 | UX | Dead data slots: cycleHistory/flow/trend always null but rendered |
| OPS-031 | Performance | Per-tile ClockDisplay = N independent 1Hz intervals |
| OPS-032 | UX | Filter chips are click-to-cycle with no visible affordance |
| OPS-033 | Bug/UX | Unvalidated tab sessionStorage; fullscreen toggle label never changes |
| OPS-034 | UX | Snooze duration hardcoded 30 min with no alternative |
| OPS-035 | UX | "連線中..." is tiny static text — no spinner during 30s warm-up |
| OPS-036 | Bug | NodeCard frozen-check disagrees with SnapshotImage about `upload == null` |
| OPS-037 | Bug | `fmtAgeOrDash` cross-file dependency (status.jsx relies on monitor.jsx export) |
| OPS-038 | Bug | `send()` doesn't check `releaseBusy` (defense-in-depth gap) |
| OPS-039 | Bug | `useStableSort` mutates ref during render (concurrent-mode hazard) |

---

## Section 5: Secondary Pages — Alerts / Weather / Handover / Audit (35 findings)

**Files:** `pages/alerts.jsx`, `pages/weather.jsx`, `pages/handover.jsx`, `pages/audit.jsx`
**Counts:** 0 Critical · 0 High · 11 Medium · 24 Low

### Medium

| ID | Page | Category | Issue |
|----|------|----------|-------|
| ALR-001 | Alerts | Bug/UX | State-change banner blames "other operator" for user's OWN ack/resolve when selection doesn't advance (Shift+A, last-alert) — cry-wolf on a safety banner |
| ALR-002 | Alerts | Bug | SnoozeMenu items ignore `busy`; `onSnooze` guard returns silently → menu closes as if snooze succeeded when nothing was sent |
| ALR-003 | Alerts | UX | Checkboxes/bulk bar on 歷史 tab; bulk-acking resolved rows fails with misleading reason |
| ALR-004 | Alerts | UX | History feed capped at 80 rows with no truncation indicator (active tab has one) |
| WXA-001 | Weather | Bug | Failed config load can never be retried in-session (cached station lists block refetch); Save stuck disabled until page reload |
| WXA-002 | Weather | Bug | Manual-refresh guard is state-only — double-click fires duplicate upstream API fan-out |
| HDO-001 | Handover | Bug | 儲存 double-click race (state-only guard) → duplicate save → spurious 409 conflict dialog against own save |
| HDO-002 | Handover | Accessibility | Confirm/Conflict dialogs: incomplete focus trap; background remains tabbable |
| HDO-003 | Handover | UX | 3 of 5 shift-summary stats hardcoded '—' forever; generated notes persist placeholder lines into official record |
| AUD-001 | Audit | Bug | "已匯出 CSV" success toast fires before download exists; export failure downloads JSON error as .csv |
| AUD-002 | Audit | UX | 詳情 column dumps raw `JSON.stringify` — `{}` noise + unbounded row stretching |

### Low (24 findings)

| ID | Page | Category | Issue (one-liner) |
|----|------|----------|-------------------|
| ALR-005 | Alerts | UX | Empty history tab shows active-state copy ("目前沒有作用中的警報") |
| ALR-006 | Alerts | Accessibility | Unread dot: empty span with `title` only — invisible to screen readers |
| ALR-007 | Alerts | Accessibility | Tab strips lack tablist/tab/aria-selected semantics |
| ALR-008 | Alerts | Performance | `filtered` useMemo defeated (new activeList identity every render) |
| ALR-009 | Alerts | Performance | O(n²) sibling-count recompute per row per render |
| ALR-010 | Alerts | UX | No existing-snooze visibility or unsnooze in SnoozeMenu |
| ALR-011 | Alerts | Bug | Peer state-change banner fires only once per selection |
| ALR-012 | Alerts | UX | Bulk bar overflows on narrow screens (no flex-wrap) |
| WXA-003 | Weather | Bug | Peak badges print axis floor "1" when real peak is 0 |
| WXA-004 | Weather | UX | Lightning hero tile permanently dead (hardcoded null) |
| WXA-005 | Weather | Bug | Typed out-of-range lat/lon bypasses validation |
| WXA-006 | Weather | Accessibility | WindArrow svg aria-label without `role="img"` |
| WXA-007 | Weather | Bug | Typhoon line renders "距離 km · 方位 °" when fields null |
| HDO-004 | Handover | Bug | Draft restore marks dirty even when draft === server text |
| HDO-005 | Handover | UX | Confirm copy promises peer-version preview that doesn't exist |
| HDO-006 | Handover | UX | Permanent dead "歷史備註" column wastes a third of the page |
| HDO-007 | Handover | UX | No Ctrl+Enter save shortcut (inconsistent with rest of dashboard) |
| AUD-003 | Audit | Accessibility | "本班·我的動作" toggle missing `aria-pressed` |
| AUD-004 | Audit | UX | Timestamps omit year (ambiguous for prior-year rows) |
| AUD-005 | Audit | UX | meOnly with unknown session user → generic empty state |
| AUD-006 | Audit | Bug | Synthesized row key can collide (same ms + by + action + target) |
| AUD-007 | Audit | UX | Click-to-cycle filter chips with dropdown-chevron affordance mismatch |
| XPG-001 | All | Architecture | Pages read `window.*` globals at render; freshness depends on shell re-render side effect |
| XPG-002 | Handover/Audit | Architecture | Interpolated Tailwind classes only work under Play CDN runtime generation |

---

## Section 6: Auth Flow, Session & Cross-Cutting UX (24 findings)

**Files:** `login.html`, `main.py`, `app.jsx` (auth logic), `api.jsx` (auth headers), `websocket_service.py`
**Counts:** 1 Critical · 5 High · 10 Medium · 8 Low

### Critical

**AUTH-001 — Logout button is broken: GET navigation to a POST-only route (405)**
- **Location:** `components.jsx:727`, `main.py:469`
- `window.location.href = '/logout'` issues GET; route is POST-only → raw `{"detail":"Method Not Allowed"}` page. Session cookie remains valid for up to 24h on a shared console.
- **Fix:** `await fetch('/logout', {method:'POST', credentials:'same-origin'})` then navigate to `/login`.

### High

| ID | Category | Issue |
|----|----------|-------|
| AUTH-002 | Security | WS session re-validation loop reads frozen connect-time scope — `if _get_session_user(websocket): continue` is ALWAYS true; loop is a no-op. Socket keeps receiving live data after logout. |
| AUTH-003 | UX | Session-expiry modal has no "session restored" probe — multi-tab operators must re-login N times even after authenticating in one tab |
| AUTH-004 | UX/Security | `GET /login` renders login form to already-authenticated users (no session check/redirect) |
| SEC-001 | Security | No absolute session lifetime: `login_at` written but never compared. `/api/session/extend` re-stamps cookie every 15min → active sessions are **immortal**. Code comment claiming 24h hard-expiry is factually wrong. |
| FLOW-001 | UX | Single toast slot, no queue, no dismiss: burst of messages overwrites action-critical failure toasts ("認領失敗") in <1s — operator believes ack landed when it didn't |

### Medium

| ID | Category | Issue |
|----|----------|-------|
| AUTH-005 | UX | F5-after-expiry redirect carries no `next` — H-1 state-restore machinery bypassed on most common path |
| AUTH-006 | Security/UX | Login throttle: per-IP (NAT = room-wide lockout from colleague typos), resets on restart, no countdown |
| FLOW-002 | Bug | No `pageshow`/bfcache handling — Back after logout restores stale live console with timers intact |
| FLOW-003 | Bug (safety) | Mute/theme never sync across tabs (no `storage` event listener) — one tab silent while another alarms |
| FLOW-004 | UX | New-alert banner renders ONLY on Alerts page — operators on Monitor wall miss it entirely |
| FLOW-005 | UX | Stale-data banner dismissible but flickers back every 20s poll → banner blindness during brown-outs |
| UX-001 | UX | All timestamps HH:MM:SS with no date — events crossing midnight ambiguous in handover review |
| SEC-002 | UX/Accessibility | Login form: no `autocomplete` attributes, no Caps Lock hint, no password reveal |
| SEC-003 | Security | Session cookie not `Secure` by default; credentials POSTed cleartext on HTTP LAN |

### Low (8 findings)

| ID | Category | Issue (one-liner) |
|----|----------|-------------------|
| UX-002 | Accessibility | `zh-Hant` (login) vs `zh-TW` (SPA) lang mismatch |
| UX-003 | UX | Lockout message lacks retry countdown |
| UX-004 | UX | No onboarding for first-ever login |
| UX-005 | UX | Expiry toast doesn't mention surviving handover draft |
| FLOW-006 | UX | Session-extend activity gate misses scroll-only interaction |
| FLOW-007 | Performance | No cross-tab leader election — N tabs multiply WS/poll/scrape load |
| SEC-004 | Accessibility | Login error region not `role="alert"`; hardcoded copyright year |

---

## Recurring Patterns (Systemic Issues)

These failure classes appear repeatedly across the codebase and suggest process-level fixes:

### 1. State-only double-submit guards (4 instances)
`weather.jsx` refresh, `handover.jsx` save, `monitor.jsx` live-start, `status.jsx` revoke — all use `if (state) return; setState(true)` which loses the same-tick race. The codebase already has the correct pattern (`alertBusyRef`, `inFlightRef`) — these 4 were missed.
**Systemic fix:** Lint rule or shared `useAsyncGate()` hook.

### 2. Fabricated "healthy" defaults for missing data (4 instances)
`power_source → 'mains'` (green badge), `bitrate → 0` (looks measured), `ageSec → 0` (looks fresh), `OPERATORS_ONLINE → []` (looks like nobody's here). The codebase's own convention (`roundOrNull`, MSP-F8) says "no data must never look like a reading" — these violate it.
**Systemic fix:** Enforce null → "—" rendering in all mappers.

### 3. Silent failure where feedback is safety-critical (5 instances)
Snooze not enforced (DATA-001), pump command success for offline nodes (DATA-008), live-start timeout silent (OPS-004), snooze-menu no-op (ALR-002), unsnooze DB failure ignored (DATA-019).
**Systemic fix:** Every operator action that changes safety state must confirm the effect, not just the transport.

### 4. Focus management gaps in newer overlays (4 instances)
TweaksPanel (no focus management at all), handover dialogs (incomplete trap), CommandPalette/ShortcutsModal (focus restore to detached node), status.jsx modals (no trap/Escape).
**Systemic fix:** Extract the working MuteDrawer/NodeSidePanel pattern into a shared `useModalFocus()`.

### 5. Keyboard interaction broken by event bubbling (3 instances)
🔑 button (Enter opens row instead), pumps wrapper (swallows ALL keys), TweakRadio (no click handler).
**Systemic fix:** Standardize `onKeyDown` stopPropagation for Enter/Space only on nested interactive elements.

---

## Recommended Remediation Order

### Sprint 1 — Safety & Security (Critical + top High)
1. AUTH-001: Fix logout (POST fetch → navigate) — 15 min
2. OPS-001: Disable backdrop dismiss when key displayed — 10 min
3. DATA-004: Cumulative byte counter in upload loop — 30 min
4. SEC-001: Enforce absolute session lifetime from `login_at` — 1 hr
5. DATA-002: Add status predicate to ack/resolve UPDATEs — 1 hr
6. DATA-001: Implement snooze enforcement in `create_alert` (or honestly degrade UI) — 2-4 hr
7. AUTH-002: WS re-validation via cookie re-parse or revocation set — 2 hr

### Sprint 2 — Operator Trust (High UX)
8. OPS-002 + OPS-013 + OPS-014: Shared stream-health fetch (fixes red column + N+1 scrape + stale button together)
9. OPS-003 + OPS-004 + OPS-006: HLS fallback chain + error toasts
10. FLOW-001: Toast queue with dismiss + longer error duration
11. AUTH-003 + AUTH-004: Session-restore probe + authenticated redirect
12. OPS-005 + OPS-025: Keyboard event fixes

### Sprint 3 — Consistency & Accessibility
13. COMP-001 + COMP-002 + COMP-003: Keyboard/focus fixes in components
14. OPS-019/020/021/022: ARIA batch (nested buttons, color-only status, labels, dialog roles)
15. SHELL-002 + SHELL-024: Contrast fixes
16. State-only guard → ref-gate conversions (WXA-002, HDO-001, OPS-026, OPS-016)

### Sprint 4 — Polish & Performance
17. Remaining Medium/Low items by page ownership

---

## Notable Strengths (for balance)

The audit team independently noted these above-average qualities:
- All intervals/listeners have proper cleanup; refresh coalescing with in-flight guard
- IME-composition guards on all keyboard handlers (rare in dashboards)
- Honest degradation for unknown severities/states (`safeSevMeta`)
- Server-derived attribution (anti-spoofing), optimistic-concurrency on handover saves
- Focus traps on primary modals, severity-by-pattern (grayscale-safe)
- Open-redirect defense, CSRF origin gate, constant-time credential comparison, EXIF stripping
- Extensive inline documentation of prior fixes and design rationale

---

*Report generated by 6 parallel audit agents. All findings include file:line references for verification. No files were modified during this audit.*
