// SDPRS — Weather Page

const { useState: useState_w, useEffect: useEffect_w } = React;

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
      setConfig({
        site_lat: cfg.site_lat != null ? cfg.site_lat : null,
        site_lon: cfg.site_lon != null ? cfg.site_lon : null,
        smg_station: cfg.smg_station || '',
        hko_station: cfg.hko_station || '',
        fallback_provider: cfg.fallback_provider || 'both',
      });
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
      await window.SDPRS_API.setWeatherConfig(payload);
      showToast && showToast('天氣設定已儲存 · 下一次更新（~10 秒）生效', 'info');
      onSaved && onSaved();
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
      >
        <Icon.Settings size={12}/>
        <span>天氣資料來源設定</span>
        <span className="text-ink-dim">{open ? '▲ 收合' : '▼ 展開'}</span>
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
                  <span className="text-[10px] text-ink-muted uppercase tracking-wider">站台緯度 (Open-Meteo)</span>
                  <input
                    type="number" step="any" min="-90" max="90"
                    placeholder="22.19"
                    value={config.site_lat != null ? config.site_lat : ''}
                    onChange={e => setConfig({...config, site_lat: e.target.value})}
                    className={inputCls}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-ink-muted uppercase tracking-wider">站台經度</span>
                  <input
                    type="number" step="any" min="-180" max="180"
                    placeholder="113.55"
                    value={config.site_lon != null ? config.site_lon : ''}
                    onChange={e => setConfig({...config, site_lon: e.target.value})}
                    className={inputCls}
                  />
                </label>
              </div>

              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  disabled={saving}
                  className="text-xs px-3 py-1 rounded border border-border-subtle hover:bg-surface-panel"
                >取消</button>
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={saving || loading}
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
    <div className="text-[9px] text-ink-dim mt-2 font-mono truncate" title={`資料來源: ${label}`}>
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
    <div className="text-[9px] text-ink-dim mt-2 font-mono space-y-0.5">
      {nonEmpty.map((it, i) => (
        <div key={i} className="truncate" title={`${it.label}來源: ${it.sourceLabel}`}>
          {it.label}: {it.sourceLabel}
        </div>
      ))}
    </div>
  );
};

const WeatherPage = ({ showToast, onRefresh } = {}) => {
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
          <div>
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
          <div className="flex-1"></div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-5">
          <div className="bg-surface-panel border border-border-subtle rounded p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted flex items-center gap-1"><Icon.Wind size={10}/> 風速</div>
            <div className="mt-1 flex items-baseline gap-1 whitespace-nowrap">
              <span className="text-4xl font-mono font-bold tnum">{wind.speed != null ? wind.speed : '—'}</span>
              <span className="text-ink-muted text-sm">km/h</span>
            </div>
            <div className="text-xs text-ink-muted mt-1 font-mono tnum whitespace-nowrap">陣風 {wind.gust != null ? wind.gust : '—'} · {wind.dir || '—'}</div>
            <SourceChip label={sources.wind_speed_ms}/>
          </div>
          <div className="bg-surface-panel border border-border-subtle rounded p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted flex items-center gap-1"><Icon.CloudRain size={10}/> 雨量</div>
            <div className="mt-1 flex items-baseline gap-1 whitespace-nowrap">
              <span className="text-4xl font-mono font-bold tnum text-sev-info">
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
              <span className="text-4xl font-mono font-bold tnum">{w.temp != null ? w.temp : '—'}</span>
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
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold">36 小時預報</h2>
          {/* D1 (audit 2026-07-16): "雷擊期間自動靜音" checkbox removed —
              placebo UI with no backend plumbing. See top-of-component
              comment for restoration guidance. */}
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
              <div className="flex gap-1 items-end" style={{ minWidth: '720px' }}>
                {fc.map((f, i) => {
                  // Distinguish "no data yet" (null from backend) from a genuine
                  // zero. Zero rain/wind should look like a flat bar; null should
                  // read as "we don't have this hour" (dashed placeholder + em-dash
                  // label) so operators don't misread absent data as calm weather.
                  const hasRain = Number.isFinite(f.rain);
                  const hasWind = Number.isFinite(f.wind);
                  return (
                    <div key={i} className="flex-1 flex flex-col items-center gap-1 min-w-[36px]">
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
                      <div className="text-[10px] text-ink-muted font-mono tnum mt-1">{f.h}</div>
                    </div>
                  );
                })}
              </div>
              <div className="flex items-center gap-4 mt-3 text-[10px] text-ink-muted">
                <span className="flex items-center gap-1"><span className="w-2 h-2 bg-sev-info/60"></span>雨量 mm/h</span>
                <span className="flex items-center gap-1"><span className="w-2 h-2 bg-sev-warn/60"></span>風速 km/h</span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

Object.assign(window, { WeatherPage });
