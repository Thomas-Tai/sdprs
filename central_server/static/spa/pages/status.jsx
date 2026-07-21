// SDPRS — Node Status Page

const { useState: useState_p, useMemo: useMemo_p } = React;

// Per-row snooze control. Local `busy` state guards against double-fire on
// slow VPN; onKeyDown must stopPropagation so Enter/Space on the focused
// button doesn't also bubble to the surrounding row's keyboard handler
// (which would open the side panel).
//
// MSP-F13: was snooze-only and invisible from the table — repeat clicks
// silently reset the 30-min window with no feedback, and there was no way to
// undo from here. Now toggles: already-snoozed → unsnooze (api.jsx already
// exposes unsnoozeNode); the button's own color (amber while snoozed) plus
// the row-level badge in the id column make the state visible without
// needing hover.
const SnoozeRowButton = ({ node, onDone, onError }) => {
  const [busy, setBusy] = React.useState(false);
  // MSP-F26: guard setState after the row's node disappears mid-request
  // (filtered out / node removed by a refresh that lands while the snooze
  // call is still in flight).
  const mountedRef = React.useRef(true);
  React.useEffect(() => () => { mountedRef.current = false; }, []);
  const isSnoozed = node.snoozeMin > 0;
  const trigger = () => {
    if (busy) return;
    // G1: don't silently no-op when the API bundle hasn't loaded / backend is
    // unreachable — route through onError so the parent's toast surfaces the
    // outage instead of the operator wondering why nothing happened.
    const api = window.SDPRS_API;
    if (!(api && api.snoozeNode && api.unsnoozeNode)) {
      onError && onError(new Error('暫時無法連線後端，請稍後再試'));
      return;
    }
    setBusy(true);
    const call = isSnoozed
      ? api.unsnoozeNode(node.id)
      : api.snoozeNode(node.id, 30, '從節點狀態列表靜音');
    Promise.resolve(call)
      // MSP-F7-style guard: stay busy through the caller's refresh, not just
      // the HTTP round-trip, so a second click can't fire before the table
      // reflects the new state.
      .then(() => Promise.resolve(onDone && onDone(node, isSnoozed ? 'unsnooze' : 30)))
      .catch(err => onError && onError(err))
      .finally(() => { if (mountedRef.current) setBusy(false); });
  };
  return (
    <button
      title={isSnoozed ? `取消靜音（剩餘 ${node.snoozeMin} 分鐘）` : '靜音 30 分鐘'}
      disabled={busy}
      onClick={e => { e.stopPropagation(); trigger(); }}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}
      className={`w-8 h-8 rounded flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed ${
        isSnoozed ? 'text-sev-warn hover:bg-sev-warn/15' : 'text-ink-muted hover:bg-surface-overlay hover:text-ink-primary'
      }`}>{isSnoozed ? <Icon.Bell size={14}/> : <Icon.BellOff size={14}/>}</button>
  );
};

// Per-row stream start/stop toggle for cameras. Mirrors SnoozeRowButton:
// local busy guard, click/key stopPropagation so the row's side-panel
// handler doesn't fire.
//
// MSP-F7 fix: previously inferred "streaming now" from n.bitrate > 0 — that
// field is mapNode's `stream_status.bitrate_mbps`, which the edge device
// never publishes and is therefore always 0 (see api.jsx's API-F3 comment),
// so it desynced from reality and a rapid double-click could fire the wrong
// command against a state the button never actually knew. Now backed by
// window.SDPRS_API.getStreamHealth(nodeId): `enabled`/`reachable` are the
// streaming bridge's own health (mediamtx up + scrapeable) and gate
// everything else — if the bridge itself is down/disabled nothing can be
// "streaming" regardless of any per-node number. Layered under that gate,
// this node has a live mediamtx path entry (bitrateMbps/viewers/drops come
// back non-null together once mediamtx has scraped a sample for this path,
// per api.jsx's roundOrNull convention) — that's the per-node signal the
// endpoint actually exposes.
//
// Double-fire guard: an in-flight ref (checked synchronously, not the
// `busy` state, which can lag a tick behind a fast double-click) so two
// clicks in the same tick can't both read "not busy" and both fire.
//
// API-gated: if SDPRS_API.startStream / stopStream / getStreamHealth are
// missing (waiting on api.jsx follow-up), render disabled with
// "串流控制 (等待 API)".
const StreamRowButton = ({ node, onDone, onError }) => {
  const [busy, setBusy] = React.useState(false);
  const [health, setHealth] = React.useState(null); // getStreamHealth() result, or null while unknown/loading
  // MSP-F26: same unmount guard as SnoozeRowButton.
  const mountedRef = React.useRef(true);
  React.useEffect(() => () => { mountedRef.current = false; }, []);
  // MSP-F7: synchronous in-flight latch, separate from `busy` state — a ref
  // is read-after-write within the same synchronous click handler, so a
  // second click in the same tick sees it immediately regardless of when
  // React actually commits the `busy` state update.
  const inFlightRef = React.useRef(false);
  const api = window.SDPRS_API || {};
  const hasApi = typeof api.startStream === 'function' && typeof api.stopStream === 'function' && typeof api.getStreamHealth === 'function';
  const refreshHealth = React.useCallback(() => {
    if (!hasApi) return Promise.resolve();
    return Promise.resolve(api.getStreamHealth(node.id))
      .then(h => { if (mountedRef.current) setHealth(h || null); })
      .catch(() => {}); // best-effort probe — fall back to "unknown", never throw into a caller that didn't ask for this
  }, [node.id, hasApi]);
  React.useEffect(() => { refreshHealth(); }, [refreshHealth]);
  const hasLiveEntry = !!(health && (health.bitrateMbps != null || health.viewers != null || health.drops != null));
  const isActive = health
    ? !!(health.enabled && health.reachable && hasLiveEntry)
    : (node.bitrate || 0) > 0; // bootstrap fallback only, before the first getStreamHealth() resolves — never the steady-state source of truth
  const label = isActive ? '停止串流' : '開始串流';
  const Glyph = isActive ? Icon.Pause : Icon.Play;
  const trigger = () => {
    if (inFlightRef.current || busy || !hasApi) return;
    inFlightRef.current = true;
    setBusy(true);
    const wasActive = isActive; // snapshot at click time — the async health refetch below must not change which command this click fires
    const call = wasActive ? api.stopStream(node.id) : api.startStream(node.id);
    Promise.resolve(call)
      .then(() => refreshHealth())
      // MSP-F7: previously cleared `busy` as soon as the HTTP call resolved,
      // before the parent's refresh had a chance to update the cached
      // bitrate this button's `isActive` is derived from — a second click in
      // that window re-read the stale bitrate and could fire the opposite
      // command against a state that had already changed (desync +
      // double-fire). Stay busy until this button's own health refetch (the
      // new source of truth) AND the caller's refresh (awaited via onDone)
      // actually land.
      .then(() => Promise.resolve(onDone && onDone(node, wasActive ? 'stop' : 'start')))
      .catch(err => onError && onError(err))
      .finally(() => { inFlightRef.current = false; if (mountedRef.current) setBusy(false); });
  };
  return (
    <button
      title={hasApi ? label : '串流控制 (等待 API)'}
      disabled={busy || !hasApi}
      onClick={e => { e.stopPropagation(); trigger(); }}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}
      className="w-8 h-8 rounded hover:bg-surface-overlay flex items-center justify-center text-ink-muted hover:text-ink-primary disabled:opacity-50 disabled:cursor-not-allowed"><Glyph size={14}/></button>
  );
};

const StatusPage = ({ nodes = [], onSelectNode, onRefresh }) => {
  const [typeFilter, setTypeFilter] = useState_p('all');    // all | camera | pump | webcam
  const [statusFilter, setStatusFilter] = useState_p('all'); // all | online | warn | critical | offline
  const [locationFilter, setLocationFilter] = useState_p('all');
  // Local toast (success/error feedback for snooze etc.). Auto-dismissed after 3s.
  const [toast, setToast] = useState_p(null);
  React.useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(t);
  }, [toast]);
  // Webcam client admin: "新增 Webcam Client" modal state.
  const [showAddModal, setShowAddModal] = useState_p(false);
  const [newClientName, setNewClientName] = useState_p('');
  const [createdKey, setCreatedKey] = useState_p(null);
  const [addBusy, setAddBusy] = useState_p(false);
  // Revoke-key result: the fresh key is a credential shown exactly once, same
  // as createdKey above. It is deliberately NOT surfaced via the 3s-auto-
  // dismissing `toast` — an operator who doesn't finish copying before the
  // toast disappears has no way to recover it short of revoking again. A
  // modal (mirrors the create flow) stays up until explicitly dismissed.
  const [revokedKey, setRevokedKey] = useState_p(null); // { nodeId, name, apiKey } | null
  // Delete confirmation for a webcam client (spec §節點管理: 撤銷 Key / 刪除).
  // An in-app modal, NOT window.confirm: the native dialog is unstyled, blocks
  // the whole tab (freezing the alert banner + WS-driven repaints behind it on
  // a 24/7 wall display), and cannot name the consequence in the same voice as
  // the rest of the console.
  //
  // THE ID TRAP: a 'webcam' row in this table is a CAMERA, but both webcam
  // admin endpoints take the owning CLIENT's node_id (n.clientId, from
  // webcam_cameras.client_id). The two ids look identical in shape
  // ("webcam_" + 8 hex) and never match, so sending n.id here is a silent,
  // permanent 404. Everything below addresses n.clientId, never n.id.
  //
  // clientName rides along for DISPLAY ONLY: a destructive confirm has to name
  // the client the way the operator named it (「Bench PC」), not as the opaque
  // hex id they have never seen. It is nullable (older backend, or a camera
  // whose client row is gone), so every read of it falls back to clientId —
  // and clientId is still the ONLY thing ever sent to the API.
  const [deleteTarget, setDeleteTarget] = useState_p(null); // { clientId, clientName, cameras: [{id,name}] } | null
  // What to call the client in operator-facing copy. Never used as an id.
  const clientLabel = (t) => (t && t.clientName) || (t && t.clientId) || '';
  const [deleteBusy, setDeleteBusy] = useState_p(false);
  // Every camera row that belongs to the same client PC. Deleting the client
  // takes ALL of them down, so the confirm dialog has to enumerate them from
  // the FULL node list — not `filtered`, or an active type/status/location
  // filter would hide siblings that are about to be destroyed anyway.
  const camerasOfClient = (clientId) => (clientId
    ? nodes.filter(x => x.type === 'webcam' && x.clientId === clientId)
        .map(x => ({ id: x.id, name: x.name || x.id }))
    : []);
  // MSP-F26-style guard: the page can unmount (nav away) while the DELETE is
  // still in flight — don't setState into a dead tree.
  const mountedRef = React.useRef(true);
  React.useEffect(() => () => { mountedRef.current = false; }, []);
  const confirmDeleteWebcam = () => {
    if (deleteBusy || !deleteTarget) return;
    // G1 guard (same as SnoozeRow/StreamRow): without the API bundle the
    // button would otherwise latch on 「刪除中...」 forever.
    const api = window.SDPRS_API;
    if (!(api && api.deleteWebcamClient)) {
      setToast({ tone: 'error', msg: '暫時無法連線後端，請稍後再試' });
      return;
    }
    const target = deleteTarget;
    // Defence in depth: the button that opens this dialog is already disabled
    // without a clientId, but NEVER let an undefined id reach the URL builder —
    // "/api/nodes/webcam/undefined" is a request we must not be able to send.
    if (!target.clientId) {
      setToast({ tone: 'error', msg: '此列缺少用戶端識別碼，無法刪除' });
      return;
    }
    setDeleteBusy(true);
    Promise.resolve(api.deleteWebcamClient(target.clientId))
      // Server contract: 204 deleted, 404 already gone. A 404 means the
      // operator's intent is already satisfied (double-click, or a peer
      // deleted it first) — refresh like a success instead of crying failure.
      .catch(err => { if (err && err.status === 404) return; throw err; })
      .then(() => {
        if (!mountedRef.current) return;
        setDeleteTarget(null);
        setToast({
          tone: 'success',
          msg: `Webcam 用戶端「${clientLabel(target)}」已刪除（含 ${(target.cameras || []).length} 支攝影機）`,
        });
        return typeof onRefresh === 'function' ? Promise.resolve(onRefresh()) : undefined;
      })
      .catch(err => { if (mountedRef.current) setToast({ tone: 'error', msg: `刪除失敗: ${err?.message || err}` }); })
      .finally(() => { if (mountedRef.current) setDeleteBusy(false); });
  };
  // Unique locations from the current node list — filter values are derived
  // so a new deployment doesn't need a config change.
  const locations = useMemo_p(() => {
    const set = new Set();
    nodes.forEach(n => { if (n.location) set.add(n.location); });
    return ['all', ...Array.from(set)];
  }, [nodes]);
  const filtered = useMemo_p(() => nodes.filter(n => {
    if (typeFilter !== 'all' && n.type !== typeFilter) return false;
    if (statusFilter !== 'all' && n.status !== statusFilter) return false;
    if (locationFilter !== 'all' && n.location !== locationFilter) return false;
    return true;
  }), [typeFilter, statusFilter, locationFilter, nodes]);
  // Cycle through preset values for the chip dropdowns (real dropdown UI is
  // a bigger design decision — keep the click surface working with cycling).
  const cycleType = () => setTypeFilter(t =>
    t === 'all' ? 'camera' : t === 'camera' ? 'pump' : t === 'pump' ? 'webcam' : 'all');
  const cycleStatus = () => setStatusFilter(s => {
    const order = ['all', 'online', 'warn', 'critical', 'offline'];
    return order[(order.indexOf(s) + 1) % order.length];
  });
  const cycleLocation = () => setLocationFilter(l => {
    const i = locations.indexOf(l);
    return locations[(i + 1) % locations.length];
  });
  const typeLabel = typeFilter === 'all' ? '全部'
    : typeFilter === 'camera' ? '攝影機'
    : typeFilter === 'webcam' ? 'Webcam'
    : '抽水站';
  const statusLabel = statusFilter === 'all' ? '全部' : statusFilter === 'online' ? '正常' : statusFilter === 'warn' ? '警告' : statusFilter === 'critical' ? '嚴重' : '離線';
  const locationLabel = locationFilter === 'all' ? '全部' : locationFilter;
  const filtersActive = typeFilter !== 'all' || statusFilter !== 'all' || locationFilter !== 'all';
  return (
    <div className="h-full flex flex-col min-h-0">
      {/* MSP-F11: an in-flow banner here pushed the whole table down mid-
          interaction — the next click could land on a different row's device
          button. Overlay it instead (matches app.jsx's global toast pattern)
          so it never shifts layout. */}
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
      <div className="px-4 py-2.5 border-b border-border-subtle bg-surface-panel flex items-center gap-3 flex-shrink-0">
        <h1 className="text-sm font-semibold">節點狀態</h1>
        <span className="text-xs text-ink-muted tnum">
          {filtered.length}{filtered.length !== nodes.length && ` / ${nodes.length}`} 個節點
        </span>
        <div className="flex-1"></div>
        <div className="flex gap-1.5">
          {/* MSP-F22 fix: this chip is a click-to-cycle control, not a real
              <select> — the ChevronDown made it look like one and promised a
              dropdown menu that never opens, so it's dropped rather than
              built out into an actual listbox (a bigger design change than
              this fix warrants). The clear-× is now FilterChip's own built-in
              `onClear` (components.jsx): a plain, non-interactive span, never
              a second interactive element nested inside this chip's own
              <button> — keyboard users clear via Delete/Backspace while the
              chip is focused. */}
          <FilterChip active={typeFilter !== 'all'} onClick={cycleType}
            onClear={typeFilter !== 'all' ? () => setTypeFilter('all') : undefined}>
            類型: {typeLabel}
          </FilterChip>
          {/* MSP-F22 fix: see 類型 chip above for the fake-chevron + nested-
              interactive-clear-× rationale. */}
          <FilterChip active={statusFilter !== 'all'} onClick={cycleStatus}
            onClear={statusFilter !== 'all' ? () => setStatusFilter('all') : undefined}>
            狀態: {statusLabel}
          </FilterChip>
          {/* MSP-F22 fix: see 類型 chip above for the fake-chevron + nested-
              interactive-clear-× rationale. */}
          <FilterChip active={locationFilter !== 'all'} onClick={cycleLocation}
            onClear={locationFilter !== 'all' ? () => setLocationFilter('all') : undefined}>
            位置: <span className="max-w-[80px] truncate inline-block align-middle">{locationLabel}</span>
          </FilterChip>
        </div>
        <button
          onClick={() => { setShowAddModal(true); setCreatedKey(null); setNewClientName(''); }}
          className="px-3 py-1.5 rounded-lg bg-sev-info text-white text-xs font-bold hover:opacity-90 transition-opacity"
        >
          + 新增 Webcam Client
        </button>
      </div>
      <div className="flex-1 overflow-y-auto overflow-x-auto scroll-thin">
        <table className="w-full text-xs tnum">
          <thead className="sticky top-0 bg-surface-base z-10 border-b border-border-strong">
            <tr className="text-[10px] text-ink-muted uppercase tracking-wider">
              <th scope="col" className="text-left font-semibold px-3 py-2">節點</th>
              <th scope="col" className="text-left font-semibold px-3 py-2">類型</th>
              <th scope="col" className="text-left font-semibold px-3 py-2">位置</th>
              <th scope="col" className="text-left font-semibold px-3 py-2">狀態</th>
              <th scope="col" className="text-right font-semibold px-3 py-2">心跳</th>
              <th scope="col" className="text-right font-semibold px-3 py-2">上傳</th>
              <th scope="col" className="text-left font-semibold px-3 py-2">串流健康</th>
              <th scope="col" className="text-right font-semibold px-3 py-2">溫度 / 水位</th>
              <th scope="col" className="text-left font-semibold px-3 py-2">電源</th>
              <th scope="col" className="text-right font-semibold px-3 py-2 pr-4">動作</th>
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
              const isWebcam = n.type === 'webcam';
              let tone = n.status === 'offline' || n.status === 'critical' ? 'critical' : n.status === 'warn' ? 'warn' : 'ok';
              if (tone === 'ok' && pumpLevelMissing) tone = 'warn';
              // Contract A: heartbeat/upload are `number | null`. Relational
              // comparison against null coerces to 0 (`null < 60` is true) —
              // was silently misclassifying a never-reported node. Guard both
              // sides explicitly.
              const uploadIssue = n.heartbeat != null && n.heartbeat < 60 && n.upload != null && n.upload > 600;
              return (
                <tr key={n.id}
                  role="button"
                  tabIndex={0}
                  className="border-b border-border-subtle/60 hover:bg-surface-elevated/60 group cursor-pointer focus:outline focus:outline-1 focus:outline-sev-info"
                  onClick={() => onSelectNode && onSelectNode(n)}
                  onKeyDown={e => {
                    if (onSelectNode && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onSelectNode(n); }
                  }}>
                  <td className="px-3 py-2 font-mono font-semibold">
                    {n.id}
                    {/* MSP-F13: snooze state was invisible from the table
                        unless you hovered the row's action button. */}
                    {n.snoozeMin > 0 && (
                      <span className="ml-1.5 inline-flex items-center gap-0.5 text-[9px] font-bold px-1 py-0.5 rounded bg-sev-warn/15 text-sev-warn align-middle">
                        <Icon.BellOff size={8} strokeWidth={2.5}/>{n.snoozeMin}m
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-ink-secondary">
                    <span className="inline-flex items-center gap-1.5">
                      {isWebcam ? <Icon.Camera size={12}/> : n.type === 'camera' ? <Icon.Camera size={12}/> : <Icon.Pump size={12}/>}
                      {isWebcam ? 'Webcam' : n.type === 'camera' ? '攝影機' : '抽水站'}
                      {/* MSP-F1: surface the actual relay state — distinct
                          from `status` (derived from water level) — the fact
                          that explains "why didn't my ON command run". */}
                      {n.type === 'pump' && (
                        <span className={`text-[9px] font-bold px-1 rounded ${
                          n.pumpState === 'on' ? 'bg-sev-ok/15 text-sev-ok'
                            : n.pumpState === 'off' ? 'bg-surface-elevated text-ink-muted'
                            : 'bg-sev-warn/15 text-sev-warn'
                        }`}>{n.pumpState === 'on' ? '運轉中' : n.pumpState === 'off' ? '已停止' : '不明'}</span>
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-ink-secondary">{n.location}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded border text-[10px] font-medium bg-sev-${tone === 'critical' ? 'critical' : tone === 'warn' ? 'warn' : 'ok'}/15 text-sev-${tone === 'critical' ? 'critical' : tone === 'warn' ? 'warn' : 'ok'} border-sev-${tone === 'critical' ? 'critical' : tone === 'warn' ? 'warn' : 'ok'}/30`}>
                      <span className={`w-1.5 h-1.5 rounded-full bg-sev-${tone === 'critical' ? 'critical' : tone === 'warn' ? 'warn' : 'ok'}`}></span>
                      {/* MSP-F12: badge color already downgraded to amber for
                          pumpLevelMissing, but the text still read n.status
                          directly — an amber badge captioned "正常" is a lie.
                          Name the actual condition instead of a generic 警告. */}
                      {n.status === 'offline' ? '離線' : n.status === 'critical' ? '嚴重' : pumpLevelMissing ? '水位未知' : n.status === 'warn' ? '警告' : '正常'}
                    </span>
                  </td>
                  {/* MSP-F20: thresholds now match monitor.jsx (10s/30s) and
                      the backend's own STALE_THRESHOLD_SECONDS=10 — this
                      column previously alarmed at 5s/60s, a different point
                      than every other page. MSP-F19: humanized via the same
                      age formatter monitor.jsx exports, so a node stale for
                      days reads "1d 2h" instead of a raw seconds count. */}
                  <td className={`px-3 py-2 text-right font-mono ${n.heartbeat == null ? 'text-ink-muted' : n.heartbeat > 30 ? 'text-sev-critical font-semibold' : n.heartbeat > 10 ? 'text-sev-warn' : 'text-ink-secondary'}`}>
                    {window.fmtAgeOrDash ? window.fmtAgeOrDash(n.heartbeat) : (n.heartbeat != null ? (n.heartbeat > 60 ? Math.floor(n.heartbeat/60)+'m' : n.heartbeat+'s') : '—')}
                  </td>
                  {/* Contract A: `upload` is null for a camera with no
                      snapshot ever. This column previously had NO null guard
                      at all and rendered the literal string "nulls". */}
                  <td className={`px-3 py-2 text-right font-mono ${uploadIssue ? 'text-sev-critical font-semibold' : n.upload == null ? 'text-ink-muted' : n.upload > 60 ? 'text-sev-critical font-semibold' : n.upload > 10 ? 'text-sev-warn' : 'text-ink-secondary'}`}>
                    {window.fmtAgeOrDash ? window.fmtAgeOrDash(n.upload) : (n.upload != null ? (n.upload > 60 ? Math.floor(n.upload/60)+'m' : n.upload+'s') : '—')}
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
                      // MSP-F21: truthy check treated a genuine 0°C reading as "no data".
                      <span className={n.temp > 50 ? 'text-sev-warn' : n.temp != null ? 'text-ink-secondary' : 'text-ink-muted'}>{n.temp != null ? n.temp+'°C' : '—'}</span>
                    ) : isWebcam ? (
                      // A webcam client is a mains PC with no temperature or
                      // water sensor — a plain muted 「—」, never the amber
                      // 「水位資料未上傳」 lie of the pump branch below.
                      <span className="text-ink-muted" title="網路攝影機無此感測">—</span>
                    ) : n.level == null ? (
                      // No water_level reading — do not render 0%/blank% which
                      // would look like a real reading. Amber "—" makes the gap
                      // visible; upstream api.jsx keeps status='online' because
                      // heartbeat is fine, so the row is still ONLINE.
                      <span className="text-sev-warn" title="水位資料未上傳">—</span>
                    ) : (
                      <span className="inline-flex items-center gap-1">
                        {/* MSP-F14: api.jsx's `trend` is always null — a fixed
                            ArrowRight claimed a real "flat" reading every
                            time. Honest dash instead of a fabricated
                            direction (still lights up if trend data ships). */}
                        {n.trend === 'up' ? <Icon.ArrowUp size={10} className="text-sev-warn"/> : n.trend === 'down' ? <Icon.ArrowDown size={10} className="text-sev-ok"/> : <span className="text-ink-dim">—</span>}
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
                    ) : isWebcam ? (
                      <span className="text-ink-muted text-[10px] font-mono">—</span>
                    ) : <span className="text-ink-muted text-[10px] font-mono">PoE</span>}
                  </td>
                  <td className="px-3 py-2 text-right pr-4">
                    {/* MSP-F23: 24px buttons hidden behind opacity-60-until-hover
                        are both a sub-32px hit target and undiscoverable on a
                        touch/no-hover NOC display. Always full opacity, ≥32px. */}
                    <div className="inline-flex gap-1">
                      {n.type === 'camera' && (
                        <StreamRowButton
                          node={n}
                          onDone={(node, action) => {
                            setToast({ tone: 'success', msg: `${node.name || node.id} ${action === 'stop' ? '串流已停止' : '串流已啟動'}` });
                            // MSP-F7: the button's own getStreamHealth() refetch
                            // is now the source of truth for its `isActive`
                            // state, but also return the parent's refresh
                            // promise so the button stays busy/disabled until
                            // BOTH the node list and the per-node health probe
                            // have settled.
                            return typeof onRefresh === 'function' ? Promise.resolve(onRefresh()) : undefined;
                          }}
                          onError={err => setToast({ tone: 'error', msg: `串流指令失敗: ${err?.message || err}` })}/>
                      )}
                      <SnoozeRowButton
                        node={n}
                        onDone={(node, minutes) => {
                          setToast({
                            tone: 'success',
                            msg: minutes === 'unsnooze'
                              ? `${node.name || node.id} 已取消靜音`
                              : `${node.name || node.id} 已靜音 ${minutes} 分鐘`,
                          });
                          return typeof onRefresh === 'function' ? Promise.resolve(onRefresh()) : undefined;
                        }}
                        onError={err => setToast({ tone: 'error', msg: `靜音失敗: ${err?.message || err}` })}/>
                      {n.type === 'webcam' && (
                        <button
                          title={n.clientId
                            ? '撤銷並重新產生 API Key'
                            : '此列缺少所屬用戶端識別碼，無法撤銷 Key'}
                          disabled={!n.clientId}
                          onClick={(e) => {
                            e.stopPropagation();
                            // G1 guard: check the API is actually there BEFORE
                            // prompting — never ask the operator to confirm a
                            // destructive action we cannot perform.
                            const api = window.SDPRS_API;
                            if (!(api && api.revokeWebcamKey)) {
                              setToast({ tone: 'error', msg: '暫時無法連線後端，請稍後再試' });
                              return;
                            }
                            // THE SHIPPED BUG: this used to send n.id — the
                            // CAMERA's id — to an endpoint that only ever knew
                            // client ids, so the 撤銷 Key button 404'd 100% of
                            // the time. The key is the CLIENT's, so rotate it
                            // by the CLIENT's id.
                            if (!n.clientId) {
                              setToast({ tone: 'error', msg: '此列缺少用戶端識別碼，無法撤銷 Key' });
                              return;
                            }
                            if (!confirm('確定要撤銷此 Key？舊 Key 將立即失效。')) return;
                            Promise.resolve(api.revokeWebcamKey(n.clientId))
                              .then(data => {
                                // Fresh key is shown once via a persistent modal,
                                // not the auto-dismissing toast — see revokedKey
                                // state comment above.
                                setRevokedKey({ nodeId: n.clientId, name: n.name, apiKey: (data || {}).api_key });
                              })
                              .catch(err => setToast({ tone: 'error', msg: err.message || '撤銷失敗' }));
                          }}
                          className="w-8 h-8 rounded text-ink-muted hover:text-sev-warn hover:bg-sev-warn/10 transition-colors text-xs disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          🔑
                        </button>
                      )}
                      {n.type === 'webcam' && (
                        <button
                          title={n.clientId
                            ? '刪除此 Webcam 用戶端（含其全部攝影機）'
                            : '此列缺少所屬用戶端識別碼，無法刪除'}
                          disabled={!n.clientId}
                          onClick={(e) => {
                            e.stopPropagation();
                            // Opens the in-app confirm modal below; nothing is
                            // sent until 「確定刪除」 is pressed there. Carry the
                            // CLIENT id (what the endpoint takes) plus every
                            // camera that will go with it, so the dialog can
                            // state the true blast radius.
                            if (!n.clientId) {
                              setToast({ tone: 'error', msg: '此列缺少用戶端識別碼，無法刪除' });
                              return;
                            }
                            setDeleteTarget({
                              clientId: n.clientId,
                              // Display-only; null falls back to the id below.
                              clientName: n.clientName || null,
                              cameras: camerasOfClient(n.clientId),
                            });
                          }}
                          onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); }}
                          className="h-8 px-2 rounded text-[11px] text-ink-muted hover:text-sev-critical hover:bg-sev-critical/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          刪除
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowAddModal(false)}>
          <div className="bg-surface-panel border border-border-subtle rounded-xl p-5 w-96 shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-bold text-ink-primary mb-3">新增 Webcam Client</h3>
            {!createdKey ? (
              <>
                <label className="block text-xs text-ink-secondary mb-1">名稱（如：櫃台電腦）</label>
                <input
                  value={newClientName}
                  onChange={e => setNewClientName(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-surface-base border border-border-subtle text-ink-primary text-sm mb-3"
                  placeholder="輸入名稱..."
                  autoFocus
                />
                <button
                  disabled={addBusy || !newClientName.trim()}
                  onClick={() => {
                    // G1 guard: without it a missing bundle throws inside the
                    // handler and the button latches on 「建立中...」 forever.
                    const api = window.SDPRS_API;
                    if (!(api && api.createWebcamClient)) {
                      setToast({ msg: '暫時無法連線後端，請稍後再試', tone: 'error' });
                      return;
                    }
                    setAddBusy(true);
                    Promise.resolve(api.createWebcamClient(newClientName.trim()))
                      .then(data => setCreatedKey(data))
                      .catch(err => setToast({ msg: err.message || '建立失敗', tone: 'error' }))
                      .finally(() => { if (mountedRef.current) setAddBusy(false); });
                  }}
                  className="w-full py-2 rounded-lg bg-sev-info text-white text-sm font-bold disabled:opacity-50"
                >
                  {addBusy ? '建立中...' : '建立'}
                </button>
              </>
            ) : (
              <>
                <p className="text-xs text-sev-warn font-bold mb-2">⚠ API Key 僅顯示一次，請立即複製</p>
                <div className="bg-surface-base border border-border-subtle rounded-lg p-3 mb-3">
                  <code className="text-xs text-ink-primary break-all select-all">{createdKey.api_key}</code>
                </div>
                <p className="text-xs text-ink-muted mb-3">Node ID: {createdKey.node_id}</p>
                <button
                  onClick={() => { setShowAddModal(false); onRefresh && onRefresh(); }}
                  className="w-full py-2 rounded-lg bg-sev-ok text-white text-sm font-bold"
                >
                  已複製，關閉
                </button>
              </>
            )}
          </div>
        </div>
      )}
      {/* Revoke-key result: same "shown once" contract as the create modal
          above, kept in its own persistent modal (not the 3s toast) so an
          operator has time to actually copy the fresh key before it's gone
          for good. */}
      {revokedKey && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setRevokedKey(null)}>
          <div className="bg-surface-panel border border-border-subtle rounded-xl p-5 w-96 shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-bold text-ink-primary mb-3">API Key 已重新產生</h3>
            <p className="text-xs text-sev-warn font-bold mb-2">⚠ 新 Key 僅顯示一次，請立即複製，並更新 {revokedKey.name || revokedKey.nodeId} 的用戶端設定</p>
            <div className="bg-surface-base border border-border-subtle rounded-lg p-3 mb-3">
              <code className="text-xs text-ink-primary break-all select-all">{revokedKey.apiKey}</code>
            </div>
            <p className="text-xs text-ink-muted mb-3">Node ID: {revokedKey.nodeId}</p>
            <button
              onClick={() => setRevokedKey(null)}
              className="w-full py-2 rounded-lg bg-sev-ok text-white text-sm font-bold"
            >
              已複製，關閉
            </button>
          </div>
        </div>
      )}
      {/* Delete confirmation — same modal shell as the create/revoke panels
          above. Backdrop-dismiss is disabled while the request is in flight so
          a stray click can't hide an operation the operator still owns. */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          role="dialog" aria-modal="true" aria-label="刪除 Webcam Client"
          onClick={() => { if (!deleteBusy) setDeleteTarget(null); }}>
          <div className="bg-surface-panel border border-border-subtle rounded-xl p-5 w-96 shadow-2xl" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-bold text-ink-primary mb-3">刪除 Webcam Client</h3>
            {/* A webcam ROW is one camera, but delete decommissions the whole
                client PC. The operator clicked one row — spell out every
                camera that disappears with it, or this is a destructive
                action with an unstated blast radius. */}
            <p className="text-xs text-ink-secondary mb-1">
              確定要刪除？將移除 Webcam 用戶端「
              <span className={deleteTarget.clientName
                ? 'text-ink-primary font-bold'
                : 'font-mono text-ink-primary'}>{clientLabel(deleteTarget)}</span>
              」及其全部 {(deleteTarget.cameras || []).length} 支攝影機：
            </p>
            <ul className="text-xs text-ink-secondary mb-2 max-h-32 overflow-y-auto pl-1">
              {(deleteTarget.cameras || []).map(c => (
                <li key={c.id} className="leading-5">
                  • <span className="text-ink-primary">{c.name}</span>
                  {' '}<span className="font-mono text-ink-muted text-[10px]">{c.id}</span>
                </li>
              ))}
            </ul>
            <p className="text-xs text-sev-warn font-bold mb-4">⚠ 此用戶端、其全部攝影機與 API Key 將被永久移除，此操作無法復原。用戶端會立即失去連線。</p>
            <div className="flex gap-2">
              <button
                disabled={deleteBusy}
                onClick={() => setDeleteTarget(null)}
                className="flex-1 py-2 rounded-lg bg-surface-elevated border border-border-subtle text-ink-secondary text-sm font-bold disabled:opacity-50"
              >
                取消
              </button>
              <button
                disabled={deleteBusy}
                onClick={confirmDeleteWebcam}
                className="flex-1 py-2 rounded-lg bg-sev-critical text-white text-sm font-bold disabled:opacity-50"
              >
                {deleteBusy ? '刪除中...' : '確定刪除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

Object.assign(window, { StatusPage });
