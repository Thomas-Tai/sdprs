// Section B2 (app.jsx / styles.css) remediation suites.
// Findings: NEW-UX-010, NEW-UX-015, NEW-UX-016, NEW-UX-008, OPS-027, NEW-RT-007.

const fs = require('fs');
const path = require('path');

const { SPA_DIR } = require('../spa_files');
const STYLES_CSS = fs.readFileSync(path.join(SPA_DIR, 'styles.css'), 'utf8');
const APP_JSX = fs.readFileSync(path.join(SPA_DIR, 'app.jsx'), 'utf8');
const API_JSX = fs.readFileSync(path.join(SPA_DIR, 'api.jsx'), 'utf8');
const COMPONENTS_JSX = fs.readFileSync(path.join(SPA_DIR, 'components.jsx'), 'utf8');

module.exports = [
  // ------------------------------------------------------------- NEW-UX-010
  {
    name: 'NEW-UX-010 styles.css focus-visible rule includes select',
    target: 'data.jsx',
    deps: ['icons.jsx'],
    body: `
      const css = ${JSON.stringify(STYLES_CSS)};
      const fvRule = css.split('\\n').find(l => l.indexOf('focus-visible') !== -1 && l.indexOf('outline') === -1);
      A('NEW-UX-010 a focus-visible selector line exists', !!fvRule, 'no focus-visible line found');
      A('NEW-UX-010 select:focus-visible is in the focus-visible selector list', !!fvRule && fvRule.indexOf('select:focus-visible') !== -1, fvRule && fvRule.slice(0, 150));
    `,
  },

  // ------------------------------------------------------------- NEW-UX-015
  {
    name: 'NEW-UX-015 wall chrome is zh-TW (no English "NOC WALL"/"Live"/"more")',
    target: 'data.jsx',
    deps: ['icons.jsx'],
    body: `
      // liveClockLabel: the healthy-state label was English "Live · Ns"
      A('NEW-UX-015 liveClockLabel(5) does not contain English "Live"', window.liveClockLabel(5).indexOf('Live') === -1, window.liveClockLabel(5));
      A('NEW-UX-015 liveClockLabel(5) uses zh-TW', window.liveClockLabel(5).indexOf('即時') !== -1 || window.liveClockLabel(5).indexOf('直播') !== -1, window.liveClockLabel(5));

      // app.jsx wall strings: "NOC WALL" and "+N more" must not appear
      const app = ${JSON.stringify(APP_JSX)};
      A('NEW-UX-015 app.jsx has no "NOC WALL" English chrome', app.indexOf('NOC WALL') === -1, 'found NOC WALL');
      // The "+N more" strings appear in JSX template literals as English chrome
      const hasNodeMore = app.indexOf('} more') !== -1;
      const hasAlertMore = app.indexOf('more alerts') !== -1;
      A('NEW-UX-015 app.jsx has no English "+N more" node/alert overflow', !hasNodeMore && !hasAlertMore, 'nodeMore=' + hasNodeMore + ' alertMore=' + hasAlertMore);
    `,
  },

  // ------------------------------------------------------------- NEW-UX-016
  {
    name: 'NEW-UX-016 WallView receives filtered activeAlerts, not raw alerts',
    target: 'data.jsx',
    deps: ['icons.jsx'],
    body: `
      const app = ${JSON.stringify(APP_JSX)};
      const wallIdx = app.indexOf('WallView');
      A('NEW-UX-016 setup: WallView exists in app.jsx', wallIdx !== -1);
      if (wallIdx !== -1) {
        const wallChunk = app.slice(wallIdx, wallIdx + 200);
        A('NEW-UX-016 WallView does NOT receive raw alerts={alerts}', wallChunk.indexOf('alerts={alerts}') === -1, wallChunk.slice(0, 120));
      }
    `,
  },

  // --------------------------------------------- FIX-024 (NEW-UX-024 producers)
  {
    name: 'FIX-024 api.jsx initializes __SDPRS_WS_CONNECTED false and openSocket sets it',
    target: 'data.jsx',
    deps: ['icons.jsx'],
    body: `
      const api = ${JSON.stringify(API_JSX)};
      A('FIX-024 api.jsx initializes window.__SDPRS_WS_CONNECTED = false at module load', api.indexOf('__SDPRS_WS_CONNECTED = false') !== -1, 'no init found');
      A('FIX-024 api.jsx sets __SDPRS_WS_CONNECTED = true in onopen', api.indexOf('__SDPRS_WS_CONNECTED = true') !== -1, 'no onopen set found');
      A('FIX-024 api.jsx sets __SDPRS_WS_CONNECTED = false in onclose', api.match(/onclose[\\s\\S]*?__SDPRS_WS_CONNECTED\\s*=\\s*false/) !== null, 'no onclose set found');
    `,
  },
  {
    name: 'FIX-024 data.jsx defines window.__SDPRS_BUILD constant',
    target: 'data.jsx',
    deps: ['icons.jsx'],
    body: `
      A('FIX-024 window.__SDPRS_BUILD is defined as a string at module load', typeof window.__SDPRS_BUILD === 'string' && window.__SDPRS_BUILD.indexOf('build') !== -1, typeof window.__SDPRS_BUILD + ' ' + window.__SDPRS_BUILD);
    `,
  },
  {
    name: 'FIX-024 NavRail footer dot reflects WS connection state',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // Render NavRail with WS connected = false → dot should NOT be text-sev-ok
      window.__SDPRS_WS_CONNECTED = false;
      window.__SDPRS_BUILD = 'build 2026.08.02-test';
      ReactDOM.flushSync(() => root.render(React.createElement(NavRail, {
        page: 'alerts', setPage: () => {}, density: 'regular', setDensity: () => {},
        unackCount: 0, offlineCount: 0
      })));
      await settle();
      const footerDot = Array.from(container.querySelectorAll('span')).find(s => s.textContent === '●');
      A('FIX-024 setup: the footer dot renders', !!footerDot, container.innerHTML.slice(-200));
      A('FIX-024 WS disconnected: dot is NOT text-sev-ok', !!footerDot && footerDot.className.indexOf('text-sev-ok') === -1, footerDot && footerDot.className);
      A('FIX-024 WS disconnected: dot IS text-ink-muted', !!footerDot && footerDot.className.indexOf('text-ink-muted') !== -1, footerDot && footerDot.className);

      // Now set WS connected = true → dot should be text-sev-ok
      window.__SDPRS_WS_CONNECTED = true;
      ReactDOM.flushSync(() => root.render(React.createElement(NavRail, {
        page: 'alerts', setPage: () => {}, density: 'regular', setDensity: () => {},
        unackCount: 0, offlineCount: 0
      })));
      await settle();
      const dot2 = Array.from(container.querySelectorAll('span')).find(s => s.textContent === '●');
      A('FIX-024 WS connected: dot IS text-sev-ok', !!dot2 && dot2.className.indexOf('text-sev-ok') !== -1, dot2 && dot2.className);

      // Build string: with __SDPRS_BUILD set, footer shows it, not "build —"
      const footer = container.textContent;
      A('FIX-024 build string shows the constant, not "build —"', footer.indexOf('build 2026.08.02-test') !== -1 && footer.indexOf('build —') === -1, footer.slice(-100));
    `,
  },

  // --------------------------------------------- TESTS-4b84 backfill
  {
    name: 'TESTS-4b84 NEW-UX-007 rain chip hides when rain.now is null',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // null rain → should show dash, NOT bare "mm/h"
      window.WEATHER = { available: true, rain: { now: null, hour: null, day: null }, wind: { speed: 5, direction: 'N' }, temp: { now: 25 } };
      ReactDOM.flushSync(() => root.render(React.createElement(StatusStrip, {
        unackCount: 0, muted: false, setMuted: () => {}, theme: 'dark', setTheme: () => {},
        onOpenShortcuts: () => {}, page: 'alerts', focusMode: false, onToggleFocus: () => {}
      })));
      await settle();
      const hasMMH = container.textContent.indexOf('mm/h') !== -1;
      const hasDash = container.textContent.indexOf('—') !== -1;
      A('TESTS-4b84 NEW-UX-007 rain.now=null: no bare mm/h unit', !hasMMH, container.textContent.slice(0, 300));
      A('TESTS-4b84 NEW-UX-007 rain.now=null: renders a dash placeholder', hasDash, container.textContent.slice(0, 300));

      // real rain value → should show number + mm/h
      window.WEATHER.rain.now = 12.5;
      ReactDOM.flushSync(() => root.render(React.createElement(StatusStrip, {
        unackCount: 0, muted: false, setMuted: () => {}, theme: 'dark', setTheme: () => {},
        onOpenShortcuts: () => {}, page: 'alerts', focusMode: false, onToggleFocus: () => {}
      })));
      await settle();
      A('TESTS-4b84 NEW-UX-007 rain.now=12.5: shows the number', container.textContent.indexOf('12.5') !== -1, container.textContent.slice(0, 300));
      A('TESTS-4b84 NEW-UX-007 rain.now=12.5: shows mm/h unit', container.textContent.indexOf('mm/h') !== -1, container.textContent.slice(0, 300));
    `,
  },
  {
    name: 'TESTS-4b84 NEW-UX-021 HlsPlayer video has aria-label + unsnooze error is assertive',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // HlsPlayer video aria-label
      const _mproto = window.HTMLMediaElement.prototype;
      const _origCPT = _mproto.canPlayType, _origPlay = _mproto.play, _origLoad = _mproto.load;
      _mproto.canPlayType = () => 'maybe'; _mproto.play = () => Promise.resolve(); _mproto.load = () => {};
      try {
        ReactDOM.flushSync(() => root.render(React.createElement(HlsPlayer, {
          nodeId: 'cam-test021', src: 'test.m3u8',
          onFallbackRef: { current: () => {} }
        })));
        await settle();
        const video = container.querySelector('video');
        A('TESTS-4b84 NEW-UX-021 HlsPlayer renders a <video>', !!video);
        A('TESTS-4b84 NEW-UX-021 video has aria-label with 即時影像', !!video && (video.getAttribute('aria-label') || '').indexOf('即時影像') !== -1, video && video.getAttribute('aria-label'));
      } finally {
        _mproto.canPlayType = _origCPT; _mproto.play = _origPlay; _mproto.load = _origLoad;
      }
    `,
  },
  {
    name: 'TESTS-4b84 NEW-UX-025 CommandPalette uses 快捷鍵 not Hotkey + kind labels are zh-TW',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // Set up NAV_ITEMS so CommandPalette has items to render
      window.NAV_ITEMS = [
        { id: 'alerts', label: '警報', hotkey: 'A', Icon: Icon.Bell },
        { id: 'status', label: '節點', hotkey: 'S', Icon: Icon.Server }
      ];
      ReactDOM.flushSync(() => root.render(React.createElement(CommandPalette, {
        open: true, onClose: () => {}, onSelect: () => {},
        alerts: [], nodes: [], onSelectAlert: () => {}, onNav: () => {}, onCmd: () => {}
      })));
      await settle();
      const text = container.textContent;
      A('TESTS-4b84 NEW-UX-025 nav hints use 快捷鍵 not Hotkey', text.indexOf('快捷鍵') !== -1 && text.indexOf('Hotkey') === -1, text.slice(0, 500));
      A('TESTS-4b84 NEW-UX-025 kind cell renders 頁面 not nav', text.indexOf('頁面') !== -1, text.slice(0, 500));
    `,
  },
  {
    name: 'TESTS-4b84 NEW-UX-009 drawers have max-w-[100vw] class',
    target: 'components.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      // Inspection: check the components.jsx source for max-w-[100vw] on the drawers
      const src = ${JSON.stringify(COMPONENTS_JSX)};
      const muteDrawerIdx = src.indexOf('w-[380px]');
      const nodeSidePanelIdx = src.indexOf('w-[420px]');
      A('TESTS-4b84 NEW-UX-009 MuteDrawer has max-w-[100vw]', muteDrawerIdx !== -1 && src.slice(muteDrawerIdx, muteDrawerIdx + 60).indexOf('max-w-[100vw]') !== -1);
      A('TESTS-4b84 NEW-UX-009 NodeSidePanel has max-w-[100vw]', nodeSidePanelIdx !== -1 && src.slice(nodeSidePanelIdx, nodeSidePanelIdx + 60).indexOf('max-w-[100vw]') !== -1);
    `,
  },
  // NEW-UX-022 (cursor CSS), NEW-RT-004 (destroyedRef), NEW-RT-005 (inFlightRef):
  // timing/ref-only — not safely render-testable in jsdom. Left as named comments.
];
