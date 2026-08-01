// render_extra suites for the app-shell audit-remediation lane (Section 1 of
// the 2026-07-26 audit: SHELL-001..SHELL-028).
//
// Owner: fix/spa-lane-2026-08-01 (Section 1 — App Shell).
//
// app.jsx auto-mounts <App/> at file end (bootstrap()), so it is NOT usable as
// a harness `target` (see tools/spa/render_extra/README.md). Every finding
// here that has an extractable pure helper puts that helper in data.jsx and is
// tested directly against `target: 'data.jsx'`. Findings that are pure CSS /
// JSX-attribute one-liners in app.jsx are implemented + inspection-verified
// (noted in the commit body) rather than render-tested here.

module.exports = [
  // ------------------------------------------- SHELL-001: page sanitizing --
  // `page` state can come from RESTORED_STATE (base64 URL param, cross-login
  // roundtrip), sessionStorage, or a popstate event — all three are strings an
  // operator, a stale bookmark, or a corrupted blob could hand back as garbage.
  // The single authoritative valid-page set is renderPage()'s switch in
  // app.jsx. sanitizePage(p) must return `p` unchanged for every one of those
  // cases and fall back to 'alerts' for anything else (including non-strings).
  {
    name: 'SHELL-001                   data.jsx (sanitizePage / VALID_PAGES)',
    target: 'data.jsx',
    deps: ['icons.jsx'],
    body: `
      A('SHELL-001 window.VALID_PAGES is published as an array', Array.isArray(window.VALID_PAGES), typeof window.VALID_PAGES);
      const expected = ['alerts', 'monitor', 'status', 'pumps', 'weather', 'handover', 'audit'];
      const gotSorted = (window.VALID_PAGES || []).slice().sort().join(',');
      A('SHELL-001 VALID_PAGES matches renderPage\\'s switch cases exactly', gotSorted === expected.slice().sort().join(','), gotSorted);
      A('SHELL-001 window.sanitizePage is published as a function', typeof window.sanitizePage === 'function', typeof window.sanitizePage);
      for (const p of expected) {
        A('SHELL-001 sanitizePage keeps a valid page unchanged: ' + p, window.sanitizePage(p) === p, window.sanitizePage(p));
      }
      A('SHELL-001 sanitizePage falls back to alerts for an unknown string', window.sanitizePage('not-a-page') === 'alerts', String(window.sanitizePage('not-a-page')));
      A('SHELL-001 sanitizePage falls back to alerts for null', window.sanitizePage(null) === 'alerts', String(window.sanitizePage(null)));
      A('SHELL-001 sanitizePage falls back to alerts for undefined', window.sanitizePage(undefined) === 'alerts', String(window.sanitizePage(undefined)));
      A('SHELL-001 sanitizePage falls back to alerts for a non-string (number)', window.sanitizePage(123) === 'alerts', String(window.sanitizePage(123)));
      A('SHELL-001 sanitizePage falls back to alerts for an empty string', window.sanitizePage('') === 'alerts', String(window.sanitizePage('')));
    `,
  },
];
