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
];
