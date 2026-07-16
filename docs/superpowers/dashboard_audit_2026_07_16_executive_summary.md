# SDPRS Dashboard Audit — Executive Summary

**Date:** 2026-07-16  
**Current-state snapshot:** 18:55 Asia/Taipei  
**Scope:** Dashboard UI, buttons, keyboard interaction, client state, API contracts, live server behavior, and regression coverage.

## Executive conclusion

The SDPRS dashboard is **not ready for operational release**.

The original interaction audit recorded 64 findings. Follow-up commits repaired many of those items, including alert state leakage, double-dispatch protection, API error details, filter pruning, pump offline styling, keyboard propagation, and several session/authentication flows.

The latest live revalidation found the following **remaining current-state risks**:

| Priority | Count | Release meaning |
|---|---:|---|
| P0 — Release blocker | 4 | Core operator workflows fail or make unsafe promises |
| P1 — High risk | 7 | Misleading status, stale data, or missing operational controls |
| P2 — Medium risk | 5 | Interaction consistency, accessibility, and regression gaps |
| **Total residual clusters** | **16** | Grouped by root cause rather than individual code occurrence |

The main problem is no longer missing `onClick` handlers. Most controls are technically wired, but several buttons disagree with backend state, invoke incomplete features, or update local UI without delivering the promised operational behavior.

## P0 — Release blockers

### 1. New alerts cannot be acknowledged

- The client maps backend status `PENDING_VIDEO` to the ordinary UI state `pending`.
- The detail panel therefore renders an enabled **認領** button.
- The backend accepts acknowledgement only when status is `PENDING`.
- Live result: `PATCH /api/alerts/{id}/acknowledge` returns **409 Conflict**.
- Bulk acknowledgement returns HTTP 200 with `{"acked":0}`, but the UI clears the selection and note as though the action succeeded.

Evidence:

- `central_server/static/spa/api.jsx:139`
- `central_server/static/spa/pages/alerts.jsx:137`
- `central_server/static/spa/pages/alerts.jsx:723`
- `central_server/api/alerts.py:400`

### 2. Mute and alert-audio controls are misleading

- Per-node snooze does not suppress the dashboard-generated alert tone.
- Lightning auto-mute is represented by two local switches, but neither is connected to alert playback or a backend action.
- Audio playback checks only the global mute flag.
- Every increase in unacknowledged-alert count plays the critical tone, including warning or informational alerts.
- The documented acknowledgement sound is implemented but never called by the acknowledgement workflow.

Evidence:

- `central_server/static/spa/components.jsx:263`
- `central_server/static/spa/components.jsx:858`
- `central_server/static/spa/pages/weather.jsx:18`
- `docs/operations/dashboard-guide.md:48`

### 3. Alert evidence playback and download are absent

- The alert detail video area is an explicit placeholder.
- The client alert mapper does not expose a playable MP4 or snapshot URL.
- The backend exposes MP4 upload with `PUT`, but no authenticated playback/download `GET` route.
- The operations guide nevertheless promises HLS playback, speed controls, frame stepping, and download.

Evidence:

- `central_server/static/spa/pages/alerts.jsx:504`
- `central_server/static/spa/api.jsx:153`
- `central_server/api/alerts.py:205`
- `docs/operations/dashboard-guide.md:72`

### 4. Audit CSV export always fails from the dashboard

- The client requires a `HEAD /api/audit/export.csv` preflight.
- The backend route supports only `GET`.
- Live result: `HEAD` returns **405**, while direct `GET` returns **200** with valid CSV.
- The UI therefore stops before initiating the valid download.

Evidence:

- `central_server/static/spa/api.jsx:613`
- `central_server/static/spa/api.jsx:627`
- `central_server/api/audit.py:58`

## P1 — High-risk findings

| Finding | Operational impact | Primary evidence |
|---|---|---|
| Handover, audit, and weather are initial-load-only | Peer handover edits can be overwritten; audit rows and recovered weather remain stale until reload | `central_server/static/spa/api.jsx:549` |
| Healthy WebSockets show “Reconnecting” | Server pings are discarded; the 20-second poll is the only liveness reset | `central_server/static/spa/api.jsx:714`, `components.jsx:247` |
| Focus mode does not hide informational alerts | The root class changes, but no alert uses `focus-hide` or `focus-dim` | `central_server/static/spa/app.jsx:137`, `styles.css:151` |
| Stream controls are missing | Backend start/stop endpoints exist, but the Status action column contains only snooze | `pages/status.jsx:206`, `api/stream.py:44` |
| Missing pump readings appear healthy | An online pump with `water_level=null` remains green/normal and renders an empty `%` value | `api.jsx:217`, `pages/pumps.jsx:23` |
| Filtered alerts can falsely show “all clear” | An empty filter result renders the global no-active-alert state even when other alerts remain active | `pages/alerts.jsx:99`, `pages/alerts.jsx:281` |
| Alerts page is not mobile-safe | The master/detail view always uses two desktop-oriented columns | `pages/alerts.jsx:204` |

## P2 — Medium-risk interaction and quality gaps

1. **Command Palette semantics** — selecting a node only navigates to Status without opening that node; “Audit · only my actions” does not enable the filter.
2. **Node side panel freshness** — the selected node object freezes while the panel remains open; recent history cannot select an alert because `onSelectAlert` is not passed.
3. **Misleading interaction affordances** — the shortcut dialog advertises `F` fullscreen without a global handler; recent-event cards use pointer/hover styling without click behavior.
4. **Accessibility** — several dialogs do not trap or restore focus, and multiple icon-only close/edit controls have no accessible name.
5. **Wall/development mode** — Wall mode has no visible exit and disables keyboard handling; the hidden segmented radio control is pointer-only.

## Root causes

The residual defects cluster around four architectural issues:

1. **Client/backend state-contract mismatch** — especially `PENDING_VIDEO` versus `PENDING` and `HEAD` versus `GET`.
2. **Non-reactive global state** — handover, audit, weather, selected-node data, and some wall/footer data rely on `window.*` snapshots.
3. **Placeholders presented as operational controls** — video, lightning auto-mute, focus mode, and stream management visibly promise behavior that is absent or incomplete.
4. **Insufficient interaction regression coverage** — backend behavior is tested extensively, but there are no component-level or real-browser button-flow tests.

## Verification evidence

### Live isolated server

| Check | Result |
|---|---|
| Server startup | Passed |
| Login | HTTP 200 |
| Dashboard root | HTTP 200 |
| Current user injection | Passed |
| New `PENDING_VIDEO` alert acknowledgement | HTTP 409 |
| Bulk acknowledgement of the same alert | HTTP 200, `acked: 0` |
| Audit CSV `HEAD` | HTTP 405 |
| Audit CSV `GET` | HTTP 200, `text/csv` |
| Weather current endpoint in isolated environment | HTTP 503; UI has no ongoing recovery refresh |

### Static and automated checks

| Check | Result |
|---|---|
| JSX compilation | **13 passed, 0 failed** |
| Interaction inventory | 74 buttons, 12 inputs, 1 select, 2 textareas, 6 dialogs |
| Visible button handlers | Present; only one hidden development radio lacks a direct handler |
| Backend test suite | **201 passed, 4 setup errors, 37 warnings** |

The four backend setup errors are collection-order dependent. Some test modules overwrite the environment with a 27-character test secret; the node-allowlist lifespan later rejects it under the current minimum-secret validation.

Evidence:

- `central_server/tests/test_alerts_api.py:24`
- `central_server/tests/test_node_allowlist.py:29`
- `central_server/config.py:293`

## What is already improved

The latest source no longer exhibits several defects from the original 64-item audit:

- Shared busy protection exists for single-alert acknowledgement, resolution, and snooze.
- Alert-specific detail state is reset when selection changes.
- Bulk selection is pruned when filters change.
- API error bodies now preserve FastAPI `detail` messages.
- Status-row keyboard bubbling and snooze-menu arrow propagation are fixed.
- Offline pump styling is no longer green.
- Audit filters, shift window, export busy state, and CSV filter forwarding are implemented.
- Node location edits now await the backend and surface errors.
- Weather null handling and several session/authentication flows are improved.

These fixed findings are intentionally excluded from the residual priority counts above.

## Recommended remediation sequence

1. **State contract:** decide whether `PENDING_VIDEO` can be acknowledged; make frontend, backend, bulk actions, upload transition, and tests agree.
2. **CSV export:** remove the unsupported preflight or add an explicit authenticated `HEAD` route.
3. **Audio contract:** drive playback from alert objects, severity, node snooze, lightning state, and acknowledgement events.
4. **Evidence pipeline:** add authenticated MP4/HLS playback and download, then replace the placeholder UI.
5. **Stream controls:** connect Status and node detail actions to the existing start/stop endpoints with busy and error states.
6. **Reactive refresh:** include weather, handover, audit, and selected-node synchronization in the refresh/event model.
7. **Truthful status UI:** fix false reconnecting, missing pump telemetry, filtered all-clear, and focus-mode behavior.
8. **Responsive/accessibility pass:** implement a mobile single-pane alert flow, focus traps/restoration, and accessible names.
9. **Regression coverage:** add browser tests for Ack, Resolve, Snooze, filters, mute, export, handover conflict, stream control, mobile navigation, and keyboard shortcuts.

## Release gate

Do not mark the dashboard operationally ready until all of the following pass:

- A new alert can be acknowledged immediately or clearly shows why acknowledgement is unavailable.
- Bulk operations report actual success/failure counts and preserve selection on no-op/failure.
- Per-node and lightning mute behavior matches the labels and documentation.
- Evidence video is viewable and downloadable through an authenticated route.
- CSV export succeeds from the visible button.
- Handover conflict detection sees peer changes without reloading the page.
- Healthy WebSocket pings keep the Live indicator green.
- Missing telemetry never appears healthy.
- The alert workflow works at mobile width and with keyboard-only navigation.
- Browser interaction tests cover every release-blocking operator flow.

## Related documents

- [Full UI/button interaction audit](dashboard_audit_2026_07_16_ui_interaction.md)
- [Earlier dashboard baseline audit](dashboard_audit_2026_07_15.md)
- [Audit and remediation progress](PROGRESS.md)

## Audit limitation

The live server and API interaction pass completed successfully. The in-app browser could not launch because the workspace folder name contains `[Cloud]`, which breaks its permission-glob parser. Responsive and focus findings are therefore source-confirmed rather than screenshot-confirmed. A final real-browser visual pass remains required after the P0/P1 fixes.
