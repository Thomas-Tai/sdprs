# Node management: delete edge device + hide (declutter)

**Date:** 2026-08-22
**Status:** Approved design (brainstorming) — implementation plan to follow
**Scope:** SPA only. No backend, database, API, or WebSocket changes.

## 1. Context & motivation

Edge devices auto-register. The moment a node publishes its first MQTT
heartbeat (glass) or status (pump), the server both records it in memory and
upserts a `nodes` row (`mqtt_service.py:_handle_heartbeat` → `upsert_node`,
`mqtt_service.py:411`). `list_nodes` then returns it — there is no manual
"add device" step, and the operator has no way to curate the resulting list.

Two consequences shape this design:

1. **A still-connected node re-appears after deletion.** Deleting a node that
   is still publishing only removes it until its next heartbeat re-creates it.
   Deletion is therefore meaningful only for a node that has genuinely stopped
   (offline / retired).
2. **The wall crowds on offline nodes.** `WallView` renders only the first
   nine nodes (`app.jsx` `sorted.slice(0, 9)`), sorted **offline/critical
   first**. An unwanted offline edge node sorts to the front and pushes live
   nodes off the nine-tile wall.

The console needs two distinct, complementary tools:

- **Delete** — permanently remove a *stopped/retired* node and its
  time-series. Backend already supports this; only the SPA affordance is
  missing for edge nodes.
- **Hide** — reversibly declutter the view of *any* node (especially a live
  one that keeps re-appearing), without touching data.

## 2. What already exists (build minimally)

- **Backend delete is complete.** `DELETE /api/nodes/{node_id}`
  (`api/nodes.py:delete_node`) removes the node row plus its `pump_readings`
  and `events`, preserves the append-only `operator_actions` audit trail, and
  broadcasts a `node_deleted` WebSocket event. No change required.
- **SPA plumbing exists.** `deleteNode()` is already defined and exported in
  `api.jsx`, and `node_deleted` already triggers a list refresh
  (`app.jsx:723`). Only a UI entry point is missing.
- **A proven delete pattern exists.** The webcam delete flow (row button →
  in-app confirm modal → busy latch → 404-tolerant → refresh) in `status.jsx`
  is the template to mirror.
- **No hide concept exists** anywhere today.

## 3. Goals / non-goals

**Goals**
- Add a guarded **delete** affordance for edge devices (glass + pump) in the
  status page, reusing the existing endpoint.
- Add a **hide** affordance for edge devices that declutters the wall, the
  monitor grid, and the status page default list, reversibly, stored
  per-display.

**Non-goals**
- No backend / DB migration / new API / new WS event.
- No refactor of the existing webcam delete flow into a shared component (the
  two flows stay parallel — different blast radius).
- No bulk delete/hide, no role/permission model.
- No shared/server-side hide — deliberately deferred as a clean future
  upgrade (see §9).

## 4. Part A — Delete edge device (glass + pump), guarded

### 4.1 Row button
Add a `刪除` button to the status-row action column (`status.jsx`, the
`<div className="inline-flex gap-1">` at ~line 758), rendered when
`n.type === 'camera'` (glass) or `n.type === 'pump'`. It sits beside the
existing snooze/key buttons and matches the webcam `刪除` button's styling.
The webcam delete button (gated on `n.type === 'webcam'`) is unchanged.

### 4.2 Confirm modal
A **new, separate** in-app modal, sibling to the webcam one — it must not
reuse the webcam modal, whose body enumerates cameras. Introduce:

- state `edgeDeleteTarget` — `{ id, name, type, status } | null`
- state `edgeDeleteBusy` — bool
- handler `confirmDeleteEdge()`

`confirmDeleteEdge()` mirrors `confirmDeleteWebcam()` exactly on the
mechanics: `window.SDPRS_API` G1 guard (else error toast, no latch), busy
latch, `deleteNode(target.id)`, **404 treated as already-gone** (refresh like
success), success toast, `onRefresh()`, `mountedRef` guard, `finally` clears
busy. Escape-to-close is added to the existing modal-stack keydown effect
(`status.jsx:425`), ordered above `showAddModal`.

### 4.3 Guard — delete requires OFFLINE (both glass and pump)
The delete button is **disabled while the node is not offline**
(`n.status !== 'offline'`). Rationale: a live node re-appears on its next
heartbeat, so deleting it is futile and misleading; hide is the tool for a
live node.

- Disabled tooltip: 「節點仍在線；若只想從畫面移除請用『隱藏』。刪除僅適用於已離線／退役節點。」
- Pump confirm adds a stern line: 「⚠ 這只會從主控台移除此節點；實體裝置仍會依本地控制邏輯繼續運作、不會停止。」

The guard is **client-side UX only** — the endpoint stays generic (it is also
used to clean up stuck/test nodes), so this prevents an operator foot-gun; it
is not a security boundary.

### 4.4 Confirm copy
- Title: 「刪除節點」
- Body: 「確定要刪除節點「<name/id>」？將永久移除此節點及其歷史資料（事件、水位讀數）；稽核紀錄保留，此操作無法復原。」
- Pump-only extra line: as in §4.3.
- Buttons: 「取消」 / 「確定刪除」（busy: 「刪除中...」）, `sev-critical` confirm.

## 5. Part B — Hide edge device (glass + pump), per-display

### 5.1 Storage
Hidden node ids live in `localStorage` under `sdprs.hiddenNodes` as a JSON
array of node_id strings. All access goes through a small helper (in
`data.jsx`, beside the existing storage helpers) with try/catch on every read
and write so an unavailable/again-throwing `localStorage` (private window,
blocked storage) degrades to "nothing hidden" rather than crashing:

- `loadHiddenNodes(): string[]`
- `saveHiddenNodes(ids: string[]): void`

### 5.2 App-level state and the single filter
`app.jsx` holds `hiddenIds` as a `Set`, initialised from `loadHiddenNodes()`.
Two handlers update both the state and localStorage:

- `onHideNode(id)` — add id, persist.
- `onUnhideNode(id)` — remove id, persist.

The app derives `visibleNodes = nodes.filter(n => !hiddenIds.has(n.id))` and
passes it to the views that should be decluttered:

- `WallView` receives `visibleNodes` — hidden nodes drop off the nine-tile
  wall (filter applied before its sort/slice).
- `MonitorPage` receives `visibleNodes` — hidden nodes drop off the grid.
- `StatusPage` receives the **full** `nodes` plus `hiddenIds`, `onHideNode`,
  `onUnhideNode` — it stays the management surface.

Hide is presentational only: it never alters alert counts or fleet totals.
Hidden nodes remain in the numeric summary; the reveal chip (§5.3) reports how
many are hidden.

### 5.3 Status page
- Each edge row (`camera`/`pump`) gets a **hide** button (eye-off icon) in the
  action column beside delete. Clicking calls `onHideNode(n.id)` and toasts
  「已隱藏節點「<name/id>」」.
- By default, hidden edge nodes are excluded from the rendered list.
- A 「顯示已隱藏 (N)」 toggle chip (beside the existing `FilterChip`s) reveals
  hidden rows when N > 0. Revealed rows render dimmed with a 「已隱藏」 badge and
  an **unhide** button (calls `onUnhideNode`, toast 「已取消隱藏」).
- Interaction with existing type/status/location filters: the hidden filter is
  applied on top of them; `N` counts hidden edge nodes across the full list
  (not the filtered subset), so siblings hidden under an active filter are
  still discoverable.

### 5.4 Wall mode
Each **edge** tile (`camera`/`pump`) in `WallView` gets an unobtrusive,
hover-revealed hide control (a small button in a tile corner, `opacity-0
group-hover:opacity-100`), calling `onHideNode(n.id)`. `WallView` gains an
`onHideNode` prop, threaded from `app.jsx`. Non-edge tiles (webcam) get no
hide control (out of scope). The wall is otherwise control-free; the control
stays visually minimal so the glance view is unaffected.

## 6. Icons
`icons.jsx` — add an `EyeOff` (hide) icon if not already present; use an
existing eye/visibility icon for unhide, or a second variant. Confirm what
exists before adding to avoid a duplicate.

## 7. Edge cases
- **Hidden id for a node that no longer exists:** harmless — the filter simply
  never matches. No pruning needed; the id lingers in localStorage without
  effect. (Optional: prune on load against the known node list — not required.)
- **Delete then reconnect:** covered by the offline-only guard (§4.3); a live
  node cannot be deleted from the UI.
- **404 on delete:** already-gone → refresh like success (§4.2).
- **localStorage unavailable:** helper degrades to empty; hide buttons still
  render but hiding has no persistence for that session (best effort).
- **Wall with fewer than 9 nodes:** unaffected; filtering can only reduce the
  count.

## 8. Testing
All SPA render tests (`tools/spa/render_extra/section4-status.js`, run via
`node tools/spa/render_tests.js`), plus one `WallView` case:

- Delete button renders for `camera` and `pump`, not for `webcam` via this
  path.
- Delete button is **disabled** when the node is online, **enabled** when
  offline.
- Clicking an enabled delete opens the confirm modal; confirming calls
  `deleteNode` with the correct id; a 404 is tolerated; success triggers
  refresh.
- Hide button on an edge row removes the node from the default list.
- 「顯示已隱藏 (N)」 reveals hidden rows; unhide restores; the hidden set
  persists to (mocked) localStorage.
- `WallView` given a hidden id excludes that tile from the grid and renders
  the hide control on an edge tile.

Backend delete is already covered by `test_nodes_api.py`; no backend test
changes.

## 9. Deferred / future
- **Shared server-side hide.** A `hidden` flag on the node synced via
  WebSocket so a single hide reaches every display. Requires DB migration +
  endpoints + WS + audit + backend tests. Clean upgrade path from this
  per-display v1 (swap the localStorage helper for API-backed state; the
  view-side filter is unchanged).
- **Hide for webcam clients** — out of scope (webcams use a separate
  table/flow).

## 10. Verification
- `node tools/spa/render_tests.js` (SPA render gate).
- The three static SPA gates (compile/read checks).
- Backend `pytest` unaffected — run as a smoke check only.

## 11. Files touched
- `central_server/static/spa/pages/status.jsx` — delete button + modal +
  `confirmDeleteEdge`; hide/unhide row buttons + reveal toggle.
- `central_server/static/spa/app.jsx` — `hiddenIds` state + handlers,
  `visibleNodes` to `WallView`/`MonitorPage`, full nodes + handlers to
  `StatusPage`; `WallView` hide control + prop.
- `central_server/static/spa/data.jsx` — `loadHiddenNodes`/`saveHiddenNodes`
  helpers.
- `central_server/static/spa/icons.jsx` — eye-off icon if missing.
- `tools/spa/render_extra/section4-status.js` — tests (+ a WallView case).
