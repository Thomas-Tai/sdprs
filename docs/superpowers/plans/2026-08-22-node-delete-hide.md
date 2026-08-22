# Node Delete + Hide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a guarded delete affordance for edge devices (glass cameras + pumps) and a reversible per-display hide/declutter for those nodes across the status page, monitor grid, and wall.

**Architecture:** SPA-only. Delete reuses the existing `DELETE /api/nodes/{id}` endpoint (already wired as `deleteNode()` in `api.jsx`, already refreshed by the `node_deleted` WS event) via a new confirm modal in `status.jsx` that mirrors the proven webcam-delete flow. Hide is per-display state in `localStorage`, held as a `Set` in `app.jsx`, applied through one shared pure filter (`filterVisibleNodes`) to the wall + monitor while the status page keeps the full list as the management surface.

**Tech Stack:** React 18 (no build step — `.jsx` transpiled by Babel at runtime), `window.*` plumbing, jsdom render tests (`tools/spa/render_tests.js` + `render_extra/`), the three static SPA gates (syntax/refs/classes).

**Spec:** `docs/superpowers/specs/2026-08-22-node-delete-hide-design.md`

## Global Constraints

- **SPA only.** No backend, DB, API, or WebSocket change. `DELETE /api/nodes/{id}` and `deleteNode()` already exist and are exported.
- **zh-TW Traditional Chinese** for every user-facing string.
- **Banned strings** must never appear in any diff: `Msc@2333`, `MSC-Person`, `broker.emqx.io`. No hardcoded credentials.
- **Delete is offline-only** for both glass (`type==='camera'`) and pump — the button is `disabled` unless `n.status === 'offline'`. A live node re-appears on its next heartbeat, so delete is for retired nodes; hide is the tool for live ones.
- **Hide is presentational only** — it never changes alert counts or fleet totals. Hidden nodes stay in the full `nodes` list; only the wall/monitor views and the status default list drop them.
- **Do NOT refactor the webcam delete flow.** The new edge flow is a parallel sibling (`edgeDeleteTarget`/`edgeDeleteBusy`/`confirmDeleteEdge`), because the blast radius and modal body differ.
- **`icons.jsx` already has `Eye` (L57) and `EyeOff` (L58)** — do NOT add icons.
- **Verify with:** `node tools/spa/run_all.js` from the worktree root (`C:\Users\sky\AppData\Local\Temp\sdprs-node-mgmt-wt`). It runs vendor/scope/syntax/refs/render gates (blocking) + tailwind tokens (advisory). Paths in this repo are relative to the worktree root — there is **no** `sdprs/` prefix here.
- **Nothing is pushed to origin** without the user's explicit literal "approved". All work stays on branch `feature/node-delete-hide-2026-08-22`.

## File Structure

- `central_server/static/spa/data.jsx` — three new pure helpers: `loadHiddenNodes()`, `saveHiddenNodes(ids)`, `filterVisibleNodes(nodes, hiddenIds)`. All storage access try/catch-guarded. Exported on `window`.
- `central_server/static/spa/pages/status.jsx` — `StatusPage` gains `hiddenIds`/`onHideNode`/`onUnhideNode` props; a guarded edge delete button + `confirmDeleteEdge` + a sibling confirm modal; per-row hide/unhide buttons; a 「顯示已隱藏 (N)」 reveal chip.
- `central_server/static/spa/app.jsx` — `hiddenIds` state + `onHideNode`/`onUnhideNode` handlers + a `visibleNodes` derivation; `visibleNodes` to `MonitorPage`/`WallView`, full `nodes` + hide props to `StatusPage`; `WallView` gains an `onHideNode` prop and a hover-revealed hide control on edge tiles.
- `central_server/static/spa/icons.jsx` — **unchanged** (Eye + EyeOff already present).
- `tools/spa/render_extra/node-delete-hide.js` — **new** test file (one file per owner, per the harness convention, to avoid collisions in the shared `render_tests.js`). Holds all three new suites (data-helper, edge-delete, edge-hide), each with its own `target`.

**Testability note (honest limitation):** `WallView` lives in `app.jsx`, which the render harness does not load as a target (it auto-boots). The wall/monitor declutter *logic* is therefore covered by unit-testing the shared `filterVisibleNodes` helper (Task 1) — the same idiom the codebase already uses for `orderWallAlerts`/`wallTileFrozen`/`activeAlertCount`. The `WallView` hover-control *rendering* (Task 4) is verified only by the static syntax/refs gates, not by a render test. This is called out rather than papered over.

---

### Task 1: Hidden-nodes storage + filter helpers (`data.jsx`)

**Files:**
- Modify: `central_server/static/spa/data.jsx` (add helpers near the other pure helpers, ~after `resolveSelectedId` at L349–360; export at the `window.*` block L428–445)
- Test: `tools/spa/render_extra/node-delete-hide.js` (new file — the data-helper suite, `target: 'data.jsx'`)

**Interfaces:**
- Produces:
  - `window.loadHiddenNodes(): string[]` — reads `localStorage['sdprs.hiddenNodes']`, returns a fresh array of node-id strings; `[]` on absent/corrupt/non-array/throwing storage.
  - `window.saveHiddenNodes(ids: string[]): void` — writes the JSON array; silent no-op if storage throws.
  - `window.filterVisibleNodes(nodes: object[], hiddenIds: Set<string>): object[]` — returns `nodes` with any `n.id` in `hiddenIds` removed; returns the input list unchanged when `hiddenIds` is null/empty/not a Set.

- [ ] **Step 1: Write the failing test**

Create `tools/spa/render_extra/node-delete-hide.js` with the data-helper suite as the first array entry. (Later tasks append to the same array.)

```js
// Node delete + hide feature suites (2026-08-22).
// One file per owner (see render_tests.js loadExtraSuites) so the shared
// render_tests.js SUITES array takes no collisions. Each entry targets its
// own file; `body` runs with the PRELUDE helpers (A, settle, click, byText,
// setInput, container, root, React, ReactDOM) plus the target file internals.
module.exports = [
  // ---------------------------------------- data.jsx: hide storage + filter
  {
    name: 'node-hide data.jsx: loadHiddenNodes / saveHiddenNodes / filterVisibleNodes',
    target: 'data.jsx',
    deps: ['icons.jsx'],
    body: `
      // Round-trips through the REAL jsdom localStorage.
      try { window.localStorage.removeItem('sdprs.hiddenNodes'); } catch (e) {}
      A('empty store loads as []', Array.isArray(loadHiddenNodes()) && loadHiddenNodes().length === 0);

      saveHiddenNodes(['CAM-9', 'pump-3']);
      const loaded = loadHiddenNodes();
      A('saved ids round-trip back', loaded.length === 2 && loaded.indexOf('CAM-9') !== -1 && loaded.indexOf('pump-3') !== -1, JSON.stringify(loaded));
      A('the raw value is JSON in the expected key', window.localStorage.getItem('sdprs.hiddenNodes') === JSON.stringify(['CAM-9','pump-3']), window.localStorage.getItem('sdprs.hiddenNodes'));

      // Corrupt payload degrades to [] instead of throwing.
      window.localStorage.setItem('sdprs.hiddenNodes', 'not-json{');
      A('a corrupt payload loads as []', loadHiddenNodes().length === 0);
      window.localStorage.setItem('sdprs.hiddenNodes', JSON.stringify({ not: 'an array' }));
      A('a non-array payload loads as []', loadHiddenNodes().length === 0);

      // A throwing store (private window / blocked storage) degrades, not crashes.
      const realLS = window.localStorage;
      Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new Error('SecurityError'); } });
      A('a throwing localStorage.load degrades to []', loadHiddenNodes().length === 0);
      let threw = false;
      try { saveHiddenNodes(['x']); } catch (e) { threw = true; }
      A('a throwing localStorage.save is a silent no-op (never throws)', threw === false);
      Object.defineProperty(window, 'localStorage', { configurable: true, value: realLS });

      // filterVisibleNodes: drops hidden ids, passes the rest through.
      const nodes = [{ id: 'CAM-1' }, { id: 'CAM-9' }, { id: 'pump-3' }];
      const hidden = new Set(['CAM-9', 'pump-3']);
      const vis = filterVisibleNodes(nodes, hidden);
      A('filterVisibleNodes removes hidden ids', vis.length === 1 && vis[0].id === 'CAM-1', JSON.stringify(vis));
      A('filterVisibleNodes with an empty set returns all', filterVisibleNodes(nodes, new Set()).length === 3);
      A('filterVisibleNodes with null hiddenIds returns all', filterVisibleNodes(nodes, null).length === 3);
      A('filterVisibleNodes null-safe on non-array nodes', filterVisibleNodes(null, hidden).length === 0);
    `,
  },
];
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node tools/spa/render_tests.js`
Expected: the new suite FAILS — `loadHiddenNodes is not defined` (helpers not written yet).

- [ ] **Step 3: Write the minimal implementation**

In `data.jsx`, immediately after `resolveSelectedId` (ends L360, before the `activeAlertCount` comment at L362), add:

```js
// Node hide/declutter (per-display). Hidden node ids live in localStorage
// under sdprs.hiddenNodes as a JSON array of node_id strings — a per-display
// convenience, deliberately NOT server-side (design spec §9). Every read and
// write is try/catch-wrapped so a private window, a cleared/blocked store, or
// a throwing accessor degrades to "nothing hidden" instead of taking the page
// down. loadHiddenNodes always returns a fresh array; a corrupt / non-array /
// non-string-element payload is treated as empty.
const HIDDEN_NODES_KEY = 'sdprs.hiddenNodes';
function loadHiddenNodes() {
  try {
    const raw = window.localStorage.getItem(HIDDEN_NODES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(id => typeof id === 'string');
  } catch (_) {
    return [];
  }
}
function saveHiddenNodes(ids) {
  try {
    const arr = Array.isArray(ids) ? ids.filter(id => typeof id === 'string') : [];
    window.localStorage.setItem(HIDDEN_NODES_KEY, JSON.stringify(arr));
  } catch (_) {
    /* best-effort: storage unavailable — hide is a per-display convenience */
  }
}
// The ONE filter both the wall (WallView) and the monitor grid apply, so a
// hidden node drops out of BOTH — and, for the wall, before its offline-first
// sort/slice. `hiddenIds` is a Set for O(1) membership; a null/absent/empty
// set means "hide nothing". Presentational only — callers keep the FULL list
// for fleet totals.
function filterVisibleNodes(nodes, hiddenIds) {
  const list = Array.isArray(nodes) ? nodes : [];
  if (!hiddenIds || typeof hiddenIds.has !== 'function' || hiddenIds.size === 0) return list;
  return list.filter(n => !hiddenIds.has(n.id));
}
```

Then extend the `window.*` export block at the bottom (after `window.resolveSelectedId = resolveSelectedId;`, L444):

```js
window.loadHiddenNodes = loadHiddenNodes;
window.saveHiddenNodes = saveHiddenNodes;
window.filterVisibleNodes = filterVisibleNodes;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node tools/spa/render_tests.js`
Expected: the data-helper suite PASSES (all assertions green). No previously-green suite regresses.

- [ ] **Step 5: Commit**

```bash
git add central_server/static/spa/data.jsx tools/spa/render_extra/node-delete-hide.js
git commit -m "feat(spa): per-display hidden-nodes storage + visible-node filter helpers"
```

---

### Task 2: Guarded edge-device delete (`status.jsx`)

**Files:**
- Modify: `central_server/static/spa/pages/status.jsx` (state near L410; Escape effect L425–437; action column L758–856; modals after the clear-key modal ~L1069)
- Test: `tools/spa/render_extra/node-delete-hide.js` (append the edge-delete suite, `target: 'pages/status.jsx'`)

**Interfaces:**
- Consumes: `window.SDPRS_API.deleteNode(id)` (already exported, `api.jsx:1230`); the existing `onRefresh` prop; existing `mountedRef`, `setToast`, `window.actionErrorText`.
- Produces: internal state `edgeDeleteTarget: {id,name,type,status}|null`, `edgeDeleteBusy: bool`, and handler `confirmDeleteEdge()`. No new props in this task.

- [ ] **Step 1: Write the failing test**

Append this entry to the array in `tools/spa/render_extra/node-delete-hide.js`:

```js
  // ---------------------------------------- status.jsx: guarded edge delete
  {
    name: 'node-delete status.jsx: edge camera/pump delete, offline-guarded',
    target: 'pages/status.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      const calls = [];
      let mode = 'ok'; // ok | notfound | error | hang
      window.SDPRS_API = {
        snoozeNode: () => Promise.resolve(), unsnoozeNode: () => Promise.resolve(),
        deleteNode: (id) => {
          calls.push(id);
          if (mode === 'hang') return new Promise(() => {});
          if (mode === 'notfound') { const e = new Error('HTTP 404'); e.status = 404; return Promise.reject(e); }
          if (mode === 'error') { const e = new Error('資料庫忙碌'); e.status = 500; return Promise.reject(e); }
          return Promise.resolve({ node_id: id, deleted: true });
        },
      };
      window.confirm = () => { throw new Error('used native confirm instead of in-app dialog'); };
      const refreshCalls = [];
      const offlineCam = { id: 'CAM-OFF', name: '西灣橋', type: 'camera', status: 'offline', heartbeat: null, upload: null, snoozeMin: 0, temp: null, level: null };
      const onlineCam  = { id: 'CAM-ON', name: '氹仔橋', type: 'camera', status: 'online', heartbeat: 5, upload: 5, snoozeMin: 0, temp: 30, level: null };
      const offlinePump = { id: 'PUMP-OFF', name: '泵站A', type: 'pump', status: 'offline', heartbeat: null, snoozeMin: 0, level: null, voltage: 12.5, power: 'mains', cycles: 0 };
      const render = (nodes) => ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
        nodes, onSelectNode: () => {}, onRefresh: () => { refreshCalls.push(1); },
        hiddenIds: new Set(), onHideNode: () => {}, onUnhideNode: () => {},
      })));

      render([offlineCam, onlineCam, offlinePump]);
      await settle();
      const delBtns = () => Array.from(container.querySelectorAll('button')).filter(b => b.textContent.trim() === '刪除' && (b.title || '').indexOf('Webcam') === -1);
      A('an offline camera row renders an enabled 刪除 button', !!delBtns().find(b => !b.disabled));
      const onlineRow = Array.from(container.querySelectorAll('tr')).find(r => r.textContent.indexOf('CAM-ON') !== -1);
      const onlineDel = onlineRow && Array.from(onlineRow.querySelectorAll('button')).find(b => b.textContent.trim() === '刪除');
      A('an ONLINE camera row disables 刪除 (offline-only guard)', !!onlineDel && onlineDel.disabled === true, onlineDel && onlineDel.title);

      // --- open confirm on the offline camera; nothing sent yet ---
      const offRow = Array.from(container.querySelectorAll('tr')).find(r => r.textContent.indexOf('CAM-OFF') !== -1);
      const offDel = Array.from(offRow.querySelectorAll('button')).find(b => b.textContent.trim() === '刪除');
      click(offDel); await settle();
      let dialog = container.querySelector('[role="dialog"][aria-label="刪除節點"]');
      A('clicking 刪除 on an offline node opens the edge delete dialog', !!dialog && dialog.textContent.indexOf('確定要刪除節點') !== -1);
      A('the dialog names the node', !!dialog && dialog.textContent.indexOf('西灣橋') !== -1);
      A('the dialog states the history + irreversibility', !!dialog && dialog.textContent.indexOf('無法復原') !== -1 && dialog.textContent.indexOf('水位讀數') !== -1);
      A('a camera dialog shows NO pump physical-device warning', !!dialog && dialog.textContent.indexOf('實體裝置仍會') === -1);
      A('opening the dialog issues no DELETE', calls.length === 0, JSON.stringify(calls));

      // --- cancel ---
      click(byText('button', '取消')); await settle();
      A('取消 closes with no DELETE', !container.querySelector('[role="dialog"][aria-label="刪除節點"]') && calls.length === 0);

      // --- confirm success ---
      click(Array.from(offRow.querySelectorAll('button')).find(b => b.textContent.trim() === '刪除')); await settle();
      click(byText('button', '確定刪除')); await settle();
      A('確定刪除 calls deleteNode(node.id)', calls.length === 1 && calls[0] === 'CAM-OFF', JSON.stringify(calls));
      A('a successful delete refreshes', refreshCalls.length === 1);
      A('a successful delete closes the dialog + toasts by name', !container.querySelector('[role="dialog"][aria-label="刪除節點"]') && container.textContent.indexOf('已刪除') !== -1);

      // --- pump confirm carries the physical-device warning ---
      mode = 'ok'; calls.length = 0;
      const pumpRow = Array.from(container.querySelectorAll('tr')).find(r => r.textContent.indexOf('PUMP-OFF') !== -1);
      click(Array.from(pumpRow.querySelectorAll('button')).find(b => b.textContent.trim() === '刪除')); await settle();
      const pDialog = container.querySelector('[role="dialog"][aria-label="刪除節點"]');
      A('a pump dialog warns the physical device keeps running', !!pDialog && pDialog.textContent.indexOf('實體裝置仍會') !== -1, pDialog && pDialog.textContent);
      click(byText('button', '取消')); await settle();

      // --- 404 = already gone → refresh like success ---
      mode = 'notfound'; calls.length = 0; refreshCalls.length = 0;
      click(Array.from(offRow.querySelectorAll('button')).find(b => b.textContent.trim() === '刪除')); await settle();
      click(byText('button', '確定刪除')); await settle();
      A('404 still calls through', calls.length === 1);
      A('404 refreshes instead of erroring', refreshCalls.length === 1 && container.textContent.indexOf('刪除失敗') === -1);

      // --- real failure: toast, dialog stays open, not latched ---
      mode = 'error'; calls.length = 0; refreshCalls.length = 0;
      click(Array.from(offRow.querySelectorAll('button')).find(b => b.textContent.trim() === '刪除')); await settle();
      click(byText('button', '確定刪除')); await settle();
      A('a real failure surfaces the backend message', container.textContent.indexOf('刪除失敗: 資料庫忙碌') !== -1);
      A('the dialog stays open + not latched on 刪除中', !!container.querySelector('[role="dialog"][aria-label="刪除節點"]') && !byText('button', '刪除中'));
      A('a failed delete does not fake a refresh', refreshCalls.length === 0);

      // --- missing API bundle: G1 guard toasts, no latch ---
      window.SDPRS_API = {}; calls.length = 0;
      click(byText('button', '確定刪除')); await settle();
      A('missing API bundle sends no DELETE + toasts', calls.length === 0 && container.textContent.indexOf('暫時無法連線後端') !== -1 && !byText('button', '刪除中'));

      // --- in-flight: busy label, no double-fire ---
      window.SDPRS_API = { deleteNode: (id) => { calls.push(id); return new Promise(() => {}); } };
      mode = 'hang'; calls.length = 0;
      click(byText('button', '確定刪除')); await settle();
      const busyBtn = byText('button', '刪除中');
      A('an in-flight delete shows 刪除中... disabled', !!busyBtn && busyBtn.disabled === true);
      click(busyBtn); await settle();
      A('a second click while in flight sends no second DELETE', calls.length === 1, JSON.stringify(calls));
    `,
  },
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node tools/spa/render_tests.js`
Expected: the edge-delete suite FAILS — no 刪除 button on camera/pump rows, no `刪除節點` dialog.

- [ ] **Step 3: Write the minimal implementation**

**3a. Add state** — in `StatusPage`, next to the other delete state (after `const [deleteBusy, setDeleteBusy] = useState_p(false);`, L410):

```js
  // Edge-node delete (glass camera / pump). A SEPARATE sibling of the webcam
  // delete flow above — different blast radius (this node + its own events/
  // pump_readings, audit preserved) and a different, non-camera-enumerating
  // confirm body. Reuses the generic DELETE /api/nodes/{id} endpoint via
  // window.SDPRS_API.deleteNode.
  const [edgeDeleteTarget, setEdgeDeleteTarget] = useState_p(null); // { id, name, type, status } | null
  const [edgeDeleteBusy, setEdgeDeleteBusy] = useState_p(false);
```

**3b. Add the handler** — after `confirmDeleteWebcam` (ends L472), add:

```js
  // Edge-node delete — mirrors confirmDeleteWebcam's mechanics exactly:
  // G1 API guard (toast, no latch), busy latch, 404-treated-as-already-gone
  // (refresh like success), mountedRef guard, finally clears busy. Sends the
  // node's OWN id (unlike the webcam flow, which addresses the owning client).
  const confirmDeleteEdge = () => {
    if (edgeDeleteBusy || !edgeDeleteTarget) return;
    const api = window.SDPRS_API;
    if (!(api && api.deleteNode)) {
      setToast({ tone: 'error', msg: '暫時無法連線後端，請稍後再試' });
      return;
    }
    const target = edgeDeleteTarget;
    if (!target.id) {
      setToast({ tone: 'error', msg: '此列缺少節點識別碼，無法刪除' });
      return;
    }
    setEdgeDeleteBusy(true);
    Promise.resolve(api.deleteNode(target.id))
      .catch(err => { if (err && err.status === 404) return; throw err; })
      .then(() => {
        if (!mountedRef.current) return;
        setEdgeDeleteTarget(null);
        setToast({ tone: 'success', msg: `節點「${target.name || target.id}」已刪除` });
        return typeof onRefresh === 'function' ? Promise.resolve(onRefresh()) : undefined;
      })
      .catch(err => { if (mountedRef.current) setToast({ tone: 'error', msg: '刪除失敗: ' + window.actionErrorText(err) }); })
      .finally(() => { if (mountedRef.current) setEdgeDeleteBusy(false); });
  };
```

**3c. Escape-to-close** — in the modal-stack keydown effect, add the `edgeDeleteTarget` branch after the `deleteTarget` branch (after L430) and add both new values to the dependency array (L437):

```js
      if (edgeDeleteTarget) { if (!edgeDeleteBusy) setEdgeDeleteTarget(null); return; }
```

Dependency array becomes (append the two new names):
```js
  }, [revokeTarget, revokeBusy, clearKeyTarget, clearKeyBusy, deleteTarget, deleteBusy, edgeDeleteTarget, edgeDeleteBusy, showAddModal, createdKey, addBusy, revokedKey, nodeKeyRevealed]);
```

**3d. Row button** — inside the action column `<div className="inline-flex gap-1">` (L758), after the `NodeKeyRowButtons` block (L790–794) and before the webcam `n.type === 'webcam'` blocks, add:

```jsx
                      {(n.type === 'camera' || n.type === 'pump') && (
                        <button
                          title={n.status === 'offline'
                            ? (n.type === 'pump' ? '刪除此抽水站節點' : '刪除此攝影機節點')
                            : '節點仍在線；若只想從畫面移除請用「隱藏」。刪除僅適用於已離線／退役節點。'}
                          aria-label={n.status === 'offline' ? '刪除節點' : '節點在線，無法刪除；請改用隱藏'}
                          disabled={n.status !== 'offline'}
                          onClick={(e) => {
                            e.stopPropagation();
                            if (n.status !== 'offline') return;
                            setEdgeDeleteTarget({ id: n.id, name: n.name, type: n.type, status: n.status });
                          }}
                          onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}
                          className="h-8 px-2 rounded text-[11px] text-ink-muted hover:text-sev-critical hover:bg-sev-critical/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          刪除
                        </button>
                      )}
```

**3e. Confirm modal** — after the clear-key modal block (ends ~L1069, before the closing `</div>` at L1070), add:

```jsx
      {/* Edge-node delete confirmation — sibling of the webcam delete modal.
          Backdrop-dismiss disabled while in flight. */}
      {edgeDeleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          role="dialog" aria-modal="true" aria-label="刪除節點"
          onClick={() => { if (!edgeDeleteBusy) setEdgeDeleteTarget(null); }}>
          <div className="bg-surface-panel border border-border-subtle rounded-xl p-5 w-96 shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-bold text-ink-primary mb-3">刪除節點</h3>
            <p className="text-xs text-ink-secondary mb-2">
              確定要刪除節點「
              <span className="text-ink-primary font-bold">{edgeDeleteTarget.name || edgeDeleteTarget.id}</span>
              」？將永久移除此節點及其歷史資料（事件、水位讀數）；稽核紀錄保留，此操作無法復原。
            </p>
            {edgeDeleteTarget.type === 'pump' && (
              <p className="text-xs text-sev-warn font-bold mb-2">⚠ 這只會從主控台移除此節點；實體裝置仍會依本地控制邏輯繼續運作、不會停止。</p>
            )}
            <p className="text-xs text-sev-warn font-bold mb-4">⚠ 若此節點稍後重新連線，將以新節點身分重新出現。</p>
            <div className="flex gap-2">
              <button
                disabled={edgeDeleteBusy}
                onClick={() => setEdgeDeleteTarget(null)}
                className="flex-1 py-2 rounded-lg bg-surface-elevated border border-border-subtle text-ink-secondary text-sm font-bold disabled:opacity-50"
              >
                取消
              </button>
              <button
                disabled={edgeDeleteBusy}
                onClick={confirmDeleteEdge}
                className="flex-1 py-2 rounded-lg bg-sev-critical text-white text-sm font-bold disabled:opacity-50"
              >
                {edgeDeleteBusy ? '刪除中...' : '確定刪除'}
              </button>
            </div>
          </div>
        </div>
      )}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node tools/spa/render_tests.js`
Expected: the edge-delete suite PASSES. The existing `TEST_STATUS_WEBCAM_DELETE` and Section-4 status suites still pass (the new button uses text `刪除` with a non-`Webcam` title, so their title-filtered / single-node-render assertions are unaffected).

- [ ] **Step 5: Commit**

```bash
git add central_server/static/spa/pages/status.jsx tools/spa/render_extra/node-delete-hide.js
git commit -m "feat(spa): guarded offline-only delete for edge camera/pump nodes"
```

---

### Task 3: Edge-device hide/unhide in the status page (`status.jsx`)

**Files:**
- Modify: `central_server/static/spa/pages/status.jsx` (component signature L310; filter block L532–537; header chips ~L579–605; count L575–577; empty state + row map L630–641; id-cell badge ~L662; action column L758; `<tr>` className L655–657)
- Test: `tools/spa/render_extra/node-delete-hide.js` (append the edge-hide suite, `target: 'pages/status.jsx'`)

**Interfaces:**
- Consumes: new props `hiddenIds: Set<string>`, `onHideNode(id): void`, `onUnhideNode(id): void` (threaded from `app.jsx` in Task 4); existing `Icon.EyeOff`/`Icon.Eye`, `setToast`.
- Produces: local `showHidden` toggle state; the 「顯示已隱藏 (N)」 chip; per-row hide/unhide buttons; the default list excludes hidden edge rows unless revealed.

- [ ] **Step 1: Write the failing test**

Append this entry to `tools/spa/render_extra/node-delete-hide.js`:

```js
  // ---------------------------------------- status.jsx: edge hide / unhide
  {
    name: 'node-hide status.jsx: hide button, default-exclude, reveal chip, unhide',
    target: 'pages/status.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.SDPRS_API = { snoozeNode: () => Promise.resolve(), unsnoozeNode: () => Promise.resolve(), deleteNode: () => Promise.resolve({}) };
      const hideCalls = [], unhideCalls = [];
      const cam  = { id: 'CAM-1', name: '西灣橋', type: 'camera', status: 'online', heartbeat: 5, upload: 5, snoozeMin: 0, temp: 30, level: null };
      const pump = { id: 'PUMP-1', name: '泵站A', type: 'pump', status: 'online', heartbeat: 5, snoozeMin: 0, level: 40, voltage: 12.5, power: 'mains', cycles: 1 };
      const render = (hiddenIds) => ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
        nodes: [cam, pump], onSelectNode: () => {}, onRefresh: () => {},
        hiddenIds, onHideNode: (id) => hideCalls.push(id), onUnhideNode: (id) => unhideCalls.push(id),
      })));

      // --- nothing hidden: both rows show a hide (eye-off) button ---
      render(new Set());
      await settle();
      const hideBtns = () => Array.from(container.querySelectorAll('button')).filter(b => (b.getAttribute('aria-label') || '') === '隱藏節點');
      A('each edge row shows a hide button', hideBtns().length === 2, hideBtns().length);
      const camRow = Array.from(container.querySelectorAll('tr')).find(r => r.textContent.indexOf('CAM-1') !== -1);
      click(Array.from(camRow.querySelectorAll('button')).find(b => (b.getAttribute('aria-label') || '') === '隱藏節點'));
      await settle();
      A('clicking hide calls onHideNode(id)', hideCalls.length === 1 && hideCalls[0] === 'CAM-1', JSON.stringify(hideCalls));

      // --- CAM-1 hidden: excluded from the default list; reveal chip shows (1) ---
      render(new Set(['CAM-1']));
      await settle();
      const rowText = () => Array.from(container.querySelectorAll('tbody tr')).map(r => r.textContent).join('||');
      A('a hidden node is excluded from the default list', rowText().indexOf('CAM-1') === -1, rowText());
      A('the pump row is still shown', rowText().indexOf('PUMP-1') !== -1);
      const chip = byText('button', '顯示已隱藏');
      A('a 顯示已隱藏 (N) reveal chip appears with the count', !!chip && chip.textContent.indexOf('1') !== -1, chip && chip.textContent);

      // --- reveal: the hidden row comes back, dimmed, with an unhide button ---
      click(chip); await settle();
      A('revealing shows the hidden row', rowText().indexOf('CAM-1') !== -1, rowText());
      const hiddenRow = Array.from(container.querySelectorAll('tbody tr')).find(r => r.textContent.indexOf('CAM-1') !== -1);
      A('the revealed row is marked 已隱藏', !!hiddenRow && hiddenRow.textContent.indexOf('已隱藏') !== -1);
      const unhideBtn = hiddenRow && Array.from(hiddenRow.querySelectorAll('button')).find(b => (b.getAttribute('aria-label') || '') === '取消隱藏節點');
      A('the revealed row has an unhide button', !!unhideBtn);
      click(unhideBtn); await settle();
      A('clicking unhide calls onUnhideNode(id)', unhideCalls.length === 1 && unhideCalls[0] === 'CAM-1', JSON.stringify(unhideCalls));

      // --- no reveal chip when nothing is hidden ---
      render(new Set());
      await settle();
      A('no reveal chip when nothing is hidden', !byText('button', '顯示已隱藏'));
    `,
  },
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node tools/spa/render_tests.js`
Expected: the edge-hide suite FAILS — no hide button, no reveal chip.

- [ ] **Step 3: Write the minimal implementation**

**3a. Signature** — change L310 to accept the new props with safe defaults:

```js
const StatusPage = ({ nodes = [], onSelectNode, onRefresh, hiddenIds = new Set(), onHideNode, onUnhideNode }) => {
```

**3b. Reveal state** — beside the other `useState_p` calls (e.g. after `const [locationFilter, setLocationFilter] = useState_p('all');`, L313):

```js
  const [showHidden, setShowHidden] = useState_p(false); // reveal hidden edge rows
```

**3c. Derived lists** — after the `filtered` memo (ends L537), add:

```js
  // Hide is presentational and applies only to edge rows (camera/pump). The
  // count is taken across the FULL node list (not `filtered`) so siblings
  // hidden under an active type/status/location filter are still discoverable
  // via the reveal chip. The rendered list drops hidden rows unless revealed.
  const isEdge = (n) => n.type === 'camera' || n.type === 'pump';
  const hiddenCount = useMemo_p(
    () => nodes.filter(n => isEdge(n) && hiddenIds.has(n.id)).length,
    [nodes, hiddenIds]);
  const visibleFiltered = useMemo_p(
    () => (showHidden ? filtered : filtered.filter(n => !hiddenIds.has(n.id))),
    [filtered, showHidden, hiddenIds]);
```

**3d. Count line** — replace the two `filtered` reads at L576 with `visibleFiltered`:

```jsx
          {visibleFiltered.length}{visibleFiltered.length !== nodes.length && ` / ${nodes.length}`} 個節點
```

**3e. Reveal chip** — inside the `<div className="flex gap-1.5">` chip row, after the location `FilterChip` (after L604), add:

```jsx
          {hiddenCount > 0 && (
            <FilterChip active={showHidden} onClick={() => setShowHidden(s => !s)}>
              顯示已隱藏 ({hiddenCount})
            </FilterChip>
          )}
```

**3f. Empty state + row map** — change the empty-state guard L630 and the map L641 from `filtered` to `visibleFiltered`:

```jsx
            {visibleFiltered.length === 0 && (
```
```jsx
            {visibleFiltered.map(n => {
```

**3g. Dim hidden revealed rows** — extend the `<tr>` className at L656 to add `opacity-60` when this row is a hidden edge node being revealed. Replace the `className` on L656 with:

```jsx
                  className={`border-b border-border-subtle/60 hover:bg-surface-elevated/60 group cursor-pointer ${isEdge(n) && hiddenIds.has(n.id) ? 'opacity-60' : ''}`}
```

**3h. 已隱藏 badge** — in the id cell, after the snooze badge block (after L666), add:

```jsx
                    {isEdge(n) && hiddenIds.has(n.id) && (
                      <span className="ml-1.5 inline-flex items-center gap-0.5 text-[9px] font-bold px-1 py-0.5 rounded bg-ink-dim/15 text-ink-muted align-middle">
                        <Icon.EyeOff size={8} strokeWidth={2.5}/>已隱藏
                      </span>
                    )}
```

**3i. Hide / unhide row buttons** — in the action column `<div className="inline-flex gap-1">` (L758), after the edge delete button added in Task 2, add:

```jsx
                      {isEdge(n) && !hiddenIds.has(n.id) && (
                        <button
                          title="從畫面隱藏此節點"
                          aria-label="隱藏節點"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (onHideNode) onHideNode(n.id);
                            setToast({ tone: 'info', msg: `已隱藏節點「${n.name || n.id}」` });
                          }}
                          onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}
                          className="w-8 h-8 rounded flex items-center justify-center text-ink-muted hover:bg-surface-overlay hover:text-ink-primary"
                        >
                          <Icon.EyeOff size={14}/>
                        </button>
                      )}
                      {isEdge(n) && hiddenIds.has(n.id) && (
                        <button
                          title="取消隱藏此節點"
                          aria-label="取消隱藏節點"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (onUnhideNode) onUnhideNode(n.id);
                            setToast({ tone: 'info', msg: '已取消隱藏' });
                          }}
                          onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}
                          className="w-8 h-8 rounded flex items-center justify-center text-sev-info hover:bg-sev-info/10"
                        >
                          <Icon.Eye size={14}/>
                        </button>
                      )}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node tools/spa/render_tests.js`
Expected: the edge-hide suite PASSES; all prior status suites still pass. (The Task-2 edge-delete suite already passed `hiddenIds`/`onHideNode`/`onUnhideNode`, so the new signature is satisfied there too.)

- [ ] **Step 5: Commit**

```bash
git add central_server/static/spa/pages/status.jsx tools/spa/render_extra/node-delete-hide.js
git commit -m "feat(spa): per-display hide/unhide + reveal chip for edge nodes in status page"
```

---

### Task 4: Wire hidden state into the app; declutter wall + monitor (`app.jsx`)

**Files:**
- Modify: `central_server/static/spa/app.jsx` (App state ~L233; a `visibleNodes` derivation + handlers before `renderPage`; render sites L1659–1660, L1762; `WallView` signature + tile L2027–2128)
- No new test (harness does not load `app.jsx` as a render target; the filter logic is covered by Task 1's `filterVisibleNodes` unit test — see the plan's Testability note). Verified by the static syntax/refs gates + full `run_all.js`.

**Interfaces:**
- Consumes: `window.loadHiddenNodes`, `window.saveHiddenNodes`, `window.filterVisibleNodes` (Task 1); the App's `nodes` state; `useStateA`/`useCallbackA` (already aliased in this file).
- Produces: `hiddenIds: Set`, `onHideNode`, `onUnhideNode`, `visibleNodes`; a new `onHideNode` prop on `WallView`.

- [ ] **Step 1: Add hidden-nodes state + handlers**

After the node state (`const [nodes, setNodes] = useStateA(...)`, L195) — or grouped with the other UI state around L231–233 — add:

```js
  // Per-display node hide (declutter). Seeded from localStorage once; every
  // mutation persists through the try/catch-guarded data.jsx helper. Held as a
  // Set for O(1) membership in the shared filterVisibleNodes.
  const [hiddenIds, setHiddenIds] = useStateA(() => new Set(window.loadHiddenNodes()));
  const onHideNode = useCallbackA((id) => {
    setHiddenIds(prev => {
      const next = new Set(prev); next.add(id);
      window.saveHiddenNodes(Array.from(next));
      return next;
    });
  }, []);
  const onUnhideNode = useCallbackA((id) => {
    setHiddenIds(prev => {
      const next = new Set(prev); next.delete(id);
      window.saveHiddenNodes(Array.from(next));
      return next;
    });
  }, []);
```

- [ ] **Step 2: Derive `visibleNodes`**

Immediately before `renderPage`/the wall branch use it (e.g. just before `const renderPage = ...` around L1630, or right after the handlers where `nodes` is in scope), add:

```js
  // Hidden nodes drop out of the wall + monitor grid (presentational only —
  // the status page keeps the FULL list as the management surface, and fleet
  // totals are unaffected).
  const visibleNodes = window.filterVisibleNodes(nodes, hiddenIds);
```

- [ ] **Step 3: Thread props into the three views**

- `MonitorPage` (L1659): change `nodes={nodes}` → `nodes={visibleNodes}`.
- `StatusPage` (L1660): keep `nodes={nodes}` (full list) and add the hide props:

```jsx
      case 'status': return wrap(<window.StatusPage nodes={nodes} onSelectNode={onSelectNode} onRefresh={refresh} hiddenIds={hiddenIds} onHideNode={onHideNode} onUnhideNode={onUnhideNode}/>);
```

- `WallView` (L1762): pass visible nodes + the hide handler:

```jsx
          <WallView alerts={activeAlerts} nodes={visibleNodes} unackCount={unackCount} dataWarnings={dataWarnings} onHideNode={onHideNode}/>
```

- [ ] **Step 4: Add the hover hide control to edge wall tiles**

- Change the `WallView` signature (L2027) to accept the prop:

```js
function WallView({ alerts, nodes, unackCount, dataWarnings, onHideNode }) {
```

- Add `group` to the tile wrapper (L2103) so the control can reveal on hover:

```jsx
              <div key={n.id} className="group bg-surface-panel rounded border border-border-subtle overflow-hidden relative">
```

- Inside that tile, as the first child of the inner `snapshot-placeholder` div (after `<WallSnapshot node={n} iconSize={64}/>`, L2105), add the edge-only hover control:

```jsx
                  {(n.type === 'camera' || n.type === 'pump') && onHideNode && (
                    <button
                      type="button"
                      title="從監控牆隱藏此節點"
                      aria-label="從監控牆隱藏此節點"
                      onClick={(e) => { e.stopPropagation(); onHideNode(n.id); }}
                      className="absolute top-2 right-2 z-10 w-7 h-7 rounded flex items-center justify-center bg-black/50 text-white/80 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity hover:bg-black/70"
                    >
                      <Icon.EyeOff size={14}/>
                    </button>
                  )}
```

- [ ] **Step 5: Verify with the full gate suite**

Run: `node tools/spa/run_all.js`
Expected: all blocking gates green — `vendor integrity`, `scope invariant`, `syntax`, `undefined refs`, `render tests` (including the three new suites from Tasks 1–3). `tailwind tokens` is advisory; confirm it introduces no NEW unknown-token warnings for the classes added here.

- [ ] **Step 6: Commit**

```bash
git add central_server/static/spa/app.jsx
git commit -m "feat(spa): apply per-display node hide to wall + monitor; wall-tile hide control"
```

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- §4 Delete (row button, sibling modal, offline-only guard for both types, 404-tolerance, pump physical-device warning, copy) → Task 2. ✅
- §5 Hide (localStorage helpers §5.1 → Task 1; app `hiddenIds`/handlers/`visibleNodes` single filter §5.2 → Tasks 1+4; status page hide button + reveal chip + unhide §5.3 → Task 3; wall hover control §5.4 → Task 4). ✅
- §6 Icons — `Eye`/`EyeOff` already exist; **no change** (verified in `icons.jsx` L57–58). ✅ (Deviation from spec §6's "add if missing" — they are present.)
- §7 Edge cases — hidden id for a gone node: `filterVisibleNodes` simply never matches (no pruning), covered by the helper's set semantics; delete-then-reconnect: offline-only guard (Task 2) + the modal's re-appear warning line; 404: Task 2; localStorage unavailable: Task 1 degradation test; wall < 9 nodes: filter only reduces count (unchanged slice logic). ✅
- §8 Testing — delete render/disabled/enabled/404/refresh (Task 2), hide button/default-exclude/reveal/unhide/persist (Tasks 1+3). The WallView render case is **replaced** by the `filterVisibleNodes` unit test (Task 1) + static gates, because `app.jsx` is not a render-test target — documented in the Testability note and here. ✅ (Deviation, justified.)
- §9 Deferred (server-side hide, webcam hide) — untouched by design. ✅
- §10 Verification → `node tools/spa/run_all.js` (Task 4 Step 5). ✅
- §11 Files touched — matches, except tests live in a **new** `render_extra/node-delete-hide.js` (own-file convention) rather than `section4-status.js`, and `icons.jsx` is untouched. ✅ (Both deviations noted above.)

**2. Placeholder scan** — no TBD/TODO/"add error handling"/"similar to Task N"; every code and test step carries literal content. ✅

**3. Type consistency** — `hiddenIds` is a `Set<string>` everywhere (app state, StatusPage prop default `new Set()`, `filterVisibleNodes`'s `.has`/`.size` guard, tests pass `new Set([...])`); `onHideNode(id)`/`onUnhideNode(id)` take a single id string in app handlers, StatusPage buttons, WallView control, and tests; `filterVisibleNodes(nodes, hiddenIds)` argument order identical in helper, app call, and test; `edgeDeleteTarget` shape `{id,name,type,status}` set in the row button and read in `confirmDeleteEdge` + modal; helper names (`loadHiddenNodes`/`saveHiddenNodes`/`filterVisibleNodes`) identical across data.jsx export, app consumption, and the Task 1 test. ✅

**Deviations from spec (all deliberate, all listed above):** (a) `icons.jsx` unchanged — icons already exist; (b) tests in a new `render_extra/node-delete-hide.js` — matches the "one file per owner" harness convention and avoids collisions; (c) the wall filter is a shared pure helper `filterVisibleNodes` (a faithful, testable factoring of the spec's inline `nodes.filter(...)`); (d) the WallView render case is covered via the helper unit test + static gates because `app.jsx` is not render-testable in this harness.
