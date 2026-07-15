// Shared UI components

const { useState, useEffect, useRef, useMemo } = React;

// ---------- Atoms ----------

const Kbd = ({ children }) => <kbd className="kbd noselect">{children}</kbd>;

const SeverityBadge = ({ sev, withLabel = true, size = 'sm' }) => {
  const m = window.sevMeta[sev];
  if (!m) return null;
  const Ico = m.Icon;
  const sz = size === 'md' ? 'text-sm px-2 py-0.5' : 'text-[10px] px-1.5 py-0.5';
  return (
    <span className={`inline-flex items-center gap-1 rounded border font-medium tnum bg-${m.color}/15 text-${m.color} border-${m.color}/30 ${sz}`}>
      <Ico />
      {withLabel && <span>{m.label}</span>}
    </span>
  );
};

const StateBadge = ({ state }) => {
  const m = window.stateMeta[state];
  if (!m) return null;
  return <span className={`inline-flex items-center text-[10px] px-1.5 py-0.5 rounded border font-medium ${m.cls}`}>{m.label}</span>;
};

const AgeCell = ({ sec }) => (
  <span className={`font-mono text-xs tnum ${window.ageColor(sec)}`}>{window.fmtAge(sec)}</span>
);

const Pill = ({ tone = 'neutral', children, dot, pulse, className = '' }) => {
  const tones = {
    neutral: 'bg-surface-elevated text-ink-secondary border-border-strong',
    critical: 'bg-sev-critical/15 text-sev-critical border-sev-critical/40',
    warn: 'bg-sev-warn/15 text-sev-warn border-sev-warn/40',
    info: 'bg-sev-info/15 text-sev-info border-sev-info/40',
    ok: 'bg-sev-ok/15 text-sev-ok border-sev-ok/40',
    muted: 'bg-surface-elevated text-ink-muted border-border-subtle',
  };
  const dotColors = {
    ok: 'bg-sev-ok', warn: 'bg-sev-warn', critical: 'bg-sev-critical', info: 'bg-sev-info', muted: 'bg-ink-muted', neutral: 'bg-ink-muted',
  };
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 h-6 rounded border text-xs font-medium tnum whitespace-nowrap ${tones[tone]} ${className}`}>
      {dot && (
        <span className="relative inline-flex w-1.5 h-1.5">
          <span className={`absolute inset-0 rounded-full ${dotColors[tone]} ${pulse ? 'animate-live-blink' : ''}`}></span>
        </span>
      )}
      {children}
    </span>
  );
};

// ---------- SnapshotImage — live camera frame or icon fallback ----------
// Used by NodeCard tile (pages.jsx), the big monitor wall (app.jsx), and
// the node detail side panel (components.jsx). Each slot needs the same
// behaviour: show a live JPEG for cameras that have uploaded a snapshot,
// fall back to an icon otherwise. Spawns a 1 Hz interval that bumps a
// counter used as the img src cache-buster — decoupled from the /api/nodes
// safety-net poll so refresh matches the edge upload rate. Ticker is gated
// on wantsLiveImg so pump tiles and offline cameras don't run intervals
// they'd only throw away.
// Server encoding: picamera2's misnamed "RGB888" numpy array is already
// B,G,R; the edge adapter passes it straight through to cv2.imencode.
// If colours ever look magenta again, check edge_glass/utils/camera.py.
const SnapshotImage = ({ node, iconSize = 48 }) => {
  const frozen = node.status === 'offline' || node.upload > 60;
  const wantsLiveImg = node.type === 'camera' && !frozen;
  const [tick, setTick] = React.useState(0);
  React.useEffect(() => {
    if (!wantsLiveImg) return;
    const id = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [wantsLiveImg]);
  if (wantsLiveImg && node.snapshotTimestamp) {
    return (
      <img
        src={`/api/edge/${node.id}/snapshot/latest?t=${tick}`}
        alt={`${node.name || node.id} snapshot`}
        className="absolute inset-0 w-full h-full object-cover"
      />
    );
  }
  return (
    <div className="absolute inset-0 flex items-center justify-center text-ink-muted/40">
      {node.type === 'camera' ? <Icon.Camera size={iconSize} strokeWidth={1}/> : <Icon.Droplet size={iconSize} strokeWidth={1}/>}
    </div>
  );
};

// ---------- Detector Health — visual + audio detector status (cameras only) ----------
// Surfaces an "online but unable to alert" camera: blinded/paused vision or a
// dead/stale mic. Renders nothing for pump nodes.
const DetectorHealth = ({ node }) => {
  if (!node || node.type !== 'camera') return null;
  const meta = window.detectorHealthMeta || {};
  const fallback = meta.unknown || { label: '未知', tone: 'muted' };
  const v = meta[node.visualHealth] || fallback;
  const a = meta[node.audioHealth] || fallback;
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      <Pill tone={v.tone} dot><span className="text-ink-muted">視覺</span> {v.label}</Pill>
      <Pill tone={a.tone} dot><span className="text-ink-muted">音訊</span> {a.label}</Pill>
    </div>
  );
};

// ---------- Drift Meter — segmented dot rail for live connection ----------

const DriftMeter = ({ sec, max = 30 }) => {
  const n = 15;
  const lit = Math.min(n, Math.ceil((sec / max) * n));
  const color = sec < 10 ? '#10B981' : sec < 20 ? '#F59E0B' : '#DC2626';
  return (
    <span className="inline-flex items-center gap-px h-3" aria-label={`Connection drift ${sec}s`}>
      {Array.from({length: n}).map((_, i) => (
        <span key={i} className="w-[3px] h-2.5 rounded-[1px] transition-colors" style={{
          background: i < lit ? color : 'rgba(100,116,139,0.25)',
          opacity: i === lit - 1 && sec < 10 ? 1 : (i < lit ? 0.95 : 1),
        }}/>
      ))}
    </span>
  );
};

// ---------- Status Strip ----------

const StatusStrip = ({ liveSec, unackCount, muted, setMuted, theme, setTheme, onOpenShortcuts, page, setPage, onOpenMuteDrawer, audioReplayIn, muteState, operators, staleAckCount, onOpenCmdK, focusMode, onToggleFocus }) => {
  const liveState = liveSec < 10 ? 'ok' : liveSec < 30 ? 'warn' : 'critical';
  const liveLabel = liveSec < 10 ? `Live · ${liveSec}s` : liveSec < 30 ? `Reconnecting… ${liveSec}s` : `Disconnected ${liveSec}s`;
  const tones = { ok: 'bg-sev-ok/15 text-sev-ok border-sev-ok/40', warn: 'bg-sev-warn/15 text-sev-warn border-sev-warn/40', critical: 'bg-sev-critical/15 text-sev-critical border-sev-critical/40' };
  const activeMutes = (muted ? 1 : 0) + (muteState?.nodes?.length || 0) + (muteState?.lightning ? 1 : 0);

  return (
    <div className="h-12 fixed inset-x-0 top-0 z-40 bg-surface-panel border-b border-border-subtle flex items-center px-4 gap-3 noselect">
      {/* Logo + wordmark */}
      <div className="flex items-center gap-2.5 pr-3 border-r border-border-subtle h-full">
        <div className={`w-7 h-7 rounded flex items-center justify-center ${unackCount > 0 ? 'bg-sev-critical/15 text-sev-critical' : 'bg-sev-ok/15 text-sev-ok'}`}>
          <Icon.ShieldAlert size={16} strokeWidth={2}/>
        </div>
        <div className="leading-tight">
          <div className="text-sm font-bold tracking-wider">SDPRS</div>
          <div className="text-[9px] text-ink-muted font-mono -mt-0.5">v2.4 · NOC</div>
        </div>
      </div>

      {/* Live pill with drift meter */}
      <div className="flex items-center gap-2">
        <span className={`inline-flex items-center gap-2 px-2 h-7 rounded border text-xs font-medium tnum whitespace-nowrap ${tones[liveState]}`}>
          <DriftMeter sec={liveSec}/>
          <span>{liveLabel}</span>
        </span>
        {unackCount > 0 && (
          <button onClick={() => setPage('alerts')} className="inline-flex items-center gap-1.5 h-7 px-2 rounded bg-sev-critical text-white text-xs font-semibold tnum hover:bg-red-700 transition-colors whitespace-nowrap">
            <Icon.Bell size={12} strokeWidth={2.5}/>
            <span>未認領 {unackCount}</span>
            {!muted && audioReplayIn != null && audioReplayIn > 0 && (
              <span className="font-mono text-[10px] bg-black/30 px-1 rounded">♪ {audioReplayIn}s</span>
            )}
          </button>
        )}
        {staleAckCount > 0 && <StaleAckPill count={staleAckCount} onClick={() => setPage('alerts')}/>}
      </div>

      {/* Weather chip — center. Only renders when the backend weather service
          is reachable (otherwise the strip stays empty rather than show zeros). */}
      <div className="flex-1 flex justify-center min-w-0">
        {window.WEATHER && window.WEATHER.available && (
          <button onClick={() => setPage('weather')} className="hidden md:flex items-center gap-3 h-7 px-3 rounded border border-border-strong bg-surface-elevated hover:bg-surface-overlay transition-colors text-xs whitespace-nowrap">
            {window.WEATHER.typhoon && (
              <>
                <span className="flex items-center gap-1.5 text-sev-warn">
                  <Icon.Typhoon size={14}/>
                  <span className="font-semibold">颱風 {window.WEATHER.typhoon.name} · {window.WEATHER.typhoon.level}</span>
                </span>
                <span className="text-ink-dim">|</span>
              </>
            )}
            <span className="flex items-center gap-1 text-ink-secondary tnum">
              <Icon.Wind size={12}/>
              <span className="font-mono">{window.WEATHER.wind.dir || ''} {window.WEATHER.wind.speed}<span className="text-ink-muted">km/h</span></span>
            </span>
            <span className="text-ink-dim">|</span>
            <span className="flex items-center gap-1 text-ink-secondary tnum">
              <Icon.CloudRain size={12}/>
              <span className="font-mono">{window.WEATHER.rain.now}<span className="text-ink-muted">mm/h</span></span>
            </span>
            {window.WEATHER.lightning && window.WEATHER.lightning.count > 0 && (
              <>
                <span className="text-ink-dim">|</span>
                <span className="flex items-center gap-1 text-sev-warn tnum">
                  <Icon.Zap size={12}/>
                  <span className="font-mono">{window.WEATHER.lightning.count}<span className="text-ink-muted">/h{window.WEATHER.lightning.nearest != null ? ' · ' + window.WEATHER.lightning.nearest + 'km' : ''}</span></span>
                </span>
              </>
            )}
          </button>
        )}
      </div>

      {/* Right cluster */}
      <div className="flex items-center gap-1">
        {operators && operators.length > 1 && <OperatorsCluster operators={operators} currentUser="alice.chen"/>}
        <button onClick={onOpenCmdK} title="命令面板 (⌘K / Ctrl+K)" className="hidden md:flex items-center gap-1 h-7 px-2 ml-1 rounded border border-border-subtle bg-surface-elevated hover:bg-surface-overlay text-xs text-ink-muted transition-colors">
          <Icon.Search size={12}/> <span>跳轉...</span> <Kbd>⌘K</Kbd>
        </button>
        <button onClick={onToggleFocus} title="夜深 / 專注模式 (Ctrl+.)"
          className={`w-8 h-8 rounded flex items-center justify-center transition-colors ${focusMode ? 'text-sev-info bg-sev-info/10' : 'text-ink-muted hover:text-ink-primary hover:bg-surface-elevated'}`}>
          <Icon.Moon size={16}/>
        </button>
        <button onClick={onOpenShortcuts} title="鍵盤捷徑 (?)" className="w-8 h-8 rounded flex items-center justify-center text-ink-muted hover:text-ink-primary hover:bg-surface-elevated transition-colors">
          <Icon.Keyboard size={16}/>
        </button>
        <button onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} title="Theme (T)" className="w-8 h-8 rounded flex items-center justify-center text-ink-muted hover:text-ink-primary hover:bg-surface-elevated transition-colors">
          {theme === 'dark' ? <Icon.Moon size={16}/> : <Icon.Sun size={16}/>}
        </button>
        <button
          onClick={onOpenMuteDrawer}
          title={`音效 (M) — ${activeMutes} 個來源已抑制`}
          className={`relative w-8 h-8 rounded flex items-center justify-center transition-colors ${activeMutes > 0 ? 'text-sev-warn hover:bg-sev-warn/10' : 'text-ink-muted hover:text-ink-primary hover:bg-surface-elevated'}`}
        >
          {muted ? <Icon.VolumeX size={16}/> : <Icon.Volume2 size={16}/>}
          {activeMutes > 0 && <span className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-sev-warn text-[9px] font-bold text-black flex items-center justify-center tnum">{activeMutes}</span>}
        </button>
        <div className="w-px h-6 bg-border-subtle mx-1"></div>
        <button onClick={() => { if (confirm('登出?')) window.location.href = '/logout'; }} className="flex items-center gap-2 h-8 pl-1 pr-2 rounded hover:bg-surface-elevated transition-colors" title="點擊登出">
          <div className="w-6 h-6 rounded-full bg-gradient-to-br from-sev-info to-purple-500 flex items-center justify-center text-[10px] font-semibold text-white">
            {(window.SDPRS_USER || '?').slice(0, 2).toUpperCase()}
          </div>
          <div className="text-left leading-tight">
            <div className="text-xs font-medium">{window.SDPRS_USER || '—'}</div>
            <div className="text-[10px] text-ink-muted font-mono tnum">已登入</div>
          </div>
          <Icon.ChevronDown size={12} className="text-ink-muted"/>
        </button>
      </div>
    </div>
  );
};

// ---------- Nav Rail ----------

const NAV_ITEMS = [
  { id: 'alerts',   label: '警報',     hotkey: '1', Icon: Icon.AlertTriangle, badge: 'unack' },
  { id: 'monitor',  label: '監看牆',   hotkey: '2', Icon: Icon.Grid },
  { id: 'status',   label: '節點狀態', hotkey: '3', Icon: Icon.Server, badge: 'offline' },
  { id: 'pumps',    label: '抽水站',   hotkey: '4', Icon: Icon.Pump },
  { id: 'weather',  label: '天氣',     hotkey: '5', Icon: Icon.CloudRain },
  { id: 'handover', label: '交接',     hotkey: '6', Icon: Icon.ClipboardList },
  { id: 'audit',    label: '稽核',     hotkey: '7', Icon: Icon.FileSearch },
];

const NavRail = ({ page, setPage, density, setDensity, unackCount, offlineCount }) => {
  return (
    <nav className="w-56 fixed left-0 top-12 bottom-10 bg-surface-panel border-r border-border-subtle flex flex-col noselect">
      <div className="px-3 py-2 text-[10px] uppercase tracking-widest text-ink-muted font-semibold">操作站</div>
      <div className="flex-1 px-2 space-y-0.5 overflow-y-auto scroll-thin">
        {NAV_ITEMS.map(item => {
          const active = page === item.id;
          const Ico = item.Icon;
          const badgeVal = item.badge === 'unack' ? unackCount : item.badge === 'offline' ? offlineCount : null;
          return (
            <button
              key={item.id}
              onClick={() => setPage(item.id)}
              className={`w-full flex items-center gap-2.5 h-9 px-2.5 rounded text-sm transition-colors group relative ${active ? 'bg-surface-elevated text-ink-primary' : 'text-ink-secondary hover:bg-surface-elevated/60 hover:text-ink-primary'}`}
            >
              {active && <span className="absolute left-0 top-1.5 bottom-1.5 w-0.5 bg-sev-info rounded-r"></span>}
              <Ico size={18} strokeWidth={active ? 2 : 1.5}/>
              <span className="flex-1 text-left">{item.label}</span>
              {badgeVal > 0 && (
                <span className={`text-[10px] font-bold tnum px-1.5 h-4 inline-flex items-center rounded ${item.badge === 'unack' ? 'bg-sev-critical text-white' : 'bg-sev-warn/20 text-sev-warn'}`}>{badgeVal}</span>
              )}
              <Kbd>{item.hotkey}</Kbd>
            </button>
          );
        })}
      </div>

      <div className="border-t border-border-subtle p-2 space-y-1.5">
        <div className="text-[10px] uppercase tracking-widest text-ink-muted font-semibold px-1">密度</div>
        <div className="flex bg-surface-base border border-border-subtle rounded p-0.5">
          {['compact','comfortable'].map(d => (
            <button key={d} onClick={() => setDensity(d)}
              className={`flex-1 text-[11px] py-1 rounded transition-colors ${density === d ? 'bg-surface-overlay text-ink-primary' : 'text-ink-muted hover:text-ink-secondary'}`}>
              {d === 'compact' ? '緊湊' : '舒適'}
            </button>
          ))}
        </div>
        <div className="text-[10px] text-ink-muted font-mono px-1 pt-1 flex justify-between">
          <span>build 2026.05.18-r4</span>
          <span className="text-sev-ok">●</span>
        </div>
      </div>
    </nav>
  );
};

// ---------- Footer ----------

const Sparkline = ({ data, width = 240, height = 28 }) => {
  const max = Math.max(...data, 1);
  const barW = width / data.length;
  const avg = data.reduce((a,b)=>a+b,0)/data.length;
  const cur = data[data.length-1];
  const surge = cur > avg * 2;
  return (
    <div className="flex items-end gap-px h-7" style={{ width, height }}>
      {data.map((v, i) => {
        const h = Math.max(2, (v / max) * (height - 2));
        const isLast = i === data.length - 1;
        return (
          <div key={i} className={`flex-1 ${surge && i >= data.length - 4 ? 'bg-sev-critical' : isLast ? 'bg-sev-info' : 'bg-sev-info/60'} rounded-sm`} style={{ height: h + 'px' }} title={`${v} alerts`}/>
        );
      })}
    </div>
  );
};

const Footer = ({ data, handover }) => {
  const avg = data.reduce((a,b)=>a+b,0)/data.length;
  const cur = data[data.length-1];
  const surge = cur > avg * 2;
  const ageMin = handover.ageMin ?? 0;
  const ageH = (ageMin / 60).toFixed(1);
  const ageTone = ageMin > 720 ? 'critical' : ageMin > 240 ? 'warn' : 'ok';
  const ageCls = ageTone === 'critical' ? 'text-sev-critical bg-sev-critical/15 border border-sev-critical/40' : ageTone === 'warn' ? 'text-ink-muted' : 'text-ink-muted';
  return (
    <div className="h-10 fixed inset-x-0 bottom-0 z-30 bg-surface-panel border-t border-border-subtle flex items-center px-4 gap-4 text-xs noselect">
      <div className="flex items-center gap-2.5">
        <Icon.Activity size={14} className="text-ink-muted"/>
        <span className="text-ink-muted">警報率</span>
        <Sparkline data={data} />
        <span className="font-mono tnum text-ink-secondary"><span className="text-ink-muted">15min × 16</span></span>
        {surge && (
          <Pill tone="critical" className="!h-5 !text-[10px]"><Icon.ArrowUp size={10} strokeWidth={2.5}/> 加劇中 · {(cur/avg).toFixed(1)}× 均值</Pill>
        )}
      </div>
      <div className="flex-1"></div>
      <div className="flex items-center gap-2 max-w-[720px] min-w-0">
        <Icon.ClipboardList size={14} className="text-sev-warn flex-shrink-0"/>
        <span className="text-ink-muted whitespace-nowrap">上一班備註:</span>
        <span className="font-mono text-ink-dim text-[11px] tnum">{handover.by} @ {handover.at}</span>
        <span className={`inline-flex items-center px-1 h-4 rounded font-mono text-[10px] tnum flex-shrink-0 ${ageCls}`}>
          {ageMin < 60 ? `${ageMin}m 前` : `${ageH}h 前`}
        </span>
        <span className="text-ink-secondary truncate">"{handover.text}"</span>
        <button className="text-ink-muted hover:text-ink-primary p-1 -m-1 flex-shrink-0"><Icon.Edit3 size={12}/></button>
      </div>
    </div>
  );
};

// ---------- Shortcuts Modal ----------

const SHORTCUTS = [
  { keys: ['/'], label: '搜尋', cat: '導覽' },
  { keys: ['1','2','3','4','5','6','7'], label: '切換頁面', cat: '導覽' },
  { keys: ['A'], label: '認領並前往下一筆', cat: '警報處置' },
  { keys: ['Shift','A'], label: '認領但停留', cat: '警報處置' },
  { keys: ['R'], label: '解決選取的警報', cat: '警報處置' },
  { keys: ['S'], label: '延期所選節點', cat: '警報處置' },
  { keys: ['N'], label: '跳至下一筆未認領', cat: '警報處置' },
  { keys: ['1','...','6'], label: '套用解決模板', cat: '警報處置' },
  { keys: ['M'], label: '開啟音效抑制面板', cat: '全域' },
  { keys: ['T'], label: '切換主題', cat: '全域' },
  { keys: ['Shift','D'], label: '切換密度', cat: '全域' },
  { keys: ['F'], label: '切換全螢幕 (監看牆)', cat: '監看' },
  { keys: ['Esc'], label: '關閉詳情/對話框', cat: '全域' },
  { keys: ['↑','↓'], label: '上下移動列表', cat: '警報處置' },
  { keys: ['Enter'], label: '開啟所選列詳情', cat: '警報處置' },
  { keys: ['?'], label: '顯示此說明', cat: '全域' },
];

const ShortcutsModal = ({ open, onClose }) => {
  const [q, setQ] = useState('');
  if (!open) return null;
  const matches = SHORTCUTS.filter(s =>
    !q || s.label.toLowerCase().includes(q.toLowerCase()) || s.keys.some(k => k.toLowerCase().includes(q.toLowerCase())) || s.cat.toLowerCase().includes(q.toLowerCase())
  );
  const byCat = matches.reduce((acc, s) => { (acc[s.cat] = acc[s.cat] || []).push(s); return acc; }, {});
  return (
    <div className="fixed inset-0 z-50 bg-surface-base/80 backdrop-blur-sm flex items-center justify-center p-6" onClick={onClose}>
      <div className="bg-surface-panel border border-border-strong rounded-lg max-w-2xl w-full" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-border-subtle">
          <h2 className="text-base font-semibold flex items-center gap-2"><Icon.Keyboard size={18}/> 鍵盤捷徑</h2>
          <button onClick={onClose} className="text-ink-muted hover:text-ink-primary"><Icon.X size={18}/></button>
        </div>
        <div className="px-5 pt-3 pb-2 border-b border-border-subtle">
          <div className="relative">
            <Icon.Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-muted"/>
            <input
              autoFocus
              value={q} onChange={e => setQ(e.target.value)}
              placeholder="搜尋捷徑或動作..."
              className="w-full h-9 pl-8 pr-3 bg-surface-base border border-border-subtle rounded text-sm placeholder-ink-muted focus:border-sev-info focus:outline-none"
            />
          </div>
        </div>
        <div className="p-5 max-h-[420px] overflow-y-auto scroll-thin">
          {Object.keys(byCat).length === 0 ? (
            <div className="text-center text-sm text-ink-muted py-6">找不到符合的捷徑</div>
          ) : (
            Object.entries(byCat).map(([cat, list]) => (
              <div key={cat} className="mb-4 last:mb-0">
                <div className="text-[10px] uppercase tracking-widest text-ink-muted font-semibold mb-1.5">{cat}</div>
                <div className="grid grid-cols-2 gap-x-8 gap-y-1">
                  {list.map((s, i) => (
                    <div key={i} className="flex items-center justify-between py-1 border-b border-border-subtle/40">
                      <span className="text-sm text-ink-secondary">{s.label}</span>
                      <span className="flex items-center gap-1">{s.keys.map((k, j) => <React.Fragment key={j}>{j > 0 && k !== '...' && s.keys[j-1] !== '...' && <span className="text-ink-dim text-[10px]">+</span>}<Kbd>{k}</Kbd></React.Fragment>)}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
        <div className="px-5 py-3 border-t border-border-subtle text-xs text-ink-muted flex items-center justify-between">
          <span>按 <Kbd>Esc</Kbd> 關閉</span>
          <span className="font-mono">SDPRS v2.4 · zh-TW</span>
        </div>
      </div>
    </div>
  );
};

// ---------- Mute Drawer ----------

const MuteDrawer = ({ open, onClose, muteState, setMuteState }) => {
  if (!open) return null;
  const activeCount = (muteState.global ? 1 : 0) + muteState.nodes.length + (muteState.lightning ? 1 : 0);
  const playTest = (kind) => {
    // mock audio test — visual feedback only
    const node = document.getElementById('test-audio-feedback');
    if (node) {
      node.textContent = `▶ 播放測試: ${kind}`;
      setTimeout(() => { if (node) node.textContent = ''; }, 1500);
    }
  };
  return (
    <div className="fixed inset-0 z-50 bg-surface-base/60 backdrop-blur-sm flex justify-end" onClick={onClose}>
      <div className="w-[380px] h-full bg-surface-panel border-l border-border-strong overflow-y-auto scroll-thin" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-border-subtle sticky top-0 bg-surface-panel z-10">
          <h2 className="text-base font-semibold flex items-center gap-2">
            <Icon.VolumeX size={18} className={activeCount > 0 ? 'text-sev-warn' : ''}/>
            音效抑制 / 音量
          </h2>
          <button onClick={onClose} className="text-ink-muted hover:text-ink-primary"><Icon.X size={18}/></button>
        </div>

        <div className="p-5 space-y-4">
          {/* Volume slider */}
          <div>
            <div className="text-[10px] uppercase tracking-widest text-ink-muted font-semibold mb-2">音量</div>
            <div className="bg-surface-elevated border border-border-subtle rounded p-3">
              <VolumeSlider value={muteState.volume ?? 70} onChange={(v) => setMuteState({ ...muteState, volume: v })}/>
              <div className="flex items-center justify-between mt-3 text-xs">
                <span className="text-ink-muted">測試音效:</span>
                <div className="flex gap-1">
                  <button onClick={() => playTest('嚴重')} className="px-2 h-6 bg-sev-critical/15 text-sev-critical border border-sev-critical/30 rounded text-[10px] font-medium hover:bg-sev-critical/25">嚴重</button>
                  <button onClick={() => playTest('警告')} className="px-2 h-6 bg-sev-warn/15 text-sev-warn border border-sev-warn/30 rounded text-[10px] font-medium hover:bg-sev-warn/25">警告</button>
                  <button onClick={() => playTest('確認')} className="px-2 h-6 bg-sev-info/15 text-sev-info border border-sev-info/30 rounded text-[10px] font-medium hover:bg-sev-info/25">確認</button>
                </div>
              </div>
              <div id="test-audio-feedback" className="text-[10px] text-sev-info font-mono tnum mt-1 h-4"></div>
            </div>
          </div>

          {activeCount > 0 && (
            <div className="px-3 py-2 bg-sev-warn/10 border border-sev-warn/30 rounded">
              <div className="flex items-center gap-2 text-sev-warn">
                <Icon.AlertCircle size={14}/>
                <span className="text-xs font-medium">目前有 {activeCount} 個音效來源被抑制</span>
              </div>
            </div>
          )}

          {/* Global */}
          <div>
            <div className="text-[10px] uppercase tracking-widest text-ink-muted font-semibold mb-2">全域</div>
            <div className="flex items-center justify-between bg-surface-elevated border border-border-subtle rounded p-3">
              <div>
                <div className="text-sm font-medium">全域靜音</div>
                <div className="text-xs text-ink-muted mt-0.5">影響所有警報音 (操作確認音不受影響)</div>
              </div>
              <button
                onClick={() => setMuteState({ ...muteState, global: !muteState.global })}
                className={`px-2.5 h-6 rounded text-xs font-medium ${muteState.global ? 'bg-sev-warn text-black' : 'bg-surface-overlay text-ink-muted'}`}
              >
                {muteState.global ? '靜音中' : '正常'}
              </button>
            </div>
          </div>

          {/* Per-node snooze */}
          <div>
            <div className="text-[10px] uppercase tracking-widest text-ink-muted font-semibold mb-2">節點延期 ({muteState.nodes.length})</div>
            {muteState.nodes.length === 0 ? (
              <div className="text-xs text-ink-muted text-center py-3 border border-dashed border-border-subtle rounded">無節點延期中</div>
            ) : (
              <div className="space-y-1.5">
                {muteState.nodes.map(nid => {
                  const n = window.NODES.find(nn => nn.id === nid);
                  return (
                    <div key={nid} className="flex items-center gap-2 bg-surface-elevated border border-border-subtle rounded p-2.5">
                      <div className="flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono text-sm font-semibold">{nid}</span>
                          <span className="text-xs text-ink-secondary">{n?.name}</span>
                        </div>
                        <div className="text-[10px] text-ink-muted font-mono tnum mt-0.5">剩餘 {n?.snoozeMin || 22} 分鐘 · alice 於 02:14 設定</div>
                      </div>
                      <button
                        onClick={() => setMuteState({ ...muteState, nodes: muteState.nodes.filter(x => x !== nid) })}
                        className="text-ink-muted hover:text-ink-primary text-xs px-2 h-6 rounded bg-surface-overlay"
                      >解除</button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Lightning */}
          <div>
            <div className="text-[10px] uppercase tracking-widest text-ink-muted font-semibold mb-2">天氣觸發</div>
            <div className="flex items-center justify-between bg-surface-elevated border border-border-subtle rounded p-3">
              <div className="flex-1">
                <div className="text-sm font-medium flex items-center gap-1.5">
                  <Icon.Zap size={12} className="text-sev-warn"/>
                  雷擊自動靜音
                </div>
                <div className="text-xs text-ink-muted mt-0.5">10km 內偵測到雷擊時自動抑制</div>
                {muteState.lightning && (
                  <div className="text-[10px] text-sev-warn mt-1 font-mono tnum">● 觸發中 — 最近雷擊 18km</div>
                )}
              </div>
              <button
                onClick={() => setMuteState({ ...muteState, lightning: !muteState.lightning })}
                className={`px-2.5 h-6 rounded text-xs font-medium ${muteState.lightning ? 'bg-sev-warn text-black' : 'bg-surface-overlay text-ink-muted'}`}
              >
                {muteState.lightning ? '啟用' : '停用'}
              </button>
            </div>
          </div>

          <button
            onClick={() => setMuteState({ global: false, nodes: [], lightning: false, volume: muteState.volume ?? 70 })}
            className="w-full mt-2 h-9 bg-sev-info hover:bg-blue-600 text-white rounded text-sm font-semibold"
          >
            全部解除
          </button>
        </div>
      </div>
    </div>
  );
};

// ---------- Empty State ----------

const EmptyState = ({ icon: IconComp = Icon.ShieldCheck, title, hint }) => (
  <div className="flex flex-col items-center justify-center text-center py-16 px-6">
    <div className="w-14 h-14 rounded-full bg-surface-elevated flex items-center justify-center text-ink-muted mb-3">
      <IconComp size={28}/>
    </div>
    <div className="text-base text-ink-secondary font-medium">{title}</div>
    {hint && <div className="text-xs text-ink-muted mt-1 font-mono tnum">{hint}</div>}
  </div>
);

Object.assign(window, {
  Kbd, SeverityBadge, StateBadge, AgeCell, Pill, DetectorHealth, DriftMeter,
  StatusStrip, NavRail, Footer, Sparkline, ShortcutsModal, EmptyState, MuteDrawer,
  NAV_ITEMS,
});

// ===================================================================
// NEW COMPONENTS — added for daily-user feedback enhancements
// ===================================================================

// ---------- Operators Online Cluster (status strip) ----------

const OperatorsCluster = ({ operators, currentUser }) => {
  return (
    <div className="flex items-center gap-1 h-6 px-1.5 rounded border border-border-subtle bg-surface-elevated">
      <span className="text-[10px] text-ink-muted">線上</span>
      <div className="flex -space-x-1">
        {operators.map(op => (
          <div key={op.id}
            title={`${op.name} · ${op.status === 'active' ? '活躍' : `閒置 ${op.lastSeen}s`}`}
            className={`relative w-5 h-5 rounded-full border-2 border-surface-panel flex items-center justify-center text-[9px] font-bold ${op.id === currentUser ? 'bg-gradient-to-br from-sev-info to-purple-500 text-white' : 'bg-gradient-to-br from-emerald-600 to-teal-500 text-white'}`}>
            {op.initials}
            <span className={`absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full border border-surface-panel ${op.status === 'active' ? 'bg-sev-ok' : 'bg-ink-muted'}`}></span>
          </div>
        ))}
      </div>
      <span className="text-[10px] font-mono tnum text-ink-secondary ml-0.5">{operators.length}</span>
    </div>
  );
};

// ---------- Stale Ack Pill (warns when ack'd alerts age) ----------

const StaleAckPill = ({ count, onClick }) => {
  if (!count) return null;
  return (
    <button onClick={onClick}
      className="inline-flex items-center gap-1.5 h-6 px-2 rounded border border-sev-warn/40 bg-sev-warn/10 text-sev-warn text-xs font-medium hover:bg-sev-warn/20 transition-colors whitespace-nowrap">
      <Icon.Clock size={12}/> <span className="tnum">逾期認領 {count}</span>
    </button>
  );
};

// ---------- New Alert Banner (floating, when scrolled past) ----------

const NewAlertBanner = ({ count, onClick }) => {
  if (!count) return null;
  return (
    <button onClick={onClick}
      className="new-alert-banner fixed top-16 left-1/2 -translate-x-1/2 z-30 inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-sev-critical text-white shadow-2xl border border-sev-critical hover:bg-red-700 transition-colors">
      <Icon.ArrowUp size={14} strokeWidth={2.5}/>
      <span className="text-sm font-semibold tnum">{count} 新警報</span>
      <Kbd>↑</Kbd>
    </button>
  );
};

// ---------- Shift Banner (start/end of shift) ----------

const ShiftBanner = ({ shiftSummary, onDismiss }) => (
  <div className="fixed top-14 right-4 z-40 w-[360px] bg-surface-panel border border-sev-info/40 rounded-lg shadow-2xl overflow-hidden">
    <div className="px-4 py-2.5 bg-sev-info/15 border-b border-sev-info/30 flex items-center justify-between">
      <div className="flex items-center gap-2 text-sev-info">
        <Icon.ClipboardList size={14}/>
        <span className="text-sm font-semibold">班次接班摘要 · alice.chen</span>
      </div>
      <button onClick={onDismiss} className="text-ink-muted hover:text-ink-primary"><Icon.X size={14}/></button>
    </div>
    <div className="p-4 space-y-3">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-ink-muted">上一班次承接</div>
        <div className="grid grid-cols-3 gap-2 mt-1.5">
          <div className="bg-surface-elevated rounded p-2 border border-border-subtle">
            <div className="text-xl font-mono font-bold tnum text-sev-info">2</div>
            <div className="text-[10px] text-ink-muted">已認領未解決</div>
          </div>
          <div className="bg-surface-elevated rounded p-2 border border-border-subtle">
            <div className="text-xl font-mono font-bold tnum text-sev-warn">1</div>
            <div className="text-[10px] text-ink-muted">節點延期中</div>
          </div>
          <div className="bg-surface-elevated rounded p-2 border border-border-subtle">
            <div className="text-xl font-mono font-bold tnum text-sev-critical">5</div>
            <div className="text-[10px] text-ink-muted">未處理 (新)</div>
          </div>
        </div>
      </div>
      <div className="bg-sev-warn/10 border border-sev-warn/30 rounded p-2.5 text-xs">
        <div className="text-sev-warn font-semibold mb-1 flex items-center gap-1">
          <Icon.AlertCircle size={12}/> 上一班重點
        </div>
        <p className="text-ink-secondary leading-relaxed">G-03 攝影機鬆動已派工 · P-02 高頻啟動需注意 · 颱風持續中</p>
      </div>
      <button className="w-full h-8 bg-sev-info text-white rounded text-xs font-semibold hover:bg-blue-600">
        檢視完整交接紀錄 →
      </button>
    </div>
  </div>
);

// ---------- Command Palette (Cmd+K) ----------

const CommandPalette = ({ open, onClose, alerts, onSelectAlert, onNav, onCmd }) => {
  const [q, setQ] = useState('');
  const [hi, setHi] = useState(0);

  React.useEffect(() => {
    if (open) { setQ(''); setHi(0); }
  }, [open]);

  if (!open) return null;

  // Build searchable items
  const items = [
    ...window.NAV_ITEMS.map(n => ({ kind: 'nav', id: n.id, label: `頁面: ${n.label}`, hint: `Hotkey ${n.hotkey}`, icon: n.Icon })),
    ...alerts.map(a => ({ kind: 'alert', id: a.id, label: `${a.id} · ${window.alertTypeLabel(a.type)}`, hint: `${a.node} · ${a.state}`, sev: a.sev })),
    ...window.NODES.map(n => ({ kind: 'node', id: n.id, label: `節點: ${n.id} · ${n.name}`, hint: n.location, status: n.status })),
    { kind: 'cmd', id: 'mute-all', label: '指令: 開啟音效抑制面板', hint: 'M', icon: Icon.VolumeX },
    { kind: 'cmd', id: 'focus-mode', label: '指令: 切換夜深 / 專注模式', hint: 'Ctrl+.', icon: Icon.Moon },
    { kind: 'cmd', id: 'density', label: '指令: 切換密度', hint: 'Shift+D', icon: Icon.Grid },
    { kind: 'cmd', id: 'shortcuts', label: '指令: 顯示鍵盤捷徑', hint: '?', icon: Icon.Keyboard },
    { kind: 'cmd', id: 'audit-me', label: '指令: 稽核 · 僅我的動作', hint: '', icon: Icon.User },
  ];

  const matches = q
    ? items.filter(it => (it.label + ' ' + (it.hint || '') + ' ' + it.id).toLowerCase().includes(q.toLowerCase()))
    : items.slice(0, 15);

  const fire = (it) => {
    if (it.kind === 'nav') onNav(it.id);
    else if (it.kind === 'alert') { onNav('alerts'); onSelectAlert(it.id); }
    else if (it.kind === 'node') { onNav('status'); }
    else if (it.kind === 'cmd') onCmd(it.id);
    onClose();
  };

  const onKey = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi(h => Math.min(matches.length - 1, h + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi(h => Math.max(0, h - 1)); }
    else if (e.key === 'Enter') { e.preventDefault(); matches[hi] && fire(matches[hi]); }
  };

  return (
    <div className="fixed inset-0 z-50 bg-surface-base/60 backdrop-blur-sm flex items-start justify-center pt-24" onClick={onClose}>
      <div className="w-[640px] max-w-[90vw] bg-surface-panel border border-border-strong rounded-lg cmdk-shadow overflow-hidden" onClick={e => e.stopPropagation()}>
        <div className="flex items-center px-3 border-b border-border-subtle">
          <Icon.Search size={16} className="text-ink-muted"/>
          <input
            autoFocus
            value={q}
            onChange={e => { setQ(e.target.value); setHi(0); }}
            onKeyDown={onKey}
            placeholder="輸入頁面、警報 ID、節點、指令... (↑↓ 選擇 · Enter 開啟)"
            className="flex-1 h-11 px-3 bg-transparent text-sm placeholder-ink-muted focus:outline-none"
          />
          <Kbd>Esc</Kbd>
        </div>
        <div className="max-h-[60vh] overflow-y-auto scroll-thin py-1">
          {matches.length === 0 ? (
            <div className="text-center text-sm text-ink-muted py-8">找不到符合的項目</div>
          ) : (
            matches.map((it, i) => {
              const Ico = it.icon;
              return (
                <button key={`${it.kind}-${it.id}-${i}`}
                  onClick={() => fire(it)}
                  onMouseEnter={() => setHi(i)}
                  className={`w-full px-4 py-2 flex items-center gap-3 text-left ${hi === i ? 'bg-sev-info/10' : ''}`}
                >
                  <div className="w-6 h-6 rounded bg-surface-elevated flex items-center justify-center text-ink-muted flex-shrink-0">
                    {Ico ? <Ico size={14}/> : it.kind === 'alert' ? <Icon.AlertTriangle size={14}/> : <Icon.Server size={14}/>}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-ink-primary truncate">{it.label}</div>
                    {it.hint && <div className="text-[11px] text-ink-muted font-mono tnum truncate">{it.hint}</div>}
                  </div>
                  {it.sev && <SeverityBadge sev={it.sev} withLabel={false}/>}
                  {it.kind === 'node' && it.status && <span className={`w-2 h-2 rounded-full bg-sev-${it.status === 'offline' || it.status === 'critical' ? 'critical' : it.status === 'warn' ? 'warn' : 'ok'}`}></span>}
                  <span className="text-[10px] text-ink-dim uppercase font-mono tnum w-12 text-right flex-shrink-0">{it.kind}</span>
                </button>
              );
            })
          )}
        </div>
        <div className="px-4 py-2 border-t border-border-subtle flex items-center justify-between text-[10px] text-ink-muted">
          <span className="flex items-center gap-2">
            <Kbd>↑</Kbd><Kbd>↓</Kbd> 選擇
            <span>·</span>
            <Kbd>↵</Kbd> 開啟
            <span>·</span>
            <Kbd>Esc</Kbd> 關閉
          </span>
          <span className="font-mono">{matches.length} 項</span>
        </div>
      </div>
    </div>
  );
};

// ---------- Node Detail Side Panel (from monitor wall / status) ----------

const NodeSidePanel = ({ node, onClose, onJumpAlert, openAlerts, onUpdateNode }) => {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(null);
  React.useEffect(() => {
    if (node) {
      setDraft({ name: node.name, floor: node.floor || '', area: node.area || '', location: node.location });
      setEditing(false);
    }
  }, [node?.id]);

  if (!node) return null;
  const nodeAlerts = openAlerts.filter(a => a.node === node.id);
  const history = window.NODE_HISTORY[node.id] || [];

  const saveEdits = () => {
    const newLocation = draft.floor && draft.area ? `${draft.floor} · ${draft.area}` : (draft.location || node.location);
    onUpdateNode && onUpdateNode(node.id, { name: draft.name, floor: draft.floor, area: draft.area, location: newLocation });
    setEditing(false);
  };

  const FLOORS = ['B3F', 'B2F', 'B1F', '1F', '2F', '3F', '4F', '5F', 'RF'];

  return (
    <div className="fixed inset-0 z-50 bg-surface-base/40 flex justify-end" onClick={onClose}>
      <div className="w-[420px] h-full bg-surface-panel border-l border-border-strong overflow-y-auto scroll-thin" onClick={e => e.stopPropagation()}>
        <div className="px-4 py-3 border-b border-border-subtle sticky top-0 bg-surface-panel z-10 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="font-mono text-base font-bold">{node.id}</span>
            <span className={`w-2 h-2 rounded-full bg-sev-${node.status === 'offline' || node.status === 'critical' ? 'critical' : node.status === 'warn' ? 'warn' : 'ok'}`}></span>
            <span className="text-sm text-ink-secondary">{node.name}</span>
          </div>
          <button onClick={onClose} className="text-ink-muted hover:text-ink-primary"><Icon.X size={18}/></button>
        </div>
        <div className="p-4 space-y-4">
          {/* Snapshot — live JPEG (camera + fresh frame) or fallback icon */}
          <div className="relative aspect-video bg-surface-base border border-border-subtle rounded overflow-hidden snapshot-placeholder">
            <SnapshotImage node={node}/>
            <div className="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/80 to-transparent p-2">
              <div className="text-[10px] text-white/80 font-mono tnum">{node.location}</div>
            </div>
          </div>

          {/* Node Config — editable */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <div className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold">節點配置</div>
              {!editing ? (
                <button onClick={() => setEditing(true)} className="text-[10px] text-sev-info hover:underline inline-flex items-center gap-1">
                  <Icon.Edit3 size={10}/> 編輯
                </button>
              ) : (
                <div className="flex gap-1">
                  <button onClick={() => setEditing(false)} className="text-[10px] text-ink-muted hover:text-ink-primary px-1.5 h-5 rounded bg-surface-elevated">取消</button>
                  <button onClick={saveEdits} className="text-[10px] text-white px-1.5 h-5 rounded bg-sev-info hover:bg-blue-600">儲存</button>
                </div>
              )}
            </div>
            <div className="bg-surface-elevated border border-border-subtle rounded p-2.5 space-y-2">
              {editing ? (
                <>
                  <div>
                    <label className="text-[10px] text-ink-muted block mb-0.5">顯示名稱</label>
                    <input value={draft.name} onChange={e => setDraft({...draft, name: e.target.value})}
                      className="w-full h-7 px-2 text-xs bg-surface-base border border-border-strong rounded focus:border-sev-info focus:outline-none"/>
                  </div>
                  <div className="grid grid-cols-[80px_1fr] gap-2">
                    <div>
                      <label className="text-[10px] text-ink-muted block mb-0.5">樓層</label>
                      <select value={draft.floor} onChange={e => setDraft({...draft, floor: e.target.value})}
                        className="w-full h-7 px-1 text-xs font-mono bg-surface-base border border-border-strong rounded focus:border-sev-info focus:outline-none">
                        {FLOORS.map(f => <option key={f} value={f}>{f}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="text-[10px] text-ink-muted block mb-0.5">區域 / 房間</label>
                      <input value={draft.area} onChange={e => setDraft({...draft, area: e.target.value})}
                        placeholder="例: 西側走廊"
                        className="w-full h-7 px-2 text-xs bg-surface-base border border-border-strong rounded focus:border-sev-info focus:outline-none"/>
                    </div>
                  </div>
                  <div className="text-[10px] text-ink-muted bg-surface-base rounded p-1.5 font-mono tnum">
                    預覽: <span className="text-ink-secondary">{draft.floor && draft.area ? `${draft.floor} · ${draft.area}` : draft.location}</span>
                  </div>
                  <div className="flex items-center gap-1 text-[10px] text-ink-dim pt-1 border-t border-border-subtle/60">
                    <Icon.Info size={10}/>
                    平面圖座標 (拖曳功能 — 開發中)
                  </div>
                </>
              ) : (
                <>
                  <div className="grid grid-cols-[60px_1fr] gap-y-1 gap-x-2 text-xs font-mono tnum">
                    <span className="text-ink-muted">名稱</span><span>{node.name}</span>
                    <span className="text-ink-muted">樓層</span><span>{node.floor || <span className="text-ink-dim">未設定</span>}</span>
                    <span className="text-ink-muted">區域</span><span>{node.area || <span className="text-ink-dim">未設定</span>}</span>
                    <span className="text-ink-muted">位置</span><span className="text-ink-secondary">{node.location}</span>
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="bg-surface-elevated rounded p-2">
              <div className="text-[10px] text-ink-muted">心跳</div>
              <div className={`font-mono tnum ${node.heartbeat > 30 ? 'text-sev-critical' : 'text-ink-primary'}`}>{node.heartbeat > 60 ? Math.floor(node.heartbeat/60)+'m' : node.heartbeat+'s'}</div>
            </div>
            <div className="bg-surface-elevated rounded p-2">
              <div className="text-[10px] text-ink-muted">上傳</div>
              <div className={`font-mono tnum ${node.upload > 60 ? 'text-sev-warn' : 'text-ink-primary'}`}>{node.upload > 60 ? Math.floor(node.upload/60)+'m' : node.upload+'s'}</div>
            </div>
            {node.type === 'camera' ? (
              <>
                <div className="bg-surface-elevated rounded p-2">
                  <div className="text-[10px] text-ink-muted">串流</div>
                  <div className="font-mono tnum">{node.bitrate}Mbps</div>
                </div>
                <div className="bg-surface-elevated rounded p-2">
                  <div className="text-[10px] text-ink-muted">溫度</div>
                  <div className="font-mono tnum">{node.temp ? node.temp+'°C' : '—'}</div>
                </div>
              </>
            ) : (
              <>
                <div className="bg-surface-elevated rounded p-2">
                  <div className="text-[10px] text-ink-muted">水位</div>
                  <div className={`font-mono tnum ${node.level > 85 ? 'text-sev-critical' : 'text-ink-primary'}`}>{node.level}%</div>
                </div>
                <div className="bg-surface-elevated rounded p-2">
                  <div className="text-[10px] text-ink-muted">循環</div>
                  <div className="font-mono tnum">每 {(60/node.cycles).toFixed(1)}m</div>
                </div>
              </>
            )}
          </div>

          {/* Detector health (camera only) */}
          {node.type === 'camera' && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold mb-1.5">偵測器狀態</div>
              <DetectorHealth node={node}/>
            </div>
          )}

          {/* Open alerts on this node */}
          {nodeAlerts.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold mb-1.5">作用中警報 ({nodeAlerts.length})</div>
              <div className="space-y-1">
                {nodeAlerts.map(a => (
                  <button key={a.id} onClick={() => onJumpAlert(a.id)}
                    className={`w-full flex items-center gap-2 p-2 rounded border border-border-subtle bg-surface-elevated hover:border-sev-info text-left transition-colors sev-bar ${window.sevMeta[a.sev].bar} relative pl-3`}>
                    <SeverityBadge sev={a.sev} withLabel={false}/>
                    <span className="text-xs flex-1 truncate">{window.alertTypeLabel(a.type)}</span>
                    <AgeCell sec={a.ageSec}/>
                    <Icon.ChevronRight size={12} className="text-ink-muted"/>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Recent history */}
          <div>
            <div className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold mb-1.5">最近事件 ({history.length})</div>
            {history.length === 0 ? (
              <div className="text-xs text-ink-muted text-center py-3 border border-dashed border-border-subtle rounded">無近期紀錄</div>
            ) : (
              <div className="space-y-1">
                {history.map((h, i) => (
                  <div key={i} className="flex items-center gap-2 p-2 rounded bg-surface-elevated text-xs">
                    <span className="font-mono tnum text-ink-muted w-16 flex-shrink-0">{h.t}</span>
                    <SeverityBadge sev={h.sev} withLabel={false}/>
                    <span className="text-ink-secondary truncate flex-1">{window.alertTypeLabel(h.type)} · {h.resolution}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="grid grid-cols-3 gap-1.5">
            <button className="h-8 bg-surface-elevated border border-border-strong rounded text-xs hover:bg-surface-overlay">
              <Icon.BellOff size={12} className="inline mr-1"/> 延期
            </button>
            <button className="h-8 bg-surface-elevated border border-border-strong rounded text-xs hover:bg-surface-overlay">
              <Icon.Settings size={12} className="inline mr-1"/> 配置
            </button>
            <button className="h-8 bg-surface-elevated border border-border-strong rounded text-xs hover:bg-surface-overlay">
              <Icon.RefreshCw size={12} className="inline mr-1"/> 重啟
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

// ---------- Volume Slider (for MuteDrawer) ----------

const VolumeSlider = ({ value, onChange }) => (
  <div className="flex items-center gap-2">
    <Icon.VolumeX size={14} className="text-ink-muted"/>
    <input type="range" min="0" max="100" value={value} onChange={e => onChange(parseInt(e.target.value, 10))}
      className="flex-1 accent-sev-info h-1"/>
    <Icon.Volume2 size={14} className="text-ink-muted"/>
    <span className="font-mono tnum text-xs text-ink-secondary w-8 text-right">{value}</span>
  </div>
);

Object.assign(window, {
  OperatorsCluster, StaleAckPill, NewAlertBanner, ShiftBanner,
  CommandPalette, NodeSidePanel, VolumeSlider,
});
