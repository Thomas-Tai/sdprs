// SDPRS — Audit Page

const { useState: useState_p, useMemo: useMemo_p } = React;

// "本班" (this shift) is a rolling 12-hour window — SDPRS operator rotations
// run 12h. Applied to both the on-screen meOnly filter (C3) and the CSV
// export sinceMs when meOnly is active (C2). Rows without a parseable a.ts
// fall through and are included — same tolerance as inDateRange below.
const SHIFT_WINDOW_MS = 12 * 60 * 60 * 1000;

// WHA-H2 fix (2026-07-20): render a full date+time stamp so multi-day rows
// (e.g. under the "近 7 天" filter) are distinguishable — `a.t` alone is
// HH:MM:SS with no date. Uses the browser's LOCAL Date getters only — `ts`
// is already-correct epoch ms (parseTs upstream), so this is a pure display
// conversion, not a compensating offset (see forecast-timezone contract).
const formatAuditTs = (ts) => {
  if (ts == null) return null;
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};

const AuditPage = ({ auditLog = [] }) => {
  const [meOnly, setMeOnly] = useState_p(false);
  const [operatorFilter, setOperatorFilter] = useState_p('all');
  const [actionFilter, setActionFilter] = useState_p('all');
  const [dateFilter, setDateFilter] = useState_p('all'); // all | today | 7d
  const [exportState, setExportState] = useState_p(null);
  React.useEffect(() => {
    // WHA-M1 fix (2026-07-20): this used to auto-dismiss unconditionally
    // after 3s, including the 'info' (匯出中...) in-flight state — but the
    // export fetch can take up to ~10s (up to 10000 rows, see exportAuditCsv
    // below), and `disabled={exportState?.tone === 'info'}` on the export
    // button is the ONLY double-submit guard. The guard was disarming itself
    // ~7s before the request it was guarding could finish. Only auto-dismiss
    // the terminal states (success/error); 'info' is cleared explicitly by
    // exportAuditCsv's own try/catch when the request actually settles.
    if (!exportState || exportState.tone === 'info') return undefined;
    const timer = setTimeout(() => setExportState(null), 3000);
    return () => clearTimeout(timer);
  }, [exportState]);
  // WHA-M2 fix (2026-07-20): 'muted' is not one of the configured Tailwind
  // sev tokens (only critical/warn/info/ok/stale exist — see index.html
  // theme config) — `bg-sev-muted/15 text-sev-muted border-sev-muted/30`
  // resolved to nothing, so these six action types rendered unstyled
  // badges. 'stale' is the closest existing neutral/gray token.
  const actionMeta = {
    ACKNOWLEDGE:      { label: '已確認',   tone: 'info' },
    RESOLVE:          { label: '已解決',   tone: 'ok' },
    BULK_ACKNOWLEDGE: { label: '批次確認', tone: 'info' },
    BULK_RESOLVE:     { label: '批次解決', tone: 'ok' },
    SNOOZE:           { label: '節點靜音', tone: 'stale' },
    UNSNOOZE:         { label: '解除靜音', tone: 'stale' },
    LOCATION_EDIT:    { label: '位置編輯', tone: 'stale' },
    HANDOVER_EDIT:    { label: '交接編輯', tone: 'stale' },
    LOGIN:            { label: '登入',     tone: 'stale' },
    LOGOUT:           { label: '登出',     tone: 'stale' },
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
  // operatorFilter for the `operator` param (they compose semantically as an
  // AND, but the button implies self); the shift window and the date-chip
  // window instead AND together (see WHA-L1 fix below) since that's how
  // `records` above actually filters the on-screen rows.
  const exportAuditCsv = async () => {
    setExportState({ tone: 'info', msg: '匯出中...' });
    const now = Date.now();
    let operatorParam;
    if (meOnly) {
      if (_sessionUser) operatorParam = _sessionUser;
    } else if (operatorFilter !== 'all') {
      operatorParam = operatorFilter;
    }
    // WHA-L1 fix (2026-07-20): meOnly and the 日期 (今日/近 7 天) chip are NOT
    // mutually exclusive on screen — the `records` filter above ANDs the
    // meOnly shift-window check together with inDateRange regardless of
    // meOnly's state. This used to branch as if only one could ever apply
    // (meOnly ⇒ shift window only, ignoring dateFilter entirely), so early in
    // a local day (e.g. 00:30) turning on both 本班·我的動作 and 今日 exported
    // rows back to the full 12h shift floor even though the on-screen table
    // ALSO excluded anything from before local midnight — CSV and screen
    // disagreed right at the boundary where "12h ago" crosses into
    // yesterday. Reuse `_shiftFloor` (the exact value `records` filters
    // with) and the same local-midnight cutoff `inDateRange` uses, and AND
    // them the same way the screen does — the later (larger) cutoff wins.
    const cutoffs = [];
    if (meOnly) cutoffs.push(_shiftFloor);
    if (dateFilter === 'today') {
      const d = new Date(now);
      // Local midnight — matches inDateRange's local-day comparison so the
      // exported set equals the on-screen set for the operator's session.
      cutoffs.push(new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime());
    } else if (dateFilter === '7d') {
      cutoffs.push(now - 7 * 24 * 60 * 60 * 1000);
    }
    const sinceMs = cutoffs.length ? Math.max(...cutoffs) : undefined;
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
      {/* WHA-M14 fix (2026-07-20): /api/audit has no total-count field, so
          api.jsx's loadAudit() can only prove a FLOOR when the server-side
          cap is hit (`totalAvailable` == the row limit it received) — the
          true count could be higher. Wording MUST NOT claim an exact total.
          Note `totalAvailable` equals the number of rows actually shown, so
          quoting it as both "at least N on the server" AND "only N loaded"
          states the same number twice for two different quantities and tells
          the operator nothing. The honest framing is simply: this is the cap,
          and more rows exist beyond it. This is a persistent banner (no auto-dismiss) since it
          reflects actual data completeness, not a transient action result —
          an operator reviewing an incident log needs to know the list may
          be incomplete for as long as it actually is. */}
      {auditLog && auditLog.truncated && (
        <div role="status" className="px-4 py-1.5 text-xs border-b border-sev-warn/30 bg-sev-warn/10 text-sev-warn flex items-center gap-2 flex-shrink-0">
          <Icon.AlertTriangle size={12}/>
          <span>稽核紀錄已達載入上限 · 僅顯示最新 {auditLog.totalAvailable} 筆，伺服器上仍有更多紀錄未顯示 · 請縮小篩選範圍或使用 CSV 匯出取得完整紀錄</span>
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
      <div className="flex-1 overflow-y-auto overflow-x-auto scroll-thin">
        <table className="w-full text-xs tnum">
          <thead className="sticky top-0 bg-surface-base z-10 border-b border-border-strong">
            <tr className="text-[10px] text-ink-muted uppercase tracking-wider">
              <th scope="col" className="text-left font-semibold px-4 py-2 w-28">時間</th>
              <th scope="col" className="text-left font-semibold px-4 py-2 w-32">操作者</th>
              <th scope="col" className="text-left font-semibold px-4 py-2 w-32">動作</th>
              <th scope="col" className="text-left font-semibold px-4 py-2 w-48">目標</th>
              <th scope="col" className="text-left font-semibold px-4 py-2">詳情</th>
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
            {records.map((a) => {
              const m = actionMeta[a.action] || { tone: 'stale', label: a.action };
              // WHA-L6 fix (2026-07-20): was keyed on array index, which
              // shifts every time a new row is prepended by a refresh —
              // React then reuses/misattributes DOM nodes across unrelated
              // rows. Synthesize a stable-enough key from the row's own
              // content (no backend row id is mapped through mapAuditRow).
              const rowKey = `${a.ts != null ? a.ts : 'na'}-${a.by}-${a.action}-${a.target}`;
              return (
                <tr key={rowKey} className="border-b border-border-subtle/60 hover:bg-surface-elevated/60">
                  {/* WHA-H2 fix (2026-07-20): `a.t` is a time-only HH:MM:SS
                      string (see api.jsx mapAuditRow's fmtClock) — under the
                      "近 7 天" filter, rows from different days become
                      indistinguishable. Derive a full MM-DD HH:MM:SS stamp
                      from `a.ts` (already-correct epoch ms per parseTs) using
                      the browser's local time getters — no manual UTC offset
                      arithmetic, so this can't reintroduce an API-F1-class
                      timezone bug. Falls back to the time-only string for
                      legacy rows with no parseable ts. */}
                  <td className="px-4 py-2 font-mono text-ink-muted whitespace-nowrap">{formatAuditTs(a.ts) || a.t || '—'}</td>
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
