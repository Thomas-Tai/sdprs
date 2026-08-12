// 2026-08-06 rainfall reconciliation: api.jsx maps the backend's two honest
// rainfall fields (rainfall_rate_mmh -> rain.now, rainfall_24h_mm -> rain.day).
// Mirrors wxa004-lightning.js: api.jsx is an IIFE, so mapWeather is only
// observable via SDPRS_API.loadWeather over a stubbed window.fetch.
module.exports = [
  {
    name: 'RAIN-MAP  api.jsx maps rainfall_rate_mmh->rain.now and rainfall_24h_mm->rain.day',
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
            temperature_c: 28, is_stale: false, source: 'SMG',
            rainfall_rate_mmh: 2.5, rainfall_24h_mm: 40,
            sources: { rainfall_rate_mmh: 'SMG 外港', rainfall_24h_mm: 'SMG 外港' },
          });
        }
        if (u.indexOf('/api/weather/forecast') !== -1) return _res({ buckets: [] });
        if (u.indexOf('/api/weather/typhoon') !== -1) return _res(null);
        return _res({}, { status: 404 });
      };
      A('RAIN-MAP SDPRS_API.loadWeather is published', typeof window.SDPRS_API.loadWeather === 'function', typeof window.SDPRS_API.loadWeather);
      const w = await window.SDPRS_API.loadWeather();
      A('RAIN-MAP rain.now maps from rainfall_rate_mmh (was hardcoded null)', w.rain && w.rain.now === 2.5, JSON.stringify(w.rain));
      A('RAIN-MAP rain.day maps from rainfall_24h_mm', w.rain && w.rain.day === 40, JSON.stringify(w.rain));
      A('RAIN-MAP rain.hour stays null (no separate 1h bucket)', w.rain && w.rain.hour === null, JSON.stringify(w.rain));
      A('RAIN-MAP rate source rides through sources dict', w.sources && w.sources.rainfall_rate_mmh === 'SMG 外港', JSON.stringify(w.sources));
    `,
  },
  {
    name: 'RAIN-TILE weather.jsx renders live rate + its source (mixed providers)',
    target: 'pages/weather.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.WEATHER = {
        available: true, source: 'SMG', station: '外港', stale: false,
        temp: 28, humidity: 80, pressure: 1005, visibility: 8,
        wind: { speed: 3, degree: 90, dir: 'E', gust: 5 },
        rain: { day: 40, now: 2.5, hour: null },
        lightning: { count: 0, nearest: null },
        typhoon: null,
        fetchedAt: new Date().toISOString(),
        forecast: [],
        sources: { rainfall_24h_mm: 'SMG 外港', rainfall_rate_mmh: 'Open-Meteo (22.19,113.55)' },
      };
      ReactDOM.flushSync(() => root.render(React.createElement(WeatherPage, { showToast: () => {}, onRefresh: () => Promise.resolve() })));
      await settle();
      const text = container.textContent;
      A('RAIN-TILE live-rate line shows 即時 2.5 mm/h', text.indexOf('即時 2.5 mm/h') !== -1, text.slice(0, 400));
      A('RAIN-TILE 24h hero shows 40 mm/24h', text.indexOf('40') !== -1 && text.indexOf('mm/24h') !== -1, '');
      A('RAIN-TILE rate source label renders (was: only 24h source rendered)', text.indexOf('Open-Meteo (22.19,113.55)') !== -1, text.slice(0, 400));
    `,
  },
  {
    name: 'RAIN-TILE weather.jsx null rate shows honest no-source fallback',
    target: 'pages/weather.jsx',
    deps: ['icons.jsx', 'data.jsx', 'components.jsx'],
    body: `
      window.WEATHER = {
        available: true, source: 'SMG', station: '外港', stale: false,
        temp: 28, humidity: 80, pressure: 1005, visibility: 8,
        wind: { speed: 3, degree: 90, dir: 'E', gust: 5 },
        rain: { day: 40, now: null, hour: null },
        lightning: { count: 0, nearest: null },
        typhoon: null,
        fetchedAt: new Date().toISOString(),
        forecast: [],
        sources: { rainfall_24h_mm: 'SMG 外港' },
      };
      ReactDOM.flushSync(() => root.render(React.createElement(WeatherPage, { showToast: () => {}, onRefresh: () => Promise.resolve() })));
      await settle();
      const text = container.textContent;
      A('RAIN-TILE null rate shows 無即時雨量資料來源 fallback', text.indexOf('無即時雨量資料來源') !== -1, '');
      A('RAIN-TILE 24h source still renders when only daily supplied', text.indexOf('SMG 外港') !== -1, '');
    `,
  },
];
