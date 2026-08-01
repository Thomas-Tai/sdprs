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

  // ------------------------------------- SHELL-004: partial-load warnings --
  // app.jsx's boot effect surfaces a warning banner + toast when loadInitial()
  // reports partial failures via window.__SDPRS_LOAD_FAILURES. The bootstrap-
  // error retry path re-runs loadInitial() but never read that global back —
  // a retry that recovered most-but-not-all loaders looked like a full
  // success, silently. describeLoadFailures is the pure decision shared by
  // both call sites (extracted so it's testable without mounting app.jsx).
  {
    name: 'SHELL-004                   data.jsx (describeLoadFailures)',
    target: 'data.jsx',
    deps: ['icons.jsx'],
    body: `
      A('SHELL-004 window.describeLoadFailures is published as a function', typeof window.describeLoadFailures === 'function', typeof window.describeLoadFailures);
      A('SHELL-004 no failures (undefined) => null (nothing to show)', window.describeLoadFailures(undefined, {}) === null);
      A('SHELL-004 no failures (empty array) => null (nothing to show)', window.describeLoadFailures([], {}) === null);
      A('SHELL-004 not-an-array (defensive) => null', window.describeLoadFailures('nodes', {}) === null);

      const labels = { nodes: '節點資料', weather: '天氣資訊' };
      const desc = window.describeLoadFailures(['nodes', 'weather'], labels);
      A('SHELL-004 partial failures => a result object, not null', !!desc, JSON.stringify(desc));
      A('SHELL-004 result carries the raw failure keys as warnings', Array.isArray(desc.warnings) && desc.warnings.length === 2 && desc.warnings.indexOf('nodes') !== -1 && desc.warnings.indexOf('weather') !== -1, JSON.stringify(desc && desc.warnings));
      A('SHELL-004 toastMessage names the failed feeds by their zh-TW label', desc.toastMessage.indexOf('節點資料') !== -1 && desc.toastMessage.indexOf('天氣資訊') !== -1, desc.toastMessage);
      A('SHELL-004 toastMessage falls back to the raw key for an unlabeled loader', window.describeLoadFailures(['mystery_loader'], labels).toastMessage.indexOf('mystery_loader') !== -1, window.describeLoadFailures(['mystery_loader'], labels).toastMessage);
      A('SHELL-004 missing labels map (undefined) still returns a usable message, not a throw', window.describeLoadFailures(['nodes'], undefined).toastMessage.indexOf('nodes') !== -1, window.describeLoadFailures(['nodes'], undefined).toastMessage);
    `,
  },

  // ------------------------------- SHELL-007: RESTORED_STATE.selectedId ----
  // app.jsx's selectedId useState initializer adopted RESTORED_STATE.selectedId
  // (decoded straight out of a base64 URL param on the cross-login roundtrip)
  // with NO check that it names an alert actually in the queue — unlike the
  // sessionStorage-saved id, which the boot effect already validates via
  // `.find(a => String(a.id) === saved)` before adopting it. A stale id
  // (resolved/aged out between the redirect and the next load), a corrupted
  // blob, or a hand-edited URL therefore selected nothing real. resolveSelectedId
  // is the single validation both untrusted sources should have gone through.
  {
    name: 'SHELL-007                   data.jsx (resolveSelectedId)',
    target: 'data.jsx',
    deps: ['icons.jsx'],
    body: `
      A('SHELL-007 window.resolveSelectedId is published as a function', typeof window.resolveSelectedId === 'function', typeof window.resolveSelectedId);
      const alerts = [{ id: 101 }, { id: 202 }, { id: 303 }];

      A('SHELL-007 a restoredId matching a live alert wins', window.resolveSelectedId(202, null, alerts) === 202, window.resolveSelectedId(202, null, alerts));
      A('SHELL-007 a restoredId matching a live alert wins even as a string/number mismatch', window.resolveSelectedId('202', null, alerts) === 202, window.resolveSelectedId('202', null, alerts));
      A('SHELL-007 a restoredId NOT among live alerts is rejected, falling through', window.resolveSelectedId(999, null, alerts) === 101, window.resolveSelectedId(999, null, alerts));
      A('SHELL-007 a savedId matching a live alert wins when no restoredId', window.resolveSelectedId(null, '303', alerts) === 303, window.resolveSelectedId(null, '303', alerts));
      A('SHELL-007 restoredId takes priority over savedId when both are valid', window.resolveSelectedId(101, '303', alerts) === 101, window.resolveSelectedId(101, '303', alerts));
      A('SHELL-007 an invalid restoredId falls through to a valid savedId', window.resolveSelectedId(999, '303', alerts) === 303, window.resolveSelectedId(999, '303', alerts));
      A('SHELL-007 both invalid falls through to the first alert', window.resolveSelectedId(999, '888', alerts) === 101, window.resolveSelectedId(999, '888', alerts));
      A('SHELL-007 no ids at all falls through to the first alert', window.resolveSelectedId(null, null, alerts) === 101, window.resolveSelectedId(null, null, alerts));
      A('SHELL-007 an empty alert queue resolves to null, never throws', window.resolveSelectedId(101, null, []) === null, String(window.resolveSelectedId(101, null, [])));
      A('SHELL-007 a null/undefined alerts array resolves to null, never throws', window.resolveSelectedId(101, null, null) === null, String(window.resolveSelectedId(101, null, null)));
    `,
  },
];
