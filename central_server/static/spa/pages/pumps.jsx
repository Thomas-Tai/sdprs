// SDPRS — Pumps Page

const PumpsPage = ({ onSelectNode }) => {
  const pumps = window.NODES.filter(n => n.type === 'pump');
  return (
    <div className="h-full overflow-y-auto scroll-thin p-4">
      <div className="flex items-center gap-2 mb-4">
        <h1 className="text-sm font-semibold">抽水站</h1>
        <span className="text-xs text-ink-muted tnum">{pumps.length} 站</span>
      </div>
      {pumps.length === 0 ? (
        <div className="h-64 flex items-center justify-center">
          <EmptyState icon={Icon.Droplet} title="尚無泵浦資料"
            hint="伺服器尚未回報任何抽水站節點"/>
        </div>
      ) : null}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {pumps.map(p => {
          const danger = p.level > 85;
          const warn = p.level > 70;
          return (
            <div key={p.id}
              role="button"
              tabIndex={0}
              onClick={() => onSelectNode && onSelectNode(p)}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelectNode && onSelectNode(p); } }}
              className={`bg-surface-panel border rounded p-4 cursor-pointer hover:border-slate-600 transition-colors ${danger ? 'border-sev-critical/40' : warn ? 'border-sev-warn/40' : 'border-border-subtle'}`}>
              <div className="flex items-start justify-between mb-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-bold">{p.id}</span>
                    <span className="text-xs text-ink-secondary">{p.name}</span>
                  </div>
                  <div className="text-xs text-ink-muted mt-0.5">{p.location}</div>
                </div>
                <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border font-medium bg-sev-${danger||p.status==='critical'?'critical':warn?'warn':'ok'}/15 text-sev-${danger||p.status==='critical'?'critical':warn?'warn':'ok'} border-sev-${danger||p.status==='critical'?'critical':warn?'warn':'ok'}/30`}>
                  <span className={`w-1.5 h-1.5 rounded-full bg-sev-${danger||p.status==='critical'?'critical':warn?'warn':'ok'}`}></span>
                  {p.status === 'critical' ? '嚴重' : danger ? '高水位' : warn ? '警戒' : '正常'}
                </span>
              </div>

              {/* Sensor conflict — prominent critical banner, mirrors the glass-node critical alerts */}
              {p.sensorConflict && (
                <div role="alert" className="flex items-center gap-1.5 mb-3 px-2.5 py-1.5 rounded border border-sev-critical/40 bg-sev-critical/15 text-sev-critical text-xs font-semibold">
                  <Icon.AlertTriangle size={12} className="animate-live-blink flex-shrink-0"/>
                  <span>⚠ 感測器衝突 — 檢查浮球開關</span>
                </div>
              )}

              {/* Rain / dry-run protect badges */}
              {(p.raining || p.dryRunProtect) && (
                <div className="flex items-center gap-1.5 mb-3 flex-wrap">
                  {p.raining && (
                    <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border font-medium bg-sev-info/15 text-sev-info border-sev-info/30">
                      🌧 Raining
                    </span>
                  )}
                  {p.dryRunProtect && (
                    <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border font-medium bg-sev-warn/15 text-sev-warn border-sev-warn/30">
                      Dry-run protect (pump held OFF)
                    </span>
                  )}
                </div>
              )}

              {/* Water level visualization */}
              <div className="relative h-32 bg-surface-base border border-border-subtle rounded overflow-hidden">
                <div className={`absolute inset-x-0 bottom-0 transition-all duration-500 ${danger ? 'bg-sev-critical/40' : warn ? 'bg-sev-warn/40' : 'bg-sev-info/40'}`} style={{ height: p.level + '%' }}>
                  <div className={`h-1 ${danger ? 'bg-sev-critical' : warn ? 'bg-sev-warn' : 'bg-sev-info'}`}></div>
                </div>
                {/* Threshold markers */}
                <div className="absolute right-2 top-1 text-[9px] font-mono text-ink-dim tnum">100</div>
                <div className="absolute right-2 bottom-1 text-[9px] font-mono text-ink-dim tnum">0</div>
                <div className="absolute left-0 right-0 border-t border-dashed border-sev-critical/40" style={{ bottom: '85%' }}>
                  <span className="absolute -top-2 right-2 text-[9px] font-mono text-sev-critical tnum bg-surface-panel px-1">85 嚴重</span>
                </div>
                <div className="absolute left-0 right-0 border-t border-dashed border-sev-warn/40" style={{ bottom: '70%' }}>
                  <span className="absolute -top-2 right-2 text-[9px] font-mono text-sev-warn tnum bg-surface-panel px-1">70 警戒</span>
                </div>
                {/* Center value */}
                <div className="absolute inset-0 flex items-center justify-center">
                  <span className={`text-4xl font-mono font-bold tnum ${danger ? 'text-sev-critical' : warn ? 'text-sev-warn' : 'text-ink-primary'}`}>{p.level}<span className="text-base text-ink-muted">%</span></span>
                </div>
              </div>
              <div className="grid grid-cols-4 gap-2 mt-3 text-xs font-mono tnum">
                <div>
                  <div className="text-[10px] text-ink-muted">啟動頻率</div>
                  <div className={p.cycles > 20 ? 'text-sev-critical font-semibold' : p.cycles > 15 ? 'text-sev-warn' : 'text-ink-secondary'}>
                    每 {p.cycles > 0 ? (60/p.cycles).toFixed(1) : '—'} 分
                  </div>
                  <div className="text-[10px] text-ink-dim">本時 {p.cycles}×</div>
                </div>
                <div>
                  <div className="text-[10px] text-ink-muted">趨勢</div>
                  <div className="text-ink-secondary inline-flex items-center gap-0.5">
                    {p.trend === 'up' ? <><Icon.ArrowUp size={10} className="text-sev-warn"/>升</> : p.trend === 'down' ? <><Icon.ArrowDown size={10} className="text-sev-ok"/>降</> : <><Icon.ArrowRight size={10}/>平</>}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-ink-muted">電壓</div>
                  <div className={p.voltage != null && p.voltage < 12 ? 'text-sev-warn' : 'text-ink-secondary'}>{p.voltage != null ? p.voltage + 'V' : '—'}</div>
                </div>
                <div>
                  <div className="text-[10px] text-ink-muted">電源</div>
                  <div className={p.power === 'mains' ? 'text-sev-ok' : p.power === 'ups' ? 'text-sev-warn' : 'text-sev-critical'}>
                    {p.power === 'mains' ? '市電' : p.power === 'ups' ? 'UPS' : '電池'}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

Object.assign(window, { PumpsPage });
