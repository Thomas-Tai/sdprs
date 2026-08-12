// alerts.jsx (delta-audit) remediation suites.
// Findings: NEW-RT-002, NEW-UX-018, NEW-UX-006.

const ALERT_FIXTURE = {
  id: 'DA-001', node: 'G-01', type: 'glass_break', sev: 'critical', state: 'pending',
  ageSec: 45, message: '測試警報訊息',
  timeline: [{ t: '10:00:00', label: 'DETECTED', detail: '' }, { t: '10:00:05', label: 'UPLOADED', detail: '' }],
};
const NODE_FIXTURE = [{ id: 'G-01', name: '測試節點', type: 'camera', status: 'online', location: '測試位置', heartbeat: 5, upload: 5 }];

const RENDER_ALERTS_PAGE = `
  window.__SDPRS_OVERLAY_STACK = [];
  const alert = ${JSON.stringify(ALERT_FIXTURE)};
  const nodes = ${JSON.stringify(NODE_FIXTURE)};
  ReactDOM.flushSync(() => root.render(React.createElement(AlertsPage, {
    density: 'regular',
    selectedId: alert.id,
    setSelectedId: () => {},
    alerts: [alert],
    onAck: () => Promise.resolve(),
    onResolve: () => Promise.resolve(),
    onSnooze: () => Promise.resolve(),
    onRefresh: () => Promise.resolve(),
    onVisibleChange: () => {},
    ackedIds: new Set(),
    resolveNote: '',
    setResolveNote: () => {},
    busy: false,
    nodes: nodes,
    nodeHistory: {},
  })));
  await settle();
`;

module.exports = [
  // ------------------------------------------------------------- NEW-RT-002
  {
    name: 'NEW-RT-002 bulk-clear Escape is gated by open overlays',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${RENDER_ALERTS_PAGE}

      const cb = container.querySelector('input[type="checkbox"]');
      A('NEW-RT-002 setup: row checkbox renders', !!cb, container.innerHTML.slice(0, 200));
      click(cb);
      await settle();
      A('NEW-RT-002 setup: checking the row populates the bulk selection', container.textContent.indexOf('已選 1') !== -1, container.textContent.slice(0, 400));

      // An overlay (drawer/modal) is open on top — Escape belongs to IT, not
      // to this page's bulk-selection clear.
      window.__SDPRS_OVERLAY_STACK.push('fake-overlay-id');
      document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await settle();
      A('NEW-RT-002 Escape while an overlay is open does NOT wipe the bulk selection', container.textContent.indexOf('已選 1') !== -1, container.textContent.slice(0, 400));

      // Overlay closed — now Escape should clear the selection as designed.
      window.__SDPRS_OVERLAY_STACK.length = 0;
      document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      await settle();
      A('NEW-RT-002 Escape with no overlay open clears the bulk selection', container.textContent.indexOf('已選 1') === -1, container.textContent.slice(0, 400));
    `,
  },

  // ------------------------------------------------------------- NEW-UX-018
  {
    name: 'NEW-UX-018 tab strips carry ARIA tab semantics',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${RENDER_ALERTS_PAGE}

      const tablists = container.querySelectorAll('[role="tablist"]');
      A('NEW-UX-018 both tab strips expose role=tablist', tablists.length === 2, 'found ' + tablists.length);

      const tabs = container.querySelectorAll('[role="tab"]');
      A('NEW-UX-018 at least one role=tab button exists', tabs.length >= 2, 'found ' + tabs.length);

      const activeTabBtn = byText('button', '作用中');
      const historyTabBtn = byText('button', '歷史');
      A('NEW-UX-018 setup: 作用中/歷史 tab buttons render', !!activeTabBtn && !!historyTabBtn);
      A('NEW-UX-018 作用中 (active tab) has aria-selected=true', !!activeTabBtn && activeTabBtn.getAttribute('aria-selected') === 'true', activeTabBtn && activeTabBtn.getAttribute('aria-selected'));
      A('NEW-UX-018 歷史 (inactive tab) has aria-selected=false', !!historyTabBtn && historyTabBtn.getAttribute('aria-selected') === 'false', historyTabBtn && historyTabBtn.getAttribute('aria-selected'));

      const timelineBtn = byText('button', '事件時間軸');
      const nodeBtn = byText('button', '節點資訊');
      A('NEW-UX-018 setup: detail-strip tab buttons render', !!timelineBtn && !!nodeBtn);
      A('NEW-UX-018 事件時間軸 (active detail tab) has aria-selected=true', !!timelineBtn && timelineBtn.getAttribute('aria-selected') === 'true', timelineBtn && timelineBtn.getAttribute('aria-selected'));
      A('NEW-UX-018 節點資訊 (inactive detail tab) has aria-selected=false', !!nodeBtn && nodeBtn.getAttribute('aria-selected') === 'false', nodeBtn && nodeBtn.getAttribute('aria-selected'));
    `,
  },

  // ------------------------------------------------------------- NEW-UX-006
  {
    name: 'NEW-UX-006 floorplan uses theme-aware classes, not hardcoded dark fills / 7px labels',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      const nodes = ${JSON.stringify(NODE_FIXTURE)};
      ReactDOM.flushSync(() => root.render(React.createElement(Floorplan, { highlightNode: 'G-01', nodes })));
      await settle();

      const rects = Array.from(container.querySelectorAll('rect'));
      A('NEW-UX-006 setup: floorplan renders room rects', rects.length === 5, 'count=' + rects.length);
      const hasHardcodedFill = rects.some(r => r.getAttribute('fill') === 'rgba(30,41,59,0.4)');
      A('NEW-UX-006 room rects no longer carry the hardcoded dark inline fill', !hasHardcodedFill, rects.map(r => r.getAttribute('fill')).join('|'));
      const allRoomsClassed = rects.every(r => (r.getAttribute('class') || '').split(' ').indexOf('floorplan-room') !== -1);
      A('NEW-UX-006 room rects use the floorplan-room class', allRoomsClassed, rects.map(r => r.getAttribute('class')).join('|'));

      const texts = Array.from(container.querySelectorAll('text'));
      const has7 = texts.some(t => t.getAttribute('font-size') === '7');
      A('NEW-UX-006 no label uses the old 7px fontSize', !has7, texts.map(t => t.getAttribute('font-size')).join('|'));
      const roomLabels = texts.filter(t => (t.getAttribute('class') || '') === 'floorplan-label');
      A('NEW-UX-006 room labels render with floorplan-label class', roomLabels.length === 5, 'count=' + roomLabels.length);
      A('NEW-UX-006 room labels bumped to fontSize=11', roomLabels.length > 0 && roomLabels.every(t => t.getAttribute('font-size') === '11'), roomLabels.map(t => t.getAttribute('font-size')).join('|'));

      const pinLabel = texts.find(t => (t.getAttribute('class') || '') === 'floorplan-label-pin');
      A('NEW-UX-006 highlighted-pin id label uses floorplan-label-pin class (not inline near-white fill)', !!pinLabel, texts.map(t => t.outerHTML).join('|'));
    `,
  },
];
