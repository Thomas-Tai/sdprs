// Section B2 (app.jsx / styles.css) remediation suites.
// Findings: NEW-UX-010, NEW-UX-015, NEW-UX-016, NEW-UX-008, OPS-027, NEW-RT-007.

const fs = require('fs');
const path = require('path');

const { SPA_DIR } = require('../spa_files');
const STYLES_CSS = fs.readFileSync(path.join(SPA_DIR, 'styles.css'), 'utf8');
const APP_JSX = fs.readFileSync(path.join(SPA_DIR, 'app.jsx'), 'utf8');

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
];
