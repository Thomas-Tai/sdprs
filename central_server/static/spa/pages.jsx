// SDPRS — Pages

const { useState: useState_p, useEffect: useEffect_p, useMemo: useMemo_p, useRef: useRef_p } = React;

// =================================================================
// ALERTS PAGE
// =================================================================

const SystemOKState = () => {
  const onlineCount = window.NODES.filter(n => n.status === 'online').length;
  const total = window.NODES.length;
  const lastCheck = new Date().toLocaleTimeString('zh-TW', { hour12: false });
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-6">
      <div className="relative w-16 h-16 rounded-full bg-sev-ok/15 flex items-center justify-center mb-4">
        <Icon.ShieldCheck size={32} className="text-sev-ok" strokeWidth={2}/>
        <span className="absolute -bottom-0.5 -right-0.5 w-4 h-4 rounded-full bg-sev-ok ring-2 ring-surface-base"></span>
      </div>
      <div className="text-base text-ink-primary font-medium">目前沒有作用中的警報</div>
      <div className="text-sm text-sev-ok mt-1.5 font-medium tnum">{onlineCount} / {total} 節點正常回報</div>
      <div className="text-xs text-ink-muted mt-3 font-mono tnum flex items-center gap-2">
        <span className="w-1.5 h-1.5 rounded-full bg-sev-ok animate-live-blink"></span>
        最後檢查 {lastCheck} · WebSocket 連線中
      </div>
    </div>
  );
};

const AlertRow = ({ alert, selected, onSelect, density, checked, onCheck, flash, siblingCount }) => {
  const m = window.sevMeta[alert.sev];
  const node = window.NODES.find(n => n.id === alert.node);
  const rowH = density === 'compact' ? 'h-9' : 'h-12';
  const isUrgent = alert.state === 'pending' && alert.sev === 'critical' && alert.ageSec < 60;
  return (
    <div
      onClick={() => onSelect(alert.id)}
      className={`relative ${rowH} flex items-center pl-3 pr-3 border-b border-border-subtle/60 cursor-pointer transition-colors sev-bar ${m.bar} ${selected ? 'row-selected' : 'hover:bg-surface-elevated/60'} ${flash ? 'row-flash' : ''} ${isUrgent ? 'animate-pulse-critical' : ''}`}
    >
      <div className="w-6 flex-shrink-0 flex items-center justify-center" onClick={e => e.stopPropagation()}>
        <input type="checkbox" checked={checked} onChange={() => onCheck(alert.id)}
          className="w-3.5 h-3.5 rounded border-border-strong bg-surface-base text-sev-info focus:ring-sev-info"/>
      </div>
      <div className="w-4 flex-shrink-0 flex items-center justify-center">
        {!alert.seen && alert.state === 'pending' && (
          <span className="w-1.5 h-1.5 rounded-full bg-sev-info animate-live-blink" title="未閱"></span>
        )}
      </div>
      <div className="w-20 flex-shrink-0"><AgeCell sec={alert.ageSec}/></div>
      <div className="w-24 flex-shrink-0 font-mono text-xs tnum text-ink-secondary flex items-center gap-1">
        <m.Icon/>
        <span>{alert.node}</span>
        {siblingCount > 0 && (
          <span title={`${alert.node} 同節點另有 ${siblingCount} 警報`} className="ml-0.5 text-[9px] font-bold tnum bg-sev-warn/20 text-sev-warn px-1 rounded">+{siblingCount}</span>
        )}
      </div>
      <div className="flex-1 min-w-0 flex items-center gap-2">
        <span className="text-xs text-ink-secondary truncate">{window.alertTypeLabel(alert.type)} <span className="text-ink-muted">· {node?.location}</span></span>
        {alert.prevShift && (
          <span className="inline-flex items-center gap-0.5 text-[9px] font-mono px-1 h-3.5 rounded bg-purple-500/15 text-purple-400 border border-purple-500/30 flex-shrink-0" title="從上一班次承接">↶ 上班</span>
        )}
        {alert.viewer && (
          <span className="inline-flex items-center gap-0.5 text-[9px] font-mono px-1 h-3.5 rounded bg-sev-warn/15 text-sev-warn border border-sev-warn/30 flex-shrink-0" title={`${alert.viewer} 正在查看`}>
            <Icon.Eye size={8}/>{alert.viewer}
          </span>
        )}
      </div>
      <div className="w-20 flex-shrink-0"><SeverityBadge sev={alert.sev}/></div>
      <div className="w-20 flex-shrink-0"><StateBadge state={alert.state}/></div>
      <div className="w-24 flex-shrink-0 font-mono text-[11px] tnum text-ink-muted text-right">
        {alert.ackBy || '—'}
      </div>
    </div>
  );
};

const FilterChip = ({ active, onClick, children, count }) => (
  <button onClick={onClick}
    className={`inline-flex items-center gap-1 px-2 h-6 rounded text-xs border transition-colors ${active ? 'bg-sev-info/15 text-sev-info border-sev-info/40' : 'bg-surface-elevated text-ink-secondary border-border-subtle hover:border-border-strong'}`}>
    {children}
    {count != null && <span className="font-mono tnum text-[10px] text-ink-muted">{count}</span>}
  </button>
);

const AlertsPage = ({ density, selectedId, setSelectedId, alerts, onAck, onResolve, onSnooze, ackedIds, resolveNote, setResolveNote }) => {
  const [tab, setTab] = useState_p('active');
  const [filterSev, setFilterSev] = useState_p('all');
  const [checked, setChecked] = useState_p(new Set());
  const [search, setSearch] = useState_p('');
  const [snoozeOpen, setSnoozeOpen] = useState_p(false);

  const activeList = tab === 'active'
    ? alerts.filter(a => a.state !== 'resolved')
    : window.HISTORY_ALERTS;

  const filtered = useMemo_p(() => {
    return activeList.filter(a => {
      if (filterSev !== 'all' && a.sev !== filterSev) return false;
      if (search && !(`${a.id} ${a.node} ${a.message}`.toLowerCase().includes(search.toLowerCase()))) return false;
      return true;
    });
  }, [activeList, filterSev, search]);

  const selected = alerts.find(a => a.id === selectedId) || window.HISTORY_ALERTS.find(a => a.id === selectedId);

  const counts = {
    all: activeList.length,
    critical: activeList.filter(a => a.sev === 'critical').length,
    warn: activeList.filter(a => a.sev === 'warn').length,
    info: activeList.filter(a => a.sev === 'info').length,
  };

  const toggleCheck = (id) => {
    setChecked(prev => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  return (
    <div className="h-full grid grid-cols-[3fr_2fr] xl:grid-cols-[7fr_5fr]">
      {/* LEFT: List */}
      <div className="border-r border-border-subtle flex flex-col min-h-0">
        {/* Tabs + filters */}
        <div className="border-b border-border-subtle bg-surface-panel">
          <div className="flex items-center px-3 pt-2.5">
            <div className="flex gap-1 text-sm">
              <button onClick={() => setTab('active')} className={`px-3 py-1.5 rounded-t border-b-2 transition-colors flex items-center gap-2 ${tab === 'active' ? 'border-sev-info text-ink-primary' : 'border-transparent text-ink-muted hover:text-ink-secondary'}`}>
                作用中 <span className={`text-[10px] font-mono tnum px-1.5 rounded ${tab==='active' ? 'bg-sev-critical text-white' : 'bg-surface-elevated text-ink-muted'}`}>{activeList.length}</span>
              </button>
              <button onClick={() => setTab('history')} className={`px-3 py-1.5 rounded-t border-b-2 transition-colors ${tab === 'history' ? 'border-sev-info text-ink-primary' : 'border-transparent text-ink-muted hover:text-ink-secondary'}`}>
                歷史
              </button>
            </div>
            <div className="flex-1"></div>
            <div className="relative">
              <Icon.Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-ink-muted"/>
              <input type="text" value={search} onChange={e => setSearch(e.target.value)}
                placeholder="搜尋..."
                className="h-7 pl-7 pr-2 w-48 bg-surface-base border border-border-subtle rounded text-xs placeholder-ink-muted focus:border-sev-info focus:outline-none"/>
              <span className="absolute right-2 top-1/2 -translate-y-1/2"><Kbd>/</Kbd></span>
            </div>
          </div>
          <div className="flex items-center gap-1.5 px-3 py-2 flex-wrap">
            <span className="text-[10px] text-ink-muted uppercase tracking-wider mr-1">嚴重度</span>
            <FilterChip active={filterSev === 'all'} onClick={() => setFilterSev('all')}>全部 <span className="font-mono tnum text-[10px] text-ink-muted">{counts.all}</span></FilterChip>
            <FilterChip active={filterSev === 'critical'} onClick={() => setFilterSev('critical')}>
              <span className="w-1.5 h-1.5 rounded-full bg-sev-critical"></span>嚴重 <span className="font-mono tnum text-[10px] text-ink-muted">{counts.critical}</span>
            </FilterChip>
            <FilterChip active={filterSev === 'warn'} onClick={() => setFilterSev('warn')}>
              <span className="w-1.5 h-1.5 rounded-full bg-sev-warn"></span>警告 <span className="font-mono tnum text-[10px] text-ink-muted">{counts.warn}</span>
            </FilterChip>
            <FilterChip active={filterSev === 'info'} onClick={() => setFilterSev('info')}>
              <span className="w-1.5 h-1.5 rounded-full bg-sev-info"></span>資訊 <span className="font-mono tnum text-[10px] text-ink-muted">{counts.info}</span>
            </FilterChip>
            <div className="w-px h-4 bg-border-subtle mx-1"></div>
            <FilterChip>節點 <Icon.ChevronDown size={10}/></FilterChip>
            <FilterChip>時間範圍 <Icon.ChevronDown size={10}/></FilterChip>
            <FilterChip>類型 <Icon.ChevronDown size={10}/></FilterChip>
          </div>
        </div>

        {/* Bulk bar */}
        {checked.size > 0 && (
          <div className="bg-sev-info/10 border-b border-sev-info/30 px-3 py-2 flex items-center gap-2 text-xs">
            <span className="text-sev-info font-medium tnum">已選 {checked.size}</span>
            <span className="text-ink-muted">|</span>
            <button className="px-2 py-1 bg-surface-elevated border border-border-strong rounded hover:bg-surface-overlay">批次認領</button>
            <button className="px-2 py-1 bg-surface-elevated border border-border-strong rounded hover:bg-surface-overlay">批次解決</button>
            <input type="text" placeholder="備註..." className="flex-1 h-7 px-2 bg-surface-base border border-border-subtle rounded text-xs"/>
            <button onClick={() => setChecked(new Set())} className="text-ink-muted hover:text-ink-primary"><Icon.X size={14}/></button>
          </div>
        )}

        {/* Column headers */}
        <div className="h-7 flex items-center pl-3 pr-3 bg-surface-base border-b border-border-strong text-[10px] text-ink-muted uppercase tracking-wider font-semibold">
          <div className="w-6 flex-shrink-0"></div>
          <div className="w-4 flex-shrink-0"></div>
          <div className="w-20 flex-shrink-0">等待</div>
          <div className="w-24 flex-shrink-0">節點</div>
          <div className="flex-1 min-w-0">類型 / 訊息</div>
          <div className="w-20 flex-shrink-0">嚴重度</div>
          <div className="w-20 flex-shrink-0">狀態</div>
          <div className="w-24 flex-shrink-0 text-right">操作者</div>
        </div>

        {/* Rows */}
        <div className="flex-1 overflow-y-auto scroll-thin">
          {filtered.length === 0 ? (
            <SystemOKState/>
          ) : (
            filtered.map(a => {
              const sib = activeList.filter(x => x.node === a.node && x.id !== a.id).length;
              return (
                <AlertRow key={a.id} alert={a}
                  selected={selectedId === a.id}
                  onSelect={setSelectedId}
                  density={density}
                  checked={checked.has(a.id)}
                  onCheck={toggleCheck}
                  flash={ackedIds.has(a.id)}
                  siblingCount={sib}
                />
              );
            })
          )}
        </div>
      </div>

      {/* RIGHT: Detail */}
      <div className="flex flex-col min-h-0 bg-surface-base">
        {selected ? <AlertDetail alert={selected} onAck={onAck} onResolve={onResolve} onSnooze={onSnooze} resolveNote={resolveNote} setResolveNote={setResolveNote} snoozeOpen={snoozeOpen} setSnoozeOpen={setSnoozeOpen} allAlerts={alerts} onSelectAlert={setSelectedId}/> : (
          <EmptyState icon={Icon.AlertCircle} title="選擇警報以查看詳情" hint="使用 ↑/↓ 鍵或滑鼠點選"/>
        )}
      </div>
    </div>
  );
};

const AlertDetail = ({ alert, onAck, onResolve, onSnooze, resolveNote, setResolveNote, snoozeOpen, setSnoozeOpen, allAlerts, onSelectAlert }) => {
  const node = window.NODES.find(n => n.id === alert.node);
  const m = window.sevMeta[alert.sev];
  const [videoSpeed, setVideoSpeed] = useState_p(1);
  const runbook = window.RUNBOOKS[alert.type];
  const history = window.NODE_HISTORY[alert.node] || [];
  const siblings = (allAlerts || []).filter(a => a.node === alert.node && a.id !== alert.id && a.state !== 'resolved');
  return (
    <>
      {/* Header */}
      <div className="border-b border-border-subtle px-4 py-3">
        {/* Co-located alerts on same node */}
        {siblings.length > 0 && (
          <div className="mb-2 flex items-center gap-2 text-xs bg-sev-warn/10 border border-sev-warn/30 rounded px-2 py-1.5">
            <Icon.AlertCircle size={12} className="text-sev-warn"/>
            <span className="text-sev-warn font-medium">{alert.node} 同節點另有 {siblings.length} 個作用中警報</span>
            <div className="flex gap-1 ml-auto">
              {siblings.map(s => (
                <button key={s.id} onClick={() => onSelectAlert(s.id)}
                  className="inline-flex items-center gap-1 text-[10px] font-mono px-1.5 h-5 rounded border border-sev-warn/40 hover:bg-sev-warn/20 transition-colors">
                  <SeverityBadge sev={s.sev} withLabel={false}/>
                  {window.alertTypeLabel(s.type)}
                </button>
              ))}
            </div>
          </div>
        )}
        {alert.viewer && (
          <div className="mb-2 flex items-center gap-2 text-xs bg-sev-warn/10 border border-sev-warn/30 rounded px-2 py-1.5">
            <Icon.Eye size={12} className="text-sev-warn"/>
            <span className="text-sev-warn font-medium">{alert.viewer} 正在查看此警報</span>
            <div className="flex-1"></div>
            <button className="text-[10px] font-mono text-sev-warn hover:text-ink-primary underline">搶下處置權</button>
          </div>
        )}
        <div className="flex items-start gap-3">
          <div className={`w-8 h-8 rounded flex items-center justify-center bg-${m.color}/15 text-${m.color} flex-shrink-0`}>
            <m.Icon/>
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <SeverityBadge sev={alert.sev}/>
              <StateBadge state={alert.state}/>
              {alert.prevShift && (
                <span className="inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-400 border border-purple-500/30">↶ 上班次承接</span>
              )}
              <span className="text-[11px] font-mono text-ink-muted tnum">{alert.id}</span>
            </div>
            <h2 className="text-base font-semibold mt-1.5">{window.alertTypeLabel(alert.type)}</h2>
            <p className="text-sm text-ink-secondary mt-0.5">{alert.message}</p>
          </div>
          <div className="text-right text-xs">
            <div className="font-mono tnum text-ink-muted">{window.fmtAge(alert.ageSec)} 前</div>
            <div className="font-mono tnum text-ink-dim mt-0.5">{alert.timeline?.[0]?.t || '—'}</div>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-4 text-xs text-ink-muted">
          <span className="flex items-center gap-1.5"><Icon.MapPin size={12}/> {node?.location}</span>
          <span className="flex items-center gap-1.5"><Icon.Server size={12}/> <span className="font-mono">{alert.node}</span></span>
          <span className="flex items-center gap-1.5"><Icon.Camera size={12}/> <span>{node?.name}</span></span>
        </div>
      </div>

      {/* Video / snapshot */}
      <div className="px-4 pt-3">
        <div className="relative aspect-video w-full rounded overflow-hidden border border-border-strong snapshot-placeholder">
          <div className="absolute inset-0 flex flex-col items-center justify-center text-ink-muted">
            <Icon.Camera size={36} strokeWidth={1.25}/>
            <div className="font-mono text-[11px] mt-2 tnum">HLS · {alert.node} · 1920×1080 · 4.2s clip</div>
            <div className="font-mono text-[10px] mt-0.5 text-ink-dim">[ 影片預覽 placeholder ]</div>
          </div>
          {/* Top overlay */}
          <div className="absolute top-2 left-2 right-2 flex items-center justify-between">
            <Pill tone={alert.sev === 'critical' ? 'critical' : 'warn'} dot pulse><span className="font-mono">REC</span></Pill>
            <Pill tone="muted" className="!bg-black/60 !text-white !border-black/0 font-mono">{alert.timeline?.[0]?.t}</Pill>
          </div>
          {/* Bottom controls */}
          <div className="absolute bottom-2 left-2 right-2 flex items-center gap-2">
            <button className="w-7 h-7 rounded bg-black/60 text-white hover:bg-black/80 flex items-center justify-center"><Icon.Play size={14}/></button>
            <div className="flex-1 h-1 bg-white/20 rounded">
              <div className="h-full w-1/3 bg-sev-info rounded"></div>
            </div>
            <span className="font-mono text-[10px] text-white/80 tnum">1.4s / 4.2s</span>
            {/* Speed control */}
            <div className="flex bg-black/60 rounded text-[10px] font-mono">
              {[1, 1.5, 2].map(s => (
                <button key={s} onClick={() => setVideoSpeed(s)} className={`px-1.5 h-7 ${videoSpeed === s ? 'bg-sev-info text-white' : 'text-white/70 hover:text-white'}`}>
                  {s}×
                </button>
              ))}
            </div>
            <button title="逐格後退" className="w-7 h-7 rounded bg-black/60 text-white hover:bg-black/80 flex items-center justify-center text-xs font-mono">⤺</button>
            <button title="逐格前進" className="w-7 h-7 rounded bg-black/60 text-white hover:bg-black/80 flex items-center justify-center text-xs font-mono">⤻</button>
            <button className="w-7 h-7 rounded bg-black/60 text-white hover:bg-black/80 flex items-center justify-center"><Icon.Maximize size={14}/></button>
            <button className="w-7 h-7 rounded bg-black/60 text-white hover:bg-black/80 flex items-center justify-center"><Icon.Download size={14}/></button>
          </div>
        </div>
        {/* Mini map / floorplan */}
        <Floorplan highlightNode={alert.node}/>

        {/* Previous events at this node — carousel */}
        {history.length > 0 && (
          <div className="mt-2 bg-surface-panel border border-border-subtle rounded p-2">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">此節點近期事件 ({history.length})</span>
              <button className="text-[10px] text-sev-info hover:underline">檢視全部 →</button>
            </div>
            <div className="flex gap-1.5 overflow-x-auto scroll-thin pb-1">
              {history.map((h, i) => (
                <div key={i} className={`flex-shrink-0 w-24 bg-surface-elevated rounded border ${i === 0 ? 'border-sev-info/40' : 'border-border-subtle'} overflow-hidden hover:border-border-strong cursor-pointer transition-colors`}>
                  <div className={`relative aspect-video snapshot-placeholder`}>
                    <div className="absolute inset-0 flex items-center justify-center text-ink-muted/40">
                      <Icon.Camera size={20} strokeWidth={1}/>
                    </div>
                    <div className={`absolute top-1 left-1 w-1.5 h-1.5 rounded-full bg-sev-${h.sev === 'critical' ? 'critical' : h.sev === 'warn' ? 'warn' : 'info'}`}></div>
                  </div>
                  <div className="p-1.5">
                    <div className="font-mono text-[9px] text-ink-muted tnum">{h.t}</div>
                    <div className="text-[10px] text-ink-secondary truncate leading-tight mt-0.5">{window.alertTypeLabel(h.type)}</div>
                    <div className="text-[9px] text-ink-dim truncate leading-tight">{h.resolution}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Runbook — suggested actions */}
        {runbook && alert.state !== 'resolved' && (
          <div className="mt-2 bg-sev-info/5 border border-sev-info/30 rounded p-3">
            <div className="flex items-center justify-between mb-2">
              <div className="text-[10px] uppercase tracking-wider text-sev-info font-semibold flex items-center gap-1.5">
                <Icon.ClipboardList size={11}/> 建議下一步 · Runbook
              </div>
              <button className="text-[10px] text-ink-muted hover:text-ink-primary">隱藏</button>
            </div>
            <p className="text-xs text-ink-secondary leading-relaxed mb-2.5">{runbook.summary}</p>
            <div className="space-y-1">
              {runbook.actions.map((a, i) => (
                <button key={i} className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded border text-left transition-colors ${a.primary ? 'bg-sev-info/15 border-sev-info/40 hover:bg-sev-info/25' : a.escalate ? 'bg-sev-critical/10 border-sev-critical/30 hover:bg-sev-critical/20' : 'bg-surface-elevated border-border-subtle hover:border-border-strong'}`}>
                  <span className={`w-4 h-4 rounded flex items-center justify-center text-[10px] font-mono font-bold flex-shrink-0 ${a.primary ? 'bg-sev-info text-white' : a.escalate ? 'bg-sev-critical text-white' : 'bg-surface-overlay text-ink-muted'}`}>{i+1}</span>
                  <div className="flex-1 min-w-0">
                    <div className={`text-xs font-medium ${a.primary ? 'text-sev-info' : a.escalate ? 'text-sev-critical' : 'text-ink-primary'}`}>
                      {a.label}
                      {a.primary && <span className="ml-1.5 text-[9px] font-mono uppercase opacity-80">優先</span>}
                      {a.escalate && <span className="ml-1.5 text-[9px] font-mono uppercase opacity-80">升級</span>}
                    </div>
                    <div className="text-[10px] text-ink-muted mt-0.5">{a.hint}</div>
                  </div>
                  {a.est && <span className="text-[10px] font-mono tnum text-sev-ok bg-sev-ok/10 px-1.5 py-0.5 rounded">{a.est}</span>}
                  <Icon.ChevronRight size={12} className="text-ink-muted flex-shrink-0"/>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Timeline */}
      <div className="px-4 py-4 flex-1 overflow-y-auto scroll-thin">
        <div className="flex items-center gap-3 mb-3 text-xs border-b border-border-subtle pb-2">
          <button className="font-semibold text-ink-primary border-b-2 border-sev-info pb-1.5 -mb-2">事件時間軸</button>
          <button className="text-ink-muted hover:text-ink-secondary">節點資訊</button>
          <button className="text-ink-muted hover:text-ink-secondary">處理紀錄</button>
        </div>
        <div className="relative pl-5">
          <div className="absolute left-1.5 top-1 bottom-1 w-px bg-border-strong"></div>
          {alert.timeline?.map((ev, i) => (
            <div key={i} className="relative pb-3 last:pb-0">
              <div className={`absolute -left-[18px] top-1 w-2 h-2 rounded-full ${i === 0 ? 'bg-sev-info ring-2 ring-sev-info/30' : 'bg-ink-muted'}`}></div>
              <div className="flex items-baseline gap-2 text-xs">
                <span className="font-mono tnum text-ink-muted">{ev.t}</span>
                <span className="font-semibold text-ink-primary text-[11px] tracking-wider font-mono">{ev.label}</span>
              </div>
              <div className="text-xs text-ink-secondary mt-0.5 font-mono">{ev.detail}</div>
            </div>
          ))}
          {alert.state === 'acknowledged' && (
            <div className="relative pb-3">
              <div className="absolute -left-[18px] top-1 w-2 h-2 rounded-full bg-sev-info ring-2 ring-sev-info/30"></div>
              <div className="flex items-baseline gap-2 text-xs">
                <span className="font-mono tnum text-ink-muted">{alert.ackAt}</span>
                <span className="font-semibold text-sev-info text-[11px] tracking-wider font-mono">ACKNOWLEDGED</span>
              </div>
              <div className="text-xs text-ink-secondary mt-0.5">by <span className="font-mono">{alert.ackBy}</span></div>
            </div>
          )}
        </div>
      </div>

      {/* Action bar */}
      <div className="border-t border-border-strong bg-surface-panel px-4 py-3 space-y-2">
        {/* Ack-age warning when stale */}
        {alert.state === 'acknowledged' && alert.ackAgeSec > 1500 && (
          <div className="flex items-center gap-2 text-xs px-2 py-1.5 bg-sev-warn/10 border border-sev-warn/30 rounded text-sev-warn">
            <Icon.Clock size={12}/>
            <span>已認領 <span className="font-mono tnum font-semibold">{window.fmtAge(alert.ackAgeSec)}</span> by {alert.ackBy} — 仍未解決</span>
          </div>
        )}

        {/* Resolve template chips */}
        {alert.state === 'acknowledged' && (
          <div>
            <div className="text-[10px] text-ink-muted uppercase tracking-wider mb-1.5">處置模板 (數字鍵套用)</div>
            <div className="flex flex-wrap gap-1">
              {window.RESOLVE_TEMPLATES.map((t, i) => (
                <button key={t} onClick={() => setResolveNote(t)}
                  className={`inline-flex items-center gap-1 text-[11px] px-2 h-6 rounded border transition-colors ${resolveNote === t ? 'bg-sev-info/20 border-sev-info text-sev-info' : 'bg-surface-elevated border-border-subtle text-ink-secondary hover:border-border-strong'}`}>
                  <span className="kbd !h-3.5 !min-w-[14px] !text-[9px] !px-0.5">{i+1}</span>
                  {t}
                </button>
              ))}
            </div>
          </div>
        )}

        {alert.state !== 'resolved' && (
          <textarea value={resolveNote} onChange={e => setResolveNote(e.target.value)}
            placeholder={alert.state === 'acknowledged' ? '處置備註 (解決時必填) — 可套用上方模板' : '備註 (選填)...'}
            rows="2"
            className="w-full px-2 py-1.5 bg-surface-base border border-border-subtle rounded text-xs placeholder-ink-muted resize-none focus:border-sev-info focus:outline-none"/>
        )}
        <div className="flex items-center gap-2">
          {alert.state === 'pending' && (
            <>
              <button onClick={() => onAck(alert.id)}
                title="我正在處理此事件 — 不會關閉警報。按下後自動跳至下一筆。"
                className="flex-1 h-9 bg-sev-info hover:bg-blue-600 text-white rounded font-semibold text-sm flex items-center justify-center gap-2 transition-colors">
                <Icon.Check size={16} strokeWidth={2.5}/>
                <span>認領</span>
                <span className="text-[10px] opacity-80 font-normal">→ 下一筆</span>
                <Kbd>A</Kbd>
              </button>
            </>
          )}
          {alert.state === 'acknowledged' && (
            <>
              <button onClick={() => onResolve(alert.id, resolveNote)}
                disabled={!resolveNote}
                title="事件已結束 — 將從作用中列表移除"
                className="flex-1 h-9 bg-sev-ok hover:bg-emerald-600 disabled:bg-sev-ok/30 disabled:cursor-not-allowed text-white rounded font-semibold text-sm flex items-center justify-center gap-2 transition-colors">
                <Icon.CheckCircle size={16} strokeWidth={2.5}/>
                <span>解決</span>
                {!resolveNote && <span className="text-[10px] opacity-80 font-normal">(需備註)</span>}
                <Kbd>R</Kbd>
              </button>
              <button title="撤銷認領 — 重新設為待處理" className="h-9 px-2.5 bg-surface-elevated border border-border-strong rounded text-xs text-ink-muted hover:text-ink-primary hover:bg-surface-overlay">
                撤銷
              </button>
            </>
          )}
          {alert.state === 'resolved' && (
            <div className="flex-1 h-9 bg-sev-ok/10 border border-sev-ok/30 text-sev-ok rounded font-medium text-sm flex items-center justify-center gap-2">
              <Icon.CheckCircle size={14}/> 已解決於 {alert.resAt} by {alert.resBy}
            </div>
          )}
          {alert.state !== 'resolved' && (
            <div className="relative">
              <button onClick={() => setSnoozeOpen(o => !o)}
                title="延期此節點 — 將不再發出告警音"
                className="h-9 px-3 bg-surface-elevated hover:bg-surface-overlay border border-border-strong rounded text-sm flex items-center gap-1.5 transition-colors">
                <Icon.Clock size={14}/> 延期節點 <Kbd>S</Kbd> <Icon.ChevronDown size={12}/>
              </button>
              {snoozeOpen && (
                <div className="absolute bottom-full mb-1 right-0 bg-surface-overlay border border-border-strong rounded shadow-xl py-1 min-w-[200px] z-10">
                  <div className="px-3 pt-1 pb-1.5 text-[10px] text-ink-muted">延期 {alert.node} · 期間將不發告警音</div>
                  {[30,60,120].map(m => (
                    <button key={m} onClick={() => { onSnooze(alert.id, m); setSnoozeOpen(false); }} className="w-full px-3 py-1.5 text-left text-sm hover:bg-surface-panel flex items-center justify-between">
                      <span>{m} 分鐘</span><span className="font-mono text-xs text-ink-muted tnum">{m}m</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
        {alert.state === 'resolved' && alert.note && (
          <div className="text-xs text-ink-muted bg-surface-base rounded p-2 border border-border-subtle">
            <span className="text-ink-dim">處置備註: </span>{alert.note}
          </div>
        )}
      </div>
    </>
  );
};

// ---------- Floorplan placeholder ----------

const Floorplan = ({ highlightNode }) => {
  // Simple SVG floorplan with pin positions
  const PINS = {
    'G-01': { x: 80, y: 60 },  'G-02': { x: 200, y: 30 }, 'G-03': { x: 280, y: 80 },
    'G-04': { x: 220, y: 140 }, 'G-05': { x: 80, y: 140 }, 'G-06': { x: 30, y: 90 },
    'G-07': { x: 150, y: 90 }, 'G-08': { x: 260, y: 30 },
    'P-01': { x: 130, y: 100 }, 'P-02': { x: 170, y: 100 },
    'P-03': { x: 240, y: 110 }, 'P-04': { x: 90, y: 110 },
  };
  return (
    <div className="mt-2 bg-surface-panel border border-border-subtle rounded p-2">
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">場域配置</span>
        <span className="text-[10px] text-ink-dim font-mono">B1F · 平面圖 [placeholder]</span>
      </div>
      <svg viewBox="0 0 320 170" className="w-full h-24" preserveAspectRatio="xMidYMid meet">
        {/* Rooms */}
        <rect x="10" y="10" width="140" height="70" fill="rgba(30,41,59,0.4)" stroke="#334155" strokeWidth="1"/>
        <rect x="160" y="10" width="150" height="55" fill="rgba(30,41,59,0.4)" stroke="#334155" strokeWidth="1"/>
        <rect x="10" y="90" width="100" height="70" fill="rgba(30,41,59,0.4)" stroke="#334155" strokeWidth="1"/>
        <rect x="120" y="75" width="100" height="50" fill="rgba(30,41,59,0.4)" stroke="#334155" strokeWidth="1" strokeDasharray="2,2"/>
        <rect x="220" y="80" width="90" height="80" fill="rgba(30,41,59,0.4)" stroke="#334155" strokeWidth="1"/>
        {/* Labels */}
        <text x="20" y="25" fill="#64748B" fontSize="7" fontFamily="JetBrains Mono">西側走廊</text>
        <text x="170" y="25" fill="#64748B" fontSize="7" fontFamily="JetBrains Mono">北側出入口</text>
        <text x="20" y="105" fill="#64748B" fontSize="7" fontFamily="JetBrains Mono">機房</text>
        <text x="130" y="92" fill="#64748B" fontSize="7" fontFamily="JetBrains Mono">集水井區</text>
        <text x="230" y="95" fill="#64748B" fontSize="7" fontFamily="JetBrains Mono">東側大廳</text>
        {/* Pins */}
        {Object.entries(PINS).map(([id, p]) => {
          const node = window.NODES.find(n => n.id === id);
          if (!node) return null;
          const isHi = id === highlightNode;
          const color = node.status === 'offline' || node.status === 'critical' ? '#DC2626' : node.status === 'warn' ? '#F59E0B' : '#10B981';
          return (
            <g key={id}>
              {isHi && <circle cx={p.x} cy={p.y} r="9" fill={color} opacity="0.25" className="animate-live-blink"/>}
              <circle cx={p.x} cy={p.y} r={isHi ? 5 : 3} fill={color} stroke={isHi ? '#FFF' : 'none'} strokeWidth={isHi ? 1.5 : 0}/>
              {isHi && <text x={p.x + 8} y={p.y + 3} fill="#F8FAFC" fontSize="8" fontFamily="JetBrains Mono" fontWeight="600">{id}</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
};

// =================================================================
// MONITOR WALL
// =================================================================

const NodeCard = ({ node, onSelect, activeAlerts = [] }) => {
  const stateTone = node.status === 'offline' ? 'critical' : node.status === 'critical' ? 'critical' : node.status === 'warn' ? 'warn' : 'ok';
  const frozen = node.status === 'offline' || node.upload > 60;
  const nodeAlerts = activeAlerts.filter(a => a.node === node.id);
  const hasCritical = nodeAlerts.some(a => a.sev === 'critical');
  // Snapshot refresh ticker — decoupled from the 20 s /api/nodes safety-net
  // poll so the tile updates at ~1 Hz (matching the edge upload cadence). Each
  // tick bumps a counter that becomes the img src cache-buster below, forcing
  // the browser to refetch a fresh JPEG from /api/edge/{id}/snapshot/latest.
  // Skipped entirely for pump nodes and offline/frozen cameras — those don't
  // render an <img> tag so a tick would be wasted.
  const wantsLiveImg = node.type === 'camera' && !frozen;
  const [snapshotTick, setSnapshotTick] = React.useState(0);
  React.useEffect(() => {
    if (!wantsLiveImg) return;
    const id = setInterval(() => setSnapshotTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [wantsLiveImg]);
  return (
    <div className={`bg-surface-panel rounded border ${hasCritical ? 'border-sev-critical/50' : nodeAlerts.length > 0 ? 'border-sev-warn/40' : 'border-border-subtle'} overflow-hidden hover:border-border-strong transition-colors group cursor-pointer`} onClick={() => onSelect(node)}>
      <div className={`relative aspect-video snapshot-placeholder ${frozen ? 'snapshot-frozen' : ''}`}>
        {/* Status dot */}
        <div className={`absolute top-2 left-2 w-3 h-3 rounded-full bg-${
          stateTone === 'critical' ? 'sev-critical' : stateTone === 'warn' ? 'sev-warn' : 'sev-ok'
        } ring-2 ring-black/50 ${stateTone === 'critical' ? 'animate-live-blink' : ''}`}></div>
        {/* Active alert badge */}
        {nodeAlerts.length > 0 && (
          <div className={`absolute top-2 left-7 flex items-center gap-1 px-1.5 h-5 rounded text-[10px] font-bold tnum text-white ${hasCritical ? 'bg-sev-critical animate-live-blink' : 'bg-sev-warn text-black'}`}>
            <Icon.Bell size={9} strokeWidth={2.5}/>{nodeAlerts.length}
          </div>
        )}
        {/* Type indicator */}
        <div className="absolute top-2 right-2 bg-black/60 text-white text-[10px] font-mono px-1.5 py-0.5 rounded">
          {node.type === 'camera' ? 'CAM' : 'PUMP'}
        </div>
        {/* Snooze indicator */}
        {node.snoozeMin > 0 && (
          <div className="absolute top-7 right-2 bg-sev-warn/90 text-black text-[10px] font-mono font-bold px-1.5 py-0.5 rounded flex items-center gap-1 tnum">
            <Icon.BellOff size={9} strokeWidth={2.5}/>{node.snoozeMin}m
          </div>
        )}
        {/* Real snapshot for live cameras; icon placeholder for pumps, offline
            cameras, or brand-new nodes. Cache-buster uses snapshotTick (1 Hz
            client-side ticker above) so the browser refetches at ~1 Hz to
            match the edge's snapshot upload rate — decoupled from the 20 s
            data poll that was making the tile feel stale. object-cover keeps
            the IMX219 native aspect ratio without letterboxing. */}
        {wantsLiveImg && node.snapshotTimestamp ? (
          <img
            src={`/api/edge/${node.id}/snapshot/latest?t=${snapshotTick}`}
            alt={`${node.name || node.id} snapshot`}
            className="absolute inset-0 w-full h-full object-cover"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-ink-muted/40">
            {node.type === 'camera' ? <Icon.Camera size={48} strokeWidth={1}/> : <Icon.Droplet size={48} strokeWidth={1}/>}
          </div>
        )}
        {/* Frozen overlay */}
        {frozen && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/40">
            <div className="bg-sev-critical text-white text-xs font-bold px-2 py-1 rounded">
              畫面凍結 {node.upload}s
            </div>
          </div>
        )}
        {/* Pump level overlay */}
        {node.type === 'pump' && !frozen && (
          <div className="absolute inset-x-2 bottom-8">
            <div className="h-1.5 bg-black/40 rounded overflow-hidden">
              <div className={`h-full ${node.level > 85 ? 'bg-sev-critical' : node.level > 70 ? 'bg-sev-warn' : 'bg-sev-info'}`} style={{ width: node.level + '%' }}></div>
            </div>
            <div className="text-[10px] font-mono text-white/90 mt-0.5 tnum text-right">水位 {node.level}%</div>
          </div>
        )}
        {/* Bottom strip — node id + time */}
        <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/80 to-transparent p-2 flex items-end justify-between">
          <div>
            <div className="font-mono text-xs font-semibold text-white tnum">{node.id}</div>
            <div className="text-[10px] text-white/70">{node.name}</div>
          </div>
          <div className="font-mono text-[10px] text-white/60 tnum">
            {new Date().toLocaleTimeString('zh-TW', { hour12: false })}
          </div>
        </div>
      </div>
      {/* Stats */}
      <div className="p-2 grid grid-cols-3 gap-1 text-[10px] font-mono tnum bg-surface-panel">
        <div className="flex flex-col">
          <span className="text-ink-muted">心跳</span>
          <span className={node.heartbeat > 30 ? 'text-sev-critical font-semibold' : node.heartbeat > 10 ? 'text-sev-warn' : 'text-ink-secondary'}>
            {node.heartbeat > 60 ? Math.floor(node.heartbeat/60)+'m' : node.heartbeat+'s'}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-ink-muted">上傳</span>
          <span className={node.upload > 60 ? 'text-sev-critical font-semibold' : node.upload > 10 ? 'text-sev-warn' : 'text-ink-secondary'}>
            {node.upload > 60 ? Math.floor(node.upload/60)+'m' : node.upload+'s'}
          </span>
        </div>
        {node.type === 'camera' ? (
          <div className="flex flex-col">
            <span className="text-ink-muted">🌡</span>
            <span className={node.temp > 50 ? 'text-sev-warn' : 'text-ink-secondary'}>{node.temp ? node.temp+'°' : '—'}</span>
          </div>
        ) : (
          <div className="flex flex-col">
            <span className="text-ink-muted">本時</span>
            <span className={node.cycles > 20 ? 'text-sev-critical font-semibold' : node.cycles > 15 ? 'text-sev-warn' : 'text-ink-secondary'}>
              {node.cycles}×
            </span>
          </div>
        )}
      </div>
      {/* Detector health (camera only) */}
      {node.type === 'camera' && (
        <div className="px-2 pb-2">
          <DetectorHealth node={node}/>
        </div>
      )}
    </div>
  );
};

const MonitorPage = ({ activeAlerts, onSelectNode }) => {
  const [tab, setTab] = useState_p('all');
  // Filter nodes by tab
  const allNodes = window.NODES;
  const cameraNodes = allNodes.filter(n => n.type === 'camera');
  const pumpNodes = allNodes.filter(n => n.type === 'pump');
  const visibleNodes = tab === 'cameras' ? cameraNodes : tab === 'pumps' ? pumpNodes : allNodes;
  // Sort: OFFLINE > critical > warn > online
  const sorted = [...visibleNodes].sort((a, b) => {
    const rank = { offline: 0, critical: 1, warn: 2, online: 3 };
    return rank[a.status] - rank[b.status];
  });

  const summary = {
    online: window.NODES.filter(n => n.status === 'online').length,
    warn: window.NODES.filter(n => n.status === 'warn').length,
    critical: window.NODES.filter(n => n.status === 'critical').length,
    offline: window.NODES.filter(n => n.status === 'offline').length,
  };

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="px-4 py-2.5 border-b border-border-subtle bg-surface-panel flex items-center gap-4 flex-shrink-0">
        <h1 className="text-sm font-semibold">監看牆</h1>
        {/* Type tabs */}
        <div className="flex bg-surface-base border border-border-subtle rounded p-0.5">
          {[
            { id: 'all',     label: '全部',   count: allNodes.length, Ico: Icon.Grid },
            { id: 'cameras', label: '攝影機', count: cameraNodes.length, Ico: Icon.Camera },
            { id: 'pumps',   label: '抽水站', count: pumpNodes.length, Ico: Icon.Droplet },
          ].map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`inline-flex items-center gap-1.5 px-2.5 h-7 rounded text-xs transition-colors ${tab === t.id ? 'bg-surface-overlay text-ink-primary' : 'text-ink-muted hover:text-ink-secondary'}`}>
              <t.Ico size={12}/>
              <span>{t.label}</span>
              <span className="font-mono tnum text-[10px] text-ink-muted">{t.count}</span>
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5 text-xs">
          <Pill tone="ok" dot>正常 {summary.online}</Pill>
          <Pill tone="warn" dot>警告 {summary.warn}</Pill>
          <Pill tone="critical" dot>嚴重 {summary.critical}</Pill>
          <Pill tone="critical" dot pulse>離線 {summary.offline}</Pill>
        </div>
        <div className="flex-1"></div>
        <div className="flex items-center gap-2 text-xs">
          <button className="flex items-center gap-1.5 h-7 px-2 bg-surface-elevated border border-border-strong rounded hover:bg-surface-overlay">
            <Icon.Filter size={12}/> 分組: <span className="text-ink-muted">{tab === 'pumps' ? '無' : '無'}</span> <Icon.ChevronDown size={12}/>
          </button>
          <button className="flex items-center gap-1.5 h-7 px-2 bg-surface-elevated border border-border-strong rounded hover:bg-surface-overlay">
            <Icon.RefreshCw size={12}/> <span>1s</span>
          </button>
          <button className="flex items-center gap-1.5 h-7 px-2 bg-surface-elevated border border-border-strong rounded hover:bg-surface-overlay">
            <Icon.Maximize size={12}/> 全螢幕 <Kbd>F</Kbd>
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto scroll-thin p-3">
        {tab === 'pumps' ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {sorted.map(n => <PumpCard key={n.id} node={n} onSelect={onSelectNode} activeAlerts={activeAlerts}/>)}
          </div>
        ) : tab === 'cameras' ? (
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3">
            {sorted.map(n => <NodeCard key={n.id} node={n} onSelect={onSelectNode} activeAlerts={activeAlerts}/>)}
          </div>
        ) : (
          // 全部 — split per type, native cards per sensor
          <div className="space-y-5">
            {pumpNodes.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <Icon.Droplet size={14} className="text-ink-muted"/>
                  <span className="text-xs font-semibold uppercase tracking-wider text-ink-muted">抽水站 · {pumpNodes.length}</span>
                  <div className="flex-1 h-px bg-border-subtle"></div>
                  <button onClick={() => setTab('pumps')} className="text-[10px] text-sev-info hover:underline">僅顯示抽水站 →</button>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
                  {[...pumpNodes].sort((a,b) => ({offline:0,critical:1,warn:2,online:3}[a.status]) - ({offline:0,critical:1,warn:2,online:3}[b.status]))
                    .map(n => <PumpCard key={n.id} node={n} onSelect={onSelectNode} activeAlerts={activeAlerts} compact/>)}
                </div>
              </div>
            )}
            {cameraNodes.length > 0 && (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <Icon.Camera size={14} className="text-ink-muted"/>
                  <span className="text-xs font-semibold uppercase tracking-wider text-ink-muted">攝影機 · {cameraNodes.length}</span>
                  <div className="flex-1 h-px bg-border-subtle"></div>
                  <button onClick={() => setTab('cameras')} className="text-[10px] text-sev-info hover:underline">僅顯示攝影機 →</button>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3">
                  {[...cameraNodes].sort((a,b) => ({offline:0,critical:1,warn:2,online:3}[a.status]) - ({offline:0,critical:1,warn:2,online:3}[b.status]))
                    .map(n => <NodeCard key={n.id} node={n} onSelect={onSelectNode} activeAlerts={activeAlerts}/>)}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

// ---------- Pump Card (gauge-first, sensor-native) ----------

const PumpCard = ({ node, onSelect, activeAlerts = [], compact = false }) => {
  const nodeAlerts = activeAlerts.filter(a => a.node === node.id);
  const hasCritical = nodeAlerts.some(a => a.sev === 'critical');
  const stateTone = node.status === 'offline' || node.status === 'critical' ? 'critical' : node.status === 'warn' ? 'warn' : 'ok';
  const levelTone = node.level > 85 ? 'critical' : node.level > 70 ? 'warn' : 'info';
  const cycleTone = node.cycles > 20 ? 'critical' : node.cycles > 15 ? 'warn' : 'ok';
  const maxCycle = Math.max(...(node.cycleHistory || [node.cycles]));
  return (
    <div onClick={() => onSelect && onSelect(node)}
      className={`relative bg-surface-panel rounded border ${hasCritical ? 'border-sev-critical/50' : nodeAlerts.length > 0 ? 'border-sev-warn/40' : 'border-border-subtle'} overflow-hidden hover:border-border-strong transition-colors cursor-pointer`}>

      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-subtle">
        <span className={`w-2.5 h-2.5 rounded-full bg-sev-${stateTone === 'critical' ? 'critical' : stateTone === 'warn' ? 'warn' : 'ok'} flex-shrink-0 ${stateTone === 'critical' ? 'animate-live-blink' : ''}`}></span>
        <span className="font-mono text-sm font-bold tnum">{node.id}</span>
        <span className="text-xs text-ink-secondary truncate flex-1">{node.name}</span>
        {nodeAlerts.length > 0 && (
          <span className={`inline-flex items-center gap-0.5 text-[10px] font-bold px-1.5 h-4 rounded text-white ${hasCritical ? 'bg-sev-critical animate-live-blink' : 'bg-sev-warn text-black'}`}>
            <Icon.Bell size={9} strokeWidth={2.5}/>{nodeAlerts.length}
          </span>
        )}
        <span className="text-[10px] text-ink-muted bg-surface-elevated px-1.5 py-0.5 rounded font-mono">PUMP</span>
      </div>

      {/* Sensor conflict — prominent critical banner, mirrors the glass-node critical alerts */}
      {node.sensorConflict && (
        <div role="alert" className="flex items-center gap-1.5 px-3 py-1.5 bg-sev-critical/15 border-b border-sev-critical/40 text-sev-critical text-xs font-semibold">
          <Icon.AlertTriangle size={12} className="animate-live-blink flex-shrink-0"/>
          <span>⚠ Sensor conflict — inspect float switch</span>
        </div>
      )}

      <div className="p-3 flex gap-3">
        {/* LEFT — Water tank gauge */}
        <div className="relative w-20 flex-shrink-0 bg-surface-base rounded border border-border-subtle overflow-hidden" style={{ height: compact ? '112px' : '140px' }}>
          {/* Tick marks on left */}
          <div className="absolute left-0 inset-y-1 w-3 flex flex-col justify-between text-[8px] text-ink-dim font-mono tnum">
            <span className="leading-none pl-0.5">100</span>
            <span className="leading-none pl-0.5">75</span>
            <span className="leading-none pl-0.5">50</span>
            <span className="leading-none pl-0.5">25</span>
            <span className="leading-none pl-0.5">0</span>
          </div>
          {/* Threshold lines */}
          <div className="absolute left-3 right-0 border-t border-dashed border-sev-critical/60" style={{ top: '15%' }}>
            <span className="absolute -top-[7px] right-0.5 text-[8px] font-mono text-sev-critical bg-surface-panel px-0.5 leading-none">85</span>
          </div>
          <div className="absolute left-3 right-0 border-t border-dashed border-sev-warn/60" style={{ top: '30%' }}>
            <span className="absolute -top-[7px] right-0.5 text-[8px] font-mono text-sev-warn bg-surface-panel px-0.5 leading-none">70</span>
          </div>
          {/* Water fill */}
          <div className={`absolute left-3 right-0 bottom-0 transition-all duration-500 ${levelTone === 'critical' ? 'bg-sev-critical/50' : levelTone === 'warn' ? 'bg-sev-warn/40' : 'bg-sev-info/40'}`} style={{ height: node.level + '%' }}>
            {/* Wave top edge */}
            <div className={`h-0.5 ${levelTone === 'critical' ? 'bg-sev-critical' : levelTone === 'warn' ? 'bg-sev-warn' : 'bg-sev-info'}`}></div>
          </div>
          {/* Big % readout */}
          <div className="absolute inset-x-3 top-1/2 -translate-y-1/2 text-center pointer-events-none">
            <div className={`text-xl font-mono font-black tnum leading-none ${levelTone === 'critical' ? 'text-sev-critical' : levelTone === 'warn' ? 'text-sev-warn' : 'text-ink-primary'}`}>
              {node.level}
              <span className="text-[10px] text-ink-muted font-normal">%</span>
            </div>
          </div>
          {/* Trend arrow */}
          <div className={`absolute bottom-0.5 right-0.5 text-[10px] font-mono ${node.trend === 'up' ? 'text-sev-warn' : node.trend === 'down' ? 'text-sev-ok' : 'text-ink-muted'}`}>
            {node.trend === 'up' ? '↑' : node.trend === 'down' ? '↓' : '→'}
          </div>
        </div>

        {/* RIGHT — Stats */}
        <div className="flex-1 min-w-0 space-y-2">
          {/* Cycle frequency — primary metric */}
          <div>
            <div className="flex items-baseline justify-between">
              <span className="text-[10px] text-ink-muted uppercase tracking-wider">啟動頻率</span>
              <span className={`text-[10px] font-mono tnum ${cycleTone === 'critical' ? 'text-sev-critical' : cycleTone === 'warn' ? 'text-sev-warn' : 'text-ink-muted'}`}>{node.cycles}×/hr</span>
            </div>
            <div className={`text-base font-mono font-semibold tnum leading-tight ${cycleTone === 'critical' ? 'text-sev-critical' : cycleTone === 'warn' ? 'text-sev-warn' : 'text-ink-primary'}`}>
              每 {node.cycles > 0 ? (60/node.cycles).toFixed(1) : '—'}<span className="text-[10px] text-ink-muted ml-0.5">分</span>
            </div>
            {/* Cycle bar timeline — last 12 buckets */}
            {node.cycleHistory && (
              <div className="flex items-end gap-px h-3 mt-1" title="近 12 個 5min 啟動次數">
                {node.cycleHistory.map((v, i) => {
                  const h = Math.max(2, (v / Math.max(maxCycle, 1)) * 12);
                  const isLast = i === node.cycleHistory.length - 1;
                  const tone = v > 20 ? 'bg-sev-critical' : v > 15 ? 'bg-sev-warn' : 'bg-sev-info/60';
                  return <div key={i} className={`flex-1 rounded-sm ${tone} ${isLast ? '' : 'opacity-70'}`} style={{ height: h + 'px' }}/>;
                })}
              </div>
            )}
          </div>

          {/* Flow + power row */}
          <div className="grid grid-cols-2 gap-1 text-[10px] font-mono tnum">
            <div className="flex items-center gap-1">
              <Icon.ArrowDown size={9} className="text-sev-info"/>
              <span className="text-ink-muted">流量</span>
              <span className="text-ink-secondary ml-auto">{node.flow ?? '—'}<span className="text-ink-dim ml-0.5">L/m</span></span>
            </div>
            <div className="flex items-center gap-1">
              <Icon.Battery size={9} className={node.voltage != null && node.voltage < 12 ? 'text-sev-warn' : 'text-ink-muted'}/>
              <span className={node.voltage != null && node.voltage < 12 ? 'text-sev-warn' : 'text-ink-secondary'}>{node.voltage != null ? node.voltage + 'V' : '—'}</span>
              <span className={`ml-auto px-1 rounded text-[9px] ${node.power === 'mains' ? 'bg-sev-ok/15 text-sev-ok' : node.power === 'ups' ? 'bg-sev-warn/15 text-sev-warn' : 'bg-sev-critical/15 text-sev-critical'}`}>
                {node.power === 'mains' ? '市電' : node.power === 'ups' ? 'UPS' : '電池'}
              </span>
            </div>
          </div>

          {/* Rain / dry-run protect badges */}
          {(node.raining || node.dryRunProtect) && (
            <div className="flex items-center gap-1.5 flex-wrap">
              {node.raining && (
                <Pill tone="info" className="!h-5 !text-[10px]">🌧 Raining</Pill>
              )}
              {node.dryRunProtect && (
                <Pill tone="warn" className="!h-5 !text-[10px]">Dry-run protect (pump held OFF)</Pill>
              )}
            </div>
          )}

          {/* Location footer */}
          <div className="flex items-center gap-1 text-[10px] text-ink-muted font-mono tnum pt-1 border-t border-border-subtle/60">
            <Icon.MapPin size={9}/>
            <span className="truncate">{node.location}</span>
            <span className="ml-auto">心跳 {node.heartbeat}s</span>
          </div>
        </div>
      </div>

      {/* Snooze ribbon */}
      {node.snoozeMin > 0 && (
        <div className="absolute top-2 right-2 bg-sev-warn/90 text-black text-[10px] font-mono font-bold px-1.5 py-0.5 rounded flex items-center gap-1 tnum">
          <Icon.BellOff size={9} strokeWidth={2.5}/>{node.snoozeMin}m
        </div>
      )}
    </div>
  );
};

// =================================================================
// NODE STATUS PAGE
// =================================================================

const StatusPage = ({ onSelectNode }) => {
  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="px-4 py-2.5 border-b border-border-subtle bg-surface-panel flex items-center gap-3 flex-shrink-0">
        <h1 className="text-sm font-semibold">節點狀態</h1>
        <span className="text-xs text-ink-muted tnum">{window.NODES.length} 個節點</span>
        <div className="flex-1"></div>
        <div className="flex gap-1.5">
          <FilterChip>類型 <Icon.ChevronDown size={10}/></FilterChip>
          <FilterChip>狀態 <Icon.ChevronDown size={10}/></FilterChip>
          <FilterChip>位置 <Icon.ChevronDown size={10}/></FilterChip>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto scroll-thin">
        <table className="w-full text-xs tnum">
          <thead className="sticky top-0 bg-surface-base z-10 border-b border-border-strong">
            <tr className="text-[10px] text-ink-muted uppercase tracking-wider">
              <th className="text-left font-semibold px-3 py-2">節點</th>
              <th className="text-left font-semibold px-3 py-2">類型</th>
              <th className="text-left font-semibold px-3 py-2">位置</th>
              <th className="text-left font-semibold px-3 py-2">狀態</th>
              <th className="text-right font-semibold px-3 py-2">心跳</th>
              <th className="text-right font-semibold px-3 py-2">上傳</th>
              <th className="text-left font-semibold px-3 py-2">串流健康</th>
              <th className="text-right font-semibold px-3 py-2">溫度 / 水位</th>
              <th className="text-left font-semibold px-3 py-2">電源</th>
              <th className="text-right font-semibold px-3 py-2 pr-4">動作</th>
            </tr>
          </thead>
          <tbody>
            {window.NODES.map(n => {
              const tone = n.status === 'offline' || n.status === 'critical' ? 'critical' : n.status === 'warn' ? 'warn' : 'ok';
              const uploadIssue = n.heartbeat < 60 && n.upload > 600;
              return (
                <tr key={n.id} className="border-b border-border-subtle/60 hover:bg-surface-elevated/60 group cursor-pointer" onClick={() => onSelectNode && onSelectNode(n)}>
                  <td className="px-3 py-2 font-mono font-semibold">{n.id}</td>
                  <td className="px-3 py-2 text-ink-secondary">
                    <span className="inline-flex items-center gap-1.5">
                      {n.type === 'camera' ? <Icon.Camera size={12}/> : <Icon.Pump size={12}/>}
                      {n.type === 'camera' ? '攝影機' : '抽水站'}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-ink-secondary">{n.location}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded border text-[10px] font-medium bg-sev-${tone === 'critical' ? 'critical' : tone === 'warn' ? 'warn' : 'ok'}/15 text-sev-${tone === 'critical' ? 'critical' : tone === 'warn' ? 'warn' : 'ok'} border-sev-${tone === 'critical' ? 'critical' : tone === 'warn' ? 'warn' : 'ok'}/30`}>
                      <span className={`w-1.5 h-1.5 rounded-full bg-sev-${tone === 'critical' ? 'critical' : tone === 'warn' ? 'warn' : 'ok'}`}></span>
                      {n.status === 'offline' ? '離線' : n.status === 'critical' ? '嚴重' : n.status === 'warn' ? '警告' : '正常'}
                    </span>
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${n.heartbeat > 60 ? 'text-sev-critical font-semibold' : n.heartbeat > 5 ? 'text-sev-warn' : 'text-ink-secondary'}`}>
                    {n.heartbeat > 60 ? Math.floor(n.heartbeat/60)+'m' : n.heartbeat+'s'}
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${uploadIssue ? 'text-sev-critical font-semibold' : n.upload > 60 ? 'text-sev-warn' : 'text-ink-secondary'}`}>
                    {n.upload > 60 ? Math.floor(n.upload/60)+'m' : n.upload+'s'}
                    {uploadIssue && <span className="ml-1 text-[10px] bg-sev-critical/20 text-sev-critical px-1 rounded">上傳異常</span>}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {n.type === 'camera' ? (
                      <span className={n.bitrate < 0.5 ? 'text-sev-critical' : n.bitrate < 1 ? 'text-sev-warn' : 'text-sev-ok'}>
                        {n.bitrate.toFixed(1)}Mbps <span className="text-ink-muted">· {n.drops} drops</span>
                      </span>
                    ) : <span className="text-ink-muted">—</span>}
                  </td>
                  <td className="px-3 py-2 text-right font-mono">
                    {n.type === 'camera' ? (
                      <span className={n.temp > 50 ? 'text-sev-warn' : n.temp ? 'text-ink-secondary' : 'text-ink-muted'}>{n.temp ? n.temp+'°C' : '—'}</span>
                    ) : (
                      <span className="inline-flex items-center gap-1">
                        {n.trend === 'up' ? <Icon.ArrowUp size={10} className="text-sev-warn"/> : n.trend === 'down' ? <Icon.ArrowDown size={10} className="text-sev-ok"/> : <Icon.ArrowRight size={10} className="text-ink-muted"/>}
                        <span className={n.level > 85 ? 'text-sev-critical font-semibold' : n.level > 70 ? 'text-sev-warn' : 'text-ink-secondary'}>{n.level}%</span>
                        <span className="text-ink-muted ml-1">· {n.cycles}×</span>
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {n.type === 'pump' ? (
                      <span className="inline-flex items-center gap-1 font-mono">
                        <Icon.Battery size={12} className={n.voltage != null && n.voltage < 12 ? 'text-sev-warn' : 'text-ink-secondary'}/>
                        <span className={n.voltage != null && n.voltage < 12 ? 'text-sev-warn' : 'text-ink-secondary'}>{n.voltage != null ? n.voltage + 'V' : '—'}</span>
                        <span className={`text-[10px] px-1 rounded ml-1 ${n.power==='mains'?'bg-sev-ok/15 text-sev-ok':n.power==='ups'?'bg-sev-warn/15 text-sev-warn':'bg-sev-critical/15 text-sev-critical'}`}>{n.power==='mains'?'市電':n.power==='ups'?'UPS':'電池'}</span>
                      </span>
                    ) : <span className="text-ink-muted text-[10px] font-mono">PoE</span>}
                  </td>
                  <td className="px-3 py-2 text-right pr-4">
                    <div className="inline-flex gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                      <button title="靜音" className="w-6 h-6 rounded hover:bg-surface-overlay flex items-center justify-center text-ink-muted hover:text-ink-primary"><Icon.BellOff size={12}/></button>
                      <button title="配置" className="w-6 h-6 rounded hover:bg-surface-overlay flex items-center justify-center text-ink-muted hover:text-ink-primary"><Icon.Settings size={12}/></button>
                      <button title="重啟" className="w-6 h-6 rounded hover:bg-surface-overlay flex items-center justify-center text-ink-muted hover:text-sev-warn"><Icon.RefreshCw size={12}/></button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

// =================================================================
// WEATHER PAGE
// =================================================================

const WeatherPage = () => {
  const w = window.WEATHER;
  const fc = w.forecast || [];
  const maxWind = fc.length ? Math.max(1, ...fc.map(f => f.wind)) : 1;
  const maxRain = fc.length ? Math.max(1, ...fc.map(f => f.rain)) : 1;
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
          <div className="flex gap-1">
            <button className="px-2 h-6 text-xs bg-surface-elevated border border-border-strong rounded font-mono">{w.source}</button>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-5">
          <div className="bg-surface-panel border border-border-subtle rounded p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted flex items-center gap-1"><Icon.Wind size={10}/> 風速</div>
            <div className="mt-1 flex items-baseline gap-1 whitespace-nowrap">
              <span className="text-4xl font-mono font-bold tnum">{w.wind.speed}</span>
              <span className="text-ink-muted text-sm">km/h</span>
            </div>
            <div className="text-xs text-ink-muted mt-1 font-mono tnum whitespace-nowrap">陣風 {w.wind.gust} · {w.wind.dir}</div>
          </div>
          <div className="bg-surface-panel border border-border-subtle rounded p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted flex items-center gap-1"><Icon.CloudRain size={10}/> 雨量</div>
            <div className="mt-1 flex items-baseline gap-1 whitespace-nowrap">
              <span className="text-4xl font-mono font-bold tnum text-sev-info">{w.rain.now}</span>
              <span className="text-ink-muted text-sm">mm/h</span>
            </div>
            <div className="text-xs text-ink-muted mt-1 font-mono tnum whitespace-nowrap">日累計 {w.rain.day}mm</div>
          </div>
          <div className="bg-surface-panel border border-border-subtle rounded p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted flex items-center gap-1"><Icon.Zap size={10}/> 雷擊</div>
            <div className="mt-1 flex items-baseline gap-1 whitespace-nowrap">
              <span className="text-4xl font-mono font-bold tnum text-sev-warn">{w.lightning.count}</span>
              <span className="text-ink-muted text-sm">次/hr</span>
            </div>
            <div className="text-xs text-sev-warn mt-1 font-mono tnum whitespace-nowrap">{w.lightning.nearest != null ? `最近 ${w.lightning.nearest}km · 警戒` : '無偵測'}</div>
          </div>
          <div className="bg-surface-panel border border-border-subtle rounded p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted flex items-center gap-1"><Icon.Thermometer size={10}/> 環境</div>
            <div className="mt-1 flex items-baseline gap-1 whitespace-nowrap">
              <span className="text-4xl font-mono font-bold tnum">{w.temp}</span>
              <span className="text-ink-muted text-sm">°C</span>
            </div>
            <div className="text-xs text-ink-muted mt-1 font-mono tnum whitespace-nowrap">濕度 {w.humidity}%{w.pressure != null ? ` · ${w.pressure}hPa` : ''}</div>
          </div>
        </div>
      </div>

      {/* Forecast */}
      <div className="p-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold">36 小時預報</h2>
          <label className="flex items-center gap-2 text-xs text-ink-secondary">
            <input type="checkbox" defaultChecked className="rounded border-border-strong bg-surface-base text-sev-info"/>
            雷擊期間自動靜音
          </label>
        </div>
        <div className="bg-surface-panel border border-border-subtle rounded p-4 overflow-x-auto">
          <div className="flex gap-1 items-end" style={{ minWidth: '720px' }}>
            {w.forecast.map((f, i) => (
              <div key={i} className="flex-1 flex flex-col items-center gap-1 min-w-[36px]">
                <div className="text-[10px] text-sev-info font-mono tnum">{f.rain}</div>
                <div className="w-full bg-sev-info/40 rounded-t" style={{ height: (f.rain / maxRain) * 60 + 'px' }}></div>
                <div className="w-full bg-sev-warn/40 rounded-t" style={{ height: (f.wind / maxWind) * 60 + 'px' }}></div>
                <div className="text-[10px] text-sev-warn font-mono tnum">{f.wind}</div>
                <div className="text-[10px] text-ink-muted font-mono tnum mt-1">{f.h}</div>
              </div>
            ))}
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

// =================================================================
// HANDOVER PAGE
// =================================================================

const HandoverPage = () => {
  const [text, setText] = useState_p(window.HANDOVER.current);
  const [savedAt, setSavedAt] = useState_p(window.HANDOVER.pinned && window.HANDOVER.pinned.at);
  const [saving, setSaving] = useState_p(false);
  const s = window.SHIFT_SUMMARY;
  const today = new Date().toISOString().slice(0, 10);
  const generateSummary = () => {
    const lines = [
      `本班次摘要 (${s.duration})`,
      `處理警報 ${s.alertsHandled} 筆 — 嚴重 ${s.critical} · 警告 ${s.warn} · 資訊 ${s.info}`,
      `中位認領時間 ${s.ackMedian} · 中位解決時間 ${s.resolveMedian}`,
      `仍未解決承接 ${s.carryOver} 筆`,
    ];
    if (s.highlights && s.highlights.length) {
      lines.push('', '主要事件:');
      s.highlights.forEach(h => lines.push(`· ${h.node} ${h.label} (${h.count}×)`));
    }
    setText(lines.join('\n'));
  };
  const save = async () => {
    setSaving(true);
    try {
      await window.SDPRS_API.saveHandover(text);
      const now = new Date();
      const p = (n) => String(n).padStart(2, '0');
      setSavedAt(p(now.getHours()) + ':' + p(now.getMinutes()) + ':' + p(now.getSeconds()));
    } catch (e) {
      alert('儲存失敗: ' + (e.message || e));
    }
    setSaving(false);
  };
  return (
    <div className="h-full overflow-y-auto scroll-thin p-6 grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6">
      <div>
        <div className="flex items-center gap-2 mb-3">
          <h1 className="text-base font-semibold">班次交接備註</h1>
          <span className="text-xs text-ink-muted font-mono tnum">{today} · {window.SDPRS_USER || ''}</span>
          <div className="flex-1"></div>
          <button onClick={generateSummary}
            className="text-xs px-3 h-7 bg-sev-info/15 border border-sev-info/40 text-sev-info rounded hover:bg-sev-info/25 inline-flex items-center gap-1.5">
            <Icon.Activity size={12}/> 自動產生本班次摘要
          </button>
        </div>

        {/* Shift summary card — pre-loaded */}
        <div className="mb-3 bg-surface-panel border border-border-subtle rounded p-3">
          <div className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold mb-2">本班次數據</div>
          <div className="grid grid-cols-5 gap-2">
            <div>
              <div className="text-lg font-mono font-bold tnum">{s.alertsHandled}</div>
              <div className="text-[10px] text-ink-muted">警報處理</div>
            </div>
            <div>
              <div className="text-lg font-mono font-bold tnum text-sev-critical">{s.critical}</div>
              <div className="text-[10px] text-ink-muted">嚴重</div>
            </div>
            <div>
              <div className="text-lg font-mono font-bold tnum">{s.ackMedian}</div>
              <div className="text-[10px] text-ink-muted">中位認領</div>
            </div>
            <div>
              <div className="text-lg font-mono font-bold tnum">{s.resolveMedian}</div>
              <div className="text-[10px] text-ink-muted">中位解決</div>
            </div>
            <div>
              <div className="text-lg font-mono font-bold tnum text-sev-warn">{s.carryOver}</div>
              <div className="text-[10px] text-ink-muted">承接</div>
            </div>
          </div>
        </div>

        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          rows="14"
          className="w-full bg-surface-panel border border-border-strong rounded p-3 text-sm font-mono leading-relaxed focus:border-sev-info focus:outline-none resize-none"
        />
        <div className="mt-3 flex items-center gap-2">
          <button onClick={save} disabled={saving} className="px-3 h-9 bg-sev-info hover:bg-blue-600 disabled:opacity-50 text-white rounded text-sm font-semibold flex items-center gap-2">
            <Icon.Check size={14}/> 儲存
          </button>
          <span className="text-xs text-ink-muted ml-2">
            {savedAt ? <>最後儲存: <span className="font-mono tnum">{savedAt}</span></> : '尚未儲存'}
          </span>
        </div>
      </div>
      <div>
        <h2 className="text-sm font-semibold mb-3 text-ink-secondary">歷史備註</h2>
        <div className="space-y-2">
          {window.HANDOVER.history.map((h, i) => (
            <div key={i} className="bg-surface-panel border border-border-subtle rounded p-3">
              <div className="flex items-center gap-2 text-xs text-ink-muted mb-1.5 font-mono tnum">
                <Icon.User size={12}/> <span className="text-ink-secondary">{h.by}</span>
                <span className="text-ink-dim">·</span>
                <span>{h.at}</span>
              </div>
              <p className="text-xs text-ink-secondary leading-relaxed">{h.text}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

// =================================================================
// AUDIT PAGE
// =================================================================

const AuditPage = () => {
  const [meOnly, setMeOnly] = useState_p(false);
  const actionMeta = {
    ALERT_CREATED: { tone: 'critical', label: '警報建立' },
    ALERT_ACK: { tone: 'info', label: '認領' },
    ALERT_RESOLVE: { tone: 'ok', label: '解決' },
    NODE_SNOOZE: { tone: 'warn', label: '節點延期' },
    LOGIN: { tone: 'muted', label: '登入' },
  };
  const records = window.AUDIT.filter(a => !meOnly || a.by === 'alice');
  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="px-4 py-2.5 border-b border-border-subtle bg-surface-panel flex items-center gap-3 flex-shrink-0">
        <h1 className="text-sm font-semibold">稽核紀錄</h1>
        <span className="text-xs text-ink-muted tnum">{records.length} 筆 {meOnly && '· 僅 alice'}</span>
        <div className="flex-1"></div>
        <div className="flex items-center gap-1.5">
          <button onClick={() => setMeOnly(!meOnly)}
            className={`inline-flex items-center gap-1 h-7 px-2 rounded text-xs border transition-colors ${meOnly ? 'bg-sev-info/15 border-sev-info/40 text-sev-info' : 'bg-surface-elevated border-border-subtle text-ink-secondary hover:border-border-strong'}`}>
            <Icon.User size={12}/> 本班 · 我的動作
          </button>
          <FilterChip>操作者 <Icon.ChevronDown size={10}/></FilterChip>
          <FilterChip>動作 <Icon.ChevronDown size={10}/></FilterChip>
          <FilterChip>日期 <Icon.ChevronDown size={10}/></FilterChip>
          <button className="ml-2 h-7 px-2 bg-surface-elevated border border-border-strong rounded text-xs flex items-center gap-1.5 hover:bg-surface-overlay">
            <Icon.Download size={12}/> 匯出 CSV
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto scroll-thin">
        <table className="w-full text-xs tnum">
          <thead className="sticky top-0 bg-surface-base z-10 border-b border-border-strong">
            <tr className="text-[10px] text-ink-muted uppercase tracking-wider">
              <th className="text-left font-semibold px-4 py-2 w-28">時間</th>
              <th className="text-left font-semibold px-4 py-2 w-32">操作者</th>
              <th className="text-left font-semibold px-4 py-2 w-32">動作</th>
              <th className="text-left font-semibold px-4 py-2 w-48">目標</th>
              <th className="text-left font-semibold px-4 py-2">詳情</th>
            </tr>
          </thead>
          <tbody>
            {records.map((a, i) => {
              const m = actionMeta[a.action] || { tone: 'muted', label: a.action };
              return (
                <tr key={i} className="border-b border-border-subtle/60 hover:bg-surface-elevated/60">
                  <td className="px-4 py-2 font-mono text-ink-muted">{a.t}</td>
                  <td className="px-4 py-2 font-mono">
                    <span className={a.by === 'system' ? 'text-ink-muted' : a.by === 'alice' ? 'text-sev-info font-semibold' : 'text-ink-primary'}>{a.by}</span>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border bg-sev-${m.tone}/15 text-sev-${m.tone} border-sev-${m.tone}/30 font-medium`}>
                      {m.label}
                    </span>
                    <span className="ml-1.5 font-mono text-[10px] text-ink-dim">{a.action}</span>
                  </td>
                  <td className="px-4 py-2 font-mono text-ink-secondary">{a.target}</td>
                  <td className="px-4 py-2 font-mono text-[10px] text-ink-muted">
                    {JSON.stringify(a.detail)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

// =================================================================
// PUMPS PAGE — focused subset of status filtered to pumps
// =================================================================

const PumpsPage = () => {
  const pumps = window.NODES.filter(n => n.type === 'pump');
  return (
    <div className="h-full overflow-y-auto scroll-thin p-4">
      <div className="flex items-center gap-2 mb-4">
        <h1 className="text-sm font-semibold">抽水站</h1>
        <span className="text-xs text-ink-muted tnum">{pumps.length} 站</span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {pumps.map(p => {
          const danger = p.level > 85;
          const warn = p.level > 70;
          return (
            <div key={p.id} className={`bg-surface-panel border rounded p-4 ${danger ? 'border-sev-critical/40' : warn ? 'border-sev-warn/40' : 'border-border-subtle'}`}>
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
                  <span>⚠ Sensor conflict — inspect float switch</span>
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

Object.assign(window, { AlertsPage, MonitorPage, StatusPage, WeatherPage, HandoverPage, AuditPage, PumpsPage });
