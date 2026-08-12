// Section 4 (Operational Pages — Monitor) remediation suites.
// Owner: fix/spa-lane-2026-08-01 monitor.jsx pass. See
// docs/audits/full_dashboard_audit_2026-07-26.md Section 4 for OPS-0xx, and
// DELTA_NEW_VERDICTS.md for NEW-*. Findings covered here: OPS-007, OPS-010,
// OPS-011, OPS-012, OPS-018, OPS-026, OPS-030, OPS-035, OPS-036, OPS-037,
// NEW-UX-001, NEW-UX-002, NEW-UX-013, NEW-RT-001, NEW-UX-026.
//
// Selector discipline: assertions target CLASS/STRUCTURE, not zh-TW copy,
// wherever a later finding in this same file also touches that copy (e.g.
// NEW-UX-026 rewrites button text) — so an earlier finding's regression
// guard can't be invalidated by a later, unrelated fix in this same pass.

const HEADER_ONLY = '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n';
const WITH_TS_SEGMENT = HEADER_ONLY + '#EXTINF:2.000,\nseg00001.ts\n';

// Shared native-HLS shim so HlsPlayer mounts a real <video> under jsdom
// (mirrors TEST_MONITOR_LIVE in render_tests.js). Call the returned restore()
// in a finally-equivalent at the end of the suite.
const HLS_SHIM = `
  const _mproto = window.HTMLMediaElement.prototype;
  const _origCPT = _mproto.canPlayType, _origPlay = _mproto.play, _origLoad = _mproto.load;
  _mproto.canPlayType = () => 'maybe';
  _mproto.play = () => Promise.resolve();
  _mproto.load = () => {};
`;
const HLS_SHIM_RESTORE = `
  _mproto.canPlayType = _origCPT; _mproto.play = _origPlay; _mproto.load = _origLoad;
`;

module.exports = [
  // ---------------------------------------------------------------- OPS-037
  {
    name: 'OPS-037 fmtAgeOrDash lives in data.jsx (not monitor.jsx)',
    target: 'data.jsx',
    deps: ['icons.jsx'],
    body: `
      A('OPS-037 window.fmtAgeOrDash is published as a function by data.jsx alone', typeof window.fmtAgeOrDash === 'function', typeof window.fmtAgeOrDash);
      A('OPS-037 fmtAgeOrDash(null) renders the em dash (never fabricates 0s)', window.fmtAgeOrDash(null) === '—', window.fmtAgeOrDash(null));
      A('OPS-037 fmtAgeOrDash humanizes via fmtAge, not a raw seconds count', window.fmtAgeOrDash(90000) === window.fmtAge(90000) && window.fmtAgeOrDash(90000).indexOf('d') !== -1, window.fmtAgeOrDash(90000));
    `,
  },
  {
    name: 'OPS-037 monitor.jsx no longer defines its own fmtAgeOrDash',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      // Node's vm context shares the top-level let/const lexical scope across
      // scripts run in it, so a bare "fmtAgeOrDash" always resolves to
      // data.jsx's const here regardless of monitor.jsx — typeof alone can't
      // prove monitor.jsx isn't ALSO shadowing it with its own definition.
      // Reference-equality against the published window.* copy can: a local
      // redefinition in monitor.jsx would be a different function object.
      A('OPS-037 monitor.jsx does not shadow fmtAgeOrDash with its own definition', fmtAgeOrDash === window.fmtAgeOrDash, fmtAgeOrDash === window.fmtAgeOrDash);
      const node = { id: 'CAM-ops037', name: 't', type: 'camera', status: 'online', upload: 90000, heartbeat: 5, snoozeMin: 0, temp: 20, visualHealth: 'ok', audioHealth: 'ok' };
      ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node, onSelect: () => {}, nodeAlerts: [] })));
      await settle();
      A('OPS-037 NodeCard still humanizes a large upload age via the shared helper', container.textContent.indexOf('1d') !== -1, container.textContent.slice(0, 300));
    `,
  },

  // ---------------------------------------------------------------- OPS-012
  {
    name: 'OPS-012 webcam source badge no longer overlaps the status dot / alert badge',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      const node = { id: 'webcam_ops012', name: 't', type: 'webcam', status: 'online', upload: 2, heartbeat: 2, snoozeMin: 0, level: null };
      ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, {
        node, onSelect: () => {}, nodeAlerts: [{ sev: 'warn' }],
      })));
      await settle();
      // Structural, not text-based (NEW-UX-026 in this same file rewrites the
      // badge's copy) — find it by its unique background tone class instead.
      const badge = Array.from(container.querySelectorAll('span')).find(s => s.className.indexOf('bg-sev-info/90') !== -1);
      A('OPS-012 setup: the webcam source badge renders', !!badge);
      A('OPS-012 the source badge no longer sits at top-1 left-1 (the old overlapping slot)', !!badge && badge.className.indexOf('top-1 left-1') === -1, badge && badge.className);
      const dot = container.querySelector('.w-3.h-3.rounded-full');
      A('OPS-012 setup: the status dot renders at top-2 left-2', !!dot && dot.className.indexOf('top-2 left-2') !== -1, dot && dot.className);
    `,
  },

  // ------------------------------------------------------------- NEW-UX-002
  {
    name: 'NEW-UX-002 warn-severity badges no longer force text-white under the black-text warn branch',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      // NodeCard alert-count badge
      const camNode = { id: 'CAM-ux002', name: 't', type: 'camera', status: 'warn', upload: 2, heartbeat: 2, snoozeMin: 0, temp: 20, visualHealth: 'ok', audioHealth: 'ok' };
      ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node: camNode, onSelect: () => {}, nodeAlerts: [{ sev: 'warn' }] })));
      await settle();
      let badge = Array.from(container.querySelectorAll('div')).find(d => d.className.indexOf('bg-sev-warn') !== -1 && d.className.indexOf('animate-live-blink') === -1 && d.className.indexOf('text-black') !== -1);
      A('NEW-UX-002 setup: NodeCard warn alert badge renders', !!badge);
      A('NEW-UX-002 NodeCard warn alert badge is NOT also text-white', !!badge && badge.className.indexOf('text-white') === -1, badge && badge.className);
      ReactDOM.flushSync(() => root.render(null));
      ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node: Object.assign({}, camNode, { status: 'critical' }), onSelect: () => {}, nodeAlerts: [{ sev: 'critical' }] })));
      await settle();
      let critBadge = Array.from(container.querySelectorAll('div')).find(d => d.className.indexOf('bg-sev-critical') !== -1 && d.className.indexOf('left-7') !== -1);
      A('NEW-UX-002 NodeCard critical alert badge IS text-white', !!critBadge && critBadge.className.indexOf('text-white') !== -1, critBadge && critBadge.className);

      // PumpCard alert-count badge
      ReactDOM.flushSync(() => root.render(null));
      const pumpNode = { id: 'pump-ux002', name: 't', location: 'x', type: 'pump', status: 'warn', heartbeat: 2, level: 40, cycles: 3, snoozeMin: 0 };
      ReactDOM.flushSync(() => root.render(React.createElement(PumpCard, { node: pumpNode, onSelect: () => {}, nodeAlerts: [{ sev: 'warn' }] })));
      await settle();
      let pumpBadge = Array.from(container.querySelectorAll('span')).find(s => s.className.indexOf('bg-sev-warn') !== -1 && s.className.indexOf('text-black') !== -1);
      A('NEW-UX-002 setup: PumpCard warn alert badge renders', !!pumpBadge);
      A('NEW-UX-002 PumpCard warn alert badge is NOT also text-white', !!pumpBadge && pumpBadge.className.indexOf('text-white') === -1, pumpBadge && pumpBadge.className);
    `,
  },

  // ------------------------------------------------------- OPS-010 / OPS-036
  {
    name: 'OPS-036 NodeCard frozen-check now agrees with SnapshotImage: upload==null is frozen',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      const node = { id: 'CAM-ops036', name: 't', type: 'camera', status: 'online', upload: null, heartbeat: 2, snoozeMin: 0, temp: 20, visualHealth: 'ok', audioHealth: 'ok' };
      ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node, onSelect: () => {}, nodeAlerts: [] })));
      await settle();
      A('OPS-036 a never-reported node (upload==null) now shows the frozen overlay', container.textContent.indexOf('畫面凍結') !== -1, container.textContent.slice(0, 200));
      const tile = container.querySelector('.snapshot-frozen');
      A('OPS-036 a never-reported node also carries the snapshot-frozen CSS class', !!tile);
    `,
  },
  {
    name: 'OPS-010 the frozen overlay no longer paints over a genuinely live HLS webcam tile',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
${HLS_SHIM}
      try {
        window.SDPRS_API = {
          startWebcamStream: () => Promise.resolve({}),
          stopWebcamStream: () => Promise.resolve({}),
          renewWebcamStream: () => Promise.resolve({}),
          getWebcamPlaylist: () => Promise.resolve('${WITH_TS_SEGMENT.replace(/\n/g, '\\n')}'),
        };
        // upload > 60 => frozen-by-staleness is TRUE, but the operator has an
        // actively live HLS view up — the overlay must defer to the live feed.
        const node = { id: 'webcam_ops010', name: 't', type: 'webcam', status: 'online', upload: 999, heartbeat: 2, snoozeMin: 0, level: null };
        ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node, onSelect: () => {}, nodeAlerts: [] })));
        const btn = container.querySelector('button');
        A('OPS-010 setup: the live-start control renders', !!btn);
        click(btn);
        await settle();
        A('OPS-010 setup: the tile actually goes live (video mounted)', !!container.querySelector('video'), container.textContent.slice(0, 200));
        A('OPS-010 the 畫面凍結 overlay is suppressed while a live HLS view is up', container.textContent.indexOf('畫面凍結') === -1, container.textContent.slice(0, 200));
      } finally {
${HLS_SHIM_RESTORE}
      }
    `,
  },

  // ---------------------------------------------------------------- OPS-030
  {
    name: 'OPS-030 PumpCard hides dead flow/trend slots instead of rendering a permanent dash',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      const base = { id: 'pump-ops030', name: 't', location: 'x', type: 'pump', status: 'online', heartbeat: 2, level: 40, cycles: 3, snoozeMin: 0 };
      // --- always-null case (current server reality): flow/trend/cycleHistory hidden ---
      ReactDOM.flushSync(() => root.render(React.createElement(PumpCard, { node: Object.assign({}, base, { flow: null, trend: null, cycleHistory: null }), onSelect: () => {}, nodeAlerts: [] })));
      await settle();
      A('OPS-030 流量 (flow) label is hidden when node.flow is null', container.textContent.indexOf('流量') === -1, container.textContent);
      const trendBox = Array.from(container.querySelectorAll('div')).find(d => d.className.indexOf('bottom-0.5') !== -1 && d.className.indexOf('right-0.5') !== -1);
      A('OPS-030 the trend arrow slot renders nothing when node.trend is null', !trendBox, trendBox && trendBox.outerHTML);
      const cycleBar = container.querySelector('[title="近 12 個 5min 啟動次數"]');
      A('OPS-030 (already-guarded) the cycle-history timeline stays hidden when cycleHistory is null', !cycleBar);

      // --- real-data case: still renders once data actually ships ---
      ReactDOM.flushSync(() => root.render(null));
      ReactDOM.flushSync(() => root.render(React.createElement(PumpCard, { node: Object.assign({}, base, { flow: 12, trend: 'up', cycleHistory: [1,2,3] }), onSelect: () => {}, nodeAlerts: [] })));
      await settle();
      A('OPS-030 流量 renders once node.flow has a real value', container.textContent.indexOf('流量') !== -1 && container.textContent.indexOf('12') !== -1, container.textContent);
      A('OPS-030 the trend arrow renders once node.trend has a real value', container.textContent.indexOf('↑') !== -1, container.textContent);
      const cycleBar2 = container.querySelector('[title="近 12 個 5min 啟動次數"]');
      A('OPS-030 the cycle-history timeline renders once cycleHistory has real data', !!cycleBar2);
    `,
  },

  // ---------------------------------------------------------------- OPS-011
  {
    name: 'OPS-011 playlist readiness probe recognizes fMP4/CMAF segments, not just .ts',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      A('OPS-011 baseline: a classic .ts segment is still recognized', playlistHasSegment('#EXTM3U\\n#EXTINF:2,\\nseg1.ts\\n') === true);
      A('OPS-011 a header-only playlist (no segment) is still NOT ready', playlistHasSegment('#EXTM3U\\n#EXT-X-TARGETDURATION:2\\n') === false);
      A('OPS-011 an fMP4/CMAF .m4s media segment is now recognized as ready', playlistHasSegment('#EXTM3U\\n#EXT-X-MAP:URI="init.mp4"\\n#EXTINF:2,\\nseg1.m4s\\n') === true);
      A('OPS-011 a plain .mp4 media segment is now recognized as ready', playlistHasSegment('#EXTM3U\\n#EXTINF:2,\\nseg1.mp4\\n') === true);
      A('OPS-011 an init-segment-only tag line (starts with #) is still NOT treated as a segment', playlistHasSegment('#EXTM3U\\n#EXT-X-MAP:URI="init.mp4"\\n') === false);
    `,
  },

  // -------------------------------------------------------- OPS-026 / NEW-RT-001
  {
    name: 'OPS-026 double-click on ▶ 即時 in one tick starts the stream exactly once',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      const startCalls = [];
      window.SDPRS_API = {
        startWebcamStream: (id) => { startCalls.push(id); return new Promise(() => {}); }, // hang: never resolves
        stopWebcamStream: () => Promise.resolve({}),
        renewWebcamStream: () => Promise.resolve({}),
        getWebcamPlaylist: () => Promise.resolve(''),
      };
      const node = { id: 'webcam_ops026', name: 't', type: 'webcam', status: 'online', upload: 2, heartbeat: 2, snoozeMin: 0, level: null };
      ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node, onSelect: () => {}, nodeAlerts: [] })));
      const btn = container.querySelector('button');
      A('OPS-026 setup: the live-start control renders', !!btn);
      click(btn); click(btn);
      await settle();
      A('OPS-026 a same-tick double-click fires startWebcamStream exactly once', startCalls.length === 1, JSON.stringify(startCalls));
    `,
  },
  {
    name: 'NEW-RT-001 a late start-rejection after promotion to live does not force-kill a working player',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
${HLS_SHIM}
      try {
        const stopCalls = [];
        let rejectStart;
        window.SDPRS_API = {
          startWebcamStream: () => new Promise((res, rej) => { rejectStart = rej; }),
          stopWebcamStream: (id) => { stopCalls.push(id); return Promise.resolve({}); },
          renewWebcamStream: () => Promise.resolve({}),
          getWebcamPlaylist: () => Promise.resolve('${WITH_TS_SEGMENT.replace(/\n/g, '\\n')}'),
        };
        const node = { id: 'webcam_rt001', name: 't', type: 'webcam', status: 'online', upload: 2, heartbeat: 2, snoozeMin: 0, level: null };
        ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node, onSelect: () => {}, nodeAlerts: [] })));
        const btn = container.querySelector('button');
        click(btn);
        await settle();
        A('NEW-RT-001 setup: the tile is promoted to live before the late rejection lands', !!container.querySelector('video'), container.textContent.slice(0, 200));
        A('NEW-RT-001 setup: rejectStart is armed', typeof rejectStart === 'function');
        rejectStart(new Error('late network blip'));
        await settle();
        A('NEW-RT-001 the tile stays live after a late start-rejection once already promoted', !!container.querySelector('video'), container.textContent.slice(0, 200));
        A('NEW-RT-001 no start-failure text is shown for a late rejection after promotion', container.textContent.indexOf('啟動失敗') === -1, container.textContent.slice(0, 200));
      } finally {
${HLS_SHIM_RESTORE}
      }
    `,
  },

  // ---------------------------------------------------------------- OPS-007
  {
    name: 'OPS-007 a live tile releases the viewer-lease on unmount, not only on the ✕ button',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
${HLS_SHIM}
      try {
        const stopCalls = [];
        window.SDPRS_API = {
          startWebcamStream: () => Promise.resolve({}),
          stopWebcamStream: (id) => { stopCalls.push(id); return Promise.resolve({}); },
          renewWebcamStream: () => Promise.resolve({}),
          getWebcamPlaylist: () => Promise.resolve('${WITH_TS_SEGMENT.replace(/\n/g, '\\n')}'),
        };
        const node = { id: 'webcam_ops007', name: 't', type: 'webcam', status: 'online', upload: 2, heartbeat: 2, snoozeMin: 0, level: null };
        ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node, onSelect: () => {}, nodeAlerts: [] })));
        const btn = container.querySelector('button');
        click(btn);
        await settle();
        A('OPS-007 setup: the tile is live', !!container.querySelector('video'));
        A('OPS-007 setup: nothing has stopped the stream yet (no ✕ click)', stopCalls.length === 0, JSON.stringify(stopCalls));
        ReactDOM.flushSync(() => root.render(null)); // navigate away / unmount, no explicit stop click
        A('OPS-007 unmounting a live tile releases the lease via stopWebcamStream', stopCalls.length === 1 && stopCalls[0] === 'webcam_ops007', JSON.stringify(stopCalls));
      } finally {
${HLS_SHIM_RESTORE}
      }
    `,
  },

  // ---------------------------------------------------------------- OPS-018
  {
    name: 'OPS-018 grid order-freeze also engages on focus and touch, not just mouse hover',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      const mk = (id, status) => ({ id, name: id, type: 'camera', status, upload: 2, heartbeat: 2, snoozeMin: 0, temp: 20, visualHealth: 'ok', audioHealth: 'ok' });
      let nodes = [mk('CAM-A', 'online'), mk('CAM-B', 'warn')];
      const render = () => ReactDOM.flushSync(() => root.render(React.createElement(MonitorPage, { nodes, activeAlerts: [], onSelectNode: () => {} })));
      const camTab = () => Array.from(container.querySelectorAll('button')).find(b => b.textContent.indexOf('攝影機') !== -1 && b.textContent.indexOf('僅顯示') === -1);
      const idOrder = () => Array.from(container.querySelectorAll('.font-mono.text-xs.font-semibold.text-white.tnum')).map(e => e.textContent);
      const grid = () => container.querySelector('.overflow-y-auto.scroll-thin');

      render();
      click(camTab());
      await settle();
      A('OPS-018 setup: initial rank order is warn(CAM-B) before online(CAM-A)', JSON.stringify(idOrder()) === JSON.stringify(['CAM-B', 'CAM-A']), idOrder());

      // --- FOCUS freeze: focusing a card must freeze order across a re-sort-worthy update ---
      // React attaches onFocus/onBlur via the native 'focusin'/'focusout'
      // events (which bubble), not 'focus'/'blur' (which don't) — a real
      // .focus() call is what actually exercises the delegated listener in
      // jsdom, exactly as a Tab keypress would in a real browser.
      const focusTarget = Array.from(container.querySelectorAll('*')).find(e => e.getAttribute && e.getAttribute('tabindex') === '0');
      A('OPS-018 setup: a focusable (tabindex=0) card exists in the grid', !!focusTarget);
      focusTarget.focus();
      await tick();
      nodes = [mk('CAM-A', 'critical'), mk('CAM-B', 'warn')]; // would flip order to A,B if NOT frozen
      render();
      await settle();
      A('OPS-018 focusing inside the grid freezes order across a status-rank-changing update', JSON.stringify(idOrder()) === JSON.stringify(['CAM-B', 'CAM-A']), idOrder());

      // --- blur (focus leaves the grid entirely) releases the freeze ---
      const outside = document.createElement('button');
      document.body.appendChild(outside);
      outside.focus(); // moves focus OUT of the grid, giving onBlur a real relatedTarget
      await tick();
      render();
      await settle();
      A('OPS-018 blurring out of the grid releases the freeze (resorts by rank)', JSON.stringify(idOrder()) === JSON.stringify(['CAM-A', 'CAM-B']), idOrder());
      document.body.removeChild(outside);

      // --- TOUCH freeze: a touchstart must freeze order the same way hover does ---
      nodes = [mk('CAM-A', 'critical'), mk('CAM-B', 'warn')];
      render();
      await settle();
      grid().dispatchEvent(new window.Event('touchstart', { bubbles: true }));
      await tick();
      nodes = [mk('CAM-A', 'critical'), mk('CAM-B', 'critical')];
      render();
      await settle();
      const orderDuringTouch = idOrder();
      nodes = [mk('CAM-A', 'online'), mk('CAM-B', 'critical')]; // would put B first if NOT frozen
      render();
      await settle();
      A('OPS-018 a touch interaction freezes order the same way mouse hover does', JSON.stringify(idOrder()) === JSON.stringify(orderDuringTouch), idOrder() + ' vs ' + JSON.stringify(orderDuringTouch));
      grid().dispatchEvent(new window.Event('touchend', { bubbles: true }));
    `,
  },

  // ------------------------------------------------------------- NEW-UX-001
  {
    name: 'NEW-UX-001 NodeCard no longer nests real <button>s inside a role="button" wrapper',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.SDPRS_API = { startWebcamStream: () => Promise.resolve({}), stopWebcamStream: () => Promise.resolve({}) };
      const node = { id: 'webcam_ux001', name: 't', type: 'webcam', status: 'online', upload: 2, heartbeat: 2, snoozeMin: 0, level: null };
      let selected = null;
      ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node, onSelect: (n) => { selected = n; }, nodeAlerts: [] })));
      await settle();
      const buttons = Array.from(container.querySelectorAll('button'));
      A('NEW-UX-001 setup: NodeCard renders at least one real <button> (the live-start control)', buttons.length > 0);
      const nested = buttons.some(b => b.closest('[role="button"]'));
      A('NEW-UX-001 no real <button> is a descendant of a role="button" wrapper (WCAG 4.1.2)', !nested);
      // Mouse click-to-open must still work from a non-button area of the tile.
      const tile = container.firstElementChild;
      click(tile);
      await settle();
      A('NEW-UX-001 clicking the tile (outside the action buttons) still opens the node', selected && selected.id === 'webcam_ux001', selected);
    `,
  },

  // ------------------------------------------------------------- NEW-UX-013
  {
    name: 'NEW-UX-013 live-view buttons meet the >=32px touch-target standard',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
${HLS_SHIM}
      try {
        window.SDPRS_API = {
          startWebcamStream: () => Promise.resolve({}),
          stopWebcamStream: () => Promise.resolve({}),
          renewWebcamStream: () => Promise.resolve({}),
          getWebcamPlaylist: () => Promise.resolve('${WITH_TS_SEGMENT.replace(/\n/g, '\\n')}'),
        };
        const node = { id: 'webcam_ux013', name: 't', type: 'webcam', status: 'online', upload: 2, heartbeat: 2, snoozeMin: 0, level: null };
        ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node, onSelect: () => {}, nodeAlerts: [] })));
        await settle();
        let btn = container.querySelector('button'); // only the 即時 button exists in 'off' state
        A('NEW-UX-013 setup: the 即時 (live-start) button renders', !!btn);
        A('NEW-UX-013 the 即時 button meets the >=32px min-height touch target', !!btn && (btn.className.indexOf('min-h-[32px]') !== -1 || btn.className.indexOf('h-8') !== -1), btn && btn.className);
        click(btn);
        await settle();
        A('NEW-UX-013 setup: the tile is live', !!container.querySelector('video'));
        btn = container.querySelector('button'); // only the LIVE-stop button exists in 'live' state
        A('NEW-UX-013 setup: the live-stop button renders', !!btn);
        A('NEW-UX-013 the live-stop button meets the >=32px min-height touch target', !!btn && (btn.className.indexOf('min-h-[32px]') !== -1 || btn.className.indexOf('h-8') !== -1), btn && btn.className);
      } finally {
${HLS_SHIM_RESTORE}
      }
    `,
  },

  // ------------------------------------------------------------- NEW-UX-026
  // SCOPE NOTE: full-text translation of "Webcam"/"Edge Cam"/"LIVE" was
  // attempted and reverted. render_tests.js (frozen — not in this lane's
  // editable file list) pins those exact literal strings: TEST_MONITOR
  // asserts BOTH the presence AND the cross-type ABSENCE of "Webcam"/"Edge
  // Cam", and TEST_MONITOR_LIVE uses the literal string "LIVE" as a
  // CLICK-TARGET SELECTOR (findBtn('LIVE')) to drive the stop button, not
  // merely as an assertion. Retranslating any of the three breaks an
  // existing suite this pass cannot touch. What IS fixed here: the raw
  // dingbat glyphs (▶, ●, ✕), which nothing in render_tests.js depends on.
  {
    name: 'NEW-UX-026 monitor.jsx drops raw dingbat glyphs for real icons',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
${HLS_SHIM}
      try {
        window.SDPRS_API = {
          startWebcamStream: () => Promise.resolve({}),
          stopWebcamStream: () => Promise.resolve({}),
          renewWebcamStream: () => Promise.resolve({}),
          getWebcamPlaylist: () => Promise.resolve('${WITH_TS_SEGMENT.replace(/\n/g, '\\n')}'),
        };
        const node = { id: 'webcam_ux026', name: 't', type: 'webcam', status: 'online', upload: 2, heartbeat: 2, snoozeMin: 0, level: null };
        ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node, onSelect: () => {}, nodeAlerts: [] })));
        await settle();
        let btn = container.querySelector('button');
        A('NEW-UX-026 the live-start button no longer uses the raw ▶ dingbat', btn.textContent.indexOf('▶') === -1, btn.textContent);
        A('NEW-UX-026 setup: the live-start button still opens (即時 kept, LIVE/Webcam untouched — see scope note)', btn.textContent.indexOf('即時') !== -1, btn.textContent);
        click(btn);
        await settle();
        btn = container.querySelector('button');
        A('NEW-UX-026 setup: the tile is live', !!container.querySelector('video'));
        A('NEW-UX-026 the live-stop button no longer uses the raw ● dingbat', btn.textContent.indexOf('●') === -1, btn.textContent);
        A('NEW-UX-026 the live-stop button no longer uses the raw ✕ dingbat', btn.textContent.indexOf('✕') === -1, btn.textContent);
        A('NEW-UX-026 the live-stop button still says 直播中 (translated from LIVE in Queue F)', btn.textContent.indexOf('直播中') !== -1, btn.textContent);
        A('NEW-UX-026 an icon element replaces the removed ✕ dingbat', !!btn.querySelector('svg'), btn.innerHTML);
      } finally {
${HLS_SHIM_RESTORE}
      }
    `,
  },

  // ---------------------------------------------------------------- OPS-035
  {
    name: 'OPS-035 the 30s warm-up state shows a visible spinner, not just tiny static text',
    target: 'pages/monitor.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.SDPRS_API = { startWebcamStream: () => Promise.resolve({}), stopWebcamStream: () => Promise.resolve({}), getWebcamPlaylist: () => Promise.resolve('') };
      const node = { id: 'webcam_ops035', name: 't', type: 'webcam', status: 'online', upload: 2, heartbeat: 2, snoozeMin: 0, level: null };
      ReactDOM.flushSync(() => root.render(React.createElement(NodeCard, { node, onSelect: () => {}, nodeAlerts: [] })));
      const btn = container.querySelector('button');
      click(btn);
      await settle();
      A('OPS-035 setup: the tile is in the loading/warm-up state', container.textContent.indexOf('連線中') !== -1, container.textContent.slice(0, 200));
      const spinner = container.querySelector('.animate-spin');
      A('OPS-035 a spinner element is visible during the warm-up wait', !!spinner);
    `,
  },
];
