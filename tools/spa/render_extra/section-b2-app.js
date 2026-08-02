// Section B2 (app.jsx / styles.css) remediation suites.
// Findings: NEW-UX-010, NEW-UX-008, NEW-UX-015, NEW-UX-016, OPS-027, NEW-RT-007.

const fs = require('fs');
const path = require('path');

// CSS is inspection-verified (no render), so we read the source once at load time.
const { SPA_DIR } = require('../spa_files');
const STYLES_CSS = fs.readFileSync(path.join(SPA_DIR, 'styles.css'), 'utf8');

module.exports = [
  // ------------------------------------------------------------- NEW-UX-010
  {
    name: 'NEW-UX-010 styles.css focus-visible rule includes select',
    target: 'data.jsx', // no-op target — test is CSS inspection, not render
    deps: ['icons.jsx'],
    body: `
      // Inspection: the CSS source must include select in the focus-visible
      // selector list so <select> elements get the same outline as other inputs.
      const css = ${JSON.stringify(STYLES_CSS)};
      const fvRule = css.split('\\n').find(l => l.indexOf('focus-visible') !== -1 && l.indexOf('outline') === -1);
      A('NEW-UX-010 a focus-visible selector line exists', !!fvRule, 'no focus-visible line found');
      A('NEW-UX-010 select:focus-visible is in the focus-visible selector list', !!fvRule && fvRule.indexOf('select:focus-visible') !== -1, fvRule && fvRule.slice(0, 150));
    `,
  },
];
