// render_extra suites for the data.jsx / api.jsx audit-remediation lane.
//
// Owner: fix/spa-data-api. Findings covered (Section 2 of the 2026-07-26 audit):
//   UX-001, DATA-011, DATA-012, DATA-013, DATA-014, DATA-015, DATA-018,
//   DATA-020, DATA-021, DATA-022, DATA-023, DATA-025.
//
// Two suites: one targets data.jsx (pure helpers + window placeholders), one
// targets api.jsx (mappers + fetch layer, driven through the public SDPRS_API
// surface over a stubbed window.fetch, since the mappers live inside api.jsx's
// IIFE and are only observable via their published output).

// A JSON-ish Response stub for the stubbed window.fetch.
const RES_HELPER = `
  const _res = (data, opts) => {
    const o = opts || {};
    const status = o.status != null ? o.status : 200;
    const ct = o.ct != null ? o.ct : 'application/json';
    const isJson = ct.indexOf('json') !== -1;
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      headers: { get: (k) => (String(k).toLowerCase() === 'content-type' ? ct : null) },
      json: () => (o.jsonThrows ? Promise.reject(new Error('bad json')) : Promise.resolve(data)),
      text: () => Promise.resolve(typeof data === 'string' ? data : JSON.stringify(data)),
    });
  };
`;

module.exports = [
  // ------------------------------------------------------- data.jsx helpers --
  {
    name: 'UX-001                      data.jsx (window.fmtTs)',
    target: 'data.jsx',
    deps: ['icons.jsx'],
    body: `
      A('UX-001 window.fmtTs is published as a function', typeof window.fmtTs === 'function', typeof window.fmtTs);
      // null-safe: never renders "null"/"Invalid Date"/"NaN"
      A('UX-001 fmtTs(null) renders the em dash', window.fmtTs(null) === '\\u2014', window.fmtTs(null));
      A('UX-001 fmtTs(undefined) renders the em dash', window.fmtTs(undefined) === '\\u2014', window.fmtTs(undefined));
      A('UX-001 fmtTs(garbage) renders the em dash', window.fmtTs('not-a-date') === '\\u2014', window.fmtTs('not-a-date'));
      // today => bare HH:MM:SS (matches fmtClock, no date noise)
      const now = new Date();
      const p = (n) => String(n).padStart(2, '0');
      const hms = p(now.getHours()) + ':' + p(now.getMinutes()) + ':' + p(now.getSeconds());
      A('UX-001 fmtTs(today) is bare HH:MM:SS (no date)', window.fmtTs(now) === hms, window.fmtTs(now));
      // a different day this year => date is prefixed so a midnight-crossing event is unambiguous
      const other = new Date(now.getTime());
      other.setMonth(now.getMonth() === 0 ? 11 : 0);
      other.setDate(now.getMonth() === 0 ? 15 : 15); // land on a definitely-different day
      const otherOut = window.fmtTs(other);
      A('UX-001 fmtTs(not today) prefixes a MM-DD date', /\\d\\d-\\d\\d /.test(otherOut) && otherOut.indexOf(hms.slice(0,0)) !== -1, otherOut);
      A('UX-001 fmtTs(not today) never renders NaN/Invalid', otherOut.indexOf('NaN') === -1 && otherOut.indexOf('Invalid') === -1, otherOut);
      // a prior YEAR => full YYYY-MM-DD prefix
      const lastYear = new Date(now.getTime()); lastYear.setFullYear(now.getFullYear() - 1);
      A('UX-001 fmtTs(prior year) prefixes the year', window.fmtTs(lastYear).indexOf(String(now.getFullYear() - 1)) === 0, window.fmtTs(lastYear));
      // accepts epoch-ms too (the api-layer parseTsMs contract)
      A('UX-001 fmtTs accepts epoch ms', window.fmtTs(now.getTime()) === hms, window.fmtTs(now.getTime()));
    `,
  },

  // --------------------------------------------------- api.jsx: DATA-011 ----
  {
    name: 'DATA-011                    api.jsx (ageSec null, not 0)',
    target: 'api.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      ${RES_HELPER}
      // An alert whose created timestamp is unparseable must map to ageSec null,
      // not 0 — 0 reads as "just now" (fresh), null reads as "unknown".
      const badAlert = { id: 7, status: 'PENDING', visual_confidence: 0.9,
        created_at: 'not-a-timestamp', timestamp: null, node_id: 'CAM-1' };
      window.fetch = (path) => {
        if (String(path).indexOf('/api/alerts?status_filter=PENDING') === 0) return _res([badAlert]);
        if (String(path).indexOf('/api/nodes') === 0) return _res([]);
        return _res([]);
      };
      const rl = await window.SDPRS_API.refreshLive();
      const mapped = (rl.alerts || []).find(a => a.id === 7);
      A('DATA-011 unparseable created_at maps to ageSec null (not 0)', !!mapped && mapped.ageSec === null, mapped && String(mapped.ageSec));
      // A genuinely fresh alert still gets a numeric age.
      const freshAlert = { id: 8, status: 'PENDING', visual_confidence: 0.9,
        created_at: new Date(Date.now() - 5000).toISOString(), node_id: 'CAM-1' };
      window.fetch = (path) => {
        if (String(path).indexOf('/api/alerts?status_filter=PENDING') === 0) return _res([freshAlert]);
        if (String(path).indexOf('/api/nodes') === 0) return _res([]);
        return _res([]);
      };
      const rl2 = await window.SDPRS_API.refreshLive();
      const fresh = (rl2.alerts || []).find(a => a.id === 8);
      A('DATA-011 a parseable created_at still yields a numeric ageSec', !!fresh && typeof fresh.ageSec === 'number' && fresh.ageSec >= 0, fresh && String(fresh.ageSec));
      // The render path must show an honest dash for a null age, never "0s".
      A('DATA-011 fmtAge(null) renders the em dash, not 0s', window.fmtAge(null) === '\\u2014', window.fmtAge(null));
      A('DATA-011 ageColor(null) is not the calm fresh color', window.ageColor(null) !== 'text-ink-secondary', window.ageColor(null));
    `,
  },
];

// keep the helper referenced so linters/bundlers don't drop it before use
void RES_HELPER;
