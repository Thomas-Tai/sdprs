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

const SUITES = [
  { name: 'MSP-F6 / MSP-F5 / API-F9   pumps.jsx',    deps: ['icons.jsx', 'data.jsx'], target: 'pages/pumps.jsx', test: TEST_PUMPS },
  { name: 'MSP-F7                      status.jsx',   deps: ['icons.jsx', 'data.jsx', 'components.jsx'], target: 'pages/status.jsx', test: TEST_STATUS },
  { name: 'CMP-F11                     components.jsx', deps: ['icons.jsx', 'data.jsx'], target: 'components.jsx', test: TEST_PALETTE },
  { name: 'WHA-M8                      handover.jsx', deps: ['icons.jsx', 'data.jsx', 'components.jsx'], target: 'pages/handover.jsx', test: TEST_HANDOVER },
  { name: 'WHA-L8                      tweaks-panel.jsx', deps: ['icons.jsx', 'data.jsx'], target: 'tweaks-panel.jsx', test: TEST_TWEAKS },
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
