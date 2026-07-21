// SDPRS SPA render tests — jsdom, offline.
//
// WHY THIS EXISTS: the three static gates prove a file COMPILES and READS
// correctly. They cannot prove it RENDERS and BEHAVES correctly. These tests
// mount the real components, dispatch real clicks, and assert the real DOM.
// Everything in static/spa/vendor/ is local, so React/ReactDOM/Babel load with
// no network access.
//
// SCOPE FIDELITY: each <script type="text/babel"> runs in its own top-level
// scope (see scope_probe.js). So every DEPENDENCY here is loaded as its own
// script, and only the file UNDER TEST shares a script with the test code —
// which is how a test reaches that file's internal, unpublished components
// (StreamRowButton, ConflictDialog, PumpManualControls). Concatenating all
// files into one script would be over-permissive: it can resolve a cross-file
// bare identifier that the browser would throw a ReferenceError on.
//
// WHAT IT DOES NOT COVER: pixels. Tailwind is not applied, so this tests
// component logic and DOM structure, never layout or color.
//
// jsdom quirk: a post-blur setState is not deterministically flushed to the
// DOM. Assert blur handlers via an observable callback branch (see WHA-L8),
// not via DOM reflection.
//
// Usage: node render_tests.js        (exit 0 = all assertions pass)
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');
const { SPA_DIR } = require('./spa_files');

const SPA = process.argv[2] || SPA_DIR;
const Babel = require(path.join(SPA, 'vendor', 'babel.min.js'));

function makeWorld() {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="root"></div></body></html>', {
    runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/',
  });
  const { window } = dom;
  const ctx = dom.getInternalVMContext();
  window.matchMedia = window.matchMedia || (q => ({ matches: false, media: q, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } }));
  class _Obs { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } }
  window.IntersectionObserver = window.IntersectionObserver || _Obs;
  window.ResizeObserver = window.ResizeObserver || _Obs;
  window.requestAnimationFrame = window.requestAnimationFrame || (cb => setTimeout(() => cb(Date.now()), 0));
  window.cancelAnimationFrame = window.cancelAnimationFrame || (id => clearTimeout(id));
  window.scrollTo = window.scrollTo || (() => {});
  window.HTMLElement.prototype.scrollIntoView = function () {};
  const run = (code, filename) => vm.runInContext(code, ctx, { filename });
  run(fs.readFileSync(path.join(SPA, 'vendor', 'react.production.min.js'), 'utf8'), 'react');
  run(fs.readFileSync(path.join(SPA, 'vendor', 'react-dom.production.min.js'), 'utf8'), 'react-dom');
  return { dom, window, run };
}

// Each dependency = its own script, exactly as the browser loads it.
function loadDeps(world, files) {
  for (const f of files) {
    const { code } = Babel.transform(fs.readFileSync(path.join(SPA, f), 'utf8'),
      { presets: ['react'], filename: f, sourceType: 'script' });
    world.run(code, f);
  }
}

// Target file + test code = ONE script, so the test can reach file internals.
function runTarget(world, targetFile, testCode) {
  const bundle = fs.readFileSync(path.join(SPA, targetFile), 'utf8') +
    '\n;/* ===== TEST CODE ===== */\n' + testCode;
  const { code } = Babel.transform(bundle, { presets: ['react'], filename: targetFile, sourceType: 'script' });
  world.run(code, targetFile);
}

// NOTE: no backticks anywhere inside the test-code strings below — a backtick
// there closes the enclosing template literal early. String concatenation only.
const PRELUDE = `
  const results = [];
  const A = (name, cond, detail) => results.push({ name, pass: !!cond, detail: detail === undefined ? '' : String(detail) });
  const tick = () => new Promise(r => setTimeout(r, 0));
  const settle = async (n) => { for (let i = 0; i < (n || 6); i++) await tick(); };
  const container = document.getElementById('root');
  const root = ReactDOM.createRoot(container);
  const click = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  function setInput(el, val) {
    const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, val);
    el.dispatchEvent(new window.Event('input', { bubbles: true }));
  }
  const byText = (sel, txt) => Array.from(container.querySelectorAll(sel)).find(e => e.textContent.indexOf(txt) !== -1);
`;

// ------------------------------------------------- pumps.jsx: F6 / F5 / F9 --
const TEST_PUMPS = `
window.__TEST_PROMISE = (async () => {
${PRELUDE}
  try {
    const calls = [];
    window.SDPRS_API = { pumpCommand: (id, action, dur) => { calls.push([id, action, dur]); return Promise.resolve({ ok: true }); } };
    const base = { pumpId: 'pump-9', pumpState: 'off', dryRunProtect: false, sensorConflict: false, disabled: false, showToast: () => {} };
    const render = (props) => ReactDOM.flushSync(() => root.render(React.createElement(PumpManualControls, props)));

    // MSP-F6 (SAFETY): a manual OFF hold must be visible and releasable —
    // otherwise a pump stays stopped through a flood with nothing on screen
    // saying why, and no way back to AUTO.
    render(Object.assign({}, base, { manualOverride: 'OFF', lastPumpCommand: { action: 'OFF', by: 'alice', at: new Date(2026, 6, 20, 14, 32) } }));
    const t1 = container.textContent;
    A('MSP-F6 OFF banner 手動停機中 renders', t1.indexOf('手動停機中') !== -1);
    const releaseBtn = Array.from(container.querySelectorAll('button')).find(b => b.textContent.indexOf('恢復自動') !== -1);
    A('MSP-F6 恢復自動 button present', !!releaseBtn);
    A('MSP-F5 上次指令 line renders with operator', t1.indexOf('上次指令') !== -1 && t1.indexOf('alice') !== -1);

    click(releaseBtn);
    await settle();
    const autoCall = calls.find(c => c[1] === 'AUTO');
    A('MSP-F6 恢復自動 actually fires pumpCommand(pumpId, AUTO)', !!autoCall && autoCall[0] === 'pump-9', JSON.stringify(calls));

    render(Object.assign({}, base, { pumpState: 'on', manualOverride: 'ON', lastPumpCommand: null }));
    A('MSP-F6 ON banner 手動強制運行中 renders', container.textContent.indexOf('手動強制運行中') !== -1);

    render(Object.assign({}, base, { manualOverride: null, lastPumpCommand: null }));
    const t3 = container.textContent;
    A('MSP-F6 no override => no banner', t3.indexOf('手動停機中') === -1 && t3.indexOf('手動強制運行中') === -1);
    A('MSP-F6 no override => no 恢復自動 button', !Array.from(container.querySelectorAll('button')).some(b => b.textContent.indexOf('恢復自動') !== -1));

    // API-F9: short-cycle banner is driven by the clean mapped boolean
    // node.cyclesAlert (NOT n._cycles.alert).
    const node = (over) => Object.assign({
      id: 'pump-1', name: '測試站', location: '測試區', type: 'pump', status: 'ONLINE',
      level: 50, pumpState: 'off', cyclesAlert: false, cycles: 5, sensorConflict: false,
      raining: false, dryRunProtect: false, manualOverride: null, lastPumpCommand: null,
      heartbeat: 2, voltage: 12.5, flow: null, trend: null, power: 'mains', snoozeMin: 0,
    }, over || {});
    ReactDOM.flushSync(() => root.render(React.createElement(PumpsPage, { nodes: [node({ cyclesAlert: true })], onSelectNode: () => {}, showToast: () => {} })));
    A('API-F9 短循環警告 banner renders when cyclesAlert=true', container.textContent.indexOf('短循環警告') !== -1);
    ReactDOM.flushSync(() => root.render(React.createElement(PumpsPage, { nodes: [node({ cyclesAlert: false })], onSelectNode: () => {}, showToast: () => {} })));
    A('API-F9 banner ABSENT when cyclesAlert=false', container.textContent.indexOf('短循環警告') === -1);
  } catch (e) {
    results.push({ name: 'pumps suite threw', pass: false, detail: e && e.stack ? e.stack.split('\\n').slice(0, 3).join(' | ') : String(e) });
  }
  window.__TEST_RESULT = results;
})();
`;

// --------------------------------------------------- status.jsx: MSP-F7 -----
const TEST_STATUS = `
window.__TEST_PROMISE = (async () => {
${PRELUDE}
  try {
    const calls = [];
    let health = null;
    let hang = false;
    window.SDPRS_API = {
      startStream: (id) => { calls.push(['start', id]); return hang ? new Promise(() => {}) : Promise.resolve({ ok: true }); },
      stopStream:  (id) => { calls.push(['stop', id]);  return hang ? new Promise(() => {}) : Promise.resolve({ ok: true }); },
      getStreamHealth: () => Promise.resolve(health),
    };
    const render = (node) => ReactDOM.flushSync(() => root.render(
      React.createElement(StreamRowButton, { node, onDone: () => {}, onError: () => {} })));

    health = { enabled: true, reachable: true, bitrateMbps: 2.4, viewers: 1, drops: 0 };
    render({ id: 'CAM-1', bitrate: 0 });
    await settle();
    let btn = container.querySelector('button');
    A('MSP-F7 healthy stream shows 停止串流', btn.title === '停止串流', btn.title);
    calls.length = 0;
    click(btn); await settle();
    A('MSP-F7 click on active stream calls stopStream', calls.length === 1 && calls[0][0] === 'stop', JSON.stringify(calls));

    // THE REGRESSION CASE: a stale cached bitrate said "active" while the
    // stream was actually down, so the click sent the OPPOSITE command.
    // Health must be the source of truth.
    ReactDOM.flushSync(() => root.render(null));
    health = { enabled: true, reachable: false, bitrateMbps: null, viewers: null, drops: null };
    render({ id: 'CAM-2', bitrate: 9 });
    await settle();
    btn = container.querySelector('button');
    A('MSP-F7 unreachable stream shows 開始串流 despite stale bitrate>0', btn.title === '開始串流', btn.title);
    calls.length = 0;
    click(btn); await settle();
    A('MSP-F7 health (not cached bitrate) decides the command: startStream', calls.length === 1 && calls[0][0] === 'start', JSON.stringify(calls));

    // Double-fire latch: two clicks in the SAME tick with a command in flight.
    ReactDOM.flushSync(() => root.render(null));
    health = { enabled: false, reachable: false, bitrateMbps: null, viewers: null, drops: null };
    hang = true;
    render({ id: 'CAM-3', bitrate: 0 });
    await settle();
    btn = container.querySelector('button');
    calls.length = 0;
    click(btn); click(btn);
    await settle();
    A('MSP-F7 double-click in one tick fires exactly ONE command', calls.length === 1, JSON.stringify(calls));
    hang = false;

    ReactDOM.flushSync(() => root.render(null));
    window.SDPRS_API = {};
    render({ id: 'CAM-4', bitrate: 0 });
    await settle();
    btn = container.querySelector('button');
    A('MSP-F7 missing API renders disabled + 等待 API', btn.disabled === true && btn.title.indexOf('等待 API') !== -1, btn.title + ' disabled=' + btn.disabled);
  } catch (e) {
    results.push({ name: 'status suite threw', pass: false, detail: e && e.stack ? e.stack.split('\\n').slice(0, 3).join(' | ') : String(e) });
  }
  window.__TEST_RESULT = results;
})();
`;

// --------------------------------------- status.jsx: webcam client admin ---
// Task 6: "新增 Webcam Client" (create) + row-level revoke-key. Both hand back
// a plaintext API key that must be shown exactly once, be easy to copy, and
// never leak into a log/URL/attribute or the 3s-auto-dismissing toast. This
// suite mounts the FULL StatusPage (not just a sub-component) since the
// create button lives in the page header and the revoke button lives in a
// table row keyed off node.type === 'webcam'.
const TEST_STATUS_WEBCAM = `
window.__TEST_PROMISE = (async () => {
${PRELUDE}
  try {
    // "Never log the key": patch console for the whole flow and check at the
    // end that neither generated key value was ever handed to it.
    const loggedStrs = [];
    const origLog = console.log, origWarn = console.warn, origErr = console.error;
    console.log = function () { loggedStrs.push(Array.prototype.slice.call(arguments).map(String).join(' ')); };
    console.warn = function () { loggedStrs.push(Array.prototype.slice.call(arguments).map(String).join(' ')); };
    console.error = function () { loggedStrs.push(Array.prototype.slice.call(arguments).map(String).join(' ')); };

    const calls = { create: [], revoke: [] };
    const confirmMsgs = [];
    let createResult = null;
    let createShouldFail = false;
    let revokeResult = null;
    let revokeShouldFail = false;
    window.SDPRS_API = {
      createWebcamClient: (name) => {
        calls.create.push(name);
        return createShouldFail ? Promise.reject(new Error('建立失敗：名稱重複')) : Promise.resolve(createResult);
      },
      revokeWebcamKey: (nodeId) => {
        calls.revoke.push(nodeId);
        return revokeShouldFail ? Promise.reject(new Error('撤銷失敗：節點不存在')) : Promise.resolve(revokeResult);
      },
    };

    const refreshCalls = [];
    // clientId is DELIBERATELY different from id: the row is a CAMERA, the key
    // belongs to the owning CLIENT PC, and the two ids share a shape but never
    // match. A handler that sends node.id here is the shipped 404 bug.
    const webcamNode = { id: 'webcam_ab12cd34', clientId: 'webcam_c11e9701', name: '櫃台電腦', location: '大堂', type: 'webcam', status: 'online', snoozeMin: 0 };
    const cameraNode = { id: 'CAM-1', name: '西灣橋', location: '西灣', type: 'camera', status: 'online', snoozeMin: 0, bitrate: 1.2, drops: 0 };
    ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
      nodes: [webcamNode, cameraNode], onSelectNode: () => {}, onRefresh: () => { refreshCalls.push(1); },
    })));
    await settle();

    // --- header button opens the create modal ---
    const addBtn = byText('button', '+ 新增 Webcam Client');
    A('add button renders in the header', !!addBtn);
    click(addBtn);
    await settle();
    let nameInput = container.querySelector('input[placeholder="輸入名稱..."]');
    A('modal opens with a name field', !!nameInput);
    let createBtn = byText('button', '建立');
    A('create button starts disabled with an empty name', !!createBtn && createBtn.disabled === true);

    setInput(nameInput, '櫃台電腦');
    await settle();
    createBtn = byText('button', '建立');
    A('create button enables once a name is entered', createBtn.disabled === false);

    createResult = { node_id: 'webcam_zz99', api_key: 'sk-webcam-TESTKEYVALUE-DO-NOT-LOG' };
    click(createBtn);
    await settle();
    A('createWebcamClient is called with the trimmed name', calls.create[0] === '櫃台電腦', JSON.stringify(calls.create));

    let keyCode = Array.from(container.querySelectorAll('code')).find(c => c.textContent.indexOf('sk-webcam-TESTKEYVALUE') !== -1);
    A('the created key is rendered exactly once, in a select-all block', !!keyCode && keyCode.className.indexOf('select-all') !== -1);
    A('the once-only warning copy is shown next to the key', container.textContent.indexOf('僅顯示一次') !== -1);
    A('the created key is never rendered into a title attribute', container.innerHTML.indexOf('title="sk-webcam') === -1);

    let closeBtn = byText('button', '已複製，關閉');
    click(closeBtn);
    await settle();
    A('closing the created-key panel triggers onRefresh', refreshCalls.length === 1, JSON.stringify(refreshCalls));
    A('modal is gone after close', !container.querySelector('input[placeholder="輸入名稱..."]') && container.textContent.indexOf('僅顯示一次') === -1);

    // --- create failure: a toast, never a silent no-op, never an echoed key ---
    click(byText('button', '+ 新增 Webcam Client'));
    await settle();
    setInput(container.querySelector('input[placeholder="輸入名稱..."]'), '重複名稱');
    await settle();
    createShouldFail = true;
    click(byText('button', '建立'));
    await settle();
    A('create failure surfaces the backend error message via toast', container.textContent.indexOf('建立失敗：名稱重複') !== -1);
    createShouldFail = false;
    const openBackdrop = container.querySelector('.fixed.inset-0');
    if (openBackdrop) { click(openBackdrop); await settle(); }

    // --- revoke: cancelling the confirm must NOT call the API ---
    window.confirm = (msg) => { confirmMsgs.push(msg); return false; };
    let revokeBtn = Array.from(container.querySelectorAll('button')).find(b => b.title === '撤銷並重新產生 API Key');
    A('revoke button renders for the webcam-type row', !!revokeBtn);
    const revokeBtnCount = Array.from(container.querySelectorAll('button')).filter(b => b.title === '撤銷並重新產生 API Key').length;
    A('exactly one revoke button exists (the camera row does not get one)', revokeBtnCount === 1, revokeBtnCount);
    click(revokeBtn);
    await settle();
    A('the confirm dialog names revocation and immediate expiry', !!confirmMsgs[0] && confirmMsgs[0].indexOf('撤銷') !== -1 && confirmMsgs[0].indexOf('失效') !== -1, JSON.stringify(confirmMsgs));
    A('cancelling the confirm does not call revokeWebcamKey', calls.revoke.length === 0, JSON.stringify(calls.revoke));

    // --- revoke: confirmed, success shows a PERSISTENT modal, not the 3s toast ---
    window.confirm = () => true;
    revokeResult = { api_key: 'sk-webcam-ROTATED-NEW-KEY' };
    revokeBtn = Array.from(container.querySelectorAll('button')).find(b => b.title === '撤銷並重新產生 API Key');
    click(revokeBtn);
    await settle();
    // THE SHIPPED BUG, pinned: revoke-key is a CLIENT endpoint. Sending the
    // camera's own node_id (webcam_ab12cd34) 404s every single time, which is
    // exactly what the dashboard did until this fix.
    A('confirmed revoke calls revokeWebcamKey(node.clientId)', calls.revoke[0] === 'webcam_c11e9701', JSON.stringify(calls.revoke));
    A('revoke NEVER sends the camera row id', calls.revoke.indexOf('webcam_ab12cd34') === -1, JSON.stringify(calls.revoke));
    const newKeyCode = Array.from(container.querySelectorAll('code')).find(c => c.textContent.indexOf('sk-webcam-ROTATED-NEW-KEY') !== -1);
    A('the rotated key is rendered exactly once, in a select-all block', !!newKeyCode && newKeyCode.className.indexOf('select-all') !== -1);
    const toastEl = container.querySelector('.fixed.top-16.right-4');
    A('the rotated key is NOT put in the auto-dismissing toast element', !toastEl || toastEl.textContent.indexOf('sk-webcam-ROTATED') === -1);
    A('the rotated key is never rendered into a title attribute', container.innerHTML.indexOf('title="sk-webcam-ROTATED') === -1);

    closeBtn = byText('button', '已複製，關閉');
    click(closeBtn);
    await settle();
    A('closing the revoke panel does not also call onRefresh (revoke != create)', refreshCalls.length === 1, JSON.stringify(refreshCalls));
    A('revoke modal is gone after close', container.textContent.indexOf('sk-webcam-ROTATED-NEW-KEY') === -1);

    // --- revoke failure: toast, no key echoed ---
    window.confirm = () => true;
    revokeShouldFail = true;
    revokeBtn = Array.from(container.querySelectorAll('button')).find(b => b.title === '撤銷並重新產生 API Key');
    click(revokeBtn);
    await settle();
    A('revoke failure surfaces the backend error message via toast', container.textContent.indexOf('撤銷失敗：節點不存在') !== -1);

    // --- no clientId (older backend / unmapped row): never guess an id ---
    // Sending node.id "because it looks like a client id" is precisely how the
    // 404 shipped. With nothing addressable, the affordance is disabled and
    // NOTHING is sent.
    revokeShouldFail = false;
    calls.revoke.length = 0;
    ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
      nodes: [{ id: 'webcam_orphan01', clientId: null, name: '無主攝影機', location: '大堂', type: 'webcam', status: 'online', snoozeMin: 0 }],
      onSelectNode: () => {}, onRefresh: () => { refreshCalls.push(1); },
    })));
    await settle();
    const orphanRevoke = Array.from(container.querySelectorAll('button')).find(b => (b.title || '').indexOf('撤銷') !== -1);
    A('a webcam row with no clientId disables the revoke button', !!orphanRevoke && orphanRevoke.disabled === true, orphanRevoke && orphanRevoke.title);
    click(orphanRevoke); await settle();
    A('a clientId-less row never calls revokeWebcamKey (no undefined id on the wire)', calls.revoke.length === 0, JSON.stringify(calls.revoke));

    console.log = origLog; console.warn = origWarn; console.error = origErr;
    const leaked = loggedStrs.some(s => s.indexOf('TESTKEYVALUE') !== -1 || s.indexOf('ROTATED-NEW-KEY') !== -1);
    A('the API key value is never passed to console.log/warn/error', !leaked, JSON.stringify(loggedStrs).slice(0, 300));
  } catch (e) {
    results.push({ name: 'status webcam-admin suite threw', pass: false, detail: e && e.stack ? e.stack.split('\\n').slice(0, 3).join(' | ') : String(e) });
  }
  window.__TEST_RESULT = results;
})();
`;

// ------------------------------ status.jsx: webcam delete (follow-up Task 2) --
// Spec §節點管理 lists 「撤銷 Key / 刪除」 — the row had no delete affordance, so a
// decommissioned webcam client stayed in the node list (and its key stayed
// valid) forever. The confirm step is an IN-APP dialog, never window.confirm:
// asserted here by leaving window.confirm patched to a value that would fail
// the flow if the handler actually called it.
// Server contract (frozen): 204 deleted, 404 already gone. 404 must refresh
// like a success, not raise an error toast.
const TEST_STATUS_WEBCAM_DELETE = `
window.__TEST_PROMISE = (async () => {
${PRELUDE}
  try {
    const calls = [];
    let mode = 'ok'; // ok | notfound | error | hang
    const mkApi = () => ({
      createWebcamClient: () => Promise.resolve({}),
      revokeWebcamKey: () => Promise.resolve({}),
      deleteWebcamClient: (nodeId) => {
        calls.push(nodeId);
        if (mode === 'hang') return new Promise(() => {});
        if (mode === 'notfound') { const e = new Error('HTTP 404 on /api/nodes/webcam/x'); e.status = 404; return Promise.reject(e); }
        if (mode === 'error') { const e = new Error('資料庫忙碌'); e.status = 500; return Promise.reject(e); }
        return Promise.resolve(null); // 204 -> apiFetch returns null
      },
    });
    window.SDPRS_API = mkApi();
    // If the handler ever regressed to window.confirm, this would throw and the
    // suite would fail loudly rather than silently passing on a native dialog.
    window.confirm = () => { throw new Error('handler used window.confirm instead of the in-app dialog'); };

    const refreshCalls = [];
    // A webcam ROW is a CAMERA; the endpoint takes the owning CLIENT's id.
    // camA/camB share one client PC, camC belongs to a different one — that
    // split is what makes "the dialog lists the right cameras" falsifiable.
    // clientName is the CLIENT PC's own name (webcam_clients.name) and is
    // deliberately NOT a substring of any camera name here, so "the dialog
    // shows the client's name" cannot pass by accidentally matching a camera.
    const camA = { id: 'webcam_cam00001', clientId: 'webcam_c11e9701', clientName: 'Bench PC', name: '櫃台電腦 前門', location: '大堂', type: 'webcam', status: 'online', snoozeMin: 0 };
    const camB = { id: 'webcam_cam00002', clientId: 'webcam_c11e9701', clientName: 'Bench PC', name: '櫃台電腦 後門', location: '大堂', type: 'webcam', status: 'online', snoozeMin: 0 };
    const camC = { id: 'webcam_cam00003', clientId: 'webcam_c0ffee11', clientName: '車道主機 PC', name: '車道主機 車道', location: '車道', type: 'webcam', status: 'online', snoozeMin: 0 };
    const cameraNode = { id: 'CAM-1', name: '西灣橋', location: '西灣', type: 'camera', status: 'online', snoozeMin: 0, bitrate: 1.2, drops: 0, temp: 30 };
    ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
      nodes: [camA, camB, camC, cameraNode], onSelectNode: () => {}, onRefresh: () => { refreshCalls.push(1); },
    })));
    await settle();

    const delBtns = () => Array.from(container.querySelectorAll('button')).filter(b => (b.title || '').indexOf('刪除此 Webcam') !== -1);
    A('every webcam row renders a 刪除 button', delBtns().length === 3 && delBtns()[0].textContent.indexOf('刪除') !== -1, delBtns().length);
    const rows = Array.from(container.querySelectorAll('tr'));
    const cameraTr = rows.find(r => r.textContent.indexOf('CAM-1') !== -1);
    A('non-webcam (camera) row has NO 刪除 button', !!cameraTr && !Array.from(cameraTr.querySelectorAll('button')).some(b => (b.title || '').indexOf('刪除此 Webcam') !== -1));

    // --- clicking 刪除 opens the in-app confirm dialog and sends NOTHING yet ---
    click(delBtns()[0]);
    await settle();
    let dialog = container.querySelector('[role="dialog"]');
    A('clicking 刪除 opens an in-app confirm dialog', !!dialog && dialog.textContent.indexOf('確定要刪除') !== -1);
    // DECIDED SEMANTICS: delete decommissions the whole client PC. The dialog
    // must therefore name the CLIENT and enumerate EVERY camera that goes with
    // it — otherwise the operator clicks one row and silently loses two.
    // ...and it must name it the way the OPERATOR named it. An irreversible
    // action identified only by 'webcam_c11e9701' asks for a confirmation the
    // operator cannot actually give: that hex string is not on any label, any
    // desk, or in any memory — the name they typed at creation is.
    A('the dialog names the owning client by its NAME, not an opaque id', !!dialog && dialog.textContent.indexOf('Bench PC') !== -1, dialog && dialog.textContent);
    A('the dialog names no OTHER client', !!dialog && dialog.textContent.indexOf('車道主機 PC') === -1 && dialog.textContent.indexOf('webcam_c0ffee11') === -1, dialog && dialog.textContent);
    A('the dialog states the camera count', !!dialog && dialog.textContent.indexOf('2 支攝影機') !== -1, dialog && dialog.textContent);
    A('the dialog lists ALL sibling cameras of that client', !!dialog && dialog.textContent.indexOf('櫃台電腦 前門') !== -1 && dialog.textContent.indexOf('櫃台電腦 後門') !== -1, dialog && dialog.textContent);
    A('the dialog does NOT list another client\\'s camera', !!dialog && dialog.textContent.indexOf('車道主機 車道') === -1, dialog && dialog.textContent);
    A('the dialog states the irreversibility', !!dialog && dialog.textContent.indexOf('無法復原') !== -1);
    A('opening the dialog issues no DELETE', calls.length === 0, JSON.stringify(calls));

    // --- cancel: closes, deletes nothing ---
    click(byText('button', '取消'));
    await settle();
    A('取消 closes the dialog without deleting', !container.querySelector('[role="dialog"]') && calls.length === 0, JSON.stringify(calls));

    // --- confirm: 204 path ---
    click(delBtns()[0]); await settle();
    click(byText('button', '確定刪除')); await settle();
    // THE BUG, pinned: the camera's own node_id is a guaranteed 404 on this
    // endpoint — and the 404 branch below would have made it LOOK successful.
    A('確定刪除 calls deleteWebcamClient(node.clientId)', calls.length === 1 && calls[0] === 'webcam_c11e9701', JSON.stringify(calls));
    A('delete NEVER sends the camera row id', calls.indexOf('webcam_cam00001') === -1, JSON.stringify(calls));
    A('a successful delete triggers onRefresh', refreshCalls.length === 1, JSON.stringify(refreshCalls));
    A('the dialog closes after a successful delete', !container.querySelector('[role="dialog"]'));
    A('a success toast names the deleted client by NAME', container.textContent.indexOf('Webcam 用戶端「Bench PC」已刪除') !== -1, container.textContent.slice(0, 200));

    // --- 404 = already gone: refresh like a success, never an error toast ---
    mode = 'notfound'; calls.length = 0;
    click(delBtns()[0]); await settle();
    click(byText('button', '確定刪除')); await settle();
    A('404 still calls through', calls.length === 1, JSON.stringify(calls));
    A('404 (already deleted) refreshes instead of erroring', refreshCalls.length === 2, JSON.stringify(refreshCalls));
    A('404 raises NO 刪除失敗 toast', container.textContent.indexOf('刪除失敗') === -1);
    A('404 closes the dialog', !container.querySelector('[role="dialog"]'));

    // --- a real failure: toast + dialog stays open, button not latched ---
    mode = 'error'; calls.length = 0;
    click(delBtns()[0]); await settle();
    click(byText('button', '確定刪除')); await settle();
    A('a real failure surfaces the backend message', container.textContent.indexOf('刪除失敗: 資料庫忙碌') !== -1);
    A('the dialog stays open after a failure so the operator can retry', !!container.querySelector('[role="dialog"]'));
    A('the confirm button is not latched on 刪除中', !!byText('button', '確定刪除') && !byText('button', '刪除中'));
    A('a failed delete does not fake a refresh', refreshCalls.length === 2, JSON.stringify(refreshCalls));

    // --- missing API bundle: toast, no latch (G1 guard) ---
    window.SDPRS_API = {};
    calls.length = 0;
    click(byText('button', '確定刪除')); await settle();
    A('missing API bundle attempts no DELETE', calls.length === 0, JSON.stringify(calls));
    A('missing API bundle toasts instead of latching on 刪除中', container.textContent.indexOf('暫時無法連線後端') !== -1 && !byText('button', '刪除中'));

    // --- in-flight: busy label + no double-fire ---
    window.SDPRS_API = mkApi();
    mode = 'hang'; calls.length = 0;
    click(byText('button', '確定刪除')); await settle();
    const busyBtn = byText('button', '刪除中');
    A('an in-flight delete shows 刪除中... and disables the confirm button', !!busyBtn && busyBtn.disabled === true, busyBtn && busyBtn.disabled);
    click(busyBtn); await settle();
    A('a second click while in flight issues no second DELETE', calls.length === 1, JSON.stringify(calls));

    // --- no clientId: nothing addressable, so nothing is sent ---
    // Never fall back to node.id "because it looks like a client id" — that is
    // the exact substitution that produced a permanent 404 in the shipped UI.
    mode = 'ok'; calls.length = 0;
    // Unmount first: the hang above left a dialog open on the previous target.
    ReactDOM.flushSync(() => root.render(null));
    ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
      nodes: [{ id: 'webcam_orphan01', clientId: null, name: '無主攝影機', location: '大堂', type: 'webcam', status: 'online', snoozeMin: 0 }],
      onSelectNode: () => {}, onRefresh: () => { refreshCalls.push(1); },
    })));
    await settle();
    // Looked up by label, not by title: the disabled button deliberately
    // carries a DIFFERENT title explaining why it cannot be used.
    const orphanDel = Array.from(container.querySelectorAll('button')).find(b => b.textContent.trim() === '刪除');
    A('a webcam row with no clientId disables the 刪除 button', !!orphanDel && orphanDel.disabled === true, orphanDel && orphanDel.title);
    click(orphanDel); await settle();
    A('a clientId-less row opens no confirm dialog and sends no DELETE', !container.querySelector('[role="dialog"]') && calls.length === 0, JSON.stringify(calls));

    // --- clientId but NO clientName: fall back to the id, never blank out ---
    // client_name is nullable on the wire: an older backend does not send it,
    // and the server LEFT JOINs webcam_clients so a camera whose client row is
    // gone still lists (with client_name null). The row must stay deletable and
    // the dialog must still say WHAT it is deleting — a confirm reading
    // 「用戶端「」」 or 「用戶端「undefined」」 is worse than the hex id.
    mode = 'ok'; calls.length = 0;
    ReactDOM.flushSync(() => root.render(null));
    ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
      nodes: [{ id: 'webcam_cam00009', clientId: 'webcam_nameless1', clientName: null, name: '無名主機 前門', location: '大堂', type: 'webcam', status: 'online', snoozeMin: 0 }],
      onSelectNode: () => {}, onRefresh: () => { refreshCalls.push(1); },
    })));
    await settle();
    const namelessDel = Array.from(container.querySelectorAll('button')).find(b => b.textContent.trim() === '刪除');
    A('a webcam row with a clientId but no clientName keeps 刪除 enabled', !!namelessDel && namelessDel.disabled === false, namelessDel && namelessDel.title);
    click(namelessDel); await settle();
    const fbDialog = container.querySelector('[role="dialog"]');
    A('a null clientName falls back to the client id in the dialog', !!fbDialog && fbDialog.textContent.indexOf('webcam_nameless1') !== -1, fbDialog && fbDialog.textContent);
    A('the fallback dialog renders no undefined/null placeholder', !!fbDialog && fbDialog.textContent.indexOf('undefined') === -1 && fbDialog.textContent.indexOf('null') === -1, fbDialog && fbDialog.textContent);
    click(byText('button', '確定刪除')); await settle();
    A('the fallback path still deletes by clientId', calls.length === 1 && calls[0] === 'webcam_nameless1', JSON.stringify(calls));
    A('the fallback success toast identifies the client by id', container.textContent.indexOf('Webcam 用戶端「webcam_nameless1」已刪除') !== -1, container.textContent.slice(0, 200));
  } catch (e) {
    results.push({ name: 'status webcam-delete suite threw', pass: false, detail: e && e.stack ? e.stack.split('\\n').slice(0, 3).join(' | ') : String(e) });
  }
  window.__TEST_RESULT = results;
})();
`;

// ------------------------ monitor.jsx: live-view readiness (follow-up Task 1) --
// The tile used to flip to 'live' on a blind setTimeout(3000): on a slow client
// that mounts <video> against a playlist with no segment yet — a black tile
// that reads as a dead camera on the wall. Readiness is now MEASURED by polling
// the playlist until it lists a .ts segment.
// The load-bearing regression guard here is the LAST block: the 30s viewer-lease
// renew interval lives in the same useEffect and must still be armed exactly
// once the tile is genuinely live (without it the server force-stops the stream
// ~90s in). window.setInterval is wrapped so the 30s arm can be asserted, and
// its callback invoked, without a 30s wait.
const TEST_MONITOR_LIVE = `
window.__TEST_PROMISE = (async () => {
${PRELUDE}
  try {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const HEADER_ONLY = '#EXTM3U\\n#EXT-X-VERSION:3\\n#EXT-X-TARGETDURATION:2\\n#EXT-X-MEDIA-SEQUENCE:0\\n';
    const WITH_SEGMENT = HEADER_ONLY + '#EXTINF:2.000,\\nseg00001.ts\\n';
    const probes = [], startCalls = [], stopCalls = [], renewCalls = [];
    let playlist = HEADER_ONLY;
    window.SDPRS_API = {
      startWebcamStream: (id) => { startCalls.push(id); return Promise.resolve({}); },
      stopWebcamStream: (id) => { stopCalls.push(id); return Promise.resolve({}); },
      renewWebcamStream: (id) => { renewCalls.push(id); return Promise.resolve({}); },
      getWebcamPlaylist: (id) => { probes.push(id); return Promise.resolve(playlist); },
    };
    const intervals = [];
    const origSetInterval = window.setInterval;
    window.setInterval = function (fn, ms) { intervals.push({ fn: fn, ms: ms }); return origSetInterval.call(window, fn, ms); };
    const ivMs = () => JSON.stringify(intervals.map(i => i.ms));

    const node = { id: 'webcam_ab12', name: '櫃台電腦', type: 'webcam', status: 'online', upload: 2, heartbeat: 2, snoozeMin: 0, level: null };
    const render = () => ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node, onSelect: () => {}, nodeAlerts: [] })));
    const findBtn = (txt) => Array.from(container.querySelectorAll('button')).find(b => b.textContent.indexOf(txt) !== -1);

    // --- playlist has no segment yet: must NOT go live ---
    render();
    click(findBtn('即時'));
    await settle();
    A('clicking 即時 starts the stream', startCalls.length === 1 && startCalls[0] === 'webcam_ab12', JSON.stringify(startCalls));
    A('the playlist is probed immediately (no blind 3s wait)', probes.length >= 1, probes.length);
    A('a segment-less playlist keeps the tile on 連線中', container.textContent.indexOf('連線中') !== -1);
    A('a segment-less playlist does NOT mount the <video> player', !container.querySelector('video'));
    A('the 30s lease-renew interval is NOT armed while still loading', !intervals.some(i => i.ms === 30000), ivMs());

    const beforeRepeat = probes.length;
    await sleep(1700);
    A('the readiness poll repeats while loading', probes.length > beforeRepeat, beforeRepeat + ' -> ' + probes.length);

    // --- unmount must stop the poll (no timer leak / no setState after unmount) ---
    ReactDOM.flushSync(() => root.render(null));
    const afterUnmount = probes.length;
    await sleep(1700);
    A('unmounting stops the readiness poll', probes.length === afterUnmount, afterUnmount + ' -> ' + probes.length);

    // --- playlist lists a segment: go live on the very first probe ---
    playlist = WITH_SEGMENT;
    probes.length = 0; startCalls.length = 0; intervals.length = 0;
    render();
    click(findBtn('即時'));
    await settle();
    A('a playlist listing a .ts segment flips the tile to live', !!findBtn('LIVE'));
    A('going live mounts the HLS <video> player', !!container.querySelector('video'));
    A('the 連線中 placeholder is gone once live', container.textContent.indexOf('連線中') === -1);

    // REGRESSION GUARD: the lease-renew arm still fires, only once live.
    const renewIv = intervals.find(i => i.ms === 30000);
    A('the live arm still arms the 30s viewer-lease renew interval', !!renewIv, ivMs());
    if (renewIv) { renewIv.fn(); await settle(); }
    A('the lease-renew tick calls renewWebcamStream(nodeId)', renewCalls[0] === 'webcam_ab12', JSON.stringify(renewCalls));

    // --- stop returns to snapshot mode ---
    click(findBtn('LIVE'));
    await settle();
    A('stopping calls stopWebcamStream and unmounts the player', stopCalls[0] === 'webcam_ab12' && !container.querySelector('video'), JSON.stringify(stopCalls));

    // --- readiness poll times out: release the armed lease, do not strand it ---
    // The 即時 click armed the server viewer-lease via startWebcamStream. If the
    // client never produces a segment, the poll must give up AND call
    // stopWebcamStream — otherwise the lease sits armed ~90s and the field PC
    // keeps encoding a stream nobody watches. Drive the deadline by jumping
    // Date.now past it instead of waiting the full LIVE_POLL_TIMEOUT_MS.
    ReactDOM.flushSync(() => root.render(null)); // fresh instance for the timeout case
    playlist = HEADER_ONLY;
    probes.length = 0; startCalls.length = 0; stopCalls.length = 0; intervals.length = 0;
    const realNow = Date.now;
    render();
    click(findBtn('即時'));
    await settle();
    A('timeout case: the stream is armed on click', startCalls.length === 1 && startCalls[0] === 'webcam_ab12', JSON.stringify(startCalls));
    A('timeout case: no <video> while still segment-less', !container.querySelector('video'));
    Date.now = () => realNow() + LIVE_POLL_TIMEOUT_MS + 5000; // jump past the readiness deadline
    await sleep(1700); // let the next scheduled probe run and hit the deadline branch
    A('a readiness timeout releases the server lease via stopWebcamStream', stopCalls[0] === 'webcam_ab12', JSON.stringify(stopCalls));
    A('a readiness timeout returns the tile to the 即時 affordance (no video)', !!findBtn('即時') && !container.querySelector('video'));
    Date.now = realNow;

    window.setInterval = origSetInterval;
  } catch (e) {
    results.push({ name: 'monitor live-readiness suite threw', pass: false, detail: e && e.stack ? e.stack.split('\\n').slice(0, 3).join(' | ') : String(e) });
  }
  window.__TEST_RESULT = results;
})();
`;

// ---------------------------------------- monitor.jsx: webcam tile (Task 5) --
// NodeCard keys its source badge + live button off node.type === 'webcam' (the
// value Step 0b makes mapNode emit). A webcam tile shows the blue "Webcam"
// badge and a ▶ 即時 live button; an edge cam (mapped type 'camera') shows the
// grey "Edge Cam" badge and NEVER the webcam badge or the live button — proof
// the badge is reachable and not dead code, which is why Tasks 5 and 12 merged.
const TEST_MONITOR = `
window.__TEST_PROMISE = (async () => {
${PRELUDE}
  try {
    window.SDPRS_API = { startWebcamStream: () => Promise.resolve({}), stopWebcamStream: () => Promise.resolve({}) };
    const base = { status: 'online', upload: 2, heartbeat: 2, snoozeMin: 0, level: null };
    const render = (node) => ReactDOM.flushSync(() => root.render(
      React.createElement(NodeCard, { node, onSelect: () => {}, nodeAlerts: [] })));

    // --- webcam node ---
    render(Object.assign({}, base, { id: 'webcam_ab12', name: '櫃台電腦', type: 'webcam' }));
    A('webcam tile renders the Webcam badge', container.textContent.indexOf('Webcam') !== -1);
    const liveBtn = Array.from(container.querySelectorAll('button')).find(b => b.textContent.indexOf('即時') !== -1);
    A('webcam tile renders the ▶ 即時 live button', !!liveBtn);
    A('webcam tile does NOT render the Edge Cam badge', container.textContent.indexOf('Edge Cam') === -1);

    // --- edge cam (glass -> mapped type 'camera') ---
    ReactDOM.flushSync(() => root.render(null));
    render(Object.assign({}, base, { id: 'CAM-1', name: '西灣橋', type: 'camera', temp: 30, visualHealth: 'ok', audioHealth: 'ok' }));
    A('edge cam renders the Edge Cam badge', container.textContent.indexOf('Edge Cam') !== -1);
    A('edge cam does NOT render the Webcam badge', container.textContent.indexOf('Webcam') === -1);
    A('edge cam (non-webcam) has NO live button', Array.from(container.querySelectorAll('button')).every(b => b.textContent.indexOf('即時') === -1));
  } catch (e) {
    results.push({ name: 'monitor webcam-tile suite threw', pass: false, detail: e && e.stack ? e.stack.split('\\n').slice(0, 3).join(' | ') : String(e) });
  }
  window.__TEST_RESULT = results;
})();
`;

// ------------------------------------ status.jsx: webcam columns (Task 5) ---
// Step 0c headline-bug guard: a webcam row must not be rendered as a pump. Its
// 類型 cell shows "Webcam" (never 「抽水站」), its 電源 cell is not "PoE", and its
// 溫度/水位 cell carries no 「水位資料未上傳」 water-sensor lie. The camera row is
// asserted unchanged so the webcam routing is proven not to have leaked.
const TEST_STATUS_WEBCAM_COLUMNS = `
window.__TEST_PROMISE = (async () => {
${PRELUDE}
  try {
    window.SDPRS_API = {};
    const webcamNode = { id: 'webcam_ab12cd34', name: '櫃台電腦', location: '大堂', type: 'webcam', status: 'online', snoozeMin: 0 };
    const cameraNode = { id: 'CAM-1', name: '西灣橋', location: '西灣', type: 'camera', status: 'online', snoozeMin: 0, bitrate: 1.2, drops: 0, temp: 30 };
    ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
      nodes: [webcamNode, cameraNode], onSelectNode: () => {}, onRefresh: () => {},
    })));
    await settle();

    const rows = Array.from(container.querySelectorAll('tr'));
    const webcamTr = rows.find(r => r.textContent.indexOf('webcam_ab12cd34') !== -1);
    A('webcam row renders in the status table', !!webcamTr);
    A('類型 cell shows Webcam and NOT 抽水站', !!webcamTr && webcamTr.textContent.indexOf('Webcam') !== -1 && webcamTr.textContent.indexOf('抽水站') === -1);
    A('電源 cell is not PoE for a webcam', !!webcamTr && webcamTr.textContent.indexOf('PoE') === -1);
    A('溫度/水位 cell carries no 水位資料未上傳 title', !!webcamTr && !webcamTr.querySelector('[title="水位資料未上傳"]'));

    // Contrast: the edge cam is still 攝影機 and still shows PoE — routing did not leak.
    const cameraTr = rows.find(r => r.textContent.indexOf('CAM-1') !== -1);
    A('camera row still labelled 攝影機', !!cameraTr && cameraTr.textContent.indexOf('攝影機') !== -1);
    A('camera row still shows PoE', !!cameraTr && cameraTr.textContent.indexOf('PoE') !== -1);
  } catch (e) {
    results.push({ name: 'status webcam-columns suite threw', pass: false, detail: e && e.stack ? e.stack.split('\\n').slice(0, 3).join(' | ') : String(e) });
  }
  window.__TEST_RESULT = results;
})();
`;

// ----------------------------------------------- components.jsx: CMP-F11 ----
const TEST_PALETTE = `
window.__TEST_PROMISE = (async () => {
${PRELUDE}
  try {
    window.NAV_ITEMS = [
      { id: 'status', label: '節點狀態', hotkey: '2', Icon: Icon.Server },
      { id: 'alerts', label: '警報', hotkey: '1', Icon: Icon.AlertTriangle },
    ];
    const nav = [], cmd = [], closed = [];
    const nodes = [{ id: 'CAM-07', name: '西灣橋攝影機', location: '西灣', status: 'ONLINE' }];
    ReactDOM.flushSync(() => root.render(React.createElement(window.CommandPalette, {
      open: true, onClose: () => closed.push(1), alerts: [], nodes,
      onSelectAlert: () => {}, onNav: (p) => nav.push(p), onCmd: (c) => cmd.push(c),
    })));
    await settle();

    const input = container.querySelector('input[role="combobox"]');
    A('CMP-F11 palette renders a search box', !!input);
    setInput(input, 'CAM-07');
    await settle();

    const opt = Array.from(container.querySelectorAll('button[role="option"]'))
      .find(b => b.textContent.indexOf('CAM-07') !== -1);
    A('CMP-F11 node result is listed for the query', !!opt, opt && opt.textContent);

    click(opt);
    await settle();
    // The chosen node id used to be thrown away — the operator landed on a
    // generic status page with no indication of which node they picked.
    A('CMP-F11 picking a node navigates to status', nav.indexOf('status') !== -1, JSON.stringify(nav));
    A('CMP-F11 the chosen node id rides onCmd as node:CAM-07', cmd.indexOf('node:CAM-07') !== -1, JSON.stringify(cmd));
    A('CMP-F11 palette closes after the pick', closed.length === 1, JSON.stringify(closed));
  } catch (e) {
    results.push({ name: 'palette suite threw', pass: false, detail: e && e.stack ? e.stack.split('\\n').slice(0, 3).join(' | ') : String(e) });
  }
  window.__TEST_RESULT = results;
})();
`;

// ------------------------------------------------- handover.jsx: WHA-M8 -----
const TEST_HANDOVER = `
window.__TEST_PROMISE = (async () => {
${PRELUDE}
  try {
    window.SDPRS_USER = 'alice';
    try { window.localStorage.clear(); } catch (_) {}
    window.HANDOVER = { current: '', pinned: { by: 'bob', at: '10:00', text: '尚無交接備註', ageMin: 3 }, history: [], updatedAt: 'T1' };
    const saveCalls = [];
    let mode = 'conflict';
    window.SDPRS_API = {
      refreshLive: () => Promise.resolve(),
      saveHandover: (note, expected) => {
        saveCalls.push([note, expected]);
        if (mode === 'conflict') {
          const err = new Error('conflict');
          err.status = 409; err.conflict = true;
          err.current = '對方寫的交接內容';
          err.updatedAt = 'T2';
          return Promise.reject(err);
        }
        return Promise.resolve({ ok: true, updated_at: 'T3' });
      },
    };

    ReactDOM.flushSync(() => root.render(React.createElement(window.HandoverPage, {})));
    await settle();

    const ta = container.querySelector('textarea');
    A('WHA-M8 handover textarea renders', !!ta);
    setInput(ta, '我的草稿');
    await settle();

    const saveBtn = byText('button', '儲存');
    A('WHA-M8 儲存 button present', !!saveBtn);
    click(saveBtn);
    await settle(12);

    // A 409 must surface BOTH versions and let the operator choose — never
    // silently clobber the peer's note, never silently drop the draft.
    const txt = container.textContent;
    A('WHA-M8 409 opens the 儲存衝突 dialog', txt.indexOf('儲存衝突') !== -1);
    A('WHA-M8 dialog shows the SERVER version', txt.indexOf('對方寫的交接內容') !== -1);
    A('WHA-M8 dialog shows MY draft', txt.indexOf('我的草稿') !== -1);
    A('WHA-M8 first save sent the stale token T1', saveCalls.length >= 1 && saveCalls[0][1] === 'T1', JSON.stringify(saveCalls));

    mode = 'ok';
    const overwriteBtn = byText('button', '覆蓋伺服器版本');
    A('WHA-M8 覆蓋伺服器版本 button present', !!overwriteBtn);
    click(overwriteBtn);
    await settle(12);

    A('WHA-M8 overwrite re-issues the save', saveCalls.length === 2, JSON.stringify(saveCalls));
    A('WHA-M8 overwrite resends MY draft', saveCalls.length === 2 && saveCalls[1][0] === '我的草稿', JSON.stringify(saveCalls));
    A('WHA-M8 overwrite uses the server token T2 (so the retry matches)', saveCalls.length === 2 && saveCalls[1][1] === 'T2', JSON.stringify(saveCalls));
    A('WHA-M8 dialog closes after resolution', container.textContent.indexOf('儲存衝突') === -1);
  } catch (e) {
    results.push({ name: 'handover suite threw', pass: false, detail: e && e.stack ? e.stack.split('\\n').slice(0, 3).join(' | ') : String(e) });
  }
  window.__TEST_RESULT = results;
})();
`;

// ---------------------------------------------- tweaks-panel.jsx: WHA-L8 ----
const TEST_TWEAKS = `
window.__TEST_PROMISE = (async () => {
${PRELUDE}
  try {
    const changes = [];
    ReactDOM.flushSync(() => root.render(React.createElement(TweakNumber, { label: '閾值', value: 5, min: 0, max: 10, step: 1, onChange: (v) => changes.push(v) })));
    const input = container.querySelector('input');
    A('WHA-L8 input seeds from value (5)', input && input.value === '5', input && input.value);

    // Clearing the field must not snap to 0 mid-typing.
    changes.length = 0;
    setInput(input, '');
    A('WHA-L8 empty field tolerated (no snap-to-0)', input.value === '', input.value);
    A('WHA-L8 clearing does NOT commit 0', changes.indexOf(0) === -1, JSON.stringify(changes));

    changes.length = 0;
    setInput(input, '8');
    A('WHA-L8 valid entry commits onChange(8)', changes[changes.length - 1] === 8, JSON.stringify(changes));

    // Assert onInputBlur RUNS via its observable onChange branch — jsdom will
    // not deterministically flush the post-blur setText to the DOM.
    setInput(input, '7');
    changes.length = 0;
    input.focus();
    input.blur();
    input.dispatchEvent(new window.FocusEvent('focusout', { bubbles: true }));
    await settle();
    A('WHA-L8 onInputBlur runs on blur (commits via onChange)', changes.indexOf(7) !== -1, JSON.stringify(changes));
  } catch (e) {
    results.push({ name: 'tweaks suite threw', pass: false, detail: e && e.stack ? e.stack.split('\\n').slice(0, 3).join(' | ') : String(e) });
  }
  window.__TEST_RESULT = results;
})();
`;

// -------------------------------------------------- api.jsx: public surface --
// api.jsx is a self-contained IIFE that publishes window.SDPRS_API at eval, so
// loading it as a target (no deps) lets us assert the exported method surface
// directly. GUARD: renewWebcamStream was wired into monitor.jsx (30s viewer-
// lease renewal) but is a no-op unless api.jsx actually exports it — dropping it
// from the public object silently re-broke the "stream force-stopped ~90s in"
// bug the refs/render gates could not catch (monitor.jsx guards the call).
const TEST_API = `
window.__TEST_PROMISE = (async () => {
${PRELUDE}
  try {
    const api = window.SDPRS_API;
    A('api.jsx publishes window.SDPRS_API', !!api);
    A('SDPRS_API.renewWebcamStream is a function', !!api && typeof api.renewWebcamStream === 'function', api && typeof api.renewWebcamStream);
    A('SDPRS_API.startWebcamStream/stopWebcamStream still present', !!api && typeof api.startWebcamStream === 'function' && typeof api.stopWebcamStream === 'function');
    // Same guard, follow-up Task 2: status.jsx's 刪除 button is a no-op unless
    // api.jsx exports this (the row guards the call, so a dropped export would
    // silently degrade to "button does nothing but toast").
    A('SDPRS_API.deleteWebcamClient is a function', !!api && typeof api.deleteWebcamClient === 'function', api && typeof api.deleteWebcamClient);
    // Follow-up Task 1: monitor.jsx polls this to decide readiness; if it is
    // missing the tile silently falls back to the old blind 3s warm-up.
    A('SDPRS_API.getWebcamPlaylist is a function', !!api && typeof api.getWebcamPlaylist === 'function', api && typeof api.getWebcamPlaylist);

    // Follow-up 3: pin mapNode's snake_case -> camelCase contract. The status +
    // monitor pages key revoke/delete on node.clientId / node.clientName, and
    // those fields are ONLY ever produced here, by mapNode, from the backend's
    // client_id / client_name. Drive the REAL mapNode end-to-end via refreshLive
    // over a stubbed /api/nodes so a dropped mapping cannot pass unnoticed.
    const jsonRes = (data) => Promise.resolve({
      ok: true, status: 200,
      headers: { get: () => 'application/json' },
      json: () => Promise.resolve(data), text: () => Promise.resolve(''),
    });
    window.fetch = (path) => (path.indexOf('/api/nodes') === 0)
      ? jsonRes([{ node_id: 'webcam_cam99', node_type: 'webcam', status: 'ONLINE', client_id: 'webcam_cli99', client_name: 'Front Desk PC', location: 'Cam 99' }])
      : jsonRes([]); // every other loader: benign empty payload
    const rl = await api.refreshLive();
    const mapped = ((rl && rl.nodes) || []).find(n => n.id === 'webcam_cam99');
    A('mapNode maps node_type webcam -> type webcam', !!mapped && mapped.type === 'webcam', mapped && mapped.type);
    A('mapNode surfaces client_id as clientId', !!mapped && mapped.clientId === 'webcam_cli99', mapped && mapped.clientId);
    A('mapNode surfaces client_name as clientName', !!mapped && mapped.clientName === 'Front Desk PC', mapped && mapped.clientName);
  } catch (e) {
    results.push({ name: 'api surface suite threw', pass: false, detail: e && e.stack ? e.stack.split('\\n').slice(0, 3).join(' | ') : String(e) });
  }
  window.__TEST_RESULT = results;
})();
`;

const SUITES = [
  { name: 'MSP-F6 / MSP-F5 / API-F9   pumps.jsx',    deps: ['icons.jsx', 'data.jsx'], target: 'pages/pumps.jsx', test: TEST_PUMPS },
  { name: 'MSP-F7                      status.jsx',   deps: ['icons.jsx', 'data.jsx', 'components.jsx'], target: 'pages/status.jsx', test: TEST_STATUS },
  { name: 'Task 6                      status.jsx (webcam admin)', deps: ['icons.jsx', 'data.jsx', 'components.jsx'], target: 'pages/status.jsx', test: TEST_STATUS_WEBCAM },
  { name: 'Task 5                      monitor.jsx (webcam tile)', deps: ['icons.jsx', 'data.jsx', 'components.jsx'], target: 'pages/monitor.jsx', test: TEST_MONITOR },
  { name: 'Task 5                      status.jsx (webcam columns)', deps: ['icons.jsx', 'data.jsx', 'components.jsx'], target: 'pages/status.jsx', test: TEST_STATUS_WEBCAM_COLUMNS },
  { name: 'Follow-up 2/3               status.jsx (webcam delete)', deps: ['icons.jsx', 'data.jsx', 'components.jsx'], target: 'pages/status.jsx', test: TEST_STATUS_WEBCAM_DELETE },
  { name: 'Follow-up 1                 monitor.jsx (live readiness)', deps: ['icons.jsx', 'data.jsx', 'components.jsx'], target: 'pages/monitor.jsx', test: TEST_MONITOR_LIVE },
  { name: 'CMP-F11                     components.jsx', deps: ['icons.jsx', 'data.jsx'], target: 'components.jsx', test: TEST_PALETTE },
  { name: 'WHA-M8                      handover.jsx', deps: ['icons.jsx', 'data.jsx', 'components.jsx'], target: 'pages/handover.jsx', test: TEST_HANDOVER },
  { name: 'WHA-L8                      tweaks-panel.jsx', deps: ['icons.jsx', 'data.jsx'], target: 'tweaks-panel.jsx', test: TEST_TWEAKS },
  { name: 'API surface                 api.jsx (renewWebcamStream)', deps: [], target: 'api.jsx', test: TEST_API },
];

(async () => {
  let pass = 0, fail = 0;
  for (const s of SUITES) {
    console.log('\n=== ' + s.name + ' ===');
    let results = [];
    try {
      const world = makeWorld();
      loadDeps(world, s.deps);
      runTarget(world, s.target, s.test);
      await world.window.__TEST_PROMISE;
      results = world.window.__TEST_RESULT || [];
      if (!results.length) results = [{ name: s.name + ' — no assertions recorded', pass: false, detail: 'suite produced nothing' }];
    } catch (e) {
      results = [{ name: s.name + ' — suite setup failed', pass: false, detail: (e && e.message) || String(e) }];
    }
    for (const r of results) {
      console.log((r.pass ? 'PASS  ' : 'FAIL  ') + r.name + (r.pass ? '' : '\n        -- ' + r.detail));
      r.pass ? pass++ : fail++;
    }
  }
  console.log('\n' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
})();
