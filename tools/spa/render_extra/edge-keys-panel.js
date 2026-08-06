// Task 11 (per-node edge API keys, Track 1): SPA node key management panel.
// Owner: feat/per-node-edge-keys-2026-08-05. See
// .superpowers/sdd/2026-08-05-per-node-edge-keys/task-11-brief.md.
//
// GATING FACT (see task-11-report.md / brief cross-task notes): mapNode
// collapses every backend node_type other than 'pump'/'webcam' into the SPA's
// n.type === 'camera' — there is no literal 'glass' type in the SPA. So these
// suites gate on node.hasKey (camelCase; api.jsx's `n.has_key ?? null`), NOT
// on a type string:
//   hasKey === false -> 設定金鑰 (provision)
//   hasKey === true  -> 重設金鑰 (rotate) + 清除金鑰 (clear)
//   hasKey == null   -> no node-key controls at all (the webcam case; webcams
//                        keep their existing 🔑 revoke + 刪除 controls).

module.exports = [
  // ---------------------------------------------------------------- gating
  {
    name: 'Task 11: node-key controls gate on hasKey (unkeyed/keyed/webcam)',
    target: 'pages/status.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.SDPRS_API = {
        provisionNodeKey: (id) => Promise.resolve({ node_id: id, api_key: 'sk-edge-TESTKEY' }),
        clearNodeKey: () => Promise.resolve(null),
        snoozeNode: () => Promise.resolve(), unsnoozeNode: () => Promise.resolve(),
        startStream: () => Promise.resolve(), stopStream: () => Promise.resolve(),
        getStreamHealth: () => Promise.resolve(null),
      };
      // An unkeyed glass/edge node renders as type 'camera' (mapNode collapses
      // 'glass' -> 'camera'); hasKey is the only signal that distinguishes it.
      const unkeyed = { id: 'CAM-glass1', name: '玻璃節點', type: 'camera', status: 'online',
        heartbeat: 5, upload: 5, snoozeMin: 0, temp: 20, level: null, hasKey: false };
      const keyed = { id: 'pump-1', name: '抽水站', type: 'pump', status: 'online',
        heartbeat: 5, upload: 5, snoozeMin: 0, level: 50, cycles: 1, voltage: 12.5,
        power: 'mains', hasKey: true };
      const webcamNode = { id: 'webcam_ab12', name: 'Webcam', type: 'webcam', status: 'online',
        heartbeat: 5, upload: 5, snoozeMin: 0, clientId: 'webcam_c1', clientName: 'Bench PC', hasKey: null };
      ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
        nodes: [unkeyed, keyed, webcamNode], onSelectNode: () => {}, onRefresh: () => Promise.resolve() })));
      await settle();

      const rows = Array.from(container.querySelectorAll('tr'));
      const unkeyedRow = rows.find(r => r.textContent.indexOf('CAM-glass1') !== -1);
      const keyedRow = rows.find(r => r.textContent.indexOf('pump-1') !== -1);
      const webcamRow = rows.find(r => r.textContent.indexOf('webcam_ab12') !== -1);
      A('setup: all three rows render', !!unkeyedRow && !!keyedRow && !!webcamRow);

      const btnTexts = (row) => Array.from(row.querySelectorAll('button')).map(b => b.textContent.trim());

      A('hasKey=false shows 設定金鑰', unkeyedRow && btnTexts(unkeyedRow).indexOf('設定金鑰') !== -1, unkeyedRow && btnTexts(unkeyedRow));
      A('hasKey=false does NOT show 重設金鑰 or 清除金鑰', unkeyedRow &&
        btnTexts(unkeyedRow).indexOf('重設金鑰') === -1 && btnTexts(unkeyedRow).indexOf('清除金鑰') === -1,
        unkeyedRow && btnTexts(unkeyedRow));

      A('hasKey=true shows 重設金鑰', keyedRow && btnTexts(keyedRow).indexOf('重設金鑰') !== -1, keyedRow && btnTexts(keyedRow));
      A('hasKey=true shows 清除金鑰', keyedRow && btnTexts(keyedRow).indexOf('清除金鑰') !== -1, keyedRow && btnTexts(keyedRow));
      A('hasKey=true does NOT show 設定金鑰', keyedRow && btnTexts(keyedRow).indexOf('設定金鑰') === -1, keyedRow && btnTexts(keyedRow));

      A('hasKey=null (webcam) shows NO node-key controls', webcamRow &&
        btnTexts(webcamRow).indexOf('設定金鑰') === -1 &&
        btnTexts(webcamRow).indexOf('重設金鑰') === -1 &&
        btnTexts(webcamRow).indexOf('清除金鑰') === -1,
        webcamRow && btnTexts(webcamRow));
      // The webcam row must keep its EXISTING controls untouched.
      A('webcam row keeps its existing 🔑 revoke control', webcamRow &&
        Array.from(webcamRow.querySelectorAll('button')).some(b => b.title === '撤銷並重新產生 API Key'));
      A('webcam row keeps its existing 刪除 control', webcamRow && btnTexts(webcamRow).indexOf('刪除') !== -1);
    `,
  },

  // -------------------------------------------------------- provision reveal
  {
    name: 'Task 11: provisioning (設定金鑰) surfaces a one-time reveal with the key + warning',
    target: 'pages/status.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      const provisionCalls = [];
      window.SDPRS_API = {
        provisionNodeKey: (id) => { provisionCalls.push(id); return Promise.resolve({ node_id: id, api_key: 'sk-edge-ABCDEF123456' }); },
        clearNodeKey: () => Promise.resolve(null),
        snoozeNode: () => Promise.resolve(), unsnoozeNode: () => Promise.resolve(),
        startStream: () => Promise.resolve(), stopStream: () => Promise.resolve(),
        getStreamHealth: () => Promise.resolve(null),
      };
      let selected = null;
      let refreshCalls = 0;
      const node = { id: 'CAM-glass2', name: '玻璃節點2', type: 'camera', status: 'online',
        heartbeat: 5, upload: 5, snoozeMin: 0, temp: 20, level: null, hasKey: false };
      ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
        nodes: [node], onSelectNode: (n) => { selected = n; }, onRefresh: () => { refreshCalls++; return Promise.resolve(); } })));
      await settle();

      const provisionBtn = Array.from(container.querySelectorAll('button')).find(b => b.textContent.trim() === '設定金鑰');
      A('setup: 設定金鑰 button renders', !!provisionBtn);

      click(provisionBtn);
      await settle();
      A('clicking 設定金鑰 calls provisionNodeKey(node.id)', provisionCalls[0] === 'CAM-glass2', JSON.stringify(provisionCalls));
      A('clicking the action button does NOT also select the row (stopPropagation)', selected === null, selected);

      const dialog = container.querySelector('[role="dialog"]');
      A('provisioning opens a reveal dialog', !!dialog);
      const keyCode = dialog && Array.from(dialog.querySelectorAll('code')).find(c => c.textContent.indexOf('sk-edge-ABCDEF123456') !== -1);
      A('the reveal shows the returned key in a select-all block', !!keyCode && keyCode.className.indexOf('select-all') !== -1);
      A('the reveal shows the 此金鑰只顯示一次 warning', !!dialog && dialog.textContent.indexOf('此金鑰只顯示一次') !== -1, dialog && dialog.textContent);
      A('the raw key never appears in a title attribute', container.innerHTML.indexOf('title="sk-edge-ABCDEF123456') === -1);

      // 複製 button copies the exact raw key (reuse of the existing pattern).
      const copied = [];
      Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
      Object.defineProperty(window.navigator, 'clipboard', { value: { writeText: (t) => { copied.push(t); return Promise.resolve(); } }, configurable: true });
      const copyBtn = dialog && Array.from(dialog.querySelectorAll('button')).find(b => b.textContent.trim() === '複製');
      A('a 複製 button is present in the reveal', !!copyBtn);
      click(copyBtn);
      await settle();
      A('複製 copies the exact raw key', copied.length === 1 && copied[0] === 'sk-edge-ABCDEF123456', JSON.stringify(copied));

      const closeBtn = dialog && Array.from(dialog.querySelectorAll('button')).find(b => b.textContent.trim() === '關閉');
      A('a 關閉 button is present in the reveal', !!closeBtn);
      click(closeBtn);
      await settle();
      A('closing the reveal modal triggers onRefresh so hasKey can flip', refreshCalls >= 1, refreshCalls);
      A('reveal modal is gone after close', !container.querySelector('[role="dialog"]'));
    `,
  },

  // ------------------------------------------------------------- rotate flow
  {
    name: 'Task 11: rotating (重設金鑰) on a keyed node also opens the reveal with the new key',
    target: 'pages/status.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.SDPRS_API = {
        provisionNodeKey: (id) => Promise.resolve({ node_id: id, api_key: 'sk-edge-ROTATED999' }),
        clearNodeKey: () => Promise.resolve(null),
        snoozeNode: () => Promise.resolve(), unsnoozeNode: () => Promise.resolve(),
      };
      const node = { id: 'pump-2', name: '抽水站2', type: 'pump', status: 'online',
        heartbeat: 5, upload: 5, snoozeMin: 0, level: 50, cycles: 1, voltage: 12.5,
        power: 'mains', hasKey: true };
      ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
        nodes: [node], onSelectNode: () => {}, onRefresh: () => Promise.resolve() })));
      await settle();

      const rotateBtn = Array.from(container.querySelectorAll('button')).find(b => b.textContent.trim() === '重設金鑰');
      A('setup: 重設金鑰 button renders', !!rotateBtn);
      click(rotateBtn);
      await settle();
      const dialog = container.querySelector('[role="dialog"]');
      A('rotate opens the reveal dialog with the newly returned key', !!dialog && dialog.textContent.indexOf('sk-edge-ROTATED999') !== -1, dialog && dialog.textContent.slice(0, 200));
      A('rotate reveal also carries the one-time warning', !!dialog && dialog.textContent.indexOf('此金鑰只顯示一次') !== -1);
    `,
  },

  // -------------------------------------------------------------- clear flow
  {
    name: 'Task 11: clearing (清除金鑰) confirms in-app first, then calls clearNodeKey + onRefresh',
    target: 'pages/status.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      const clearCalls = [];
      let refreshCalls = 0;
      window.SDPRS_API = {
        provisionNodeKey: () => Promise.resolve({ node_id: 'x', api_key: 'sk-unused' }),
        clearNodeKey: (id) => { clearCalls.push(id); return Promise.resolve(null); },
        snoozeNode: () => Promise.resolve(), unsnoozeNode: () => Promise.resolve(),
      };
      const node = { id: 'pump-3', name: '抽水站3', type: 'pump', status: 'online',
        heartbeat: 5, upload: 5, snoozeMin: 0, level: 50, cycles: 1, voltage: 12.5,
        power: 'mains', hasKey: true };
      ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
        nodes: [node], onSelectNode: () => {}, onRefresh: () => { refreshCalls++; return Promise.resolve(); } })));
      await settle();

      let confirmCalled = false;
      const origConfirm = window.confirm;
      window.confirm = () => { confirmCalled = true; return true; };
      const clearBtn = Array.from(container.querySelectorAll('button')).find(b => b.textContent.trim() === '清除金鑰');
      A('setup: 清除金鑰 button renders', !!clearBtn);
      click(clearBtn);
      await settle();
      window.confirm = origConfirm;
      A('清除金鑰 does NOT call native confirm()', !confirmCalled);
      A('清除金鑰 does not call clearNodeKey before confirming', clearCalls.length === 0, JSON.stringify(clearCalls));

      const confirmDialog = container.querySelector('[role="dialog"][aria-label="清除 API Key"]');
      A('清除金鑰 opens an in-app confirm modal (role="dialog")', !!confirmDialog);

      // cancel -> no API call, dialog closes
      const cancelBtn = confirmDialog && Array.from(confirmDialog.querySelectorAll('button')).find(b => b.textContent.trim() === '取消');
      A('setup: the confirm modal has a 取消 button', !!cancelBtn);
      click(cancelBtn);
      await settle();
      A('cancelling the clear confirm calls no API', clearCalls.length === 0, JSON.stringify(clearCalls));
      A('cancelling closes the confirm modal', !container.querySelector('[role="dialog"][aria-label="清除 API Key"]'));

      // re-open, confirm this time
      click(Array.from(container.querySelectorAll('button')).find(b => b.textContent.trim() === '清除金鑰'));
      await settle();
      const dialog2 = container.querySelector('[role="dialog"][aria-label="清除 API Key"]');
      const confirmBtn = dialog2 && Array.from(dialog2.querySelectorAll('button')).find(b => b.textContent.indexOf('確定') !== -1);
      A('setup: the confirm modal has a confirm button', !!confirmBtn);
      click(confirmBtn);
      await settle();
      A('confirming calls clearNodeKey(node.id)', clearCalls[0] === 'pump-3', JSON.stringify(clearCalls));
      A('a confirmed clear calls onRefresh so hasKey can flip', refreshCalls === 1, refreshCalls);
      A('the confirm dialog closes after a successful clear', !container.querySelector('[role="dialog"][aria-label="清除 API Key"]'));
    `,
  },

  // ------------------------------------------------- busy guards 清除金鑰 too
  {
    name: 'Task 11 / T11 review-fix: 清除金鑰 is disabled while a provision/rotate is in-flight',
    target: 'pages/status.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      // provisionNodeKey deliberately never resolves during this test — it
      // lets us observe the component while it is still "busy" (in-flight),
      // mirroring how the sibling 設定/重設金鑰 button's own disabled={busy}
      // is proven elsewhere in this suite.
      let resolveProvision;
      window.SDPRS_API = {
        provisionNodeKey: () => new Promise(resolve => { resolveProvision = resolve; }),
        clearNodeKey: () => Promise.resolve(null),
        snoozeNode: () => Promise.resolve(), unsnoozeNode: () => Promise.resolve(),
      };
      const node = { id: 'pump-busyclear', name: '抽水站-busy', type: 'pump', status: 'online',
        heartbeat: 5, upload: 5, snoozeMin: 0, level: 50, cycles: 1, voltage: 12.5,
        power: 'mains', hasKey: true };
      ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
        nodes: [node], onSelectNode: () => {}, onRefresh: () => Promise.resolve() })));
      await settle();

      const findBtn = (label) => Array.from(container.querySelectorAll('button')).find(b => b.textContent.trim() === label);
      const rotateBtn = findBtn('重設金鑰');
      A('setup: 重設金鑰 button renders', !!rotateBtn);
      const clearBtnBefore = findBtn('清除金鑰');
      A('setup: 清除金鑰 is enabled before any in-flight request', !!clearBtnBefore && !clearBtnBefore.disabled);

      click(rotateBtn);
      await settle();
      // Still in-flight: provisionNodeKey's promise has not been resolved yet.
      const clearBtnDuring = findBtn('清除金鑰');
      A('清除金鑰 is disabled while provision/rotate is in-flight (busy)', !!clearBtnDuring && clearBtnDuring.disabled === true,
        clearBtnDuring && clearBtnDuring.disabled);

      // Let the in-flight request resolve so it doesn't leak into other tests.
      resolveProvision({ node_id: node.id, api_key: 'sk-edge-BUSYCLEARTEST' });
      await settle();
      const dialog = container.querySelector('[role="dialog"]');
      if (dialog) {
        const closeBtn = Array.from(dialog.querySelectorAll('button')).find(b => b.textContent.trim() === '關閉');
        if (closeBtn) { click(closeBtn); await settle(); }
      }
    `,
  },

  // --------------------------------------------------------- Escape handling
  {
    name: 'Task 11: Escape closes the node-key reveal and clear-confirm modals',
    target: 'pages/status.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.SDPRS_API = {
        provisionNodeKey: () => Promise.resolve({ node_id: 'x', api_key: 'sk-edge-ESC1' }),
        clearNodeKey: () => Promise.resolve(null),
        snoozeNode: () => Promise.resolve(), unsnoozeNode: () => Promise.resolve(),
      };
      const unkeyed = { id: 'CAM-esc1', name: 't', type: 'camera', status: 'online',
        heartbeat: 5, upload: 5, snoozeMin: 0, temp: 20, level: null, hasKey: false };
      const keyed = { id: 'pump-esc2', name: 't2', type: 'pump', status: 'online',
        heartbeat: 5, upload: 5, snoozeMin: 0, level: 50, cycles: 1, voltage: 12.5,
        power: 'mains', hasKey: true };
      ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
        nodes: [unkeyed, keyed], onSelectNode: () => {}, onRefresh: () => Promise.resolve() })));
      await settle();

      // --- reveal modal ---
      click(Array.from(container.querySelectorAll('button')).find(b => b.textContent.trim() === '設定金鑰'));
      await settle();
      A('setup: reveal dialog open before Escape', !!container.querySelector('[role="dialog"]'));
      document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await settle();
      A('Escape closes the node-key reveal modal', !container.querySelector('[role="dialog"]'));

      // --- clear-confirm modal ---
      const clearBtn = Array.from(container.querySelectorAll('button')).find(b => b.textContent.trim() === '清除金鑰');
      A('setup: 清除金鑰 button renders on the keyed node', !!clearBtn);
      click(clearBtn);
      await settle();
      A('setup: clear-confirm dialog open before Escape', !!container.querySelector('[role="dialog"][aria-label="清除 API Key"]'));
      document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await settle();
      A('Escape closes the node-key clear-confirm modal', !container.querySelector('[role="dialog"][aria-label="清除 API Key"]'));
    `,
  },
];
