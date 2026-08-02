// Section-5 remediation — frozen contract tests.
// Findings: ALR-002, ALR-001+ALR-011, WXA-003, WXA-005, AUD-006, HDO-004,
//           ALR-003, ALR-004, ALR-005, ALR-006, ALR-008, ALR-009, ALR-010,
//           ALR-012, WXA-006, HDO-005, HDO-006, AUD-004, AUD-005.

// ---- shared fixtures ----

const S5_ALERT = {
  id: 'S5-001', node: 'G-01', type: 'glass_break', sev: 'critical', state: 'pending',
  ageSec: 45, message: 'S5 測試警報', ackBy: null,
  timeline: [{ t: '10:00:00', label: 'DETECTED', detail: '' }, { t: '10:00:05', label: 'UPLOADED', detail: '' }],
};
const S5_NODE = [{ id: 'G-01', name: 'S5 節點', type: 'camera', status: 'online', location: 'S5 位置', heartbeat: 5, upload: 5, snoozeMin: 0 }];

const S5_RENDER_ALERTS = `
  window.__SDPRS_OVERLAY_STACK = [];
  window.__SDPRS_FLASHED_ALERT_IDS = new Set();
  window.HISTORY_ALERTS = [];
  window.ALERTS = [];
  window.SDPRS_USER = 'op1';
  const alert = ${JSON.stringify(S5_ALERT)};
  const nodes = ${JSON.stringify(S5_NODE)};
  const renderAlerts = (overrides) => ReactDOM.flushSync(() => root.render(React.createElement(AlertsPage, Object.assign({
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
  }, overrides || {}))));
`;

module.exports = [

  // ================================================================ BUG 1: ALR-002
  {
    name: 'ALR-002: snooze menu stays open when busy and items are disabled',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${S5_RENDER_ALERTS}
      // Stub onSnooze that mimics app.jsx's busy guard — throws on busy/not-found.
      let snoozeCalls = [];
      const busySnooze = (id, mins) => { snoozeCalls.push([id, mins]); throw new Error('busy'); };
      renderAlerts({ busy: true, onSnooze: busySnooze });
      await settle();

      // Open the SnoozeMenu — the trigger button should be disabled when busy.
      const triggerBtn = Array.from(container.querySelectorAll('button')).find(b => b.getAttribute('aria-haspopup') === 'menu');
      A('ALR-002 snooze trigger button renders', !!triggerBtn, 'no trigger found');
      A('ALR-002 snooze trigger is disabled when busy', triggerBtn && triggerBtn.disabled === true, triggerBtn && String(triggerBtn.disabled));

      // Force-open the menu to test item-level disabled state.
      // (Even though trigger is disabled, the menu should still protect against stale open.)
      // Render with busy=false but onSnooze that throws (simulating busy guard race).
      snoozeCalls = [];
      renderAlerts({ busy: false, onSnooze: busySnooze });
      await settle();

      const trigger2 = Array.from(container.querySelectorAll('button')).find(b => b.getAttribute('aria-haspopup') === 'menu');
      click(trigger2);
      await settle();

      const menuItems = container.querySelectorAll('[role="menuitem"]');
      A('ALR-002 menu opens and shows duration items', menuItems.length === 3, 'items: ' + menuItems.length);

      // Click the first duration item — onSnooze throws → menu must stay open.
      if (menuItems.length > 0) {
        click(menuItems[0]);
        await settle();
      }
      const menuStillOpen = container.querySelectorAll('[role="menuitem"]').length > 0;
      A('ALR-002 menu stays open after onSnooze throws (busy guard)', menuStillOpen, 'menu closed when it should stay open');
    `,
  },

  // ================================================================ BUG 2: ALR-001 + ALR-011
  {
    name: 'ALR-001: peer banner does NOT fire for the operator own action',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${S5_RENDER_ALERTS}
      window.SDPRS_USER = 'op1';
      // Start with a pending alert selected.
      const baseAlert = Object.assign({}, ${JSON.stringify(S5_ALERT)}, { state: 'pending' });
      renderAlerts({ alerts: [baseAlert], selectedId: baseAlert.id });
      await settle();

      // Verify no peer-change banner initially.
      A('ALR-001 setup: no peer banner initially', container.textContent.indexOf('可能已由其他操作人員') === -1, '');

      // Simulate: the SAME operator acks the alert (state → acknowledged, ackBy = SDPRS_USER).
      const ackedAlert = Object.assign({}, baseAlert, { state: 'acknowledged', ackBy: 'op1' });
      renderAlerts({ alerts: [ackedAlert], selectedId: ackedAlert.id });
      await settle();

      A('ALR-001 peer banner NOT shown for own action (ackBy === SDPRS_USER)', container.textContent.indexOf('可能已由其他操作人員') === -1, container.textContent.slice(0, 300));
    `,
  },
  {
    name: 'ALR-011: peer banner re-arms for a second distinct peer transition',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${S5_RENDER_ALERTS}
      window.SDPRS_USER = 'op1';
      // Start with pending alert.
      const baseAlert = Object.assign({}, ${JSON.stringify(S5_ALERT)}, { state: 'pending' });
      renderAlerts({ alerts: [baseAlert], selectedId: baseAlert.id });
      await settle();

      // Peer change A: state → acknowledged by someone else.
      const peerAcked = Object.assign({}, baseAlert, { state: 'acknowledged', ackBy: 'peer-op' });
      renderAlerts({ alerts: [peerAcked], selectedId: peerAcked.id });
      await settle();
      A('ALR-011 setup: first peer change shows banner', container.textContent.indexOf('可能已由其他操作人員') !== -1, '');

      // Dismiss the banner.
      const dismissBtn = Array.from(container.querySelectorAll('button')).find(b => b.textContent.trim() === '知道了');
      if (dismissBtn) click(dismissBtn);
      await settle();
      A('ALR-011 setup: banner dismissed', container.textContent.indexOf('可能已由其他操作人員') === -1, '');

      // Peer change B: state → resolved by someone else.
      const peerResolved = Object.assign({}, peerAcked, { state: 'resolved', resBy: 'peer-op' });
      renderAlerts({ alerts: [peerResolved], selectedId: peerResolved.id });
      await settle();
      A('ALR-011 banner re-appears for second distinct peer transition', container.textContent.indexOf('可能已由其他操作人員') !== -1, container.textContent.slice(0, 300));
    `,
  },

  // ================================================================ BUG 3: WXA-003
  {
    name: 'WXA-003: peak badges show true peak 0 not axis floor 1',
    target: 'pages/weather.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.WEATHER = {
        available: true, source: 'Open-Meteo', station: 'Test', stale: false,
        temp: 25, humidity: 60, pressure: 1013, visibility: 10,
        wind: { speed: 5, degree: 90, dir: 'E', gust: 10 },
        rain: { day: 0, now: null },
        lightning: { count: 0, nearest: null },
        typhoon: null,
        fetchedAt: new Date().toISOString(),
        forecast: [
          { h: '10', rain: 0, wind: 0 },
          { h: '11', rain: 0, wind: 0 },
          { h: '12', rain: 0, wind: 0 },
        ],
        sources: {},
      };
      ReactDOM.flushSync(() => root.render(React.createElement(WeatherPage, { showToast: () => {}, onRefresh: () => Promise.resolve() })));
      await settle();

      const text = container.textContent;
      // The peak badges must show 0, not 1.
      const rainBadge = text.indexOf('峰值雨量');
      const windBadge = text.indexOf('峰值風速');
      A('WXA-003 peak rain badge area renders', rainBadge !== -1, '');
      A('WXA-003 peak wind badge area renders', windBadge !== -1, '');

      // Extract the badge content — it should contain "0" not "1".
      // The badge text pattern is "峰值雨量 0 mm/h" and "峰值風速 0 km/h".
      const badgeSection = text.slice(rainBadge, windBadge + 30);
      A('WXA-003 rain peak badge shows 0 not 1', badgeSection.indexOf('0 mm/h') !== -1 && badgeSection.indexOf('1 mm/h') === -1, badgeSection.slice(0, 60));
      const windSection = text.slice(windBadge, windBadge + 30);
      A('WXA-003 wind peak badge shows 0 not 1', windSection.indexOf('0 km/h') !== -1 && windSection.indexOf('1 km/h') === -1, windSection.slice(0, 60));
    `,
  },

  // ================================================================ BUG 4: WXA-005
  {
    name: 'WXA-005: out-of-range lat/lon blocks Save and shows error',
    target: 'pages/weather.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.WEATHER = {
        available: true, source: 'Open-Meteo', station: 'Test', stale: false,
        temp: 25, humidity: 60, pressure: 1013, visibility: 10,
        wind: { speed: 5, degree: 90, dir: 'E', gust: 10 },
        rain: { day: 0, now: null },
        lightning: { count: 0, nearest: null },
        typhoon: null,
        fetchedAt: new Date().toISOString(),
        forecast: [],
        sources: {},
      };
      window.SDPRS_API = Object.assign(window.SDPRS_API || {}, {
        getWeatherConfig: () => Promise.resolve({ site_lat: null, site_lon: null, smg_station: '', hko_station: '', fallback_provider: 'both' }),
        setWeatherConfig: () => Promise.resolve(),
        refreshWeather: () => Promise.resolve(),
        listSmgStations: () => Promise.resolve({ stations: [] }),
        listHkoStations: () => Promise.resolve({ stations: [] }),
      });
      ReactDOM.flushSync(() => root.render(React.createElement(WeatherPage, { showToast: () => {}, onRefresh: () => Promise.resolve() })));
      await settle();

      // Open the settings pane.
      const settingsBtn = Array.from(container.querySelectorAll('button')).find(b => b.textContent.indexOf('天氣資料來源設定') !== -1);
      if (settingsBtn) click(settingsBtn);
      await settle();

      // Type out-of-range lat.
      const latInput = container.querySelector('input[placeholder*="澳門"]');
      A('WXA-005 setup: lat input found', !!latInput, '');
      if (latInput) {
        setInput(latInput, '999');
        await settle();
      }
      // Type a valid lon.
      const lonInput = container.querySelector('input[placeholder*="113.55"]');
      if (lonInput) {
        setInput(lonInput, '113.5');
        await settle();
      }

      // Save should be disabled and a range error should show.
      const saveBtn = Array.from(container.querySelectorAll('button')).find(b => b.textContent.indexOf('儲存設定') !== -1);
      A('WXA-005 Save button is disabled for out-of-range lat', saveBtn && saveBtn.disabled === true, saveBtn && String(saveBtn.disabled));
      A('WXA-005 range error message renders', container.textContent.indexOf('緯度') !== -1 && (container.textContent.indexOf('範圍') !== -1 || container.textContent.indexOf('-90') !== -1 || container.textContent.indexOf('90') !== -1), container.textContent.slice(0, 500));
    `,
  },

  // ================================================================ BUG 5: AUD-006
  {
    name: 'AUD-006: duplicate row keys do not collide',
    target: 'pages/audit.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      // Two rows with identical ts/by/action/target — must both render.
      const dupRows = [
        { ts: 1000, t: '10:00:00', by: 'op1', action: 'ACKNOWLEDGE', target: 'alert-1', detail: null },
        { ts: 1000, t: '10:00:00', by: 'op1', action: 'ACKNOWLEDGE', target: 'alert-1', detail: null },
      ];
      window.AUDIT = { rows: dupRows, truncated: false, totalAvailable: 2, forbidden: false };
      ReactDOM.flushSync(() => root.render(React.createElement(AuditPage, { auditLog: dupRows })));
      await settle();

      // Both rows should render as separate <tr> elements.
      const trs = container.querySelectorAll('tbody tr');
      // Subtract the empty-state row if present.
      const dataRows = Array.from(trs).filter(tr => tr.querySelectorAll('td').length >= 4 && !tr.querySelector('[colspan]'));
      A('AUD-006 both duplicate-key rows render as separate DOM rows', dataRows.length === 2, 'data rows: ' + dataRows.length);
    `,
  },

  // ================================================================ BUG 6: HDO-004
  {
    name: 'HDO-004: draft restore does NOT mark dirty when draft === server text',
    target: 'pages/handover.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      const serverText = '這是伺服器上的交接備註內容';
      // Set localStorage draft to EXACTLY the server text.
      window.SDPRS_USER = 'op1';
      const key = 'sdprs.handover.draft.op1';
      try { window.localStorage.setItem(key, serverText); } catch(_) {}
      window.HANDOVER = { current: serverText, pinned: null, history: [], updatedAt: 'tok1' };
      window.SHIFT_SUMMARY = {};
      ReactDOM.flushSync(() => root.render(React.createElement(HandoverPage)));
      await settle();

      // Should NOT show the dirty indicator "未儲存變更".
      A('HDO-004 no dirty indicator when draft === server text', container.textContent.indexOf('未儲存變更') === -1, container.textContent.slice(0, 400));

      // Cleanup.
      try { window.localStorage.removeItem(key); } catch(_) {}
    `,
  },

  // ================================================================ UX: ALR-003
  {
    name: 'ALR-003: checkboxes hidden on history tab (resolved rows)',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${S5_RENDER_ALERTS}
      window.HISTORY_ALERTS = [
        Object.assign({}, ${JSON.stringify(S5_ALERT)}, { id: 'H1', state: 'resolved', resBy: 'op1', resAt: '10:05:00' }),
      ];
      renderAlerts({ alerts: [], selectedId: null });
      await settle();

      // Switch to history tab.
      const historyTab = Array.from(container.querySelectorAll('[role="tab"]')).find(b => b.textContent.indexOf('歷史') !== -1);
      if (historyTab) click(historyTab);
      await settle();

      // On the history tab, row checkboxes should be absent.
      const cbs = container.querySelectorAll('input[type="checkbox"]');
      A('ALR-003 no checkboxes on history tab', cbs.length === 0, 'found ' + cbs.length + ' checkboxes');
    `,
  },

  // ================================================================ UX: ALR-004
  {
    name: 'ALR-004: history tab shows truncation banner when capped',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${S5_RENDER_ALERTS}
      const histAlerts = [];
      for (let i = 0; i < 5; i++) histAlerts.push(Object.assign({}, ${JSON.stringify(S5_ALERT)}, { id: 'HA-' + i, state: 'resolved' }));
      histAlerts.truncated = true;
      histAlerts.totalAvailable = 50;
      window.HISTORY_ALERTS = histAlerts;
      renderAlerts({ alerts: [], selectedId: null });
      await settle();

      const historyTab = Array.from(container.querySelectorAll('[role="tab"]')).find(b => b.textContent.indexOf('歷史') !== -1);
      if (historyTab) click(historyTab);
      await settle();

      A('ALR-004 truncation banner renders on history tab', container.textContent.indexOf('已達顯示上限') !== -1, container.textContent.slice(0, 400));
    `,
  },

  // ================================================================ UX: ALR-005
  {
    name: 'ALR-005: empty history tab shows history-appropriate copy',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${S5_RENDER_ALERTS}
      window.HISTORY_ALERTS = [];
      renderAlerts({ alerts: [], selectedId: null });
      await settle();

      const historyTab = Array.from(container.querySelectorAll('[role="tab"]')).find(b => b.textContent.indexOf('歷史') !== -1);
      if (historyTab) click(historyTab);
      await settle();

      A('ALR-005 empty history does NOT show active-state copy', container.textContent.indexOf('目前沒有作用中的警報') === -1, container.textContent.slice(0, 400));
    `,
  },

  // ================================================================ a11y: ALR-006
  {
    name: 'ALR-006: unread dot has accessible name',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${S5_RENDER_ALERTS}
      const unseenAlert = Object.assign({}, ${JSON.stringify(S5_ALERT)}, { seen: false, state: 'pending' });
      renderAlerts({ alerts: [unseenAlert], selectedId: null });
      await settle();

      // The unread dot should have an accessible name "未閱".
      const unreadEl = container.querySelector('[aria-label*="未閱"]') ||
        Array.from(container.querySelectorAll('.sr-only, [class*="sr-only"]')).find(e => e.textContent.indexOf('未閱') !== -1);
      A('ALR-006 unread dot has accessible name 未閱', !!unreadEl, container.innerHTML.slice(0, 500));
    `,
  },

  // ================================================================ perf: ALR-008
  {
    name: 'ALR-008: filtered output correct across tab/filter/search changes',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${S5_RENDER_ALERTS}
      const alerts = [
        Object.assign({}, ${JSON.stringify(S5_ALERT)}, { id: 'A1', sev: 'critical', state: 'pending', node: 'G-01' }),
        Object.assign({}, ${JSON.stringify(S5_ALERT)}, { id: 'A2', sev: 'warn', state: 'pending', node: 'G-02' }),
        Object.assign({}, ${JSON.stringify(S5_ALERT)}, { id: 'A3', sev: 'info', state: 'acknowledged', node: 'G-01' }),
      ];
      renderAlerts({ alerts: alerts, selectedId: null });
      await settle();

      // Default: all active (non-resolved) shown.
      const countRows = () => container.querySelectorAll('[role="button"][aria-pressed]').length;
      A('ALR-008 setup: 3 active alerts render', countRows() === 3, 'rows: ' + countRows());

      // Filter by critical.
      const critChip = Array.from(container.querySelectorAll('button')).find(b => b.textContent.indexOf('嚴重') !== -1);
      if (critChip) click(critChip);
      await settle();
      A('ALR-008 severity filter narrows to critical only', countRows() === 1, 'rows: ' + countRows());

      // Clear filter.
      const allChip = Array.from(container.querySelectorAll('button')).find(b => b.textContent.trim().startsWith('全部'));
      if (allChip) click(allChip);
      await settle();
      A('ALR-008 clearing filter restores all rows', countRows() === 3, 'rows: ' + countRows());
    `,
  },

  // ================================================================ perf: ALR-009
  {
    name: 'ALR-009: sibling count badges correct with precomputed map',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${S5_RENDER_ALERTS}
      // 3 alerts on same node → each should show +2 sibling badge.
      const alerts = [
        Object.assign({}, ${JSON.stringify(S5_ALERT)}, { id: 'S1', node: 'G-01', state: 'pending' }),
        Object.assign({}, ${JSON.stringify(S5_ALERT)}, { id: 'S2', node: 'G-01', state: 'pending' }),
        Object.assign({}, ${JSON.stringify(S5_ALERT)}, { id: 'S3', node: 'G-01', state: 'acknowledged' }),
      ];
      renderAlerts({ alerts: alerts, selectedId: null });
      await settle();

      // Each row should have a "+2" sibling badge (3 on same node, minus self = 2).
      const badges = Array.from(container.querySelectorAll('span')).filter(s => s.textContent.indexOf('+2') !== -1 && s.title && s.title.indexOf('同節點') !== -1);
      A('ALR-009 sibling badges show correct count (+2) for co-located alerts', badges.length === 3, 'badges: ' + badges.length);
    `,
  },

  // ================================================================ UX: ALR-010
  {
    name: 'ALR-010: unsnooze menuitem renders for currently-snoozed node',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${S5_RENDER_ALERTS}
      const snoozedNode = ${JSON.stringify(S5_NODE)}.map(n => Object.assign({}, n, { snoozeMin: 30 }));
      let unsnoozeCalled = false;
      renderAlerts({ nodes: snoozedNode, onUnsnooze: () => { unsnoozeCalled = true; return Promise.resolve(); } });
      await settle();

      // Open the SnoozeMenu.
      const trigger = Array.from(container.querySelectorAll('button')).find(b => b.getAttribute('aria-haspopup') === 'menu');
      if (trigger) click(trigger);
      await settle();

      // Should show an unsnooze menuitem "解除靜音".
      const unsnoozeItem = Array.from(container.querySelectorAll('[role="menuitem"]')).find(m => m.textContent.indexOf('解除靜音') !== -1);
      A('ALR-010 unsnooze menuitem renders for snoozed node', !!unsnoozeItem, 'menu items: ' + container.querySelectorAll('[role="menuitem"]').length);

      if (unsnoozeItem) {
        click(unsnoozeItem);
        await settle();
        A('ALR-010 clicking unsnooze calls onUnsnooze handler', unsnoozeCalled, '');
      }
    `,
  },

  // ================================================================ UX: ALR-012
  {
    name: 'ALR-012: bulk bar has flex-wrap for narrow screens',
    target: 'pages/alerts.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      ${S5_RENDER_ALERTS}
      renderAlerts({ alerts: [${JSON.stringify(S5_ALERT)}], selectedId: null });
      await settle();

      // Check a row to trigger the bulk bar.
      const cb = container.querySelector('input[type="checkbox"]');
      if (cb) click(cb);
      await settle();

      // Find the bulk bar container — it contains "已選" text and has
      // flex-wrap directly (not an ancestor that merely inherits the text).
      const bulkBar = Array.from(container.querySelectorAll('div')).find(d => d.textContent.indexOf('已選') !== -1 && d.className.indexOf('flex-wrap') !== -1);
      A('ALR-012 bulk bar renders with flex-wrap', !!bulkBar, Array.from(container.querySelectorAll('div')).filter(d => d.textContent.indexOf('已選') !== -1).map(d => d.className).join(' || '));
    `,
  },

  // ================================================================ a11y: WXA-006
  {
    name: 'WXA-006: WindArrow svg has role="img" with aria-label',
    target: 'pages/weather.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.WEATHER = {
        available: true, source: 'Test', station: 'Test', stale: false,
        temp: 25, humidity: 60, pressure: 1013, visibility: 10,
        wind: { speed: 10, degree: 180, dir: 'S', gust: 20 },
        rain: { day: 5, now: 2 },
        lightning: { count: 0, nearest: null },
        typhoon: null,
        fetchedAt: new Date().toISOString(),
        forecast: [],
        sources: {},
      };
      ReactDOM.flushSync(() => root.render(React.createElement(WeatherPage, { showToast: () => {}, onRefresh: () => Promise.resolve() })));
      await settle();

      const windSvg = container.querySelector('svg[aria-label*="風向"]');
      A('WXA-006 WindArrow svg with aria-label exists', !!windSvg, '');
      A('WXA-006 WindArrow svg has role="img"', windSvg && windSvg.getAttribute('role') === 'img', windSvg && windSvg.getAttribute('role'));
    `,
  },

  // ================================================================ UX: HDO-005
  {
    name: 'HDO-005: confirm message does not promise unreachable preview',
    target: 'pages/handover.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.SDPRS_USER = 'op1';
      window.HANDOVER = { current: 'server text', pinned: null, history: [], updatedAt: 'tok1' };
      window.SHIFT_SUMMARY = {};
      // Clear any draft.
      try { window.localStorage.removeItem('sdprs.handover.draft.op1'); } catch(_) {}
      ReactDOM.flushSync(() => root.render(React.createElement(HandoverPage)));
      await settle();

      // The confirm copy "取消可先預覽對方版本" should not appear.
      A('HDO-005 confirm copy does not promise a preview that cancel does not provide', container.textContent.indexOf('取消可先預覽對方版本') === -1, container.textContent.slice(0, 400));
    `,
  },

  // ================================================================ UX: HDO-006
  {
    name: 'HDO-006: layout does not reserve dead history column',
    target: 'pages/handover.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.SDPRS_USER = 'op1';
      window.HANDOVER = { current: 'test', pinned: null, history: [], updatedAt: 'tok1' };
      window.SHIFT_SUMMARY = {};
      try { window.localStorage.removeItem('sdprs.handover.draft.op1'); } catch(_) {}
      ReactDOM.flushSync(() => root.render(React.createElement(HandoverPage)));
      await settle();

      // The grid should not have the 2fr_1fr column layout.
      const gridEl = container.querySelector('[class*="grid-cols"]');
      const gridClass = gridEl ? gridEl.className : '';
      A('HDO-006 layout does not reserve 1fr dead column (no 2fr_1fr grid)', gridClass.indexOf('2fr_1fr') === -1 && gridClass.indexOf('2fr 1fr') === -1, gridClass.slice(0, 200));
    `,
  },

  // ================================================================ UX: AUD-004
  {
    name: 'AUD-004: audit timestamps include year for prior-year rows',
    target: 'pages/audit.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      // A row from 2025 — must show the year.
      const priorYearTs = new Date(2025, 5, 15, 14, 30, 0).getTime();
      const rows = [
        { ts: priorYearTs, t: '14:30:00', by: 'op1', action: 'ACKNOWLEDGE', target: 'alert-1', detail: null },
      ];
      ReactDOM.flushSync(() => root.render(React.createElement(AuditPage, { auditLog: rows })));
      await settle();

      const td = container.querySelector('tbody td');
      const cellText = td ? td.textContent : '';
      A('AUD-004 prior-year timestamp includes year 2025', cellText.indexOf('2025') !== -1, 'cell: ' + cellText);
    `,
  },

  // ================================================================ UX: AUD-005
  {
    name: 'AUD-005: meOnly with unknown user shows specific empty message',
    target: 'pages/audit.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.SDPRS_USER = '';
      const rows = [
        { ts: Date.now(), t: '10:00:00', by: 'op1', action: 'ACKNOWLEDGE', target: 'alert-1', detail: null },
      ];
      ReactDOM.flushSync(() => root.render(React.createElement(AuditPage, { auditLog: rows })));
      await settle();

      // Enable meOnly.
      const meBtn = Array.from(container.querySelectorAll('button')).find(b => b.textContent.indexOf('本班') !== -1);
      if (meBtn) click(meBtn);
      await settle();

      A('AUD-005 specific unknown-user empty message renders', container.textContent.indexOf('無法辨識目前使用者') !== -1, container.textContent.slice(0, 400));
    `,
  },
];
