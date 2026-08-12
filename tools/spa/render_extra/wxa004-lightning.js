// WXA-004: lightning data flows from the backend through mapWeather to the tile.
// Owner: feat/wxa-004-lightning. api.jsx is an IIFE, so mapWeather/loadWeather
// are only observable via the published window.SDPRS_API surface — this suite
// drives SDPRS_API.loadWeather over a stubbed window.fetch.
module.exports = [
  {
    name: 'WXA-004                     api.jsx lightning maps through loadWeather',
    target: 'api.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      const _res = (data, opts) => {
        const o = opts || {};
        const status = o.status != null ? o.status : 200;
        return Promise.resolve({
          ok: status >= 200 && status < 300,
          status,
          headers: { get: (k) => (String(k).toLowerCase() === 'content-type' ? 'application/json' : null) },
          json: () => Promise.resolve(data),
          text: () => Promise.resolve(typeof data === 'string' ? data : JSON.stringify(data)),
        });
      };
      window.fetch = (url) => {
        const u = String(url);
        if (u.indexOf('/api/weather/current') !== -1) {
          return _res({
            temperature_c: 20, is_stale: false, source: 'SMG',
            sources: { temperature_c: 'SMG', lightning: 'Blitzortung.org' },
            lightning: { count: 3, nearest: 5 },
          });
        }
        if (u.indexOf('/api/weather/forecast') !== -1) return _res({ buckets: [] });
        if (u.indexOf('/api/weather/typhoon') !== -1) return _res(null);
        return _res({}, { status: 404 });
      };
      A('WXA-004 SDPRS_API.loadWeather is published', typeof window.SDPRS_API.loadWeather === 'function', typeof window.SDPRS_API.loadWeather);
      const w = await window.SDPRS_API.loadWeather();
      A('WXA-004 lightning.count flows from backend (was hardcoded null)', w.lightning && w.lightning.count === 3, JSON.stringify(w.lightning));
      A('WXA-004 lightning.nearest flows from backend', w.lightning && w.lightning.nearest === 5, JSON.stringify(w.lightning));
      A('WXA-004 lightning source flows via sources.lightning', w.sources && w.sources.lightning === 'Blitzortung.org', JSON.stringify(w.sources));
    `,
  },
];
