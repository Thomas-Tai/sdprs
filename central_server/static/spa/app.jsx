// SDPRS — App shell + state management

const { useState: useStateA, useEffect: useEffectA, useMemo: useMemoA, useCallback: useCallbackA, useRef: useRefA } = React;

const DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "dark",
  "density": "compact",
  "muted": false,
  "wallMode": false,
  "accent": "blue"
}/*EDITMODE-END*/;

function App() {
  const [tweaks, setTweak] = window.useTweaks(DEFAULTS);

  const [page, setPageRaw] = useStateA('alerts');
  const [pageHistory, setPageHistory] = useStateA([]); // for Alt+← back
  const [alerts, setAlerts] = useStateA(window.ALERTS);
  const [nodes, setNodes] = useStateA(window.NODES);
  const [selectedId, setSelectedId] = useStateA(window.ALERTS[0] ? window.ALERTS[0].id : null);
  const [liveSec, setLiveSec] = useStateA(0);
  const [shortcutsOpen, setShortcutsOpen] = useStateA(false);
  const [muteDrawerOpen, setMuteDrawerOpen] = useStateA(false);
  const [cmdkOpen, setCmdkOpen] = useStateA(false);
  const [shiftBannerOpen, setShiftBannerOpen] = useStateA(false);
  const [nodePanelNode, setNodePanelNode] = useStateA(null);
  const [focusMode, setFocusMode] = useStateA(false);
  const [newAlertBannerCount, setNewAlertBannerCount] = useStateA(0);
  const [muteState, setMuteState] = useStateA({
    global: false,
    nodes: [],
    lightning: false,
    volume: 70,
  });
  const [ackedIds, setAckedIds] = useStateA(new Set());
  const [toast, setToast] = useStateA(null);
  const [audioReplayIn, setAudioReplayIn] = useStateA(30);

  // Wrap setPage to track history
  const setPage = useCallbackA((p) => {
    setPageRaw(prev => {
      if (prev !== p) setPageHistory(h => [...h.slice(-9), prev]);
      return p;
    });
  }, []);

  const goBack = useCallbackA(() => {
    setPageHistory(h => {
      if (h.length === 0) return h;
      const prev = h[h.length - 1];
      setPageRaw(prev);
      return h.slice(0, -1);
    });
  }, []);

  // Apply theme/wall/focus classes
  useEffectA(() => {
    document.documentElement.classList.toggle('dark', tweaks.theme === 'dark');
    document.documentElement.classList.toggle('light', tweaks.theme === 'light');
    document.documentElement.classList.toggle('wall-mode', !!tweaks.wallMode);
    document.documentElement.classList.toggle('focus-mode', !!focusMode);
  }, [tweaks.theme, tweaks.wallMode, focusMode]);

  // liveSec = seconds since the last server contact; refresh() and WebSocket
  // pings reset it to 0. StatusStrip turns it into Live/Reconnecting/Disconnected.
  useEffectA(() => {
    const id = setInterval(() => setLiveSec(s => s + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const unackCount = useMemoA(() => alerts.filter(a => a.state === 'pending').length, [alerts]);
  const offlineCount = window.NODES.filter(n => n.status === 'offline').length;
  const staleAckCount = useMemoA(() => alerts.filter(a => a.state === 'acknowledged' && a.ackAgeSec > window.STALE_ACK_THRESHOLD).length, [alerts]);

  useEffectA(() => {
    document.title = unackCount > 0
      ? `(${unackCount}) SDPRS · 防災監控`
      : 'SDPRS · 防災監控';
  }, [unackCount]);

  useEffectA(() => {
    if (unackCount === 0 || muteState.global || tweaks.muted) {
      setAudioReplayIn(30);
      return;
    }
    const id = setInterval(() => {
      setAudioReplayIn(s => s <= 1 ? 30 : s - 1);
    }, 1000);
    return () => clearInterval(id);
  }, [unackCount, muteState.global, tweaks.muted]);

  const showToast = useCallbackA((message, tone = 'info') => {
    setToast({ message, tone, id: Date.now() });
    setTimeout(() => setToast(null), 3000);
  }, []);

  // Pull fresh alerts/nodes from the server and push them into React state.
  const refresh = useCallbackA(async () => {
    try {
      const r = await window.SDPRS_API.refreshLive();
      setAlerts(r.alerts);
      setNodes(r.nodes);
      setLiveSec(0);
    } catch (e) {
      console.warn('[SDPRS] refresh failed', e);
    }
  }, []);

  // Live updates: refresh on every relevant WebSocket event, with a slow poll
  // as a safety net (it also keeps alert ages current between events).
  useEffectA(() => {
    const stop = window.SDPRS_API.openSocket((msg) => {
      if (!msg || !msg.type) return;
      if (msg.type === 'ping') { setLiveSec(0); return; }
      if (msg.type === 'new_alert') setNewAlertBannerCount(c => c + 1);
      refresh();
    });
    const poll = setInterval(refresh, 20000);
    return () => { stop(); clearInterval(poll); };
  }, [refresh]);

  const findNextUnack = useCallbackA((currentId) => {
    const list = alerts.filter(a => a.state === 'pending' && a.id !== currentId);
    if (list.length === 0) return null;
    // Severity-first, then RECENCY (newest first) — critical+new always wins
    const sorted = list.sort((a, b) => {
      const rank = { critical: 0, warn: 1, info: 2 };
      if (rank[a.sev] !== rank[b.sev]) return rank[a.sev] - rank[b.sev];
      return a.ageSec - b.ageSec; // smaller ageSec = newer
    });
    return sorted[0].id;
  }, [alerts]);

  const markSeen = useCallbackA((id) => {
    window.SDPRS_API.markSeen(id);
    setAlerts(prev => prev.map(a => a.id === id ? { ...a, seen: true } : a));
  }, []);

  const onAck = useCallbackA(async (id, advance = true) => {
    try {
      await window.SDPRS_API.ackAlert(id);
    } catch (e) {
      showToast('認領失敗: ' + (e.message || e), 'warn');
      return;
    }
    setAckedIds(prev => new Set(prev).add(id));
    showToast('已認領' + (advance ? ' → 下一筆' : ''), 'info');
    const next = advance ? findNextUnack(id) : null;
    await refresh();
    if (next) setSelectedId(next);
  }, [showToast, findNextUnack, refresh]);

  const onResolve = useCallbackA(async (id, note) => {
    if (!note) {
      showToast('需備註才能解決', 'warn');
      return;
    }
    try {
      await window.SDPRS_API.resolveAlert(id, note);
    } catch (e) {
      showToast('解決失敗: ' + (e.message || e), 'warn');
      return;
    }
    showToast('警報已解決', 'ok');
    const next = findNextUnack(id);
    await refresh();
    if (next) setSelectedId(next);
  }, [showToast, findNextUnack, refresh]);

  const onSnooze = useCallbackA(async (id, mins) => {
    const a = alerts.find(x => x.id === id);
    if (!a) return;
    try {
      await window.SDPRS_API.snoozeNode(a.node, mins);
    } catch (e) {
      showToast('延期失敗: ' + (e.message || e), 'warn');
      return;
    }
    setMuteState(prev => ({ ...prev, nodes: prev.nodes.includes(a.node) ? prev.nodes : [...prev.nodes, a.node] }));
    showToast(`${a.node} 已延期 ${mins} 分鐘`, 'warn');
    await refresh();
  }, [showToast, alerts, refresh]);

  useEffectA(() => {
    if (selectedId) markSeen(selectedId);
  }, [selectedId, markSeen]);

  const [resolveNote, setResolveNote] = useStateA('');
  useEffectA(() => { setResolveNote(''); }, [selectedId]);

  // Command palette command dispatch
  const onCmdkCommand = useCallbackA((id) => {
    if (id === 'mute-all') setMuteDrawerOpen(true);
    else if (id === 'focus-mode') setFocusMode(f => !f);
    else if (id === 'density') setTweak('density', tweaks.density === 'compact' ? 'comfortable' : 'compact');
    else if (id === 'shortcuts') setShortcutsOpen(true);
    else if (id === 'audit-me') setPage('audit');
  }, [tweaks.density, setTweak, setPage]);

  useEffectA(() => {
    const handler = (e) => {
      const tag = e.target.tagName;
      const inField = tag === 'INPUT' || tag === 'TEXTAREA';

      // Cmd+K / Ctrl+K (always)
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setCmdkOpen(true);
        return;
      }
      // Ctrl+. focus mode
      if ((e.ctrlKey || e.metaKey) && e.key === '.') {
        e.preventDefault();
        setFocusMode(f => !f);
        showToast(focusMode ? '已關閉專注模式' : '已啟用專注模式 — 隱藏資訊級警報', 'info');
        return;
      }
      // Alt+← back navigation
      if (e.altKey && e.key === 'ArrowLeft') {
        e.preventDefault();
        goBack();
        return;
      }

      if (e.key === 'Escape') {
        setShortcutsOpen(false);
        setMuteDrawerOpen(false);
        setCmdkOpen(false);
        setNodePanelNode(null);
        setShiftBannerOpen(false);
        return;
      }
      if (inField) return;

      if (e.key === '?') { e.preventDefault(); setShortcutsOpen(true); return; }
      if (e.key === '/') { e.preventDefault(); document.querySelector('input[type="text"][placeholder*="搜尋"]')?.focus(); return; }
      if (e.key === 'm' || e.key === 'M') { setMuteDrawerOpen(true); return; }
      if (e.key === 't' || e.key === 'T') { setTweak('theme', tweaks.theme === 'dark' ? 'light' : 'dark'); return; }
      if (e.shiftKey && (e.key === 'D' || e.key === 'd')) { setTweak('density', tweaks.density === 'compact' ? 'comfortable' : 'compact'); return; }

      const navMap = { '1': 'alerts', '2': 'monitor', '3': 'status', '4': 'pumps', '5': 'weather', '6': 'handover', '7': 'audit' };
      const sel = alerts.find(a => a.id === selectedId);
      if (navMap[e.key] && !(page === 'alerts' && sel && sel.state === 'acknowledged' && /^[1-6]$/.test(e.key))) {
        setPage(navMap[e.key]);
        return;
      }
      if (page === 'alerts' && sel && sel.state === 'acknowledged' && /^[1-6]$/.test(e.key)) {
        const idx = parseInt(e.key, 10) - 1;
        if (window.RESOLVE_TEMPLATES[idx]) {
          setResolveNote(window.RESOLVE_TEMPLATES[idx]);
          showToast(`已套用模板: ${window.RESOLVE_TEMPLATES[idx]}`, 'info');
        }
        return;
      }

      if (page !== 'alerts' || !selectedId || !sel) return;

      if (e.key === 'n' || e.key === 'N') {
        e.preventDefault();
        const next = findNextUnack(selectedId);
        if (next) setSelectedId(next);
        else showToast('沒有更多未認領警報', 'ok');
        return;
      }

      if (e.key === 'a' || e.key === 'A') {
        if (sel.state === 'pending') onAck(sel.id, !e.shiftKey);
        return;
      }
      if (e.key === 'r' || e.key === 'R') {
        if (sel.state === 'acknowledged') onResolve(sel.id, resolveNote);
        return;
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const list = alerts.filter(a => a.state !== 'resolved');
        const idx = list.findIndex(a => a.id === selectedId);
        const nextIdx = e.key === 'ArrowDown' ? Math.min(list.length - 1, idx + 1) : Math.max(0, idx - 1);
        setSelectedId(list[nextIdx].id);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [page, selectedId, alerts, tweaks.theme, tweaks.density, setTweak, setPage, goBack, onAck, onResolve, findNextUnack, resolveNote, showToast, focusMode]);

  useEffectA(() => {
    setTweak('muted', muteState.global);
  }, [muteState.global, setTweak]);

  const onUpdateNode = useCallbackA(async (id, patch) => {
    try {
      if (patch.location) await window.SDPRS_API.updateNodeLocation(id, patch.location);
    } catch (e) {
      showToast('更新失敗: ' + (e.message || e), 'warn');
      return;
    }
    // Reflect immediately, then refresh from the server for canonical values.
    setNodePanelNode(prev => prev && prev.id === id ? { ...prev, ...patch } : prev);
    showToast(`${id} 配置已更新`, 'ok');
    await refresh();
  }, [showToast, refresh]);

  const onSelectNode = useCallbackA((n) => setNodePanelNode(n), []);
  const onJumpAlert = useCallbackA((id) => {
    setNodePanelNode(null);
    setPage('alerts');
    setSelectedId(id);
  }, [setPage]);

  const renderPage = () => {
    switch (page) {
      case 'alerts': return <window.AlertsPage density={tweaks.density} selectedId={selectedId} setSelectedId={setSelectedId} alerts={alerts} onAck={onAck} onResolve={onResolve} onSnooze={onSnooze} ackedIds={ackedIds} resolveNote={resolveNote} setResolveNote={setResolveNote}/>;
      case 'monitor': return <window.MonitorPage activeAlerts={alerts.filter(a => a.state !== 'resolved')} onSelectNode={onSelectNode}/>;
      case 'status': return <window.StatusPage onSelectNode={onSelectNode}/>;
      case 'pumps': return <window.PumpsPage/>;
      case 'weather': return <window.WeatherPage/>;
      case 'handover': return <window.HandoverPage/>;
      case 'audit': return <window.AuditPage/>;
      default: return null;
    }
  };

  if (tweaks.wallMode) {
    return <WallView alerts={alerts} liveSec={liveSec} unackCount={unackCount}/>;
  }

  return (
    <div className="h-screen w-screen overflow-hidden text-ink-primary">
      <window.StatusStrip
        liveSec={liveSec}
        unackCount={unackCount}
        muted={muteState.global}
        setMuted={(v) => setMuteState({...muteState, global: v})}
        theme={tweaks.theme}
        setTheme={(v) => setTweak('theme', v)}
        onOpenShortcuts={() => setShortcutsOpen(true)}
        page={page}
        setPage={setPage}
        onOpenMuteDrawer={() => setMuteDrawerOpen(true)}
        audioReplayIn={audioReplayIn}
        muteState={muteState}
        operators={window.OPERATORS_ONLINE}
        staleAckCount={staleAckCount}
        onOpenCmdK={() => setCmdkOpen(true)}
        focusMode={focusMode}
        onToggleFocus={() => setFocusMode(f => !f)}
      />
      <window.NavRail
        page={page} setPage={setPage}
        density={tweaks.density} setDensity={(v) => setTweak('density', v)}
        unackCount={unackCount}
        offlineCount={offlineCount}
      />
      <main className="ml-56 mt-12 mb-10 h-[calc(100vh-88px)] overflow-hidden">
        {renderPage()}
      </main>
      <window.Footer data={window.ALERT_RATE} handover={window.HANDOVER.pinned}/>

      {/* Floating new-alert banner */}
      {page === 'alerts' && newAlertBannerCount > 0 && (
        <window.NewAlertBanner count={newAlertBannerCount} onClick={() => {
          setNewAlertBannerCount(0);
          const firstUnseen = alerts.find(a => !a.seen);
          if (firstUnseen) setSelectedId(firstUnseen.id);
        }}/>
      )}

      {/* Shift onboarding banner */}
      {shiftBannerOpen && <window.ShiftBanner shiftSummary={window.SHIFT_SUMMARY} onDismiss={() => setShiftBannerOpen(false)}/>}

      <window.ShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)}/>
      <window.MuteDrawer open={muteDrawerOpen} onClose={() => setMuteDrawerOpen(false)} muteState={muteState} setMuteState={setMuteState}/>
      <window.CommandPalette open={cmdkOpen} onClose={() => setCmdkOpen(false)} alerts={alerts} onSelectAlert={setSelectedId} onNav={setPage} onCmd={onCmdkCommand}/>
      <window.NodeSidePanel node={nodePanelNode} onClose={() => setNodePanelNode(null)} onJumpAlert={onJumpAlert} openAlerts={alerts.filter(a => a.state !== 'resolved')} onUpdateNode={onUpdateNode}/>

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-14 right-4 z-50 animate-in">
          <div className={`bg-surface-overlay border rounded-lg px-3 py-2 shadow-2xl flex items-center gap-2 text-sm ${
            toast.tone === 'ok' ? 'border-sev-ok/50' : toast.tone === 'warn' ? 'border-sev-warn/50' : 'border-sev-info/50'
          }`}>
            {toast.tone === 'ok' ? <Icon.CheckCircle size={16} className="text-sev-ok"/> : toast.tone === 'warn' ? <Icon.AlertCircle size={16} className="text-sev-warn"/> : <Icon.Info size={16} className="text-sev-info"/>}
            <span>{toast.message}</span>
          </div>
        </div>
      )}

      {/* Tweaks panel */}
      <window.TweaksPanel>
        <window.TweakSection label="顯示" />
        <window.TweakRadio label="主題" value={tweaks.theme} onChange={(v) => setTweak('theme', v)} options={[{ value: 'dark', label: '深色' }, { value: 'light', label: '淺色' }]}/>
        <window.TweakRadio label="密度" value={tweaks.density} onChange={(v) => setTweak('density', v)} options={[{ value: 'compact', label: '緊湊' }, { value: 'comfortable', label: '舒適' }]}/>
        <window.TweakToggle label="專注 / 夜深模式" value={focusMode} onChange={setFocusMode}/>
        <window.TweakSection label="檢視模式" />
        <window.TweakToggle label="4K 牆面模式" value={tweaks.wallMode} onChange={(v) => setTweak('wallMode', v)}/>
        <window.TweakSection label="跳轉頁面" />
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:4}}>
          {window.NAV_ITEMS.map(item => (
            <button key={item.id} onClick={() => setPage(item.id)}
              style={{
                fontSize:11,padding:'5px 4px',borderRadius:6,
                border:'1px solid '+(page===item.id?'rgba(59,130,246,.5)':'rgba(0,0,0,.12)'),
                background:page===item.id?'rgba(59,130,246,.15)':'rgba(0,0,0,.04)',
                color:page===item.id?'#1e40af':'#29261b',
                cursor:'pointer',
              }}>
              {item.label}
            </button>
          ))}
        </div>
      </window.TweaksPanel>
    </div>
  );
}

// =================================================================
// WALL VIEW — 4K NOC display
// =================================================================

function WallView({ alerts, liveSec, unackCount }) {
  const liveState = liveSec < 10 ? 'ok' : liveSec < 30 ? 'warn' : 'critical';
  const sorted = [...window.NODES].sort((a, b) => {
    const rank = { offline: 0, critical: 1, warn: 2, online: 3 };
    return rank[a.status] - rank[b.status];
  });
  return (
    <div className="h-screen w-screen overflow-hidden bg-black text-ink-primary flex flex-col">
      {/* Top status strip — bigger */}
      <div className="h-16 bg-surface-panel border-b-2 border-border-strong flex items-center px-6 gap-4 flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className={`w-10 h-10 rounded ${unackCount > 0 ? 'bg-sev-critical/15 text-sev-critical' : 'bg-sev-ok/15 text-sev-ok'} flex items-center justify-center`}>
            <Icon.ShieldAlert size={22} strokeWidth={2}/>
          </div>
          <div>
            <div className="text-xl font-bold tracking-wider">SDPRS</div>
            <div className="text-[10px] font-mono text-ink-muted -mt-0.5">NOC WALL · v2.4</div>
          </div>
        </div>
        <div className="w-px h-10 bg-border-subtle"></div>
        <window.Pill tone={liveState} dot pulse={liveState==='ok'} className="!h-8 !text-sm !px-3">{`Live · ${liveSec}s`}</window.Pill>
        {unackCount > 0 && (
          <div className="h-8 px-3 rounded bg-sev-critical text-white text-sm font-bold inline-flex items-center gap-2 tnum animate-live-blink">
            <Icon.Bell size={16}/> 未認領 {unackCount}
          </div>
        )}
        <div className="flex-1"></div>
        <div className="flex items-center gap-4 text-base">
          {window.WEATHER.typhoon && (
            <>
              <span className="flex items-center gap-2 text-sev-warn font-bold"><Icon.Typhoon size={20}/> 颱風 {window.WEATHER.typhoon.name} · {window.WEATHER.typhoon.level}</span>
              <span className="text-ink-dim">|</span>
            </>
          )}
          <span className="font-mono tnum">{window.WEATHER.wind.dir} {window.WEATHER.wind.speed} km/h</span>
          <span className="text-ink-dim">|</span>
          <span className="font-mono tnum text-sev-info">{window.WEATHER.rain.now} mm/h</span>
        </div>
        <div className="w-px h-10 bg-border-subtle"></div>
        <div className="font-mono tnum text-base text-ink-secondary">{new Date().toLocaleTimeString('zh-TW', { hour12: false })}</div>
      </div>

      {/* Body: 3-column wall layout */}
      <div className="flex-1 grid grid-cols-[2fr_1fr_1fr] gap-3 p-3 min-h-0">
        {/* Monitor wall */}
        <div className="grid grid-cols-3 gap-3 min-h-0 overflow-hidden">
          {sorted.slice(0, 9).map(n => (
            <div key={n.id} className="bg-surface-panel rounded border border-border-subtle overflow-hidden relative">
              <div className={`relative h-full snapshot-placeholder ${n.status === 'offline' ? 'snapshot-frozen' : ''}`}>
                <div className={`absolute top-2 left-2 w-4 h-4 rounded-full bg-sev-${n.status === 'offline' || n.status === 'critical' ? 'critical' : n.status === 'warn' ? 'warn' : 'ok'} ring-2 ring-black/50 ${n.status === 'offline' || n.status === 'critical' ? 'animate-live-blink' : ''}`}></div>
                <div className="absolute inset-0 flex items-center justify-center text-ink-muted/30">
                  {n.type === 'camera' ? <Icon.Camera size={64} strokeWidth={1}/> : <Icon.Droplet size={64} strokeWidth={1}/>}
                </div>
                {n.status === 'offline' && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/40">
                    <div className="bg-sev-critical text-white text-base font-bold px-3 py-1 rounded">離線 {Math.floor(n.heartbeat/60)}m</div>
                  </div>
                )}
                <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/80 to-transparent p-2">
                  <div className="font-mono text-base font-bold text-white tnum">{n.id}</div>
                  <div className="text-xs text-white/80">{n.name}</div>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Center: live alert ticker */}
        <div className="bg-surface-panel border border-border-subtle rounded flex flex-col min-h-0">
          <div className="px-4 py-2.5 border-b border-border-subtle flex items-center justify-between flex-shrink-0">
            <h2 className="text-base font-bold uppercase tracking-wider">即時警報</h2>
            <span className="text-xs font-mono text-ink-muted tnum">{alerts.length} 筆</span>
          </div>
          <div className="flex-1 overflow-y-auto scroll-thin">
            {alerts.slice(0, 12).map(a => {
              const m = window.sevMeta[a.sev];
              return (
                <div key={a.id} className={`px-3 py-2 border-b border-border-subtle ${m.bar} ${a.state === 'pending' && a.sev === 'critical' ? 'bg-sev-critical/5' : ''}`}>
                  <div className="flex items-center gap-2">
                    <window.SeverityBadge sev={a.sev}/>
                    <span className="font-mono text-sm font-bold tnum">{a.node}</span>
                    <div className="flex-1"></div>
                    <span className={`text-xs font-mono tnum ${window.ageColor(a.ageSec)}`}>{window.fmtAge(a.ageSec)}</span>
                  </div>
                  <div className="text-sm text-ink-secondary mt-1 truncate">{a.message}</div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right: weather + system health */}
        <div className="flex flex-col gap-3 min-h-0">
          <div className="bg-surface-panel border border-sev-warn/30 rounded p-4 flex-1">
            <div className="text-xs uppercase tracking-wider text-sev-warn font-bold flex items-center gap-2"><Icon.Wind size={14}/> 風速</div>
            <div className="mt-2 flex items-baseline gap-1">
              <span className="text-7xl font-mono font-black tnum text-sev-warn">{window.WEATHER.wind.speed}</span>
              <span className="text-ink-muted text-xl">km/h</span>
            </div>
            <div className="text-sm text-ink-muted font-mono tnum mt-1">{window.WEATHER.wind.dir || '—'} {window.WEATHER.wind.degree}°</div>
          </div>
          <div className="bg-surface-panel border border-sev-info/30 rounded p-4 flex-1">
            <div className="text-xs uppercase tracking-wider text-sev-info font-bold flex items-center gap-2"><Icon.CloudRain size={14}/> 雨量</div>
            <div className="mt-2 flex items-baseline gap-1">
              <span className="text-7xl font-mono font-black tnum text-sev-info">{window.WEATHER.rain.now}</span>
              <span className="text-ink-muted text-xl">mm/h</span>
            </div>
            <div className="text-sm text-ink-muted font-mono tnum mt-1">日累計 {window.WEATHER.rain.day}mm</div>
          </div>
          <div className="bg-surface-panel border border-border-subtle rounded p-3">
            <div className="text-xs uppercase tracking-wider text-ink-muted font-bold mb-2">系統健康</div>
            <div className="grid grid-cols-2 gap-2 text-xs font-mono tnum">
              <div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-sev-ok"></span><span className="text-ink-secondary">線上</span><span className="ml-auto font-bold">{window.NODES.filter(n=>n.status==='online').length}</span></div>
              <div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-sev-warn"></span><span className="text-ink-secondary">警告</span><span className="ml-auto font-bold">{window.NODES.filter(n=>n.status==='warn').length}</span></div>
              <div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-sev-critical"></span><span className="text-ink-secondary">嚴重</span><span className="ml-auto font-bold">{window.NODES.filter(n=>n.status==='critical').length}</span></div>
              <div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-sev-critical animate-live-blink"></span><span className="text-ink-secondary">離線</span><span className="ml-auto font-bold">{window.NODES.filter(n=>n.status==='offline').length}</span></div>
            </div>
          </div>
        </div>
      </div>

      {/* Footer ticker */}
      <div className="h-8 bg-surface-panel border-t border-border-strong flex items-center px-4 gap-4 text-xs flex-shrink-0">
        <Icon.Activity size={12} className="text-ink-muted"/>
        <window.Sparkline data={window.ALERT_RATE} width={180} height={20}/>
        <span className="text-ink-muted font-mono tnum">警報率 · 15min × 16</span>
        <div className="flex-1"></div>
        <span className="text-ink-muted">上一班: <span className="text-ink-secondary font-mono tnum">{window.HANDOVER.pinned.by} @ {window.HANDOVER.pinned.at}</span> "<span className="text-ink-secondary">{window.HANDOVER.pinned.text}</span>"</span>
      </div>
    </div>
  );
}

// Load the first batch of live data, then mount. The loading spinner in
// index.html stays visible until render() replaces #root.
(async function bootstrap() {
  try {
    await window.SDPRS_API.loadInitial();
  } catch (e) {
    console.error('[SDPRS] initial data load failed:', e);
  }
  ReactDOM.createRoot(document.getElementById('root')).render(<App/>);
})();
