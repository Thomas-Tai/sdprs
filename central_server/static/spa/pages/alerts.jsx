// SDPRS — Alerts Page

const { useState: useState_p, useMemo: useMemo_p, useRef: useRef_p, useEffect: useEffect_p } = React;

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
  const m = window.safeSevMeta(alert.sev);
  const node = window.NODES.find(n => n.id === alert.node);
  const rowH = density === 'compact' ? 'h-9' : 'h-12';
  const isUrgent = alert.state === 'pending' && alert.sev === 'critical' && alert.ageSec < 60;
  return (
    <div
      onClick={() => onSelect(alert.id)}
      role="button"
      tabIndex={0}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(alert.id); }
      }}
      className={`relative ${rowH} flex items-center pl-3 pr-3 border-b border-border-subtle/60 cursor-pointer transition-colors sev-bar ${m.bar} ${selected ? 'row-selected' : 'hover:bg-surface-elevated/60'} ${flash ? 'row-flash' : ''} ${isUrgent ? 'animate-pulse-critical' : ''}`}
    >
      <div className="w-6 flex-shrink-0 flex items-center justify-center" onClick={e => e.stopPropagation()}>
        <input type="checkbox" checked={checked} onChange={() => onCheck(alert.id)}
          onKeyDown={e => { if (e.key === ' ' || e.key === 'Enter') e.stopPropagation(); }}
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

const AlertsPage = ({ density, selectedId, setSelectedId, alerts, onAck, onResolve, onSnooze, onRefresh, ackedIds, resolveNote, setResolveNote, busy }) => {
  const [tab, setTab] = useState_p('active');
  const [filterSev, setFilterSev] = useState_p('all');
  const [checked, setChecked] = useState_p(new Set());
  const [search, setSearch] = useState_p('');
  const [snoozeOpen, setSnoozeOpen] = useState_p(false);
  const [bulkNote, setBulkNote] = useState_p('');
  const [bulkBusy, setBulkBusy] = useState_p(false);

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

  // Bulk actions — single API round-trip via SDPRS_API.bulkAckAlerts /
  // bulkResolveAlerts. On success we call the parent's onRefresh (app.jsx's
  // refresh) so React `alerts` state updates in the same tick — reaching into
  // SDPRS_API.refreshLive() alone only updates window.ALERTS and leaves the
  // visible list stale until the next 20s poll (audit finding MED #5).
  const runRefreshAfterBulk = async () => {
    if (typeof onRefresh === 'function') {
      try { await onRefresh(); return; } catch (_) { /* fall through */ }
    }
    if (window.SDPRS_API && typeof window.SDPRS_API.refreshLive === 'function') {
      try { await window.SDPRS_API.refreshLive(); } catch (_) {}
    }
  };
  const handleBulkAck = async () => {
    if (checked.size === 0 || bulkBusy) return;
    setBulkBusy(true);
    const ids = [...checked];
    try {
      await window.SDPRS_API.bulkAckAlerts(ids, bulkNote);
      setChecked(new Set());
      setBulkNote('');
      await runRefreshAfterBulk();
    } catch (e) {
      alert('批次操作失敗');
    } finally {
      setBulkBusy(false);
    }
  };
  const handleBulkResolve = async () => {
    if (checked.size === 0 || bulkBusy) return;
    if (!bulkNote.trim()) { alert('批次解決需填寫備註'); return; }
    setBulkBusy(true);
    const ids = [...checked];
    try {
      await window.SDPRS_API.bulkResolveAlerts(ids, bulkNote);
      setChecked(new Set());
      setBulkNote('');
      await runRefreshAfterBulk();
    } catch (e) {
      alert('批次操作失敗');
    } finally {
      setBulkBusy(false);
    }
  };

  // Prune bulk-select set to intersect with the currently visible list
  // whenever filter/tab/search change. Hidden IDs must never leak into a
  // batch API call (audit finding C1).
  useEffect_p(() => {
    setChecked(prev => {
      if (prev.size === 0) return prev;
      const visible = new Set(filtered.map(a => a.id));
      let changed = false;
      const next = new Set();
      for (const id of prev) {
        if (visible.has(id)) next.add(id); else changed = true;
      }
      return changed ? next : prev;
    });
  }, [filtered]);

  // Reset per-selection state when the operator flips to a different alert,
  // so snooze menu / resolve draft from the previous alert don't leak into
  // the new one (audit findings A1, A3).
  useEffect_p(() => {
    setSnoozeOpen(false);
    setResolveNote('');
  }, [selectedId]);

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
              <input id="global-search" type="text" value={search} onChange={e => setSearch(e.target.value)}
                placeholder="搜尋..."
                className={`h-7 pl-7 ${search.length > 0 ? 'pr-7' : 'pr-2'} w-48 bg-surface-base border border-border-subtle rounded text-xs placeholder-ink-muted focus:border-sev-info focus:outline-none`}/>
              {search.length > 0 ? (
                <button
                  aria-label="清除搜尋"
                  onClick={() => setSearch('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-muted hover:text-ink-primary text-sm leading-none"
                >×</button>
              ) : (
                <span className="absolute right-2 top-1/2 -translate-y-1/2"><Kbd>/</Kbd></span>
              )}
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
          </div>
        </div>

        {/* Bulk bar */}
        {checked.size > 0 && (
          <div className="bg-sev-info/10 border-b border-sev-info/30 px-3 py-2 flex items-center gap-2 text-xs">
            <span className="text-sev-info font-medium tnum">已選 {checked.size}</span>
            <span className="text-ink-muted">|</span>
            <button onClick={handleBulkAck} disabled={bulkBusy}
              className="px-2 py-1 bg-surface-elevated border border-border-strong rounded hover:bg-surface-overlay disabled:opacity-50 disabled:cursor-not-allowed">批次認領</button>
            <button onClick={handleBulkResolve} disabled={bulkBusy || !bulkNote.trim()}
              title={!bulkNote.trim() ? '批次解決需備註' : ''}
              className="px-2 py-1 bg-surface-elevated border border-border-strong rounded hover:bg-surface-overlay disabled:opacity-50 disabled:cursor-not-allowed">批次解決</button>
            <input type="text" value={bulkNote} onChange={e => setBulkNote(e.target.value)}
              placeholder="批次備註 (解決時必填)..."
              className="flex-1 h-7 px-2 bg-surface-base border border-border-subtle rounded text-xs placeholder-ink-muted focus:border-sev-info focus:outline-none"/>
            <button onClick={() => { setChecked(new Set()); setBulkNote(''); }} className="text-ink-muted hover:text-ink-primary"><Icon.X size={14}/></button>
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
        {selected ? <AlertDetail key={selected.id} alert={selected} onAck={onAck} onResolve={onResolve} onSnooze={onSnooze} resolveNote={resolveNote} setResolveNote={setResolveNote} snoozeOpen={snoozeOpen} setSnoozeOpen={setSnoozeOpen} allAlerts={alerts} onSelectAlert={setSelectedId} busy={busy}/> : (
          <EmptyState icon={Icon.AlertCircle} title="選擇警報以查看詳情" hint="使用 ↑/↓ 鍵或滑鼠點選"/>
        )}
      </div>
    </div>
  );
};

// ---------- SnoozeMenu ----------
// Keyboard-accessible menu (WAI-ARIA menu pattern). ↑/↓ move focus among the
// duration items, Enter/Space activate, Escape closes and returns focus to
// the trigger, focus outside the menu also closes it.
const SNOOZE_DURATIONS = [30, 60, 120];

const SnoozeMenu = ({ alert, open, setOpen, onSnooze, busy }) => {
  const triggerRef = useRef_p(null);
  const menuRef = useRef_p(null);
  const itemRefs = useRef_p([]);
  const [activeIdx, setActiveIdx] = useState_p(0);

  useEffect_p(() => {
    if (!open) return;
    setActiveIdx(0);
    // Move focus into the menu on next paint so the ↑/↓/Escape handlers below
    // pick up the events instead of the underlying page shortcut layer.
    const t = setTimeout(() => { itemRefs.current[0]?.focus(); }, 0);
    const onDocClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)
          && triggerRef.current && !triggerRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => { clearTimeout(t); document.removeEventListener('mousedown', onDocClick); };
  }, [open, setOpen]);

  const onKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = (activeIdx + 1) % SNOOZE_DURATIONS.length;
      setActiveIdx(next);
      itemRefs.current[next]?.focus();
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      const prev = (activeIdx - 1 + SNOOZE_DURATIONS.length) % SNOOZE_DURATIONS.length;
      setActiveIdx(prev);
      itemRefs.current[prev]?.focus();
      return;
    }
    if (e.key === 'Home') {
      e.preventDefault(); setActiveIdx(0); itemRefs.current[0]?.focus(); return;
    }
    if (e.key === 'End') {
      e.preventDefault();
      const last = SNOOZE_DURATIONS.length - 1;
      setActiveIdx(last); itemRefs.current[last]?.focus(); return;
    }
  };

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        onClick={() => setOpen(o => !o)}
        disabled={busy}
        title="延期此節點 — 將不再發出告警音"
        aria-haspopup="menu"
        aria-expanded={open}
        className="h-9 px-3 bg-surface-elevated hover:bg-surface-overlay border border-border-strong rounded text-sm flex items-center gap-1.5 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <Icon.Clock size={14} aria-hidden="true"/> 延期節點 <Kbd aria-hidden="true">S</Kbd> <Icon.ChevronDown size={12} aria-hidden="true"/>
      </button>
      {open && (
        <div
          ref={menuRef}
          role="menu"
          aria-label={`延期 ${alert.node}`}
          onKeyDown={onKeyDown}
          className="absolute bottom-full mb-1 right-0 bg-surface-overlay border border-border-strong rounded shadow-xl py-1 min-w-[200px] z-10"
        >
          <div className="px-3 pt-1 pb-1.5 text-[10px] text-ink-muted">延期 {alert.node} · 期間將不發告警音</div>
          {SNOOZE_DURATIONS.map((m, i) => (
            <button
              key={m}
              ref={el => (itemRefs.current[i] = el)}
              role="menuitem"
              tabIndex={i === activeIdx ? 0 : -1}
              onClick={async () => {
                try {
                  await onSnooze(alert.id, m);
                  setOpen(false);
                  triggerRef.current?.focus();
                } catch (e) {
                  /* keep menu open so operator can retry — parent already toasts */
                }
              }}
              className="w-full px-3 py-1.5 text-left text-sm hover:bg-surface-panel focus:bg-surface-panel focus:outline-none focus:ring-1 focus:ring-sev-info flex items-center justify-between"
            >
              <span>{m} 分鐘</span><span className="font-mono text-xs text-ink-muted tnum" aria-hidden="true">{m}m</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

const AlertDetail = ({ alert, onAck, onResolve, onSnooze, resolveNote, setResolveNote, snoozeOpen, setSnoozeOpen, allAlerts, onSelectAlert, busy }) => {
  const node = window.NODES.find(n => n.id === alert.node);
  const m = window.safeSevMeta(alert.sev);
  const runbook = window.RUNBOOKS[alert.type];
  const [detailTab, setDetailTab] = useState_p('timeline');
  // Tracks whether the operator has typed into the note textarea since the
  // last template apply. Used so template chips do NOT clobber freeform
  // typing — see C-6 (template overwrite warning).
  const [noteEdited, setNoteEdited] = useState_p(false);
  // `busy` is a prop from app.jsx now (hoisted from a local useState). The
  // ref-backed guard lives in App so keyboard shortcuts A/R share it with
  // these buttons — a rapid A-A double-tap or a button-click-during-in-flight
  // keyboard-A both hit the same guard (follow-up to audit A4).
  const applyTemplate = (t) => {
    // If operator hasn't touched the textarea (or it's empty), replace.
    // If they've typed something, append on a new line so their work isn't lost.
    // Do NOT reset noteEdited — that flag tracks operator typing, not template
    // application, so a second template still appends after the first (A2).
    if (noteEdited && resolveNote.trim().length > 0) {
      setResolveNote(resolveNote.replace(/\s+$/, '') + '\n' + t);
    } else {
      setResolveNote(t);
    }
  };
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

      {/* Video / snapshot — honest placeholder. If a snapshot_url is available
          (edge pushed a frame), show it. Otherwise: static "no preview yet"
          card. The real HLS clip needs stream endpoint wiring — see TODO. */}
      {/* TODO(dashboard-audit-2026-07-15): real HLS preview needs stream endpoint wiring */}
      <div className="px-4 pt-3">
        <div className="relative aspect-video w-full rounded overflow-hidden border border-border-strong snapshot-placeholder">
          {alert.snapshot_url ? (
            <img src={alert.snapshot_url}
              alt={`${alert.node} snapshot`}
              className="absolute inset-0 w-full h-full object-cover"/>
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-ink-muted">
              <Icon.Camera size={36} strokeWidth={1.25}/>
              <div className="font-mono text-[11px] mt-2 tnum">尚無即時影像</div>
              <div className="font-mono text-[10px] mt-0.5 text-ink-dim">MP4 稍後上傳 · HLS 預覽尚未接線</div>
            </div>
          )}
          {/* Top overlay — node + capture time (read-only, not a fake REC light) */}
          <div className="absolute top-2 left-2 right-2 flex items-center justify-between">
            <Pill tone="muted" className="!bg-black/60 !text-white !border-black/0 font-mono">{alert.node}</Pill>
            {alert.timeline?.[0]?.t && (
              <Pill tone="muted" className="!bg-black/60 !text-white !border-black/0 font-mono">{alert.timeline[0].t}</Pill>
            )}
          </div>
        </div>
        {/* Mini map / floorplan */}
        <Floorplan highlightNode={alert.node}/>

        {/* Previous events at this node — carousel */}
        {history.length > 0 && (
          <div className="mt-2 bg-surface-panel border border-border-subtle rounded p-2">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">此節點近期事件 ({history.length})</span>
            </div>
            <div className="flex gap-1.5 overflow-x-auto scroll-thin pb-1">
              {history.map((h, i) => (
                <div key={i} className={`flex-shrink-0 w-24 bg-surface-elevated rounded border ${i === 0 ? 'border-sev-info/40' : 'border-border-subtle'} overflow-hidden hover:border-border-strong cursor-pointer transition-colors`}>
                  <div className={`relative aspect-video snapshot-placeholder`}>
                    {h.snapshot_url ? (
                      <img src={h.snapshot_url}
                        alt={`${h.t} snapshot`}
                        className="absolute inset-0 w-full h-full object-cover"/>
                    ) : (
                      <div className="absolute inset-0 flex items-center justify-center text-ink-muted/40">
                        <Icon.Camera size={20} strokeWidth={1}/>
                      </div>
                    )}
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

        {/* Runbook — suggested actions (read-only summary + step list).
            Action buttons removed 2026-07-16: they were disabled placeholders
            pending runbook completion-tracking design. Steps still render as
            informational cues so operators can follow them manually. */}
        {runbook && alert.state !== 'resolved' && (
          <div className="mt-2 bg-sev-info/5 border border-sev-info/30 rounded p-3">
            <div className="flex items-center gap-1.5 mb-2 text-[10px] uppercase tracking-wider text-sev-info font-semibold">
              <Icon.ClipboardList size={11}/> 建議下一步 · Runbook
            </div>
            <p className="text-xs text-ink-secondary leading-relaxed mb-2.5">{runbook.summary}</p>
            <ol className="space-y-1">
              {(runbook.actions || []).map((a, i) => (
                <li key={i}
                  className={`flex items-center gap-2 px-2.5 py-1.5 rounded border ${a.primary ? 'bg-sev-info/10 border-sev-info/30' : a.escalate ? 'bg-sev-critical/10 border-sev-critical/30' : 'bg-surface-elevated border-border-subtle'}`}>
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
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>

      {/* Timeline / node info / processing log — real tabs */}
      <div className="px-4 py-4 flex-1 overflow-y-auto scroll-thin">
        <div className="flex items-center gap-3 mb-3 text-xs border-b border-border-subtle pb-2">
          {[
            { id: 'timeline', label: '事件時間軸' },
            { id: 'node',     label: '節點資訊' },
            { id: 'log',      label: '處理紀錄' },
          ].map(t => (
            <button key={t.id} onClick={() => setDetailTab(t.id)}
              className={detailTab === t.id
                ? 'font-semibold text-ink-primary border-b-2 border-sev-info pb-1.5 -mb-2'
                : 'text-ink-muted hover:text-ink-secondary'}>
              {t.label}
            </button>
          ))}
        </div>
        {detailTab === 'timeline' && (
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
        )}
        {detailTab === 'node' && (
          <dl className="text-xs grid grid-cols-[6rem_1fr] gap-y-1.5 gap-x-3">
            <dt className="text-ink-muted">節點 ID</dt><dd className="font-mono tnum text-ink-primary">{alert.node}</dd>
            <dt className="text-ink-muted">類型</dt><dd className="text-ink-secondary">{node ? (node.type === 'camera' ? '攝影機' : '抽水站') : '—'}</dd>
            <dt className="text-ink-muted">名稱</dt><dd className="text-ink-secondary">{node?.name || '—'}</dd>
            <dt className="text-ink-muted">位置</dt><dd className="text-ink-secondary">{node?.location || '—'}</dd>
            <dt className="text-ink-muted">狀態</dt><dd className="text-ink-secondary">{node?.status || '—'}</dd>
            <dt className="text-ink-muted">心跳</dt><dd className="font-mono tnum text-ink-secondary">{node ? node.heartbeat + 's' : '—'}</dd>
            <dt className="text-ink-muted">上傳</dt><dd className="font-mono tnum text-ink-secondary">{node ? node.upload + 's' : '—'}</dd>
            {node?.type === 'camera' && (
              <>
                <dt className="text-ink-muted">溫度</dt><dd className="font-mono tnum text-ink-secondary">{node.temp != null ? node.temp + '°C' : '—'}</dd>
                <dt className="text-ink-muted">串流</dt><dd className="font-mono tnum text-ink-secondary">{node.bitrate?.toFixed?.(1) ?? '—'} Mbps · {node.drops ?? 0} drops</dd>
              </>
            )}
            {node?.type === 'pump' && (
              <>
                <dt className="text-ink-muted">水位</dt><dd className="font-mono tnum text-ink-secondary">{node.level != null ? node.level + '%' : '—'}</dd>
                <dt className="text-ink-muted">啟動頻率</dt><dd className="font-mono tnum text-ink-secondary">{node.cycles}×/hr</dd>
              </>
            )}
          </dl>
        )}
        {detailTab === 'log' && (
          <div className="text-xs space-y-2">
            {alert.state === 'pending' && <div className="text-ink-muted">尚未處理。</div>}
            {alert.ackAt && (
              <div className="bg-surface-panel border border-border-subtle rounded p-2">
                <div className="flex items-baseline gap-2">
                  <span className="font-mono tnum text-ink-muted">{alert.ackAt}</span>
                  <span className="text-sev-info font-semibold">認領</span>
                  <span className="text-ink-secondary">by <span className="font-mono">{alert.ackBy || '—'}</span></span>
                </div>
              </div>
            )}
            {alert.resAt && (
              <div className="bg-surface-panel border border-border-subtle rounded p-2">
                <div className="flex items-baseline gap-2">
                  <span className="font-mono tnum text-ink-muted">{alert.resAt}</span>
                  <span className="text-sev-ok font-semibold">解決</span>
                  <span className="text-ink-secondary">by <span className="font-mono">{alert.resBy || '—'}</span></span>
                </div>
                {alert.note && (
                  <div className="mt-1 text-ink-secondary leading-relaxed">{alert.note}</div>
                )}
              </div>
            )}
          </div>
        )}
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
                <button key={t} onClick={() => applyTemplate(t)}
                  title={noteEdited && resolveNote.trim().length > 0 ? '將附加到現有備註後 (換行)' : ''}
                  className={`inline-flex items-center gap-1 text-[11px] px-2 h-6 rounded border transition-colors ${resolveNote === t ? 'bg-sev-info/20 border-sev-info text-sev-info' : 'bg-surface-elevated border-border-subtle text-ink-secondary hover:border-border-strong'}`}>
                  <span className="kbd !h-3.5 !min-w-[14px] !text-[9px] !px-0.5">{i+1}</span>
                  {t}
                </button>
              ))}
            </div>
          </div>
        )}

        {alert.state !== 'resolved' && (
          <textarea value={resolveNote}
            onChange={e => { setResolveNote(e.target.value); setNoteEdited(true); }}
            placeholder={alert.state === 'acknowledged' ? '處置備註 (解決時必填) — 可套用上方模板' : '備註 (選填)...'}
            rows="2"
            aria-label={alert.state === 'acknowledged' ? '處置備註（解決時必填）' : '處置備註（選填）'}
            className="w-full px-2 py-1.5 bg-surface-base border border-border-subtle rounded text-xs placeholder-ink-muted resize-none focus:border-sev-info focus:outline-none"/>
        )}
        <div className="flex items-center gap-2">
          {alert.state === 'pending' && (
            <>
              <button onClick={async () => {
                  if (busy) return;
                  try { await onAck(alert.id); }
                  catch (e) { /* parent toasts; keep UI state so operator can retry */ }
                }}
                disabled={busy}
                title="我正在處理此事件 — 不會關閉警報。按下後自動跳至下一筆。"
                className="flex-1 h-9 bg-sev-info hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded font-semibold text-sm flex items-center justify-center gap-2 transition-colors">
                <Icon.Check size={16} strokeWidth={2.5}/>
                <span>認領</span>
                <span className="text-[10px] opacity-80 font-normal">→ 下一筆</span>
                <Kbd>A</Kbd>
              </button>
            </>
          )}
          {alert.state === 'acknowledged' && (
            <>
              <button onClick={async () => {
                  if (busy) return;
                  try {
                    await onResolve(alert.id, resolveNote);
                    setResolveNote('');
                    setNoteEdited(false);
                  } catch (e) {
                    /* keep the drafted note so the operator doesn't lose their write-up */
                  }
                }}
                disabled={busy || !resolveNote.trim()}
                title="事件已結束 — 將從作用中列表移除"
                className="flex-1 h-9 bg-sev-ok hover:bg-emerald-600 disabled:bg-sev-ok/30 disabled:cursor-not-allowed text-white rounded font-semibold text-sm flex items-center justify-center gap-2 transition-colors">
                <Icon.CheckCircle size={16} strokeWidth={2.5}/>
                <span>解決</span>
                {!resolveNote.trim() && <span className="text-[10px] opacity-80 font-normal">(需備註)</span>}
                <Kbd>R</Kbd>
              </button>
            </>
          )}
          {alert.state === 'resolved' && (
            <div className="flex-1 h-9 bg-sev-ok/10 border border-sev-ok/30 text-sev-ok rounded font-medium text-sm flex items-center justify-center gap-2">
              <Icon.CheckCircle size={14}/> 已解決於 {alert.resAt} by {alert.resBy}
            </div>
          )}
          {alert.state !== 'resolved' && (
            <SnoozeMenu
              alert={alert}
              open={snoozeOpen}
              setOpen={setSnoozeOpen}
              onSnooze={onSnooze}
              busy={busy}
            />
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

Object.assign(window, { AlertsPage });
