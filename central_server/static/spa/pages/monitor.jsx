// SDPRS — Monitor Wall Page

const { useState: useState_p, useMemo: useMemo_p } = React;

// ---------- ClockDisplay — isolated 1Hz leaf ----------
// Extracted from MonitorPage so the 1-second tick only re-renders this tiny
// component, not every NodeCard in the grid. Each NodeCard renders its own
// ClockDisplay instance; the setInterval is per-instance but lightweight
// (one DOM text node update per second).
const ClockDisplay = React.memo(() => {
  const [now, setNow] = useState_p(() => Date.now());
  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span>{new Date(now).toLocaleTimeString('zh-TW', { hour12: false })}</span>
  );
});

// MSP-F19/F8: heartbeat/upload ages are now `null` for a never-reported node
// (the old 999 sentinel is gone). window.fmtAge (data.jsx) does `sec || 0`,
// which would silently print "0s" for null — a fabricated reading, exactly
// the bug this replaces. Guard first, then reuse the app-wide humanizer
// (data.jsx fmtAge) so "86400s" reads as "1d 0h" like every other age in
// the dashboard, instead of a raw, unreadable seconds count.
function fmtAgeOrDash(sec) {
  return sec == null ? '—' : window.fmtAge(sec);
}

// MSP-F10: status-rank used both to freely re-sort (pointer not over the
// grid) and to seed the "newcomer" order for nodes not yet seen while frozen.
const STATUS_RANK = { offline: 0, critical: 1, warn: 2, online: 3 };
const _rankOf = (status) => STATUS_RANK[status] ?? 99;

// Keeps the grid's visual order stable while the operator's pointer is over
// it, so a background refresh (~20s poll / WS event) never reshuffles cards
// out from under an in-flight click — status-sorted grids used to "teleport"
// click targets on every tick (MSP-F10). While NOT frozen, re-sorts freely by
// status rank so the wall always leads with what needs attention. While
// frozen, the existing slot order is preserved — even if a card's own rank
// changes — and only genuinely new node ids are appended (in rank order) at
// the end.
function useStableSort(list, frozen) {
  const orderRef = React.useRef([]);
  const byId = new Map(list.map(n => [n.id, n]));
  if (!frozen) {
    const fresh = [...list].sort((a, b) => _rankOf(a.status) - _rankOf(b.status));
    orderRef.current = fresh.map(n => n.id);
    return fresh;
  }
  const known = orderRef.current.filter(id => byId.has(id));
  const knownSet = new Set(known);
  const newcomers = [...list]
    .filter(n => !knownSet.has(n.id))
    .sort((a, b) => _rankOf(a.status) - _rankOf(b.status));
  const order = known.concat(newcomers.map(n => n.id));
  orderRef.current = order;
  return order.map(id => byId.get(id)).filter(Boolean);
}

// Live-view warm-up tuning. The player is only mounted once the HLS playlist
// actually references a media segment, so these bound the POLL, not a guess at
// how long ffmpeg takes.
const LIVE_POLL_MS = 1500;      // gap between playlist probes while 'loading'
const LIVE_POLL_TIMEOUT_MS = 30000; // give up and return to 'off' after this
// A playlist that exists but lists no segment yet is just the header
// (#EXTM3U/#EXT-X-TARGETDURATION…) — mounting <video> against it is exactly the
// black-tile case. "Ready" means at least one media segment URI is listed.
function playlistHasSegment(text) {
  if (!text || typeof text !== 'string') return false;
  return text.split(/\r?\n/).some(line => {
    const l = line.trim();
    return l !== '' && l.charAt(0) !== '#' && /\.ts(\?.*)?$/i.test(l);
  });
}

const NodeCard = React.memo(({ node, onSelect, nodeAlerts = [] }) => {
  const stateTone = node.status === 'offline' ? 'critical' : node.status === 'critical' ? 'critical' : node.status === 'warn' ? 'warn' : 'ok';
  // MSP-F9 contract: `upload` is null for a camera with no snapshot ever —
  // `null > 60` is false, so this correctly does NOT freeze on "no data yet"
  // and instead only freezes once we know the age (offline always freezes).
  const frozen = node.status === 'offline' || node.upload > 60;
  const hasCritical = nodeAlerts.some(a => a.sev === 'critical');
  const isWebcam = node.type === 'webcam';
  // Live-view state for webcam tiles: off → loading (stream spinning up) → live
  // (HlsPlayer mounted). Non-webcam tiles never leave 'off'.
  const [liveMode, setLiveMode] = useState_p('off'); // 'off' | 'loading' | 'live'
  // Live-view timers are lifecycle-managed off liveMode so BOTH the warm-up
  // transition and the lease renewal auto-clean on unmount/stop (state is the
  // single source of truth — no dangling setTimeout after the tile unmounts).
  React.useEffect(() => {
    if (liveMode === 'loading') {
      // Readiness is MEASURED, not guessed. The old blind 3s timer mounted
      // <video> whether or not the client had produced a segment yet — on a
      // slow client that paints a black tile, which on a monitor wall reads as
      // a dead camera. Poll the playlist instead and only go 'live' once it
      // lists a real segment; give up after LIVE_POLL_TIMEOUT_MS so a client
      // that never starts falls back to snapshots instead of spinning forever.
      let cancelled = false;
      let timer = null;
      const api = window.SDPRS_API;
      if (!(api && api.getWebcamPlaylist)) {
        // Old/partial bundle without the readiness probe — degrade to the
        // legacy fixed warm-up rather than never leaving 'loading'.
        timer = setTimeout(() => setLiveMode('live'), 3000);
        return () => { cancelled = true; clearTimeout(timer); };
      }
      const deadline = Date.now() + LIVE_POLL_TIMEOUT_MS;
      const probe = () => {
        if (cancelled) return;
        Promise.resolve(api.getWebcamPlaylist(node.id))
          .catch(() => '') // getWebcamPlaylist swallows its own errors; belt-and-braces for a stub that doesn't
          .then(text => {
            if (cancelled) return;
            if (playlistHasSegment(text)) { setLiveMode('live'); return; }
            if (Date.now() >= deadline) {
              // Giving up: the ▶ 即時 click armed the server viewer-lease via
              // startWebcamStream. Release it now — otherwise it stays armed up
              // to LEASE_TTL_SECONDS (~90s) and the field PC keeps encoding a
              // stream no one will ever watch. Symmetric with the ✕ button's
              // stop. Best-effort; a failed stop just lets the lease lapse.
              if (api.stopWebcamStream) api.stopWebcamStream(node.id).catch(() => {});
              setLiveMode('off');
              return;
            }
            timer = setTimeout(probe, LIVE_POLL_MS);
          });
      };
      probe(); // probe immediately — a client that is already streaming should not eat a full poll interval
      return () => { cancelled = true; if (timer) clearTimeout(timer); };
    }
    if (liveMode === 'live') {
      // Keep the 90s server viewer-lease alive while actually watching; without
      // this the stream is force-stopped ~90s in. Best-effort; a failed renew just
      // lets the lease lapse (HlsPlayer.onFallback then returns to snapshot mode).
      const iv = setInterval(() => {
        const api = window.SDPRS_API;
        if (api && api.renewWebcamStream) api.renewWebcamStream(node.id).catch(() => {});
      }, 30000);
      return () => clearInterval(iv);
    }
  }, [liveMode, node.id]);
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
          {node.type === 'camera' ? 'CAM' : node.type === 'webcam' ? 'WEB' : 'PUMP'}
        </div>
        {/* Source badge — webcam client (blue) vs edge cam (grey). */}
        {isWebcam && (
          <span className="absolute top-1 left-1 z-10 px-1.5 py-0.5 rounded text-[9px] font-bold bg-sev-info/90 text-sev-info-fg uppercase tracking-wide">
            Webcam
          </span>
        )}
        {!isWebcam && node.type === 'camera' && (
          <span className="absolute top-1 left-1 z-10 px-1.5 py-0.5 rounded text-[9px] font-bold bg-ink-muted/60 text-surface-base uppercase tracking-wide">
            Edge Cam
          </span>
        )}
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
        {isWebcam && liveMode === 'live' ? (
          <HlsPlayer nodeId={node.id} onFallback={() => setLiveMode('off')} />
        ) : (
          <SnapshotImage node={node}/>
        )}
        {/* Frozen overlay */}
        {frozen && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/40">
            <div className="bg-sev-critical text-xs font-bold px-2 py-1 rounded">
              {/* MSP-F19: raw seconds ("86400s") for a node offline for days is
                  unreadable — humanize via the shared age formatter. */}
              畫面凍結 {fmtAgeOrDash(node.upload)}
            </div>
          </div>
        )}
        {/* Pump level overlay */}
        {node.type === 'pump' && !frozen && (
          <div className="absolute inset-x-2 bottom-8">
            <div className="h-1.5 bg-black/40 rounded overflow-hidden">
              <div className={`h-full ${node.level > 85 ? 'bg-sev-critical' : node.level > 70 ? 'bg-sev-warn' : 'bg-sev-info'}`} style={{ width: (node.level ?? 0) + '%' }}></div>
            </div>
            <div className="text-[10px] font-mono text-white/90 mt-0.5 tnum text-right">水位 {node.level != null ? node.level + '%' : '—'}</div>
          </div>
        )}
        {/* Bottom strip — node id + time */}
        <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/80 to-transparent p-2 flex items-end justify-between">
          <div>
            <div className="font-mono text-xs font-semibold text-white tnum">{node.id}</div>
            <div className="text-[10px] text-white/70">{node.name}</div>
          </div>
          {/* MSP-F25: a live-ticking wall clock next to a frozen frame reads as
              "this is the frame's current timestamp" — exactly the false
              freshness signal this disaster console must never give. Freeze
              the clock display itself once the frame is frozen. */}
          <div className="font-mono text-[10px] text-white/60 tnum">
            {frozen ? <span className="text-white/35">--:--:--</span> : <ClockDisplay />}
          </div>
        </div>
        {/* Live-view controls (webcam only): off → loading → live. */}
        {isWebcam && liveMode === 'off' && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              // Optimistic-first: enter 'loading' so the useEffect owns the
              // playlist-readiness poll → 'live' transition (and its cleanup).
              // A failed start returns to 'off', leaving no dangling timer.
              const api = window.SDPRS_API;
              // Same defensive guard the status-page rows use: without the API
              // bundle there is nothing to start, so stay 'off' rather than
              // sticking the tile on 「連線中...」 until the poll times out.
              if (!(api && api.startWebcamStream)) return;
              setLiveMode('loading');
              Promise.resolve(api.startWebcamStream(node.id))
                .catch(() => setLiveMode('off'));
            }}
            className="absolute bottom-1 right-1 z-10 px-2 py-1 rounded bg-sev-info/80 hover:bg-sev-info text-white text-[10px] font-bold transition-colors"
          >
            ▶ 即時
          </button>
        )}
        {isWebcam && liveMode === 'loading' && (
          <div className="absolute bottom-1 right-1 z-10 px-2 py-1 rounded bg-surface-overlay/80 text-ink-secondary text-[10px]">
            連線中...
          </div>
        )}
        {isWebcam && liveMode === 'live' && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              // Stop the tile FIRST and unconditionally: a missing/failed API
              // must never leave the tile stuck showing 「● LIVE」 over a stream
              // the operator asked to close.
              setLiveMode('off');
              const api = window.SDPRS_API;
              if (api && api.stopWebcamStream) {
                Promise.resolve(api.stopWebcamStream(node.id)).catch(() => {});
              }
            }}
            className="absolute top-1 right-1 z-20 px-2 py-1 rounded bg-sev-critical/80 hover:bg-sev-critical text-white text-[10px] font-bold"
          >
            ● LIVE ✕
          </button>
        )}
      </div>
      {/* Stats */}
      <div className="p-2 grid grid-cols-3 gap-1 text-[10px] font-mono tnum bg-surface-panel">
        <div className="flex flex-col">
          <span className="text-ink-muted">心跳</span>
          <span className={node.heartbeat > 30 ? 'text-sev-critical font-semibold' : node.heartbeat > 10 ? 'text-sev-warn' : 'text-ink-secondary'}>
            {fmtAgeOrDash(node.heartbeat)}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-ink-muted">上傳</span>
          <span className={node.upload > 60 ? 'text-sev-critical font-semibold' : node.upload > 10 ? 'text-sev-warn' : 'text-ink-secondary'}>
            {fmtAgeOrDash(node.upload)}
          </span>
        </div>
        {node.type === 'camera' ? (
          <div className="flex flex-col">
            <span className="text-ink-muted">🌡</span>
            {/* MSP-F21: truthy check treated a genuine 0°C reading as "no data" */}
            <span className={node.temp > 50 ? 'text-sev-warn' : 'text-ink-secondary'}>{node.temp != null ? node.temp+'°' : '—'}</span>
          </div>
        ) : (
          <div className="flex flex-col">
            <span className="text-ink-muted">本時</span>
            <span className={node.cycles > 20 ? 'text-sev-critical font-semibold' : node.cycles > 15 ? 'text-sev-warn' : 'text-ink-secondary'}>
              {node.cycles != null ? node.cycles + '×' : '—'}
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
}, (prev, next) => {
  const pn = prev.node, nn = next.node;
  return pn === nn &&
    prev.nodeAlerts === next.nodeAlerts &&
    prev.onSelect === next.onSelect;
});

// Stable empty array — avoids creating a new [] on every render when a node
// has no alerts, which would break React.memo's reference-equality check.
const EMPTY_ALERTS = Object.freeze([]);

const MonitorPage = ({ nodes, activeAlerts, onSelectNode }) => {
  // Tab state persisted to sessionStorage so navigating away and back preserves
  // the user's filter choice. sessionStorage (not localStorage) — fresh browser
  // session starts on 'all'. try/catch guards Safari private mode.
  const [tab, setTab] = useState_p(() => {
    try { return window.sessionStorage.getItem('sdprs.monitor.tab') || 'all'; }
    catch (_) { return 'all'; }
  });
  React.useEffect(() => {
    try { window.sessionStorage.setItem('sdprs.monitor.tab', tab); }
    catch (_) {}
  }, [tab]);
  // Local toast for fullscreen failures etc. Auto-dismissed after 3s.
  const [toast, setToast] = useState_p(null);
  React.useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(t);
  }, [toast]);
  // MSP-F10: freeze card order while the pointer is over the grid (see
  // useStableSort above) so a background refresh never reshuffles cards out
  // from under an in-flight click.
  const [gridHover, setGridHover] = useState_p(false);
  // Pre-compute Map<nodeId, alert[]> so each NodeCard/PumpCard does O(1) lookup
  // instead of O(n) filter on every render. Recomputes only when activeAlerts
  // reference changes (i.e. on refresh, not on tick).
  const alertMap = useMemo_p(() => {
    const m = new Map();
    for (const a of (activeAlerts || [])) {
      const list = m.get(a.node);
      if (list) list.push(a); else m.set(a.node, [a]);
    }
    return m;
  }, [activeAlerts]);
  // Audit fix: read from React `nodes` prop (fed by app.jsx setNodes on every
  // refreshLive tick), NOT `window.NODES` directly. The window mirror is
  // updated in the same tick but consuming it bypasses the load-bearing
  // React state contract — any future refactor that updates NODES without
  // triggering a re-render would silently freeze this page.
  const allNodes = nodes ?? [];
  const cameraNodes = allNodes.filter(n => n.type === 'camera');
  const pumpNodes = allNodes.filter(n => n.type === 'pump');
  const visibleNodes = tab === 'cameras' ? cameraNodes : tab === 'pumps' ? pumpNodes : allNodes;
  // MSP-F10: was `[...visibleNodes].sort(...)` recomputed fresh every render —
  // reshuffled the whole grid under the cursor on every ~20s poll/WS tick.
  // useStableSort (defined above) keeps the last-rendered slot order while
  // gridHover is true. Three independent hook instances (rules-of-hooks safe:
  // called unconditionally, same order every render) because the 全部 tab
  // renders pumps/cameras as two separate sorted sub-lists below.
  const sorted = useStableSort(visibleNodes, gridHover);
  const sortedPumpsAll = useStableSort(pumpNodes, gridHover);
  const sortedCamerasAll = useStableSort(cameraNodes, gridHover);

  const summary = {
    online: allNodes.filter(n => n.status === 'online').length,
    warn: allNodes.filter(n => n.status === 'warn').length,
    critical: allNodes.filter(n => n.status === 'critical').length,
    offline: allNodes.filter(n => n.status === 'offline').length,
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
                // MSP-F24: exitFullscreen() returns a promise that can reject
                // (denied by the browser); it was unhandled.
                document.exitFullscreen && document.exitFullscreen().catch(() => {});
              } else if (el.requestFullscreen) {
                el.requestFullscreen().catch(err => setToast({ tone: 'warn', msg: '全螢幕請求被瀏覽器拒絕' }));
              }
            }}
            className="flex items-center gap-1.5 h-7 px-2 bg-surface-elevated border border-border-strong rounded hover:bg-surface-overlay">
            <Icon.Maximize size={12}/> 全螢幕
          </button>
        </div>
      </div>
      {/* MSP-F11: an in-flow banner here would push the whole grid down mid-
          interaction, landing the next click on the wrong card. Overlay it
          instead (matches app.jsx's global toast pattern) so it never moves
          layout. */}
      {toast && (
        <div className="fixed top-16 right-4 z-40 animate-in" role="status" aria-live="polite">
          <div className={`px-3 py-2 rounded-lg border shadow-2xl text-xs bg-surface-overlay ${
            toast.tone === 'success' ? 'border-sev-ok/50 text-sev-ok'
              : toast.tone === 'error' ? 'border-sev-critical/50 text-sev-critical'
              : toast.tone === 'warn' ? 'border-sev-warn/50 text-sev-warn'
              : 'border-sev-info/50 text-sev-info'
          }`}>{toast.msg}</div>
        </div>
      )}
      <div className="flex-1 overflow-y-auto scroll-thin p-3"
        onMouseEnter={() => setGridHover(true)}
        onMouseLeave={() => setGridHover(false)}>
        {sorted.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <EmptyState icon={Icon.Camera} title="尚無節點資料"
              hint="伺服器尚未回報任何節點,或當前分頁篩選結果為空"/>
          </div>
        ) : tab === 'pumps' ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {sorted.map(n => <PumpCard key={n.id} node={n} onSelect={onSelectNode} nodeAlerts={alertMap.get(n.id) || EMPTY_ALERTS}/>)}
          </div>
        ) : tab === 'cameras' ? (
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3">
            {sorted.map(n => <NodeCard key={n.id} node={n} onSelect={onSelectNode} nodeAlerts={alertMap.get(n.id) || EMPTY_ALERTS}/>)}
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
                  {sortedPumpsAll.map(n => <PumpCard key={n.id} node={n} onSelect={onSelectNode} nodeAlerts={alertMap.get(n.id) || EMPTY_ALERTS} compact/>)}
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
                  {sortedCamerasAll.map(n => <NodeCard key={n.id} node={n} onSelect={onSelectNode} nodeAlerts={alertMap.get(n.id) || EMPTY_ALERTS}/>)}
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

const PumpCard = React.memo(({ node, onSelect, nodeAlerts = [], compact = false }) => {
  const hasCritical = nodeAlerts.some(a => a.sev === 'critical');
  const stateTone = node.status === 'offline' || node.status === 'critical' ? 'critical' : node.status === 'warn' ? 'warn' : 'ok';
  // MSP-F3: an offline pump (or one with no water_level reading yet) must
  // never draw its last-known level as a live-looking gauge fill — a stale
  // number silently read as current is exactly the "frozen panel mistaken
  // for live" failure this console must avoid. Ported from pumps.jsx's
  // already-shipped guard.
  const isOffline = node.status === 'offline';
  const isNoTelemetry = node.level == null;
  const levelTone = (isOffline || isNoTelemetry) ? 'stale' : node.level > 85 ? 'critical' : node.level > 70 ? 'warn' : 'info';
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

      {/* MSP-F1: the actual relay state (node.pumpState), distinct from
          `status` which is derived from water level / online-ness. This is
          the single most important fact on a pump card during a flood — the
          device silently drops ON commands under dry-run/sensor-conflict
          protection, and without this an operator has no way to learn the
          pump never actually ran. */}
      <div className={`flex items-center gap-1.5 px-3 py-1 text-[11px] font-bold border-b ${
        node.pumpState === 'on' ? 'bg-sev-ok/15 border-sev-ok/30 text-sev-ok'
          : node.pumpState === 'off' ? 'bg-surface-elevated border-border-subtle text-ink-secondary'
          : 'bg-sev-warn/10 border-sev-warn/30 text-sev-warn'
      }`}>
        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
          node.pumpState === 'on' ? 'bg-sev-ok animate-live-blink' : node.pumpState === 'off' ? 'bg-ink-dim' : 'bg-sev-warn'
        }`}></span>
        {node.pumpState === 'on' ? '運轉中' : node.pumpState === 'off' ? '已停止' : '狀態不明'}
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
        <div className={`relative w-20 flex-shrink-0 bg-surface-base rounded border overflow-hidden ${(isOffline || isNoTelemetry) ? 'border-dashed border-sev-stale/50' : 'border-border-subtle'}`} style={{ height: compact ? '112px' : '140px' }}>
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
          {/* Water fill — MSP-F3: suppressed entirely when offline/no-data so a
              stale reading never renders as a live measurement. */}
          {!(isOffline || isNoTelemetry) && (
            <div className={`absolute left-3 right-0 bottom-0 transition-all duration-500 ${levelTone === 'critical' ? 'bg-sev-critical/50' : levelTone === 'warn' ? 'bg-sev-warn/40' : 'bg-sev-info/40'}`} style={{ height: node.level + '%' }}>
              {/* Wave top edge */}
              <div className={`h-0.5 ${levelTone === 'critical' ? 'bg-sev-critical' : levelTone === 'warn' ? 'bg-sev-warn' : 'bg-sev-info'}`}></div>
            </div>
          )}
          {/* Big % readout */}
          <div className="absolute inset-x-3 top-1/2 -translate-y-1/2 text-center pointer-events-none">
            <div className={`text-xl font-mono font-black tnum leading-none ${(isOffline || isNoTelemetry) ? 'text-sev-stale' : levelTone === 'critical' ? 'text-sev-critical' : levelTone === 'warn' ? 'text-sev-warn' : 'text-ink-primary'}`}>
              {(isOffline || isNoTelemetry) ? '—' : node.level}
              <span className="text-[10px] text-ink-muted font-normal">%</span>
            </div>
          </div>
          {isOffline && (
            <div className="absolute top-1 left-1 text-[8px] font-bold px-1 py-0.5 rounded bg-sev-stale/20 text-sev-stale tracking-wide">離線</div>
          )}
          {/* Trend arrow — MSP-F14: api.jsx's `trend` is always null (not yet
              computed server-side); showing a fixed "→" claimed a real,
              flat reading. Render an honest dash instead of a fabricated
              direction — this still lights up correctly if trend data ever
              ships. */}
          <div className="absolute bottom-0.5 right-0.5 text-[10px] font-mono">
            {node.trend === 'up' ? <span className="text-sev-warn">↑</span> : node.trend === 'down' ? <span className="text-sev-ok">↓</span> : <span className="text-ink-dim">—</span>}
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

          {/* Rain / dry-run protect badges. MSP-F15: was English-only
              ("Dry-run protect (pump held OFF)") in an otherwise all-zh-TW
              UI — this is the exact flag that explains "why didn't my ON
              command run", so it must be readable at a glance. */}
          {(node.raining || node.dryRunProtect) && (
            <div className="flex items-center gap-1.5 flex-wrap">
              {node.raining && (
                <Pill tone="info" className="!h-5 !text-[10px]">🌧 降雨中</Pill>
              )}
              {node.dryRunProtect && (
                <Pill tone="warn" className="!h-5 !text-[10px]">防乾轉保護中（幫浦已停止）</Pill>
              )}
            </div>
          )}

          {/* Location footer */}
          <div className="flex items-center gap-1 text-[10px] text-ink-muted font-mono tnum pt-1 border-t border-border-subtle/60">
            <Icon.MapPin size={9}/>
            <span className="truncate">{node.location}</span>
            {/* Contract A: heartbeat is `number | null` — was rendered with no
                null guard, printing the literal string "心跳 nulls" for a
                never-reported pump. */}
            <span className="ml-auto">心跳 {fmtAgeOrDash(node.heartbeat)}</span>
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
}, (prev, next) => {
  return prev.node === next.node &&
    prev.nodeAlerts === next.nodeAlerts &&
    prev.onSelect === next.onSelect &&
    prev.compact === next.compact;
});

// fmtAgeOrDash exported explicitly (not relied on as an implicit sloppy-mode
// global — see SHL-17) so status.jsx can reuse the same age humanizer for a
// consistent "raw seconds" fix across both pages (MSP-F19/F20).
Object.assign(window, { MonitorPage, fmtAgeOrDash });
