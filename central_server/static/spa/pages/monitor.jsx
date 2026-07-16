// SDPRS — Monitor Wall Page

const { useState: useState_p } = React;

const NodeCard = ({ node, onSelect, activeAlerts = [], now }) => {
  const stateTone = node.status === 'offline' ? 'critical' : node.status === 'critical' ? 'critical' : node.status === 'warn' ? 'warn' : 'ok';
  const frozen = node.status === 'offline' || node.upload > 60;
  const nodeAlerts = activeAlerts.filter(a => a.node === node.id);
  const hasCritical = nodeAlerts.some(a => a.sev === 'critical');
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(node)}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(node); }
      }}
      className={`bg-surface-panel rounded border ${hasCritical ? 'border-sev-critical/50' : nodeAlerts.length > 0 ? 'border-sev-warn/40' : 'border-border-subtle'} overflow-hidden hover:border-border-strong transition-colors group cursor-pointer`}>
      <div className={`relative aspect-video snapshot-placeholder ${frozen ? 'snapshot-frozen' : ''}`}>
        {/* Status dot — z-10 so it stays above SnapshotImage which paints later */}
        <div className={`absolute z-10 top-2 left-2 w-3 h-3 rounded-full bg-${
          stateTone === 'critical' ? 'sev-critical' : stateTone === 'warn' ? 'sev-warn' : 'sev-ok'
        } ring-2 ring-black/50 ${stateTone === 'critical' ? 'animate-live-blink' : ''}`}></div>
        {/* Active alert badge */}
        {nodeAlerts.length > 0 && (
          <div className={`absolute z-10 top-2 left-7 flex items-center gap-1 px-1.5 h-5 rounded text-[10px] font-bold tnum text-white ${hasCritical ? 'bg-sev-critical animate-live-blink' : 'bg-sev-warn text-black'}`}>
            <Icon.Bell size={9} strokeWidth={2.5}/>{nodeAlerts.length}
          </div>
        )}
        {/* Type indicator */}
        <div className="absolute z-10 top-2 right-2 bg-black/60 text-white text-[10px] font-mono px-1.5 py-0.5 rounded">
          {node.type === 'camera' ? 'CAM' : 'PUMP'}
        </div>
        {/* Snooze indicator */}
        {node.snoozeMin > 0 && (
          <div className="absolute z-10 top-7 right-2 bg-sev-warn/90 text-black text-[10px] font-mono font-bold px-1.5 py-0.5 rounded flex items-center gap-1 tnum">
            <Icon.BellOff size={9} strokeWidth={2.5}/>{node.snoozeMin}m
          </div>
        )}
        {/* Snapshot: live JPEG (camera + fresh frame) or fallback icon. All the
            ticker + <img>/icon-fallback logic lives in SnapshotImage — shared
            with the big monitor-wall view (app.jsx) and the node side panel
            (components.jsx) so all three refresh at the same 1 Hz cadence. */}
        <SnapshotImage node={node}/>
        {/* Frozen overlay */}
        {frozen && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/40">
            <div className="bg-sev-critical text-xs font-bold px-2 py-1 rounded">
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
            {new Date(now || Date.now()).toLocaleTimeString('zh-TW', { hour12: false })}
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
  // Local toast for fullscreen failures etc. Auto-dismissed after 3s.
  const [toast, setToast] = useState_p(null);
  React.useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(t);
  }, [toast]);
  // Single page-level 1 Hz tick, passed down as `now` so every NodeCard's
  // header timestamp re-renders alongside the SnapshotImage underneath
  // (whose own ticker lives module-private in components.jsx). Without this
  // the "last updated" label freezes for minutes.
  const [now, setNow] = useState_p(() => Date.now());
  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  // Filter nodes by tab
  const allNodes = window.NODES;
  const cameraNodes = allNodes.filter(n => n.type === 'camera');
  const pumpNodes = allNodes.filter(n => n.type === 'pump');
  const visibleNodes = tab === 'cameras' ? cameraNodes : tab === 'pumps' ? pumpNodes : allNodes;
  // Sort: OFFLINE > critical > warn > online (unknown statuses sink to the bottom via ?? 99)
  const sorted = [...visibleNodes].sort((a, b) => {
    const rank = { offline: 0, critical: 1, warn: 2, online: 3 };
    return (rank[a.status] ?? 99) - (rank[b.status] ?? 99);
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
          <button
            onClick={() => {
              const el = document.documentElement;
              if (document.fullscreenElement) {
                document.exitFullscreen && document.exitFullscreen();
              } else if (el.requestFullscreen) {
                el.requestFullscreen().catch(err => setToast({ tone: 'warn', msg: '全螢幕請求被瀏覽器拒絕' }));
              }
            }}
            className="flex items-center gap-1.5 h-7 px-2 bg-surface-elevated border border-border-strong rounded hover:bg-surface-overlay">
            <Icon.Maximize size={12}/> 全螢幕 <Kbd>F</Kbd>
          </button>
        </div>
      </div>
      {toast && (
        <div className={`px-4 py-2 text-xs border-b tone-${toast.tone} ${
          toast.tone === 'success' ? 'bg-sev-ok/15 text-sev-ok border-sev-ok/30'
            : toast.tone === 'error' ? 'bg-sev-critical/15 text-sev-critical border-sev-critical/30'
            : toast.tone === 'warn' ? 'bg-sev-warn/15 text-sev-warn border-sev-warn/30'
            : 'bg-sev-info/15 text-sev-info border-sev-info/30'
        }`}>{toast.msg}</div>
      )}
      <div className="flex-1 overflow-y-auto scroll-thin p-3">
        {sorted.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <EmptyState icon={Icon.Camera} title="尚無節點資料"
              hint="伺服器尚未回報任何節點,或當前分頁篩選結果為空"/>
          </div>
        ) : tab === 'pumps' ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {sorted.map(n => <PumpCard key={n.id} node={n} onSelect={onSelectNode} activeAlerts={activeAlerts}/>)}
          </div>
        ) : tab === 'cameras' ? (
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3">
            {sorted.map(n => <NodeCard key={n.id} node={n} onSelect={onSelectNode} activeAlerts={activeAlerts} now={now}/>)}
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
                  {[...pumpNodes].sort((a,b) => (({offline:0,critical:1,warn:2,online:3}[a.status]) ?? 99) - (({offline:0,critical:1,warn:2,online:3}[b.status]) ?? 99))
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
                  {[...cameraNodes].sort((a,b) => (({offline:0,critical:1,warn:2,online:3}[a.status]) ?? 99) - (({offline:0,critical:1,warn:2,online:3}[b.status]) ?? 99))
                    .map(n => <NodeCard key={n.id} node={n} onSelect={onSelectNode} activeAlerts={activeAlerts} now={now}/>)}
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
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect && onSelect(node)}
      onKeyDown={e => {
        if (onSelect && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onSelect(node); }
      }}
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
          <span>⚠ 感測器衝突 — 檢查浮球開關</span>
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

Object.assign(window, { MonitorPage });
