// SDPRS — Audit Page

const { useState: useState_p, useMemo: useMemo_p } = React;

const AuditPage = () => {
  const [meOnly, setMeOnly] = useState_p(false);
  const [operatorFilter, setOperatorFilter] = useState_p('all');
  const [actionFilter, setActionFilter] = useState_p('all');
  const [dateFilter, setDateFilter] = useState_p('all'); // all | today | 7d
  const actionMeta = {
    ALERT_CREATED: { tone: 'critical', label: '警報建立' },
    ALERT_ACK: { tone: 'info', label: '認領' },
    ALERT_RESOLVE: { tone: 'ok', label: '解決' },
    NODE_SNOOZE: { tone: 'warn', label: '節點延期' },
    LOGIN: { tone: 'muted', label: '登入' },
  };
  const operators = useMemo_p(() => {
    const set = new Set();
    (window.AUDIT || []).forEach(a => { if (a.by) set.add(a.by); });
    return ['all', ...Array.from(set)];
  }, [window.AUDIT]);
  const actions = useMemo_p(() => {
    const set = new Set();
    (window.AUDIT || []).forEach(a => { if (a.action) set.add(a.action); });
    return ['all', ...Array.from(set)];
  }, [window.AUDIT]);
  const cycleOperator = () => {
    const i = operators.indexOf(operatorFilter);
    setOperatorFilter(operators[(i + 1) % operators.length]);
  };
  const cycleAction = () => {
    const i = actions.indexOf(actionFilter);
    setActionFilter(actions[(i + 1) % actions.length]);
  };
  const cycleDate = () => {
    const order = ['all', 'today', '7d'];
    setDateFilter(order[(order.indexOf(dateFilter) + 1) % order.length]);
  };
  // Audit rows carry a full ms timestamp on `a.ts` (see api.jsx mapAuditRow).
  // If missing, treat as "include" so the filter isn't silently dropping rows
  // with legacy schemas.
  const inDateRange = (a) => {
    if (dateFilter === 'all') return true;
    if (a.ts == null) return true;
    const now = Date.now();
    if (dateFilter === 'today') {
      const d = new Date(a.ts);
      const today = new Date(now);
      return d.getFullYear() === today.getFullYear()
        && d.getMonth() === today.getMonth()
        && d.getDate() === today.getDate();
    }
    if (dateFilter === '7d') {
      return (now - a.ts) <= 7 * 24 * 60 * 60 * 1000;
    }
    return true;
  };
  const _sessionUser = (window.SDPRS_USER && String(window.SDPRS_USER).trim()) || '';
  const records = (window.AUDIT || []).filter(a => {
    // meOnly requires a known session user — if SDPRS_USER is unset, meOnly
    // returns nothing (safer than matching a hardcoded default).
    if (meOnly) {
      if (!_sessionUser || a.by !== _sessionUser) return false;
    }
    if (operatorFilter !== 'all' && a.by !== operatorFilter) return false;
    if (actionFilter !== 'all' && a.action !== actionFilter) return false;
    if (!inDateRange(a)) return false;
    return true;
  });
  const filtersActive = meOnly || operatorFilter !== 'all' || actionFilter !== 'all' || dateFilter !== 'all';
  const dateLabel = dateFilter === 'all' ? '全部' : dateFilter === 'today' ? '今日' : '近 7 天';
  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="px-4 py-2.5 border-b border-border-subtle bg-surface-panel flex items-center gap-3 flex-shrink-0">
        <h1 className="text-sm font-semibold">稽核紀錄</h1>
        <span className="text-xs text-ink-muted tnum">
          {records.length} 筆{filtersActive && ` · 已篩選`}
        </span>
        <div className="flex-1"></div>
        <div className="flex items-center gap-1.5">
          <button onClick={() => setMeOnly(!meOnly)}
            className={`inline-flex items-center gap-1 h-7 px-2 rounded text-xs border transition-colors ${meOnly ? 'bg-sev-info/15 border-sev-info/40 text-sev-info' : 'bg-surface-elevated border-border-subtle text-ink-secondary hover:border-border-strong'}`}>
            <Icon.User size={12}/> 本班 · 我的動作
          </button>
          <FilterChip active={operatorFilter !== 'all'} onClick={cycleOperator}>操作者: {operatorFilter === 'all' ? '全部' : operatorFilter} <Icon.ChevronDown size={10}/></FilterChip>
          <FilterChip active={actionFilter !== 'all'} onClick={cycleAction}>動作: {actionFilter === 'all' ? '全部' : (actionMeta[actionFilter]?.label || actionFilter)} <Icon.ChevronDown size={10}/></FilterChip>
          <FilterChip active={dateFilter !== 'all'} onClick={cycleDate}>日期: {dateLabel} <Icon.ChevronDown size={10}/></FilterChip>
          <button
            onClick={() => window.SDPRS_API.exportAuditCsv({
              limit: 1000,
              type: actionFilter !== 'all' ? actionFilter : undefined,
            })}
            title="下載目前條件的稽核紀錄 (CSV)"
            className="ml-2 h-7 px-2 bg-surface-elevated border border-border-strong rounded text-xs flex items-center gap-1.5 hover:bg-surface-overlay">
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
            {records.length === 0 && (
              <tr>
                <td colSpan={5} className="p-0">
                  <div className="py-8">
                    {/* api.jsx flags window.AUDIT.forbidden when GET /api/audit
                        returned 403 — show an explicit no-permission state so
                        non-admins understand why the table is empty. Otherwise
                        fall back to the generic empty message (respects the
                        active filters). Entries are checked first (records.length
                        === 0 here) so a stale forbidden flag can never hide data. */}
                    {window.AUDIT?.forbidden === true ? (
                      <EmptyState icon={Icon.ShieldAlert}
                        title="無權限查看稽核紀錄,請聯絡管理員"
                        hint="需具備管理員權限才能檢視稽核紀錄"/>
                    ) : (
                      <EmptyState icon={Icon.ClipboardList}
                        title={filtersActive ? '無符合條件的稽核紀錄' : '尚無稽核紀錄'}
                        hint={filtersActive ? '調整上方篩選條件' : '本班尚無操作紀錄'}/>
                    )}
                  </div>
                </td>
              </tr>
            )}
            {records.map((a, i) => {
              const m = actionMeta[a.action] || { tone: 'muted', label: a.action };
              return (
                <tr key={i} className="border-b border-border-subtle/60 hover:bg-surface-elevated/60">
                  <td className="px-4 py-2 font-mono text-ink-muted">{a.t}</td>
                  <td className="px-4 py-2 font-mono">
                    <span className={a.by === 'system' ? 'text-ink-muted' : (_sessionUser && a.by === _sessionUser) ? 'text-sev-info font-semibold' : 'text-ink-primary'}>{a.by}</span>
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

Object.assign(window, { AuditPage });
