// SDPRS — Weather Page

const { useState: useState_p } = React;

const WeatherPage = () => {
  // Guard: window.WEATHER may be null before the first weather event arrives.
  // All property accesses below must default so we don't throw pre-hydration.
  const w = window.WEATHER || {};
  const fc = w.forecast || [];
  const wind = w.wind || {};
  const rain = w.rain || {};
  const lightning = w.lightning || {};
  // Filter nulls before Math.max — api.jsx C5 fix emits `null` for future
  // hours whose data hasn't landed yet; a single NaN in Math.max poisons the
  // whole axis and every bar height becomes `NaNpx` → blank chart.
  const maxWind = fc.length ? Math.max(1, ...fc.map(f => f.wind).filter(v => Number.isFinite(v))) : 1;
  const maxRain = fc.length ? Math.max(1, ...fc.map(f => f.rain).filter(v => Number.isFinite(v))) : 1;
  // TODO(dashboard-audit-2026-07-15): backend action for auto-mute on lightning.
  // Local state only until the pipeline (weather WS event -> snooze fan-out) ships.
  const [autoMuteLightning, setAutoMuteLightning] = useState_p(true);
  if (!w.available) {
    return (
      <div className="h-full flex items-center justify-center">
        <EmptyState icon={Icon.CloudRain} title="天氣資料未就緒"
          hint="後端天氣服務尚未啟用,或暫時無法取得資料"/>
      </div>
    );
  }
  return (
    <div className="h-full overflow-y-auto scroll-thin">
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
                  測站 {w.station || '—'} · 來源 {w.source}{w.stale ? ' · 資料較舊' : ''}
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
          </div>
          <div className="bg-surface-panel border border-border-subtle rounded p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted flex items-center gap-1"><Icon.CloudRain size={10}/> 雨量</div>
            <div className="mt-1 flex items-baseline gap-1 whitespace-nowrap">
              <span className="text-4xl font-mono font-bold tnum text-sev-info">
                {rain.now != null ? rain.now : '—'}
              </span>
              {/* api.jsx is splitting rain into 10min / 1h / 24h. `now` is 10-min accumulation once that lands. */}
              <span className="text-ink-muted text-sm">mm (10min)</span>
            </div>
            <div className="text-xs text-ink-muted mt-1 font-mono tnum whitespace-nowrap">
              24h 累計 {rain.day != null ? rain.day : '—'} mm
            </div>
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
          </div>
          <div className="bg-surface-panel border border-border-subtle rounded p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted flex items-center gap-1"><Icon.Thermometer size={10}/> 環境</div>
            <div className="mt-1 flex items-baseline gap-1 whitespace-nowrap">
              <span className="text-4xl font-mono font-bold tnum">{w.temp != null ? w.temp : '—'}</span>
              <span className="text-ink-muted text-sm">°C</span>
            </div>
            <div className="text-xs text-ink-muted mt-1 font-mono tnum whitespace-nowrap">濕度 {w.humidity != null ? w.humidity : '—'}%{w.pressure != null ? ` · ${w.pressure}hPa` : ''}</div>
          </div>
        </div>
      </div>

      {/* Forecast */}
      <div className="p-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold">36 小時預報</h2>
          <label className="flex items-center gap-2 text-xs text-ink-secondary" title="偏好僅存於本次 session — 後端行動尚未接線">
            <input type="checkbox"
              checked={autoMuteLightning}
              onChange={e => setAutoMuteLightning(e.target.checked)}
              className="rounded border-border-strong bg-surface-base text-sev-info"/>
            雷擊期間自動靜音
          </label>
        </div>
        <div className="bg-surface-panel border border-border-subtle rounded p-4 overflow-x-auto">
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
        </div>
      </div>
    </div>
  );
};

Object.assign(window, { WeatherPage });
