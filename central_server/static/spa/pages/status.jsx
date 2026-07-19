// SDPRS — Node Status Page

const { useState: useState_p, useMemo: useMemo_p } = React;

// Per-row snooze control. Local `busy` state guards against double-fire on
// slow VPN; onKeyDown must stopPropagation so Enter/Space on the focused
// button doesn't also bubble to the surrounding row's keyboard handler
// (which would open the side panel).
const SnoozeRowButton = ({ node, onDone, onError }) => {
  const [busy, setBusy] = React.useState(false);
  const trigger = () => {
    if (busy) return;
    // G1: don't silently no-op when the API bundle hasn't loaded / backend is
    // unreachable — route through onError so the parent's toast surfaces the
    // outage instead of the operator wondering why nothing happened.
    if (!(window.SDPRS_API && window.SDPRS_API.snoozeNode)) {
      onError && onError(new Error('暫時無法連線後端，請稍後再試'));
      return;
    }
    setBusy(true);
    Promise.resolve(window.SDPRS_API.snoozeNode(node.id, 30, '從節點狀態列表靜音'))
      .then(() => onDone && onDone(node, 30))
      .catch(err => onError && onError(err))
      .finally(() => setBusy(false));
  };
  return (
    <button
      title="靜音 30 分鐘"
      disabled={busy}
      onClick={e => { e.stopPropagation(); trigger(); }}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}
      className="w-6 h-6 rounded hover:bg-surface-overlay flex items-center justify-center text-ink-muted hover:text-ink-primary disabled:opacity-50 disabled:cursor-not-allowed"><Icon.BellOff size={12}/></button>
  );
};

// Per-row stream start/stop toggle for cameras. Mirrors SnoozeRowButton:
// local busy guard, click/key stopPropagation so the row's side-panel
// handler doesn't fire.
//
// State proxy: mapNode does not expose stream_status directly, so we infer
// "streaming now" from n.bitrate > 0. Not perfect (a just-started stream
// hasn't reported kbps yet, and a stopped stream that leaked a last
// bitrate sample also shows > 0 briefly), but good enough for a toggle
// — backend will reject inconsistent state with a 4xx/5xx that we toast.
//
// API-gated: if SDPRS_API.startStream / stopStream are missing (waiting
// on api.jsx follow-up), render disabled with "串流控制 (等待 API)".
const StreamRowButton = ({ node, onDone, onError }) => {
  const [busy, setBusy] = React.useState(false);
  const api = window.SDPRS_API || {};
  const hasApi = typeof api.startStream === 'function' && typeof api.stopStream === 'function';
  const isActive = (node.bitrate || 0) > 0;
  const label = isActive ? '停止串流' : '開始串流';
  const Glyph = isActive ? Icon.Pause : Icon.Play;
  const trigger = () => {
    if (busy || !hasApi) return;
    setBusy(true);
    const call = isActive ? api.stopStream(node.id) : api.startStream(node.id);
    Promise.resolve(call)
      .then(() => onDone && onDone(node, isActive ? 'stop' : 'start'))
      .catch(err => onError && onError(err))
      .finally(() => setBusy(false));
  };
  return (
    <button
      title={hasApi ? label : '串流控制 (等待 API)'}
      disabled={busy || !hasApi}
      onClick={e => { e.stopPropagation(); trigger(); }}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}
      className="w-6 h-6 rounded hover:bg-surface-overlay flex items-center justify-center text-ink-muted hover:text-ink-primary disabled:opacity-50 disabled:cursor-not-allowed"><Glyph size={12}/></button>
  );
};

const StatusPage = ({ onSelectNode, onRefresh }) => {
  const [typeFilter, setTypeFilter] = useState_p('all');    // all | camera | pump
  const [statusFilter, setStatusFilter] = useState_p('all'); // all | online | warn | critical | offline
  const [locationFilter, setLocationFilter] = useState_p('all');
  // Local toast (success/error feedback for snooze etc.). Auto-dismissed after 3s.
  const [toast, setToast] = useState_p(null);
  React.useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(t);
  }, [toast]);
  // Unique locations from the current node list — filter values are derived
  // so a new deployment doesn't need a config change.
  const locations = useMemo_p(() => {
    const set = new Set();
    window.NODES.forEach(n => { if (n.location) set.add(n.location); });
    return ['all', ...Array.from(set)];
  }, [window.NODES]);
  const filtered = useMemo_p(() => window.NODES.filter(n => {
    if (typeFilter !== 'all' && n.type !== typeFilter) return false;
    if (statusFilter !== 'all' && n.status !== statusFilter) return false;
    if (locationFilter !== 'all' && n.location !== locationFilter) return false;
    return true;
  }), [typeFilter, statusFilter, locationFilter, window.NODES]);
  // Cycle through preset values for the chip dropdowns (real dropdown UI is
  // a bigger design decision — keep the click surface working with cycling).
  const cycleType = () => setTypeFilter(t => t === 'all' ? 'camera' : t === 'camera' ? 'pump' : 'all');
  const cycleStatus = () => setStatusFilter(s => {
    const order = ['all', 'online', 'warn', 'critical', 'offline'];
    return order[(order.indexOf(s) + 1) % order.length];
  });
  const cycleLocation = () => setLocationFilter(l => {
    const i = locations.indexOf(l);
    return locations[(i + 1) % locations.length];
  });
  const typeLabel = typeFilter === 'all' ? '全部' : typeFilter === 'camera' ? '攝影機' : '抽水站';
  const statusLabel = statusFilter === 'all' ? '全部' : statusFilter === 'online' ? '正常' : statusFilter === 'warn' ? '警告' : statusFilter === 'critical' ? '嚴重' : '離線';
  const locationLabel = locationFilter === 'all' ? '全部' : locationFilter;
  const filtersActive = typeFilter !== 'all' || statusFilter !== 'all' || locationFilter !== 'all';
  return (
    <div className="h-full flex flex-col min-h-0">
      {toast && (
        <div className={`px-4 py-2 text-xs border-b tone-${toast.tone} ${
          toast.tone === 'success' ? 'bg-sev-ok/15 text-sev-ok border-sev-ok/30'
            : toast.tone === 'error' ? 'bg-sev-critical/15 text-sev-critical border-sev-critical/30'
            : toast.tone === 'warn' ? 'bg-sev-warn/15 text-sev-warn border-sev-warn/30'
            : 'bg-sev-info/15 text-sev-info border-sev-info/30'
        }`}>{toast.msg}</div>
      )}
      <div className="px-4 py-2.5 border-b border-border-subtle bg-surface-panel flex items-center gap-3 flex-shrink-0">
        <h1 className="text-sm font-semibold">節點狀態</h1>
        <span className="text-xs text-ink-muted tnum">
          {filtered.length}{filtered.length !== window.NODES.length && ` / ${window.NODES.length}`} 個節點
        </span>
        <div className="flex-1"></div>
        <div className="flex gap-1.5">
          <FilterChip active={typeFilter !== 'all'} onClick={cycleType}>
            類型: {typeLabel} <Icon.ChevronDown size={10}/>
            {typeFilter !== 'all' && (
              // G2: FilterChip renders a <button>; a nested real <button> is
              // invalid HTML (browsers unwrap or split). Use span+role so the
              // clear × stays keyboard-operable without breaking the DOM.
              <span
                role="button"
                tabIndex={0}
                aria-label="清除類型篩選"
                className="ml-1 text-slate-500 hover:text-slate-200 cursor-pointer"
                onClick={(e) => { e.stopPropagation(); setTypeFilter('all'); }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    e.stopPropagation();
                    setTypeFilter('all');
                  }
                }}
              >×</span>
            )}
          </FilterChip>
          <FilterChip active={statusFilter !== 'all'} onClick={cycleStatus}>
            狀態: {statusLabel} <Icon.ChevronDown size={10}/>
            {statusFilter !== 'all' && (
              <span
                role="button"
                tabIndex={0}
                aria-label="清除狀態篩選"
                className="ml-1 text-slate-500 hover:text-slate-200 cursor-pointer"
                onClick={(e) => { e.stopPropagation(); setStatusFilter('all'); }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    e.stopPropagation();
                    setStatusFilter('all');
                  }
                }}
              >×</span>
            )}
          </FilterChip>
          <FilterChip active={locationFilter !== 'all'} onClick={cycleLocation}>
            位置: <span className="max-w-[80px] truncate inline-block align-middle">{locationLabel}</span> <Icon.ChevronDown size={10}/>
            {locationFilter !== 'all' && (
              <span
                role="button"
                tabIndex={0}
                aria-label="清除位置篩選"
                className="ml-1 text-slate-500 hover:text-slate-200 cursor-pointer"
                onClick={(e) => { e.stopPropagation(); setLocationFilter('all'); }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    e.stopPropagation();
                    setLocationFilter('all');
                  }
                }}
              >×</span>
            )}
          </FilterChip>
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
            {filtered.length === 0 && (
              <tr>
                <td colSpan={10} className="p-0">
                  <div className="py-8">
                    <EmptyState icon={Icon.Server}
                      title={filtersActive ? '無符合條件的節點' : '尚無節點資料'}
                      hint={filtersActive ? '清除或調整上方篩選條件' : '伺服器尚未回報任何節點'}/>
                  </div>
                </td>
              </tr>
            )}
            {filtered.map(n => {
              // A pump with a live heartbeat but no water_level reading (sensor
              // down / not yet reported) should not read as green — downgrade
              // the badge tone locally so the row doesn't lie about health.
              const pumpLevelMissing = n.type === 'pump' && n.level == null;
              let tone = n.status === 'offline' || n.status === 'critical' ? 'critical' : n.status === 'warn' ? 'warn' : 'ok';
              if (tone === 'ok' && pumpLevelMissing) tone = 'warn';
              const uploadIssue = n.heartbeat < 60 && n.upload > 600;
              return (
                <tr key={n.id}
                  role="button"
                  tabIndex={0}
                  className="border-b border-border-subtle/60 hover:bg-surface-elevated/60 group cursor-pointer focus:outline focus:outline-1 focus:outline-sev-info"
                  onClick={() => onSelectNode && onSelectNode(n)}
                  onKeyDown={e => {
                    if (onSelectNode && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onSelectNode(n); }
                  }}>
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
                    {n.heartbeat != null ? (n.heartbeat > 60 ? Math.floor(n.heartbeat/60)+'m' : n.heartbeat+'s') : '—'}
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${uploadIssue ? 'text-sev-critical font-semibold' : n.upload > 60 ? 'text-sev-warn' : 'text-ink-secondary'}`}>
                    {n.upload > 60 ? Math.floor(n.upload/60)+'m' : n.upload+'s'}
                    {uploadIssue && <span className="ml-1 text-[10px] bg-sev-critical/20 text-sev-critical px-1 rounded">上傳異常</span>}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {n.type === 'camera' ? (
                      <span className={n.bitrate < 0.5 ? 'text-sev-critical' : n.bitrate < 1 ? 'text-sev-warn' : 'text-sev-ok'}>
                        {n.bitrate != null ? n.bitrate.toFixed(1) : '—'}Mbps <span className="text-ink-muted">· {n.drops} drops</span>
                      </span>
                    ) : <span className="text-ink-muted">—</span>}
                  </td>
                  <td className="px-3 py-2 text-right font-mono">
                    {n.type === 'camera' ? (
                      <span className={n.temp > 50 ? 'text-sev-warn' : n.temp ? 'text-ink-secondary' : 'text-ink-muted'}>{n.temp ? n.temp+'°C' : '—'}</span>
                    ) : n.level == null ? (
                      // No water_level reading — do not render 0%/blank% which
                      // would look like a real reading. Amber "—" makes the gap
                      // visible; upstream api.jsx keeps status='online' because
                      // heartbeat is fine, so the row is still ONLINE.
                      <span className="text-sev-warn" title="水位資料未上傳">—</span>
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
                      {n.type === 'camera' && (
                        <StreamRowButton
                          node={n}
                          onDone={(node, action) => {
                            setToast({ tone: 'success', msg: `${node.name || node.id} ${action === 'stop' ? '串流已停止' : '串流已啟動'}` });
                            if (typeof onRefresh === 'function') onRefresh();
                          }}
                          onError={err => setToast({ tone: 'error', msg: `串流指令失敗: ${err?.message || err}` })}/>
                      )}
                      <SnoozeRowButton
                        node={n}
                        onDone={(node, minutes) => {
                          setToast({ tone: 'success', msg: `${node.name || node.id} 已靜音 ${minutes} 分鐘` });
                          if (typeof onRefresh === 'function') onRefresh();
                        }}
                        onError={err => setToast({ tone: 'error', msg: `靜音失敗: ${err?.message || err}` })}/>
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

Object.assign(window, { StatusPage });
