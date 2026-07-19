// SDPRS — Audit Page

const { useState: useState_p, useMemo: useMemo_p } = React;

// "本班" (this shift) is a rolling 12-hour window — SDPRS operator rotations
// run 12h. Applied to both the on-screen meOnly filter (C3) and the CSV
// export sinceMs when meOnly is active (C2). Rows without a parseable a.ts
// fall through and are included — same tolerance as inDateRange below.
const SHIFT_WINDOW_MS = 12 * 60 * 60 * 1000;

const AuditPage = ({ auditLog = [] }) => {
  const [meOnly, setMeOnly] = useState_p(false);
  const [operatorFilter, setOperatorFilter] = useState_p('all');
  const [actionFilter, setActionFilter] = useState_p('all');
  const [dateFilter, setDateFilter] = useState_p('all'); // all | today | 7d
  const [exportState, setExportState] = useState_p(null);
  React.useEffect(() => {
    if (!exportState) return undefined;
    const timer = setTimeout(() => setExportState(null), 3000);
    return () => clearTimeout(timer);
  }, [exportState]);
  const actionMeta = {
    ACKNOWLEDGE:      { label: '已確認',   tone: 'info' },
    RESOLVE:          { label: '已解決',   tone: 'ok' },
    BULK_ACKNOWLEDGE: { label: '批次確認', tone: 'info' },
    BULK_RESOLVE:     { label: '批次解決', tone: 'ok' },
    SNOOZE:           { label: '節點靜音', tone: 'muted' },
    UNSNOOZE:         { label: '解除靜音', tone: 'muted' },
    LOCATION_EDIT:    { label: '位置編輯', tone: 'muted' },
    HANDOVER_EDIT:    { label: '交接編輯', tone: 'muted' },
    LOGIN:            { label: '登入',     tone: 'muted' },
    LOGOUT:           { label: '登出',     tone: 'muted' },
  };
  const operators = useMemo_p(() => {
    const set = new Set();
    (auditLog || []).forEach(a => { if (a.by) set.add(a.by); });
    return ['all', ...Array.from(set)];
  }, [auditLog]);
  const actions = useMemo_p(() => {
    const set = new Set();
    (auditLog || []).forEach(a => { if (a.action) set.add(a.action); });
    return ['all', ...Array.from(set)];
  }, [auditLog]);
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
  const _shiftFloor = Date.now() - SHIFT_WINDOW_MS;
  const records = (auditLog || []).filter(a => {
    // meOnly requires a known session user — if SDPRS_USER is unset, meOnly
    // returns nothing (safer than matching a hardcoded default). meOnly also
    // enforces the rolling 12h shift window (C3 fix) so 本班·我的動作 no
    // longer returns every action the operator ever took. Rows without a
    // parseable ts pass the window filter (same tolerance as inDateRange).
    if (meOnly) {
      if (!_sessionUser || a.by !== _sessionUser) return false;
      if (a.ts != null && a.ts < _shiftFloor) return false;
    }
    if (operatorFilter !== 'all' && a.by !== operatorFilter) return false;
    if (actionFilter !== 'all' && a.action !== actionFilter) return false;
    if (!inDateRange(a)) return false;
    return true;
  });
  const filtersActive = meOnly || operatorFilter !== 'all' || actionFilter !== 'all' || dateFilter !== 'all';
  const dateLabel = dateFilter === 'all' ? '全部' : dateFilter === 'today' ? '今日' : '近 7 天';
  // C2 fix (2026-07-16): mirror ALL active on-screen filters into the CSV
  // query. Previously only `type` was forwarded, so compliance would receive
  // every SNOOZE row across all operators/history instead of the narrow
  // "today · my actions · SNOOZE" the operator saw. meOnly wins over
  // operatorFilter (they compose semantically as an AND, but the button
  // implies self), and the shift window replaces any date-chip window when
  // meOnly is active (12h ≤ today ≤ 7d for typical use).
  const exportAuditCsv = async () => {
    setExportState({ tone: 'info', msg: '匯出中...' });
    const now = Date.now();
    let operatorParam;
    let sinceMs;
    if (meOnly) {
      if (_sessionUser) operatorParam = _sessionUser;
      sinceMs = now - SHIFT_WINDOW_MS;
    } else {
      if (operatorFilter !== 'all') operatorParam = operatorFilter;
      if (dateFilter === 'today') {
        const d = new Date(now);
        // Local midnight — matches inDateRange's local-day comparison so the
        // exported set equals the on-screen set for the operator's session.
        sinceMs = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
      } else if (dateFilter === '7d') {
        sinceMs = now - 7 * 24 * 60 * 60 * 1000;
      }
    }
    try {
      await window.SDPRS_API.exportAuditCsv({
        // G4: was 1000 — docs advertise 10000 as the ceiling; the previous
        // silent cap made "matched but truncated" invisible to compliance.
        limit: 10000,
        type: actionFilter !== 'all' ? actionFilter : undefined,
        operator: operatorParam,
        sinceMs,
      });
      setExportState({ tone: 'success', msg: '已匯出 CSV' });
    } catch (e) {
      const isForbidden = e?.status === 403 || String(e?.message || '').includes('403');
      setExportState({
        tone: 'error',
        msg: isForbidden ? '需要管理員權限' : '匯出失敗，請重試',
      });
    }
  };
  return (
    <div className="h-full flex flex-col min-h-0">
      {exportState && (
        <div
          role={exportState.tone === 'error' ? 'alert' : 'status'}
          className={`px-4 py-2 text-xs border-b flex items-center gap-3 flex-shrink-0 ${
            exportState.tone === 'success' ? 'bg-sev-ok/15 text-sev-ok border-sev-ok/30'
              : exportState.tone === 'error' ? 'bg-sev-critical/15 text-sev-critical border-sev-critical/30'
              : 'bg-sev-info/15 text-sev-info border-sev-info/30'
          }`}
        >
          <span>{exportState.msg}</span>
          <button
            aria-label="關閉匯出通知"
            className="ml-auto text-current opacity-70 hover:opacity-100"
            onClick={() => setExportState(null)}
          >×</button>
        </div>
      )}
      <div className="px-4 py-2.5 border-b border-border-subtle bg-surface-panel flex items-center gap-3 flex-shrink-0">
        <h1 className="text-sm font-semibold">稽核紀錄</h1>
        <span className="text-xs text-ink-muted tnum">
          {records.length} 筆{filtersActive && ` · 已篩選`}
        </span>
        <div className="flex-1"></div>
        <div className="flex items-center gap-1.5">
          <button onClick={() => setMeOnly(!meOnly)}
            title="僅顯示我在近 12 小時內的操作"
            className={`inline-flex items-center gap-1 h-7 px-2 rounded text-xs border transition-colors ${meOnly ? 'bg-sev-info/15 border-sev-info/40 text-sev-info' : 'bg-surface-elevated border-border-subtle text-ink-secondary hover:border-border-strong'}`}>
            <Icon.User size={12}/> 本班 · 我的動作
            {meOnly && <span className="text-[10px] opacity-70 ml-0.5">(近12h)</span>}
          </button>
          <div className="inline-flex items-center">
            <FilterChip active={operatorFilter !== 'all'} onClick={cycleOperator}>
              操作者: {operatorFilter === 'all' ? '全部' : operatorFilter} <Icon.ChevronDown size={10}/>
            </FilterChip>
            {operatorFilter !== 'all' && (
              <button
                type="button"
                aria-label="清除操作者篩選"
                className="ml-1 text-slate-500 hover:text-slate-200"
                onClick={(e) => { e.stopPropagation(); setOperatorFilter('all'); }}
              >×</button>
            )}
          </div>
          <div className="inline-flex items-center">
            <FilterChip active={actionFilter !== 'all'} onClick={cycleAction}>
              動作: {actionFilter === 'all' ? '全部' : (actionMeta[actionFilter]?.label || actionFilter)} <Icon.ChevronDown size={10}/>
            </FilterChip>
            {actionFilter !== 'all' && (
              <button
                type="button"
                aria-label="清除動作篩選"
                className="ml-1 text-slate-500 hover:text-slate-200"
                onClick={(e) => { e.stopPropagation(); setActionFilter('all'); }}
              >×</button>
            )}
          </div>
          <div className="inline-flex items-center">
            <FilterChip active={dateFilter !== 'all'} onClick={cycleDate}>
              日期: {dateLabel} <Icon.ChevronDown size={10}/>
            </FilterChip>
            {dateFilter !== 'all' && (
              <button
                type="button"
                aria-label="清除日期篩選"
                className="ml-1 text-slate-500 hover:text-slate-200"
                onClick={(e) => { e.stopPropagation(); setDateFilter('all'); }}
              >×</button>
            )}
          </div>
          {/* H-4: disable while an export is in flight (exportState.tone==='info'
              is the "匯出中..." message set by exportAuditCsv). Prevents a
              stressed operator on slow network from queueing 3-4 concurrent
              generations + downloads + audit_export rows. */}
          <button
            onClick={exportAuditCsv}
            disabled={exportState?.tone === 'info'}
            aria-busy={exportState?.tone === 'info'}
            title="下載目前條件的稽核紀錄 (CSV,最多 10000 筆)"
            className="ml-2 h-7 px-2 bg-surface-elevated border border-border-strong rounded text-xs flex items-center gap-1.5 hover:bg-surface-overlay disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-surface-elevated">
            <Icon.Download size={12}/> 匯出 CSV
          </button>
          {/* G4: expose the server-side row ceiling so operators know when
              they're at risk of a silent truncation and should narrow filters. */}
          <span className="text-[10px] text-ink-muted tnum ml-1">最多 10000 筆</span>
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
                    {auditLog?.forbidden === true ? (
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
