// SDPRS — Weather Page

const { useState: useState_w, useEffect: useEffect_w, useRef: useRef_w } = React;

// ============================================================================
// UX enhancement helpers (2026-07-19 audit pass)
// ============================================================================

// Live-updating "N 秒前 / 分前 / 小時前" ticker. Rerenders every second
// so the age stays fresh without triggering data refetch. Null-safe:
// returns em-dash placeholder when the ISO is missing so callers can
// inline it: <span>更新於 <AgoTicker iso={w.fetchedAt}/></span>
const AgoTicker = ({ iso }) => {
  const [, tick] = useState_w(0);
  useEffect_w(() => {
    if (!iso) return;
    const id = setInterval(() => tick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [iso]);
  if (!iso) return <span className="text-ink-dim">—</span>;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return <span className="text-ink-dim">—</span>;
  const secs = Math.max(0, Math.floor((Date.now() - t) / 1000));
  let text, tone;
  if (secs < 60) { text = `${secs} 秒前`; tone = 'text-ink-secondary'; }
  else if (secs < 3600) { text = `${Math.floor(secs / 60)} 分前`; tone = 'text-ink-secondary'; }
  else if (secs < 86400) { text = `${Math.floor(secs / 3600)} 小時前`; tone = 'text-sev-warn'; }
  else { text = `${Math.floor(secs / 86400)} 天前`; tone = 'text-sev-warn'; }
  return <span className={tone}>{text}</span>;
};

// Rotating arrow SVG showing wind direction. `degree` is the "from"
// bearing (meteorological convention — wind FROM north = degree 0);
// the arrowhead is rotated 180° so it POINTS in the direction the wind
// is blowing TOWARDS, which is what operators intuitively expect when
// asking "which way is the wind going?". Null degree renders a dashed
// disc as a no-data placeholder.
const WindArrow = ({ degree, size = 16 }) => {
  if (degree == null) {
    return (
      <svg width={size} height={size} viewBox="0 0 20 20" className="text-ink-dim inline-block" aria-hidden="true">
        <circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" strokeWidth="1" strokeDasharray="2 2"/>
      </svg>
    );
  }
  // +180° because meteorological "wind from 90°" (east wind) BLOWS towards
  // west (270°) — the operator wants the arrow to point west.
  const rot = (Number(degree) + 180) % 360;
  return (
    <svg
      width={size} height={size} viewBox="0 0 20 20"
      className="inline-block text-ink-secondary"
      style={{ transform: `rotate(${rot}deg)`, transition: 'transform 400ms ease' }}
      aria-label={`風向 ${degree}度（吹向 ${rot} 度）`}
    >
      <path d="M10 2 L15 15 L10 12 L5 15 Z" fill="currentColor" stroke="currentColor" strokeLinejoin="round"/>
    </svg>
  );
};

// Value-based severity color for the tile big-number. Pure UI helper —
// decoupled from backend severity flags (which drive alert triggering).
// Thresholds chosen for Macau/HK subtropical context: wind by Beaufort-ish
// bands, temperature by comfort/danger zones.
const windColorClass = (kmh) => {
  if (kmh == null) return 'text-ink-primary';
  if (kmh >= 100) return 'text-sev-critical';   // hurricane force (T10 signal ≈ 118+ km/h)
  if (kmh >= 63)  return 'text-sev-warn';        // gale / T8 signal territory
  if (kmh >= 40)  return 'text-sev-info';        // strong wind / T3 territory
  return 'text-ink-primary';
};
const tempColorClass = (c) => {
  if (c == null) return 'text-ink-primary';
  if (c >= 35) return 'text-sev-critical';       // heat warning territory
  if (c >= 32) return 'text-sev-warn';           // very hot
  if (c <= 10) return 'text-sev-info';           // cold warning territory in HK/Macau
  return 'text-ink-primary';
};
const rainColorClass = (mmh) => {
  if (mmh == null) return 'text-sev-info';
  if (mmh >= 30) return 'text-sev-critical';     // amber rainstorm signal ≈ 30 mm/h
  if (mmh >= 10) return 'text-sev-warn';
  return 'text-sev-info';
};


// Option C settings pane (2026-07-19). Expandable ⚙️ panel above the
// hero tiles that lets operators pick the SMG station, HKO station,
// fallback provider, and site lat/lon that feed the multi-source merge.
// Fetches station lists on expand (not on mount) — the SMG/HKO
// enumeration endpoints hit their upstreams live, so we avoid the
// extra HTTPs until the operator actually clicks to configure.
const WeatherSettings = ({ onSaved, showToast }) => {
  const [open, setOpen] = useState_w(false);
  const [loading, setLoading] = useState_w(false);
  const [saving, setSaving] = useState_w(false);
  const [config, setConfig] = useState_w({
    site_lat: null, site_lon: null,
    smg_station: '', hko_station: '', fallback_provider: 'both',
  });
  const [smgList, setSmgList] = useState_w([]);
  const [hkoList, setHkoList] = useState_w([]);
  const [loadError, setLoadError] = useState_w(null);
  // Snapshot of the last-saved config so 取消 can revert unsaved
  // changes (audit S3) — without this, closing and re-opening the pane
  // showed stale edits as if they had been saved.
  const savedConfigRef = useRef_w(null);

  // S2: paired lat/lon validation — user must set BOTH or NEITHER, or
  // Open-Meteo breaks silently at fetch time (the fetcher checks
  // both != None; only one set = skipped). Compute here so both the
  // error message and the Save-disabled state share one source of truth.
  const latEntered = config.site_lat != null && config.site_lat !== '';
  const lonEntered = config.site_lon != null && config.site_lon !== '';
  const latLonError = latEntered !== lonEntered
    ? '緯度和經度必須同時設定或同時留空'
    : null;
  const canSave = !saving && !loading && !latLonError;

  useEffect_w(() => {
    if (!open) return;
    // Only fetch once per expand — the lists rarely change hour-to-hour.
    if (smgList.length > 0 || hkoList.length > 0) return;
    setLoading(true);
    setLoadError(null);
    Promise.all([
      window.SDPRS_API.getWeatherConfig().catch(() => ({})),
      window.SDPRS_API.listSmgStations().catch(() => ({ stations: [] })),
      window.SDPRS_API.listHkoStations().catch(() => ({ stations: [] })),
    ]).then(([cfg, smgResp, hkoResp]) => {
      const loaded = {
        site_lat: cfg.site_lat != null ? cfg.site_lat : null,
        site_lon: cfg.site_lon != null ? cfg.site_lon : null,
        smg_station: cfg.smg_station || '',
        hko_station: cfg.hko_station || '',
        fallback_provider: cfg.fallback_provider || 'both',
      };
      setConfig(loaded);
      // S3: baseline for revert-on-cancel. Refresh on load AND after
      // each successful save so 取消 only discards changes-since-baseline.
      savedConfigRef.current = loaded;
      setSmgList(smgResp.stations || []);
      setHkoList(hkoResp.stations || []);
      if ((smgResp.stations || []).length === 0 && (hkoResp.stations || []).length === 0) {
        setLoadError('無法載入測站清單（上游服務可能不可用）');
      }
    }).finally(() => setLoading(false));
  }, [open, smgList.length, hkoList.length]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload = {
        site_lat: config.site_lat != null && config.site_lat !== '' ? Number(config.site_lat) : null,
        site_lon: config.site_lon != null && config.site_lon !== '' ? Number(config.site_lon) : null,
        smg_station: config.smg_station || null,
        hko_station: config.hko_station || null,
        fallback_provider: config.fallback_provider || null,
      };
      // Save config first (fast, DB write).
      await window.SDPRS_API.setWeatherConfig(payload);
      // Force an immediate server-side re-tick so the cache reflects the
      // new config choice BEFORE we refetch it from the SPA. Without this,
      // the tiles wouldn't visibly change until the next scheduled tick
      // (WEATHER_REFRESH_SECONDS = 600s / 10 min default) — that's what
      // made the earlier "settings appear to do nothing" complaint valid.
      // The refresh call may take 2-5s (fans out to SMG + HKO + Open-Meteo).
      // If it fails (503 / network), we still call onRefresh so the SPA
      // reflects whatever the server currently has cached — degrades to
      // "changes visible on next tick" rather than blocking the save.
      // We track the outcome so the toast doesn't lie: on refresh failure
      // the config IS in the DB but tiles won't show it until the next
      // scheduled tick (up to WEATHER_REFRESH_SECONDS later).
      let refreshOk = true;
      try {
        await window.SDPRS_API.refreshWeather();
      } catch (refreshErr) {
        refreshOk = false;
        console.warn('[weather] refresh-after-save failed:', refreshErr);
      }
      // Now fetch the freshly-cached data (or the still-stale cache if
      // the refresh above failed — SPA will show the latest server state
      // either way).
      onSaved && onSaved();
      // S3: update revert baseline so subsequent 取消 discards only
      // changes made after this successful save.
      savedConfigRef.current = { ...config };
      if (refreshOk) {
        showToast && showToast('天氣設定已儲存並套用', 'info');
      } else {
        // Honest: save succeeded but immediate re-tick didn't. Operator
        // needs to know they won't see the new source labels until the
        // scheduled tick fires (up to 10 minutes).
        showToast && showToast(
          '設定已儲存，但即時重刷失敗 · 下次自動更新（最多 10 分鐘）後套用',
          'warn');
      }
    } catch (e) {
      showToast && showToast('儲存失敗：' + (e && e.message ? e.message : '未知錯誤'), 'warn');
    } finally {
      setSaving(false);
    }
  };

  const inputCls = 'text-xs bg-surface-base border border-border-subtle rounded px-2 py-1 font-mono';
  return (
    <div className="border-b border-border-subtle bg-surface-panel/40">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full px-6 py-2 text-left text-xs text-ink-muted hover:text-ink-primary flex items-center gap-2 transition-colors"
        aria-expanded={open}
        aria-label={open ? '收合天氣資料來源設定' : '展開天氣資料來源設定'}
      >
        <Icon.Settings size={12}/>
        <span>天氣資料來源設定</span>
        {/* S1: chevron icon that rotates 180° on open — replaces the
            static "▼ 展開 / ▲ 收合" text. Uses CSS transform + transition
            so state change animates smoothly. */}
        <Icon.ChevronDown
          size={12}
          className={`text-ink-dim transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
          aria-hidden="true"
        />
        <span className="text-ink-dim">{open ? '收合' : '展開'}</span>
      </button>
      {open && (
        <div className="px-6 pb-4 space-y-3">
          {loading && <div className="text-xs text-ink-muted">載入中…</div>}
          {loadError && <div className="text-xs text-sev-warn">{loadError}</div>}
          {!loading && (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-ink-muted uppercase tracking-wider">SMG 澳門觀測站</span>
                  <select
                    value={config.smg_station}
                    onChange={e => setConfig({...config, smg_station: e.target.value})}
                    className={inputCls}
                  >
                    <option value="">(預設: 外港)</option>
                    {smgList.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-ink-muted uppercase tracking-wider">HKO 香港天文台測站</span>
                  <select
                    value={config.hko_station}
                    onChange={e => setConfig({...config, hko_station: e.target.value})}
                    className={inputCls}
                  >
                    <option value="">(預設: Hong Kong Observatory)</option>
                    {hkoList.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </label>
              </div>

              <fieldset className="border border-border-subtle rounded p-2">
                <legend className="text-[10px] text-ink-muted uppercase tracking-wider px-1">備援資料源優先順序</legend>
                <div className="flex gap-4 flex-wrap text-xs">
                  {[
                    { v: 'both', label: '兩者皆抓 (SMG > HKO > Open-Meteo)', hint: '推薦：地理相鄰的 HKO 優先，Open-Meteo 補氣壓/能見度' },
                    { v: 'hko', label: '偏好 HKO', hint: '與 both 相同順序；語意標籤明確為 HKO 優先' },
                    { v: 'openmeteo', label: '偏好 Open-Meteo', hint: 'SMG > Open-Meteo > HKO；模型座標精確時選' },
                  ].map(opt => (
                    <label key={opt.v} className="flex items-start gap-1.5 cursor-pointer">
                      <input
                        type="radio"
                        name="fallback"
                        checked={config.fallback_provider === opt.v}
                        onChange={() => setConfig({...config, fallback_provider: opt.v})}
                        className="mt-0.5"
                      />
                      <span>
                        <span className="font-mono text-[11px]">{opt.label}</span>
                        <div className="text-[10px] text-ink-dim">{opt.hint}</div>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <div className="grid grid-cols-2 gap-3">
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-ink-muted uppercase tracking-wider">
                    站台緯度 (Open-Meteo) <span className="text-ink-dim normal-case">留白=用預設 22.19</span>
                  </span>
                  <input
                    type="number" step="any" min="-90" max="90"
                    placeholder="22.19 (澳門)"
                    value={config.site_lat != null ? config.site_lat : ''}
                    onChange={e => setConfig({...config, site_lat: e.target.value})}
                    className={inputCls + (latLonError && lonEntered && !latEntered ? ' border-sev-warn/60' : '')}
                    aria-invalid={latLonError && lonEntered && !latEntered}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-ink-muted uppercase tracking-wider">
                    站台經度 <span className="text-ink-dim normal-case">留白=用預設 113.55</span>
                  </span>
                  <input
                    type="number" step="any" min="-180" max="180"
                    placeholder="113.55 (澳門)"
                    value={config.site_lon != null ? config.site_lon : ''}
                    onChange={e => setConfig({...config, site_lon: e.target.value})}
                    className={inputCls + (latLonError && latEntered && !lonEntered ? ' border-sev-warn/60' : '')}
                    aria-invalid={latLonError && latEntered && !lonEntered}
                  />
                </label>
              </div>

              {/* S2: paired-required error — visible next to the button so
                  the operator sees why Save is disabled. */}
              {latLonError && (
                <div className="text-xs text-sev-warn flex items-center gap-1">
                  <Icon.AlertTriangle size={12}/> {latLonError}
                </div>
              )}

              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => {
                    // S3: revert unsaved changes to the last-saved baseline
                    // (or the initial-loaded values if no save has happened
                    // this session) so re-opening the pane doesn't show
                    // stale edits as if they were persisted.
                    if (savedConfigRef.current) setConfig(savedConfigRef.current);
                    setOpen(false);
                  }}
                  disabled={saving}
                  className="text-xs px-3 py-1 rounded border border-border-subtle hover:bg-surface-panel"
                >取消</button>
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={!canSave}
                  title={latLonError || (saving ? '儲存中' : '儲存設定')}
                  className="text-xs px-3 py-1 rounded border border-sev-info/40 bg-sev-info/10 text-sev-info hover:bg-sev-info/20 disabled:opacity-40 disabled:cursor-not-allowed"
                >{saving ? '儲存中…' : '儲存設定'}</button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};

// Small "資料來源" chip rendered at the bottom of each hero tile.
// `label` may be undefined (backend didn't attribute this field to any
// source — happens when the raw reading came from a hardcoded null in
// api.jsx, e.g. pressure/lightning) — in that case, render nothing so
// the tile stays clean rather than displaying an em-dash source too.
const SourceChip = ({ label }) => {
  if (!label) return null;
  return (
    // Bumped from text-[9px] to text-[10px] + ink-muted (audit D3) so the
    // chip is legible on non-retina and lower-DPI monitors. Truncate + title
    // preserves the long labels (e.g. "Open-Meteo (22.190,113.550)") that
    // sometimes overflow tile width.
    <div className="text-[10px] text-ink-muted mt-2 font-mono truncate" title={`資料來源: ${label}`}>
      來源: {label}
    </div>
  );
};

// Multi-field tile source line. Accepts an array of {label, sourceLabel}
// tuples (label = "溫度"/"濕度"/"氣壓"/"能見度", sourceLabel = provider
// string from the sources dict). Collapses to a single SourceChip when
// every provided sourceLabel is identical (SMG-only, HKO-only, etc.);
// otherwise stacks per-field rows so operators immediately see the
// Env tile is showing mixed-provider data.
const MultiSourceChip = ({ items }) => {
  const nonEmpty = (items || []).filter(it => it && it.sourceLabel);
  if (nonEmpty.length === 0) return null;
  const uniqueSources = new Set(nonEmpty.map(it => it.sourceLabel));
  if (uniqueSources.size === 1) {
    return <SourceChip label={nonEmpty[0].sourceLabel}/>;
  }
  return (
    <div className="text-[10px] text-ink-muted mt-2 font-mono space-y-0.5">
      {nonEmpty.map((it, i) => (
        <div key={i} className="truncate" title={`${it.label}來源: ${it.sourceLabel}`}>
          {it.label}: {it.sourceLabel}
        </div>
      ))}
    </div>
  );
};

const WeatherPage = ({ showToast, onRefresh } = {}) => {
  // Manual-refresh button state (audit T2). Local to WeatherPage so the
  // spinner is scoped to this page — refetches all weather + related data.
  const [refreshing, setRefreshing] = useState_w(false);
  const handleManualRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      // Force server-side re-tick so cached data is fresh, then let
      // app-level refresh pick up the new state.
      try { await window.SDPRS_API.refreshWeather(); }
      catch (e) { /* Non-fatal — onRefresh still shows latest cache. */ }
      if (onRefresh) await onRefresh();
      showToast && showToast('天氣資料已重新載入', 'info');
    } finally {
      setRefreshing(false);
    }
  };

  // D2 (audit 2026-07-16): distinguish loading vs unavailable. api.jsx
  // loadInitial()/refreshLive() only assign window.WEATHER once the fetch
  // settles, so `undefined` = still loading (show spinner-style hint); an
  // assigned object with available:false = fetch completed but the backend
  // refused or has no data yet (show explicit "unavailable").
  if (typeof window.WEATHER === 'undefined') {
    return (
      <div className="h-full flex items-center justify-center">
        <EmptyState icon={Icon.CloudRain} title="載入中…"
          hint="正在取得天氣資料"/>
      </div>
    );
  }
  // Guard: window.WEATHER may be null before the first weather event arrives.
  // All property accesses below must default so we don't throw pre-hydration.
  const w = window.WEATHER || {};
  const fc = w.forecast || [];
  const wind = w.wind || {};
  const rain = w.rain || {};
  const lightning = w.lightning || {};
  // Per-field sources dict from backend Phase 1 multi-source merge (2026-07-19).
  // Missing key = that field wasn't supplied by any provider — SourceChip
  // renders nothing rather than misleading label.
  const sources = w.sources || {};
  // Filter nulls before Math.max — api.jsx C5 fix emits `null` for future
  // hours whose data hasn't landed yet; a single NaN in Math.max poisons the
  // whole axis and every bar height becomes `NaNpx` → blank chart.
  const maxWind = fc.length ? Math.max(1, ...fc.map(f => f.wind).filter(v => Number.isFinite(v))) : 1;
  const maxRain = fc.length ? Math.max(1, ...fc.map(f => f.rain).filter(v => Number.isFinite(v))) : 1;
  // D1 (audit 2026-07-16): the "雷擊期間自動靜音" checkbox previously lived
  // here (useState_p toggle) but had no backend plumbing — no
  // SDPRS_MUTE.setLightningAuto() exists (grep confirmed) and no weather-WS →
  // snooze fan-out ever shipped. Removed rather than leave a placebo. Re-add
  // both the toggle and its state alongside the real backend action.
  if (!w.available) {
    return (
      <div className="h-full flex items-center justify-center">
        <EmptyState icon={Icon.CloudRain} title="天氣資料暫時不可用"
          hint="後端天氣服務尚未啟用,或暫時無法取得資料"/>
      </div>
    );
  }
  return (
    <div className="h-full overflow-y-auto scroll-thin">
      {/* Settings pane (Option C, 2026-07-19) — collapsed by default so
          operators aren't distracted. Save triggers onRefresh so tile
          values update on the next weather tick (~10s worst case). */}
      <WeatherSettings onSaved={onRefresh} showToast={showToast}/>
      {/* Hero */}
      <div className="px-6 py-5 border-b border-border-subtle bg-gradient-to-b from-surface-panel to-surface-base">
        <div className="flex items-start gap-6">
          {/* L2: reserved min-height so the layout doesn't jump when a
              typhoon warning appears / dismisses (single-line no-typhoon
              vs. two-line typhoon-active states used to snap). */}
          <div style={{ minHeight: '2.5rem' }}>
            {w.typhoon ? (
              <>
                <div className="flex items-center gap-2 text-sev-warn text-xs font-semibold uppercase tracking-wider">
                  <Icon.Typhoon size={14}/> {w.typhoon.level} · {w.typhoon.name}
                </div>
                <div className="text-xs text-ink-muted mt-0.5 font-mono tnum">
                  距離 {w.typhoon.distance}km · 方位 {w.typhoon.direction} {w.typhoon.bearing}° · 來源 {w.source}
                </div>
              </>
            ) : (
              <>
                <div className="flex items-center gap-2 text-sev-ok text-xs font-semibold uppercase tracking-wider">
                  <Icon.CloudRain size={14}/> 無熱帶氣旋警報
                </div>
                <div className="text-xs text-ink-muted mt-0.5 font-mono tnum">
                  主測站 {w.station || '—'} · 主源 {w.source}{w.stale ? ' · 資料較舊' : ''}
                  {Object.keys(sources).length > 0 && (
                    <span className="ml-1 text-ink-dim">· 各欄位來源見下方標籤</span>
                  )}
                </div>
              </>
            )}
          </div>
          {/* L1: previously-empty flex-1 now holds a compact
              freshness+refresh cluster. Freshness reads from the ISO the
              backend attaches to CurrentWeather.fetched_at — updates every
              second via AgoTicker so operators see continuous progress.
              Refresh button forces an immediate server-side re-tick. */}
          <div className="flex-1 flex justify-end items-start gap-3">
            <div className="text-xs font-mono tnum text-ink-muted whitespace-nowrap pt-0.5">
              更新於 <AgoTicker iso={w.fetchedAt}/>
            </div>
            <button
              type="button"
              onClick={handleManualRefresh}
              disabled={refreshing}
              title="立即向 SMG / HKO / Open-Meteo 重新抓取（略過 10 分鐘週期）"
              aria-label="重新抓取天氣資料"
              className="text-xs px-2 py-1 rounded border border-border-subtle hover:border-sev-info/40 hover:bg-sev-info/10 hover:text-sev-info disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center gap-1 transition-colors"
            >
              <Icon.RefreshCw size={12} className={refreshing ? 'animate-spin' : ''}/>
              {refreshing ? '抓取中…' : '重新抓取'}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-5">
          <div className="bg-surface-panel border border-border-subtle rounded p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted flex items-center gap-1"><Icon.Wind size={10}/> 風速</div>
            <div className="mt-1 flex items-baseline gap-1 whitespace-nowrap">
              {/* D2: value-based color band (40 km/h fresh / 63 km/h gale /
                  100+ hurricane force — Beaufort-ish, HK T-signal aligned). */}
              <span className={`text-4xl font-mono font-bold tnum ${windColorClass(wind.speed)}`}>
                {wind.speed != null ? wind.speed : '—'}
              </span>
              <span className="text-ink-muted text-sm">km/h</span>
            </div>
            {/* D1: rotating wind arrow next to the direction letters. C1:
                distinguish "no gust data" from "0 km/h gust" via 無資料. */}
            <div className="text-xs text-ink-muted mt-1 font-mono tnum whitespace-nowrap flex items-center gap-1">
              <WindArrow degree={wind.degree} size={14}/>
              <span>{wind.dir || '—'}</span>
              <span className="mx-1 text-ink-dim">·</span>
              <span>陣風 {wind.gust != null ? `${wind.gust} km/h` : '無資料'}</span>
            </div>
            <SourceChip label={sources.wind_speed_ms}/>
          </div>
          <div className="bg-surface-panel border border-border-subtle rounded p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted flex items-center gap-1"><Icon.CloudRain size={10}/> 雨量</div>
            <div className="mt-1 flex items-baseline gap-1 whitespace-nowrap">
              {/* D2: rain color band — Amber signal ≈ 30 mm/h in HK/Macau,
                  Yellow ≈ 10 mm/h. Falls back to plain sev-info at 0. */}
              <span className={`text-4xl font-mono font-bold tnum ${rainColorClass(rain.now)}`}>
                {rain.now != null ? rain.now : '—'}
              </span>
              {/* D3 (audit 2026-07-16): unify units with the forecast legend
                  below. Backend rain is per-hour (Open-Meteo forecast
                  `precipitation` is hourly; SMG current `rainfall_24h_mm` is
                  stored from `rainfall_hourly` — see weather_service.py). No
                  10-min bucket exists yet, so the previous "mm (10min)" label
                  was a lie. */}
              <span className="text-ink-muted text-sm">mm/h</span>
            </div>
            <div className="text-xs text-ink-muted mt-1 font-mono tnum whitespace-nowrap">
              24h 累計 {rain.day != null ? rain.day : '—'} mm
            </div>
            <SourceChip label={sources.rainfall_24h_mm}/>
          </div>
          <div className="bg-surface-panel border border-border-subtle rounded p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted flex items-center gap-1"><Icon.Zap size={10}/> 雷擊</div>
            <div className="mt-1 flex items-baseline gap-1 whitespace-nowrap">
              <span className="text-4xl font-mono font-bold tnum text-sev-warn">{lightning.count != null ? lightning.count : '—'}</span>
              <span className="text-ink-muted text-sm">次/hr</span>
            </div>
            {(() => {
              const near = lightning.nearest;
              if (near == null) return <div className="text-xs text-ink-muted mt-1 font-mono tnum whitespace-nowrap">無偵測</div>;
              // Only cry-wolf 警戒 when strike is close enough to actually matter.
              const alarming = near < 20;
              return (
                <div className={`text-xs mt-1 font-mono tnum whitespace-nowrap ${alarming ? 'text-sev-warn' : 'text-ink-muted'}`}>
                  最近 {near}km{alarming ? ' · 警戒' : ''}
                </div>
              );
            })()}
            {/* No source label — lightning has no backend source yet
                (rendered as null in api.jsx mapWeather). Reserved for a
                future Blitzortung / HKO thunderstorm-warning integration. */}
          </div>
          <div className="bg-surface-panel border border-border-subtle rounded p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted flex items-center gap-1"><Icon.Thermometer size={10}/> 環境</div>
            <div className="mt-1 flex items-baseline gap-1 whitespace-nowrap">
              {/* D2: temperature color band — heat-warning territory in HK
                  is ≥35°C; cold ≤10°C. */}
              <span className={`text-4xl font-mono font-bold tnum ${tempColorClass(w.temp)}`}>
                {w.temp != null ? w.temp : '—'}
              </span>
              <span className="text-ink-muted text-sm">°C</span>
            </div>
            <div className="text-xs text-ink-muted mt-1 font-mono tnum whitespace-nowrap">
              濕度 {w.humidity != null ? w.humidity : '—'}%
              {w.pressure != null ? ` · ${w.pressure} hPa` : ''}
              {w.visibility != null ? ` · 能見度 ${w.visibility}km` : ''}
            </div>
            <MultiSourceChip items={[
              { label: '溫度', sourceLabel: sources.temperature_c },
              { label: '濕度', sourceLabel: sources.humidity_pct },
              { label: '氣壓', sourceLabel: sources.pressure_hpa },
              { label: '能見度', sourceLabel: sources.visibility_km },
            ]}/>
          </div>
        </div>
      </div>

      {/* Forecast */}
      <div className="p-6">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h2 className="text-sm font-semibold">
            36 小時預報
            {/* F3: dynamic time-range subheading. Reads first/last hour
                labels straight from the fc data so it stays honest even
                when Open-Meteo returns fewer buckets than expected. */}
            {fc.length > 0 && fc[0].h !== '--' && fc[fc.length - 1].h !== '--' && (
              <span className="ml-2 text-xs font-normal font-mono tnum text-ink-muted">
                · {fc[0].h}:00 起 · {fc.length} 小時
              </span>
            )}
          </h2>
          {/* Peak-value badges — quick-scan for "worst hour" without
              hunting the chart. Reuses maxRain/maxWind already computed. */}
          {fc.length > 0 && (
            <div className="flex items-center gap-3 text-[10px] text-ink-muted font-mono tnum">
              <span>峰值雨量 <span className="text-sev-info">{maxRain > 1 ? maxRain : '—'} mm/h</span></span>
              <span>峰值風速 <span className="text-sev-warn">{maxWind > 1 ? maxWind : '—'} km/h</span></span>
            </div>
          )}
        </div>
        <div className="bg-surface-panel border border-border-subtle rounded p-4 overflow-x-auto">
          {/* F5 (audit 2026-07-19): fc is [] when /api/weather/current
              succeeded (w.available true, page renders) but
              /api/weather/forecast failed or returned no rows. Previously
              this fell through to an empty flex row + legend with no
              explanation. Show an explicit EmptyState instead of a silent
              blank chart. */}
          {fc.length === 0 ? (
            <EmptyState icon={Icon.CloudRain} title="36 小時預報暫時無法載入"
              hint="目前僅有即時天氣資料，預報資料稍後會自動重試"/>
          ) : (
            <>
              <div className="relative" style={{ minWidth: '720px' }}>
                {/* F2: horizontal reference gridlines — 25%/50%/75%/100%
                    of maxRain span so operators can eyeball bar heights
                    against a scale instead of guessing. Absolutely
                    positioned behind the bars; pointer-events-none so
                    they don't interfere with any future click-to-detail. */}
                <div className="absolute inset-0 pointer-events-none" aria-hidden="true">
                  {[0.25, 0.5, 0.75, 1.0].map(frac => (
                    <div
                      key={frac}
                      className="absolute left-0 right-0 border-t border-border-subtle/40"
                      style={{ top: `${(1 - frac) * 60 + 20}px` }}
                    >
                      <span className="absolute -top-2 -left-1 text-[9px] font-mono text-ink-dim bg-surface-panel px-0.5">
                        {Math.round(maxRain * frac)}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="flex gap-1 items-end relative">
                  {fc.map((f, i) => {
                    // Distinguish "no data yet" (null from backend) from a genuine
                    // zero. Zero rain/wind should look like a flat bar; null should
                    // read as "we don't have this hour" (dashed placeholder + em-dash
                    // label) so operators don't misread absent data as calm weather.
                    const hasRain = Number.isFinite(f.rain);
                    const hasWind = Number.isFinite(f.wind);
                    // F1: first bucket = "now"; highlight it so the eye lands
                    // on the current-hour value immediately. Uses a subtle
                    // background band + "現在" label above.
                    const isNow = i === 0;
                    return (
                      <div
                        key={i}
                        className={`flex-1 flex flex-col items-center gap-1 min-w-[36px] px-0.5 rounded ${
                          isNow ? 'bg-sev-info/5 ring-1 ring-inset ring-sev-info/30' : ''
                        }`}
                      >
                        {isNow ? (
                          <div className="text-[9px] font-mono font-semibold text-sev-info tracking-wider">現在</div>
                        ) : (
                          <div className="text-[9px] h-3"></div>
                        )}
                        <div className="text-[10px] text-sev-info font-mono tnum">{hasRain ? f.rain : '—'}</div>
                        <div
                          className={`w-full rounded-t ${hasRain ? 'bg-sev-info/40' : 'border-t border-dashed border-sev-info/40'}`}
                          style={{ height: hasRain ? (f.rain / maxRain) * 60 + 'px' : '4px' }}
                        ></div>
                        <div
                          className={`w-full rounded-t ${hasWind ? 'bg-sev-warn/40' : 'border-t border-dashed border-sev-warn/40'}`}
                          style={{ height: hasWind ? (f.wind / maxWind) * 60 + 'px' : '4px' }}
                        ></div>
                        <div className="text-[10px] text-sev-warn font-mono tnum">{hasWind ? f.wind : '—'}</div>
                        <div className={`text-[10px] font-mono tnum mt-1 ${isNow ? 'text-sev-info font-semibold' : 'text-ink-muted'}`}>{f.h}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div className="flex items-center gap-4 mt-3 text-[10px] text-ink-muted flex-wrap">
                <span className="flex items-center gap-1"><span className="w-2 h-2 bg-sev-info/60"></span>雨量 mm/h</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 bg-sev-warn/60"></span>風速 km/h</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-sev-info/20 ring-1 ring-sev-info/30"></span>「現在」時段</span>
                <span className="text-ink-dim">來源：Open-Meteo</span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

Object.assign(window, { WeatherPage });
