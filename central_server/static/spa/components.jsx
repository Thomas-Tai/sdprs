// Shared UI components

const { useState, useEffect, useRef, useMemo } = React;

// ---------- Safe meta lookups ----------
// Backend can send severities / states / detector-health values the UI has not
// yet been taught about (schema drift, edge sending a preview enum, etc.).
// Rather than render nothing (null-return) or crash on `.bar`/`.tone`, degrade
// to a labelled placeholder so operators still see *something*.
const safeSevMeta = (sev) => {
  const m = window.sevMeta && window.sevMeta[sev];
  if (m) return m;
  return window.sevMeta && window.sevMeta.info
    ? { ...window.sevMeta.info, label: sev || '未知' }
    : { label: sev || '未知', color: 'ink-muted', bar: 'sev-bar-info',
        Icon: () => (window.Icon ? window.Icon.HelpCircle({size:14}) : null) };
};
const safeStateMeta = (state) => {
  const m = window.stateMeta && window.stateMeta[state];
  if (m) return m;
  return { label: state || '未知',
           cls: 'bg-surface-elevated text-ink-muted border-border-subtle' };
};
const safeDetectorHealthMeta = (v) => {
  const meta = window.detectorHealthMeta || {};
  return meta[v] || meta.unknown || { label: v || '未知', tone: 'muted' };
};

// Expose the safe helpers so pages/* / app.jsx call sites (rendered rows,
// wall-view ticker, alert-detail header) don't crash on unknown severities.
window.safeSevMeta = safeSevMeta;
window.safeStateMeta = safeStateMeta;
window.safeDetectorHealthMeta = safeDetectorHealthMeta;

// ---------- AudioController — synthetic-tone alert audio ----------
// Zero external assets: uses the Web Audio API to generate oscillator tones.
// The `static/audio/` directory is intentionally empty and Howler is NOT
// loaded — this replaces the previous placebo pipeline. Real MP3 samples can
// swap in later without changing the public surface (playCritical, playWarning,
// playAck, playTest, setVolume, setMuted, isArmed, arm, subscribe).
//
// Browser autoplay policy: an AudioContext can't produce sound until the user
// has interacted with the page. We attach a one-shot document click listener
// below that calls arm() on the first gesture anywhere in the app, and expose
// a subscribe() so the StatusStrip pill can reactively reflect armed state.
const AudioController = (() => {
  let ctx = null, gainNode = null;
  let muted = false;
  let volume = 0.7;
  let armed = false;
  const listeners = new Set();
  const notify = () => listeners.forEach(fn => { try { fn(); } catch (_) {} });

  const ensure = () => {
    if (ctx) return ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try {
      ctx = new AC();
      gainNode = ctx.createGain();
      gainNode.connect(ctx.destination);
      gainNode.gain.value = volume;
    } catch (_) { ctx = null; }
    return ctx;
  };

  const beep = (freq, dur, wave = 'sine', delay = 0) => {
    if (muted || !ensure()) return;
    const start = ctx.currentTime + delay;
    const osc = ctx.createOscillator();
    const env = ctx.createGain();
    osc.type = wave;
    osc.frequency.value = freq;
    osc.connect(env);
    env.connect(gainNode);
    env.gain.setValueAtTime(0, start);
    env.gain.linearRampToValueAtTime(1, start + 0.02);
    env.gain.linearRampToValueAtTime(0, start + dur);
    osc.start(start);
    osc.stop(start + dur + 0.05);
  };

  const api = {
    arm: () => {
      if (armed) return;
      armed = true;
      const c = ensure();
      if (c && typeof c.resume === 'function') { try { c.resume(); } catch (_) {} }
      notify();
    },
    isArmed: () => armed,
    playCritical: () => { beep(880, 0.25); beep(660, 0.25, 'sine', 0.30); beep(880, 0.40, 'sine', 0.60); },
    playWarning:  () => { beep(660, 0.20); beep(880, 0.30, 'sine', 0.25); },
    playAck:      () => { beep(1200, 0.10, 'triangle'); },
    playTest: (severity) => {
      if (severity === 'critical') return api.playCritical();
      if (severity === 'warning')  return api.playWarning();
      return api.playAck();
    },
    setVolume: (v) => {
      volume = Math.max(0, Math.min(1, (Number(v) || 0) / 100));
      if (gainNode) gainNode.gain.value = volume;
    },
    setMuted: (m) => { muted = !!m; notify(); },
    isMuted:  () => muted,
    isAvailable: () => !!(window.AudioContext || window.webkitAudioContext),
    subscribe: (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
  };
  return api;
})();
window.SDPRS_AUDIO = AudioController;

// One-shot arm() on first user gesture anywhere in the app. Browsers block
// AudioContext playback until the user interacts, so this hook lets audio
// "just work" the moment the operator clicks/keys anything — no need for
// them to hunt for the pill in the status strip.
(() => {
  if (typeof document === 'undefined') return;
  const arm = () => {
    try { AudioController.arm(); } catch (_) {}
    document.removeEventListener('click', arm, true);
    document.removeEventListener('keydown', arm, true);
    document.removeEventListener('touchstart', arm, true);
  };
  document.addEventListener('click', arm, true);
  document.addEventListener('keydown', arm, true);
  document.addEventListener('touchstart', arm, true);
})();

// ---------- Atoms ----------

const Kbd = ({ children }) => <kbd className="kbd noselect">{children}</kbd>;

const SeverityBadge = ({ sev, withLabel = true, size = 'sm' }) => {
  const m = safeSevMeta(sev);
  const Ico = m.Icon;
  const sz = size === 'md' ? 'text-sm px-2 py-0.5' : 'text-[10px] px-1.5 py-0.5';
  return (
    <span className={`inline-flex items-center gap-1 rounded border font-medium tnum bg-${m.color}/15 text-${m.color} border-${m.color}/30 ${sz}`}>
      {Ico && <Ico />}
      {withLabel && <span>{m.label}</span>}
    </span>
  );
};

const StateBadge = ({ state }) => {
  const m = safeStateMeta(state);
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
// Used by NodeCard tile (pages/monitor.jsx), the big monitor wall (app.jsx), and
// the node detail side panel (components.jsx). Each slot needs the same
// behaviour: show a live JPEG for cameras that have uploaded a snapshot,
// fall back to an icon otherwise.
//
// Shared 1 Hz tick: instead of every tile spawning its own setInterval
// (which used to mean ~30 drifting timers on the monitor wall), all live
// tiles subscribe to a single module-level ticker below. The ticker only
// runs while at least one tile is subscribed, and each tile still gates
// its subscription on wantsLiveImg so pump tiles and offline cameras
// don't re-render for a signal they'd ignore. Callback receives Date.now()
// which drops straight into the ?t= cache-buster.
// Server encoding: picamera2's misnamed "RGB888" numpy array is already
// B,G,R; the edge adapter passes it straight through to cv2.imencode.
// If colours ever look magenta again, check edge_glass/utils/camera.py.
const _snapshotTickListeners = new Set();
let _snapshotTickId = null;
const _snapshotTickInterval = 1000; // ms — matches previous per-tile cadence
function _startSnapshotTick() {
  if (_snapshotTickId != null) return;
  _snapshotTickId = setInterval(() => {
    const now = Date.now();
    _snapshotTickListeners.forEach(cb => { try { cb(now); } catch (e) {} });
  }, _snapshotTickInterval);
}
function _stopSnapshotTickIfIdle() {
  if (_snapshotTickListeners.size === 0 && _snapshotTickId != null) {
    clearInterval(_snapshotTickId);
    _snapshotTickId = null;
  }
}
const SnapshotImage = ({ node, iconSize = 48 }) => {
  const frozen = node.status === 'offline' || node.upload > 60;
  const wantsLiveImg = node.type === 'camera' && !frozen;
  const [ts, setTs] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (!wantsLiveImg) return;
    const cb = (now) => setTs(now);
    _snapshotTickListeners.add(cb);
    _startSnapshotTick();
    return () => {
      _snapshotTickListeners.delete(cb);
      _stopSnapshotTickIfIdle();
    };
  }, [wantsLiveImg]);
  if (wantsLiveImg && node.snapshotTimestamp) {
    return (
      <img
        src={`/api/edge/${node.id}/snapshot/latest?t=${ts}`}
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
  const v = safeDetectorHealthMeta(node.visualHealth);
  const a = safeDetectorHealthMeta(node.audioHealth);
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

  // Reactive audio-armed state — flips true after the first user gesture
  // anywhere on the page (see AudioController's one-shot listener). The
  // subscribe() hook keeps the pill in sync without prop-drilling.
  const [audioArmed, setAudioArmed] = useState(() => !!(window.SDPRS_AUDIO && window.SDPRS_AUDIO.isArmed()));
  useEffect(() => {
    if (!window.SDPRS_AUDIO || typeof window.SDPRS_AUDIO.subscribe !== 'function') return;
    const off = window.SDPRS_AUDIO.subscribe(() => setAudioArmed(window.SDPRS_AUDIO.isArmed()));
    return () => { off && off(); };
  }, []);

  // Play a critical tone the moment a NEW unack alert appears. Uses a ref
  // to hold the previous count so unrelated re-renders don't retrigger.
  const prevUnackRef = useRef(unackCount);
  useEffect(() => {
    const prev = prevUnackRef.current;
    prevUnackRef.current = unackCount;
    if (muted || !window.SDPRS_AUDIO) return;
    if (unackCount > prev) {
      // A newly-arrived unack alert. Play at the "critical" cadence — the
      // status strip doesn't have per-alert severity here, and the loudest
      // tone is safer than silence in a 24/7 NOC context.
      try { window.SDPRS_AUDIO.playCritical(); } catch (_) {}
    }
  }, [unackCount, muted]);

  // Replay tone when the audio countdown resets (transitions from a small
  // value UP to a larger one, e.g. 1 → 30). app.jsx owns the countdown ticker;
  // we just react to the reset edge, which happens exactly when the
  // "replay every 30s while unacked" loop cycles.
  const prevReplayRef = useRef(audioReplayIn);
  useEffect(() => {
    const prev = prevReplayRef.current;
    prevReplayRef.current = audioReplayIn;
    if (muted || !window.SDPRS_AUDIO) return;
    if (unackCount > 0 && prev != null && audioReplayIn != null && audioReplayIn > prev) {
      try { window.SDPRS_AUDIO.playCritical(); } catch (_) {}
    }
  }, [audioReplayIn, unackCount, muted]);

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
              <span className="font-mono text-[10px] bg-black/30 px-1 rounded" aria-hidden="true">♪ {audioReplayIn}s</span>
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
        {operators && operators.length > 1 && <OperatorsCluster operators={operators} currentUser={window.SDPRS_USER || ''}/>}
        <button onClick={onOpenCmdK} title="命令面板 (⌘K / Ctrl+K)" className="hidden md:flex items-center gap-1 h-7 px-2 ml-1 rounded border border-border-subtle bg-surface-elevated hover:bg-surface-overlay text-xs text-ink-muted transition-colors">
          <Icon.Search size={12}/> <span>跳轉...</span> <Kbd>⌘K</Kbd>
        </button>
        <button onClick={onToggleFocus} title="夜深 / 專注模式 (Ctrl+.)"
          aria-pressed={!!focusMode}
          aria-label={focusMode ? '關閉專注模式' : '啟用專注模式（隱藏資訊級警報）'}
          className={`w-8 h-8 rounded flex items-center justify-center transition-colors ${focusMode ? 'text-sev-info bg-sev-info/10 ring-1 ring-sev-info/60' : 'text-ink-muted hover:text-ink-primary hover:bg-surface-elevated'}`}>
          <Icon.Moon size={16} aria-hidden="true"/>
        </button>
        <button onClick={onOpenShortcuts} title="鍵盤捷徑 (?)" aria-label="開啟鍵盤捷徑說明" className="w-8 h-8 rounded flex items-center justify-center text-ink-muted hover:text-ink-primary hover:bg-surface-elevated transition-colors">
          <Icon.Keyboard size={16} aria-hidden="true"/>
        </button>
        <button onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} title="Theme (T)"
          aria-label={theme === 'dark' ? '切換為淺色主題' : '切換為深色主題'}
          className="w-8 h-8 rounded flex items-center justify-center text-ink-muted hover:text-ink-primary hover:bg-surface-elevated transition-colors">
          {theme === 'dark' ? <Icon.Moon size={16} aria-hidden="true"/> : <Icon.Sun size={16} aria-hidden="true"/>}
        </button>
        {/* Audio-armed pill — browsers block AudioContext playback until the
            operator interacts with the page. Shows armed status and lets the
            operator arm audio explicitly if the auto-listener somehow missed. */}
        {window.SDPRS_AUDIO && window.SDPRS_AUDIO.isAvailable() && (
          <button
            onClick={() => { try { window.SDPRS_AUDIO.arm(); } catch (_) {} }}
            title={audioArmed ? '瀏覽器音效已啟用' : '點擊啟用瀏覽器音效 (瀏覽器需要一次使用者互動)'}
            disabled={audioArmed}
            className={`hidden md:inline-flex items-center gap-1 h-6 px-2 rounded border text-[10px] font-medium tnum whitespace-nowrap transition-colors ${audioArmed ? 'border-sev-ok/40 bg-sev-ok/10 text-sev-ok cursor-default' : 'border-sev-warn/40 bg-sev-warn/10 text-sev-warn hover:bg-sev-warn/20 animate-live-blink'}`}
          >
            {audioArmed ? '🔊 音效已啟用' : '🔇 點擊啟用音效'}
          </button>
        )}
        <button
          onClick={onOpenMuteDrawer}
          title={`音效 (M) — ${activeMutes} 個來源已抑制`}
          aria-pressed={activeMutes > 0}
          aria-label={
            muted
              ? `開啟音效抽屜（目前全域靜音，${activeMutes} 個來源已抑制）`
              : `開啟音效抽屜（${activeMutes} 個來源已抑制）`
          }
          className={`relative w-8 h-8 rounded flex items-center justify-center transition-colors ${activeMutes > 0 ? 'text-sev-warn hover:bg-sev-warn/10 ring-1 ring-sev-warn/60' : 'text-ink-muted hover:text-ink-primary hover:bg-surface-elevated'}`}
        >
          {muted ? <Icon.VolumeX size={16} aria-hidden="true"/> : <Icon.Volume2 size={16} aria-hidden="true"/>}
          {activeMutes > 0 && <span aria-hidden="true" className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-sev-warn text-[9px] font-bold text-black flex items-center justify-center tnum">{activeMutes}</span>}
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
  // TODO(dashboard-audit-2026-07-15): mobile nav drawer — currently the rail
  // just hides on <md. app.jsx <main> should mirror this (ml-0 md:ml-56) and
  // add a hamburger toggle that slides this nav in. Layout-only fix here so
  // portrait mobile doesn't crush the content underneath.
  return (
    <nav className="hidden md:flex w-56 fixed left-0 top-12 bottom-10 bg-surface-panel border-r border-border-subtle flex-col noselect">
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
  if (!Array.isArray(data) || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-7 text-[10px] text-ink-muted font-mono tnum" style={{ width, height }}>
        無資料
      </div>
    );
  }
  const max = Math.max(...data, 1);
  const avg = data.reduce((a,b)=>a+b,0) / data.length;
  const cur = data[data.length-1];
  const surge = avg > 0 && cur > avg * 2;
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
  const arr = Array.isArray(data) && data.length ? data : [0];
  const avg = arr.reduce((a,b)=>a+b,0) / arr.length;
  const cur = arr[arr.length-1];
  const surge = avg > 0 && cur > avg * 2;
  const ageMin = handover?.ageMin ?? null;
  const ageH = ageMin != null ? (ageMin / 60).toFixed(1) : null;
  const ageTone = ageMin == null ? 'muted'
    : ageMin > 720 ? 'critical' : ageMin > 240 ? 'warn' : 'ok';
  const ageCls = ageTone === 'critical' ? 'text-sev-critical bg-sev-critical/15 border border-sev-critical/40' : 'text-ink-muted';
  const by = handover?.by ?? '—';
  const at = handover?.at ?? '';
  const text = handover?.text ?? '尚無交接事項';
  return (
    <div className="h-10 fixed inset-x-0 bottom-0 z-30 bg-surface-panel border-t border-border-subtle flex items-center px-4 gap-4 text-xs noselect">
      <div className="flex items-center gap-2.5">
        <Icon.Activity size={14} className="text-ink-muted"/>
        <span className="text-ink-muted">警報率</span>
        <Sparkline data={arr} />
        <span className="font-mono tnum text-ink-secondary"><span className="text-ink-muted">15min × 16</span></span>
        {surge && (
          <Pill tone="critical" className="!h-5 !text-[10px]"><Icon.ArrowUp size={10} strokeWidth={2.5}/> 加劇中 · {(cur/avg).toFixed(1)}× 均值</Pill>
        )}
      </div>
      <div className="flex-1"></div>
      <div className="flex items-center gap-2 max-w-[720px] min-w-0">
        <Icon.ClipboardList size={14} className="text-sev-warn flex-shrink-0"/>
        <span className="text-ink-muted whitespace-nowrap">上一班備註:</span>
        <span className="font-mono text-ink-dim text-[11px] tnum">{by}{at ? ` @ ${at}` : ''}</span>
        {ageMin != null && (
          <span className={`inline-flex items-center px-1 h-4 rounded font-mono text-[10px] tnum flex-shrink-0 ${ageCls}`}>
            {ageMin < 60 ? `${ageMin}m 前` : `${ageH}h 前`}
          </span>
        )}
        <span className="text-ink-secondary truncate">"{text}"</span>
        {/* Footer note is edited via the Handover page — no inline pencil here
            (removed 2026-07-16 as part of dead-button cleanup; the pencil had
            no onClick and duplicated the Handover editor). */}
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
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  const matches = SHORTCUTS.filter(s =>
    !q || s.label.toLowerCase().includes(q.toLowerCase()) || s.keys.some(k => k.toLowerCase().includes(q.toLowerCase())) || s.cat.toLowerCase().includes(q.toLowerCase())
  );
  const byCat = matches.reduce((acc, s) => { (acc[s.cat] = acc[s.cat] || []).push(s); return acc; }, {});
  return (
    <div className="fixed inset-0 z-50 bg-surface-base/80 backdrop-blur-sm flex items-center justify-center p-6" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="鍵盤捷徑"
        className="bg-surface-panel border border-border-strong rounded-lg max-w-2xl w-full"
        onClick={e => e.stopPropagation()}
      >
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

const MuteDrawer = ({ open, onClose, muteState, setMuteState, nodes }) => {
  const drawerRef = useRef(null);
  const headingRef = useRef(null);
  // Inline error for the "解除" unsnooze API call; keyed by node id.
  const [unsnoozeErr, setUnsnoozeErr] = useState(null);
  // 30s tick so the "剩餘 X 分鐘" text refreshes while the drawer sits open.
  // Cheap — a single setState per drawer, only while mounted.
  const [, setNow] = useState(0);
  useEffect(() => {
    if (!open) return;
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, [open]);
  // Focus + Escape trap. On open, focus lands on the panel's heading (not the
  // ✕ close button) so screen readers announce the panel's purpose. See H-9.
  useEffect(() => {
    if (!open) return;
    if (headingRef.current) {
      try { headingRef.current.focus(); } catch (_) {}
    }
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  // Prefer prop-supplied nodes (React state from caller — always fresh); fall
  // back to window.NODES only if the caller didn't wire it up. Callers should
  // pass `nodes={nodes}` from useState so refreshes reach the drawer.
  // TODO(dashboard-audit-2026-07-15): remove window.NODES fallback once every
  // call site (app.jsx / pages/*) passes the `nodes` prop.
  const nodeList = Array.isArray(nodes) ? nodes : (window.NODES || []);
  const activeCount = (muteState.global ? 1 : 0) + muteState.nodes.length + (muteState.lightning ? 1 : 0);
  const nearestKm = window.WEATHER?.lightning?.nearest;
  // Test buttons: actually play a synthetic tone via AudioController AND keep
  // the visual "▶ 播放測試: …" pulse so operators see confirmation even when
  // the browser hasn't been armed yet.
  const playTest = (severity, label) => {
    const node = document.getElementById('test-audio-feedback');
    if (node) {
      node.textContent = `▶ 播放測試: ${label}`;
      setTimeout(() => { if (node) node.textContent = ''; }, 1500);
    }
    if (window.SDPRS_AUDIO) {
      try {
        if (!window.SDPRS_AUDIO.isArmed()) window.SDPRS_AUDIO.arm();
        window.SDPRS_AUDIO.playTest(severity);
      } catch (_) { /* AudioContext refused — visual feedback still fires */ }
    }
  };
  // Unsnooze a single node via api.jsx. Only mutate local state after the
  // server confirms; otherwise the next poll (~20s) resurfaces the node and
  // the operator's action looks like it did nothing.
  const unsnoozeOne = async (nid) => {
    setUnsnoozeErr(null);
    if (!window.SDPRS_API || typeof window.SDPRS_API.unsnoozeNode !== 'function') {
      setUnsnoozeErr({ nid, msg: '解除功能尚未就緒' });
      return;
    }
    try {
      await window.SDPRS_API.unsnoozeNode(nid);
      setMuteState({ ...muteState, nodes: muteState.nodes.filter(x => x !== nid) });
    } catch (err) {
      console.error('unsnooze failed', err);
      setUnsnoozeErr({ nid, msg: '伺服器解除失敗，請重試' });
    }
  };
  // Unsnooze all snoozed nodes then clear local mute state. Errors on any
  // node are collected and surfaced without wiping the remaining flags.
  const unsnoozeAll = async () => {
    setUnsnoozeErr(null);
    const targets = [...muteState.nodes];
    if (window.SDPRS_API && typeof window.SDPRS_API.unsnoozeNode === 'function' && targets.length > 0) {
      const results = await Promise.allSettled(targets.map(nid => window.SDPRS_API.unsnoozeNode(nid)));
      const failed = results.map((r, i) => r.status === 'rejected' ? targets[i] : null).filter(Boolean);
      if (failed.length > 0) {
        setUnsnoozeErr({ nid: null, msg: `${failed.length} 個節點解除失敗: ${failed.join(', ')}` });
      }
    }
    setMuteState({ global: false, nodes: [], lightning: false, volume: muteState.volume ?? 70 });
  };
  return (
    <div className="fixed inset-0 z-50 bg-surface-base/60 backdrop-blur-sm flex justify-end" onClick={onClose}>
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label="音效抑制 / 音量"
        className="w-[380px] h-full bg-surface-panel border-l border-border-strong overflow-y-auto scroll-thin"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-border-subtle sticky top-0 bg-surface-panel z-10">
          <h2
            ref={headingRef}
            tabIndex={-1}
            className="text-base font-semibold flex items-center gap-2 focus:outline-none"
          >
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
              <VolumeSlider
                value={muteState.volume ?? 70}
                onChange={(v) => {
                  setMuteState({ ...muteState, volume: v });
                  if (window.SDPRS_AUDIO) { try { window.SDPRS_AUDIO.setVolume(v); } catch (_) {} }
                }}
              />
              <div className="flex items-center justify-between mt-3 text-xs">
                <span className="text-ink-muted">測試音效:</span>
                <div className="flex gap-1">
                  <button onClick={() => playTest('critical', '嚴重')} className="px-2 h-6 bg-sev-critical/15 text-sev-critical border border-sev-critical/30 rounded text-[10px] font-medium hover:bg-sev-critical/25">嚴重</button>
                  <button onClick={() => playTest('warning', '警告')} className="px-2 h-6 bg-sev-warn/15 text-sev-warn border border-sev-warn/30 rounded text-[10px] font-medium hover:bg-sev-warn/25">警告</button>
                  <button onClick={() => playTest('ack', '確認')} className="px-2 h-6 bg-sev-info/15 text-sev-info border border-sev-info/30 rounded text-[10px] font-medium hover:bg-sev-info/25">確認</button>
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
                onClick={() => {
                  const next = !muteState.global;
                  setMuteState({ ...muteState, global: next });
                  if (window.SDPRS_AUDIO) { try { window.SDPRS_AUDIO.setMuted(next); } catch (_) {} }
                }}
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
                  const n = nodeList.find(nn => nn.id === nid);
                  // n.snoozeMin is computed in api.jsx mapNode from
                  // snoozed_until − now(), so it drifts between polls; the
                  // 30s tick above nudges the re-render. snoozedBy /
                  // snoozedAt are now surfaced by api.jsx mapNode — only
                  // render provenance when snoozedBy is truthy (never fake).
                  const remain = n?.snoozeMin;
                  const snoozeAtText = n?.snoozedAt
                    ? (() => {
                        try {
                          return new Date(n.snoozedAt).toLocaleTimeString('zh-TW', {
                            hour: '2-digit', minute: '2-digit', hour12: false,
                          });
                        } catch (_) { return ''; }
                      })()
                    : '';
                  return (
                    <div key={nid} className="flex items-center gap-2 bg-surface-elevated border border-border-subtle rounded p-2.5">
                      <div className="flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono text-sm font-semibold">{nid}</span>
                          <span className="text-xs text-ink-secondary">{n?.name}</span>
                        </div>
                        <div className="text-[10px] text-ink-muted font-mono tnum mt-0.5">
                          {remain != null && remain > 0 ? `剩餘 ${remain} 分鐘` : '靜音中'}
                        </div>
                        {n?.snoozedBy && (
                          <div className="text-[10px] text-ink-muted font-mono tnum mt-0.5 opacity-70">
                            由 {n.snoozedBy}{snoozeAtText ? ` 於 ${snoozeAtText}` : ''} 設定
                          </div>
                        )}
                      </div>
                      <button
                        onClick={() => unsnoozeOne(nid)}
                        className="text-ink-muted hover:text-ink-primary text-xs px-2 h-6 rounded bg-surface-overlay"
                      >解除</button>
                    </div>
                  );
                })}
                {unsnoozeErr && (
                  <div className="text-[10px] text-sev-critical mt-1 px-1">
                    {unsnoozeErr.nid ? `${unsnoozeErr.nid}: ` : ''}{unsnoozeErr.msg}
                  </div>
                )}
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
                  <div className="text-[10px] text-sev-warn mt-1 font-mono tnum">
                    ● 觸發中{nearestKm != null ? ` — 最近雷擊 ${nearestKm}km` : ''}
                  </div>
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
            onClick={unsnoozeAll}
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

const FilterChip = ({ active, onClick, children, count }) => (
  <button onClick={onClick}
    aria-pressed={!!active}
    className={`inline-flex items-center gap-1 px-2 h-6 rounded text-xs border transition-colors ${active ? 'bg-sev-info/15 text-sev-info border-sev-info/40' : 'bg-surface-elevated text-ink-secondary border-border-subtle hover:border-border-strong'}`}>
    {children}
    {count != null && <span className="font-mono tnum text-[10px] text-ink-muted">{count}</span>}
  </button>
);

Object.assign(window, {
  Kbd, SeverityBadge, StateBadge, AgeCell, Pill, DetectorHealth, DriftMeter,
  StatusStrip, NavRail, Footer, Sparkline, ShortcutsModal, EmptyState, MuteDrawer,
  FilterChip, NAV_ITEMS,
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
      role="alert"
      aria-live="assertive"
      aria-label={`${count} 個新警報 — 點擊或按上鍵跳轉`}
      className="new-alert-banner fixed top-16 left-1/2 -translate-x-1/2 z-30 inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-sev-critical text-white shadow-2xl border border-sev-critical hover:bg-red-700 transition-colors">
      <Icon.ArrowUp size={14} strokeWidth={2.5} aria-hidden="true"/>
      <span className="text-sm font-semibold tnum" aria-hidden="true">{count} 新警報</span>
      <Kbd aria-hidden="true">↑</Kbd>
    </button>
  );
};

// ---------- Shift Banner (start/end of shift) ----------

const ShiftBanner = ({ shiftSummary, onDismiss, onViewHandover }) => {
  const s = shiftSummary || {};
  const operator = s.operator ?? window.SDPRS_USER ?? '—';
  // Field-name flex: try the audit-suggested names first, then the current
  // window.SHIFT_SUMMARY shape (alertsHandled/carryOver/highlights) from
  // data.jsx, then '—'. Prevents blanks if backend renames fields.
  const carryOver = s.carryOver ?? s.handled ?? s.alertsHandled ?? '—';
  const snoozed   = s.snoozed   ?? s.warn ?? '—';
  const pending   = s.pending   ?? s.critical ?? '—';
  const recent = s.recentIncident
    ?? (Array.isArray(s.highlights) && s.highlights.length ? s.highlights.join(' · ') : '尚無交接事項');
  return (
    <div className="fixed top-14 right-4 z-40 w-[360px] bg-surface-panel border border-sev-info/40 rounded-lg shadow-2xl overflow-hidden">
      <div className="px-4 py-2.5 bg-sev-info/15 border-b border-sev-info/30 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sev-info">
          <Icon.ClipboardList size={14}/>
          <span className="text-sm font-semibold">班次接班摘要 · {operator}</span>
        </div>
        <button onClick={onDismiss} className="text-ink-muted hover:text-ink-primary"><Icon.X size={14}/></button>
      </div>
      <div className="p-4 space-y-3">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-ink-muted">上一班次承接</div>
          <div className="grid grid-cols-3 gap-2 mt-1.5">
            <div className="bg-surface-elevated rounded p-2 border border-border-subtle">
              <div className="text-xl font-mono font-bold tnum text-sev-info">{carryOver}</div>
              <div className="text-[10px] text-ink-muted">已認領未解決</div>
            </div>
            <div className="bg-surface-elevated rounded p-2 border border-border-subtle">
              <div className="text-xl font-mono font-bold tnum text-sev-warn">{snoozed}</div>
              <div className="text-[10px] text-ink-muted">節點延期中</div>
            </div>
            <div className="bg-surface-elevated rounded p-2 border border-border-subtle">
              <div className="text-xl font-mono font-bold tnum text-sev-critical">{pending}</div>
              <div className="text-[10px] text-ink-muted">未處理 (新)</div>
            </div>
          </div>
        </div>
        <div className="bg-sev-warn/10 border border-sev-warn/30 rounded p-2.5 text-xs">
          <div className="text-sev-warn font-semibold mb-1 flex items-center gap-1">
            <Icon.AlertCircle size={12}/> 上一班重點
          </div>
          <p className="text-ink-secondary leading-relaxed">{recent}</p>
        </div>
        {/* TODO(dashboard-audit-2026-07-15): needs handover-history route.
            When app.jsx wires onViewHandover (e.g. setPage('handover')),
            this button becomes live. Until then it renders disabled. */}
        <button
          onClick={onViewHandover || undefined}
          disabled={!onViewHandover}
          title={onViewHandover ? undefined : '尚未實作'}
          className="w-full h-8 bg-sev-info text-white rounded text-xs font-semibold hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          檢視完整交接紀錄 →
        </button>
      </div>
    </div>
  );
};

// ---------- Command Palette (Cmd+K) ----------

const CommandPalette = ({ open, onClose, alerts, nodes, onSelectAlert, onNav, onCmd }) => {
  const [q, setQ] = useState('');
  const [hi, setHi] = useState(0);
  const paletteRef = useRef(null);

  React.useEffect(() => {
    if (open) { setQ(''); setHi(0); }
  }, [open]);

  // Escape closes; focus handled by <input autoFocus/>.
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  // Prefer React-state nodes from the caller; window.NODES is a stale-read
  // fallback for legacy call sites.
  // TODO(dashboard-audit-2026-07-15): remove window.NODES fallback once every
  // call site (app.jsx / pages/*) passes the `nodes` prop.
  const nodeList = Array.isArray(nodes) ? nodes : (window.NODES || []);

  // Build searchable items
  const items = [
    ...window.NAV_ITEMS.map(n => ({ kind: 'nav', id: n.id, label: `頁面: ${n.label}`, hint: `Hotkey ${n.hotkey}`, icon: n.Icon })),
    ...alerts.map(a => ({ kind: 'alert', id: a.id, label: `${a.id} · ${window.alertTypeLabel(a.type)}`, hint: `${a.node} · ${a.state}`, sev: a.sev })),
    ...nodeList.map(n => ({ kind: 'node', id: n.id, label: `節點: ${n.id} · ${n.name}`, hint: n.location, status: n.status })),
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
      <div
        ref={paletteRef}
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        className="w-[640px] max-w-[90vw] bg-surface-panel border border-border-strong rounded-lg cmdk-shadow overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
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

const NodeSidePanel = ({ node, history, onClose, onJumpAlert, onNavigate, onSelectAlert, openAlerts, onUpdateNode }) => {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(null);
  const [locationError, setLocationError] = useState(null);
  const panelRef = useRef(null);
  const headingRef = useRef(null);
  React.useEffect(() => {
    if (node) {
      setDraft({ location: node.location || '' });
      setEditing(false);
      setLocationError(null);
    }
  }, [node?.id]);

  // A11y: Escape closes; focus lands on the panel heading (not the ✕ close
  // button) so screen readers announce the node id/name first. See H-9.
  React.useEffect(() => {
    if (!node) return;
    if (headingRef.current) {
      try { headingRef.current.focus(); } catch (_) {}
    }
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [node?.id, onClose]);

  if (!node) return null;
  const nodeAlerts = openAlerts.filter(a => a.node === node.id);
  // Prefer prop-supplied history (fresh from caller's useState); fall back to
  // the window global for legacy call sites that haven't been updated yet.
  // See C-8 — direct window reads were non-reactive so panel history looked
  // stale until the panel was re-opened.
  const items = history || (window.NODE_HISTORY?.[node?.id] || []);

  const saveEdits = () => {
    // Backend PATCH /api/nodes/{id} only accepts `location`. name/floor/area
    // are DERIVED from location by api.jsx mapNode (see api.jsx:184-193), so
    // only `location` is editable here — the other fields are read-only.
    const newLocation = (draft?.location || '').trim();
    if (!newLocation) {
      // H-7: previously silently exited edit mode when the field was blank,
      // which read as "discarded my change without saying why". Show an
      // inline validation error instead.
      setLocationError('位置為必填');
      return;
    }
    setLocationError(null);
    onUpdateNode && onUpdateNode(node.id, { location: newLocation });
    setEditing(false);
  };

  return (
    <div className="fixed inset-0 z-50 bg-surface-base/40 flex justify-end" onClick={onClose}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={`節點詳情 ${node.id}`}
        className="w-[420px] h-full bg-surface-panel border-l border-border-strong overflow-y-auto scroll-thin"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-4 py-3 border-b border-border-subtle sticky top-0 bg-surface-panel z-10 flex items-center justify-between">
          <h2
            ref={headingRef}
            tabIndex={-1}
            className="flex items-center gap-2 focus:outline-none m-0 text-base font-normal"
          >
            <span className="font-mono text-base font-bold">{node.id}</span>
            <span className={`w-2 h-2 rounded-full bg-sev-${node.status === 'offline' || node.status === 'critical' ? 'critical' : node.status === 'warn' ? 'warn' : 'ok'}`}></span>
            <span className="text-sm text-ink-secondary">{node.name}</span>
          </h2>
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
              {/* Name / floor / area are DERIVED from location by api.jsx mapNode
                  (splits on "·"). Backend PATCH only accepts `location`, so those
                  three are shown as read-only <dd>s in both modes and only
                  `location` gets an input. Edit `location` to rename downstream. */}
              <div className="grid grid-cols-[60px_1fr] gap-y-1 gap-x-2 text-xs font-mono tnum">
                <span className="text-ink-muted">名稱</span><span>{node.name}</span>
                <span className="text-ink-muted">樓層</span><span>{node.floor || <span className="text-ink-dim">未設定</span>}</span>
                <span className="text-ink-muted">區域</span><span>{node.area || <span className="text-ink-dim">未設定</span>}</span>
              </div>
              {editing ? (
                <div className="pt-2 border-t border-border-subtle/60">
                  <label className="text-[10px] text-ink-muted block mb-0.5">
                    位置 <span className="text-ink-dim">(格式: 樓層 · 區域)</span>
                  </label>
                  <input
                    value={draft?.location ?? ''}
                    onChange={e => {
                      setDraft({ ...(draft || {}), location: e.target.value });
                      if (locationError) setLocationError(null);
                    }}
                    placeholder="例: 3F · 西側走廊"
                    aria-invalid={!!locationError}
                    className={`w-full h-7 px-2 text-xs bg-surface-base border rounded focus:outline-none ${locationError ? 'border-sev-critical focus:border-sev-critical' : 'border-border-strong focus:border-sev-info'}`}
                  />
                  {locationError && (
                    <div className="text-sev-critical text-xs mt-1">{locationError}</div>
                  )}
                  <div className="text-[10px] text-ink-dim mt-1 flex items-center gap-1">
                    <Icon.Info size={10}/>
                    其他欄位變更請聯絡管理員
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-[60px_1fr] gap-y-1 gap-x-2 text-xs font-mono tnum">
                  <span className="text-ink-muted">位置</span>
                  <span className="text-ink-secondary">{node.location}</span>
                </div>
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
                    className={`w-full flex items-center gap-2 p-2 rounded border border-border-subtle bg-surface-elevated hover:border-sev-info text-left transition-colors sev-bar ${safeSevMeta(a.sev).bar} relative pl-3`}>
                    <SeverityBadge sev={a.sev} withLabel={false}/>
                    <span className="text-xs flex-1 truncate">{window.alertTypeLabel(a.type)}</span>
                    <AgeCell sec={a.ageSec}/>
                    <Icon.ChevronRight size={12} className="text-ink-muted"/>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Recent history — rows clickable when the item carries an alertId
              (H-8). Fall back to a plain div when there's nothing to open, so
              we don't render fake affordances. */}
          <div>
            <div className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold mb-1.5">最近事件 ({items.length})</div>
            {items.length === 0 ? (
              <div className="text-xs text-ink-muted text-center py-3 border border-dashed border-border-subtle rounded">無近期紀錄</div>
            ) : (
              <div className="space-y-1">
                {items.map((h, i) => {
                  const alertId = h.alertId || h.id;
                  const clickable = !!(alertId && onSelectAlert);
                  const rowCls = `flex items-center gap-2 p-2 rounded bg-surface-elevated text-xs w-full text-left ${clickable ? 'hover:bg-surface-overlay cursor-pointer' : ''}`;
                  const body = (
                    <>
                      <span className="font-mono tnum text-ink-muted w-16 flex-shrink-0">{h.t}</span>
                      <SeverityBadge sev={h.sev} withLabel={false}/>
                      <span className="text-ink-secondary truncate flex-1">{window.alertTypeLabel(h.type)} · {h.resolution}</span>
                      {clickable && <Icon.ChevronRight size={12} className="text-ink-muted flex-shrink-0"/>}
                    </>
                  );
                  if (clickable) {
                    return (
                      <button key={i} onClick={() => onSelectAlert(alertId)} className={rowCls}>
                        {body}
                      </button>
                    );
                  }
                  return <div key={i} className={rowCls}>{body}</div>;
                })}
              </div>
            )}
          </div>

          {/* Actions — the previous 延期 / 配置 / 重啟 trio was silent no-op
              (no onClick handlers). Replaced 2026-07-16 with a single link
              that closes the drawer and (if the caller wired onNavigate)
              jumps to the Status page where node config lives. */}
          <div>
            <button
              onClick={() => {
                onClose();
                if (typeof onNavigate === 'function') onNavigate('status');
              }}
              className="w-full h-8 bg-surface-elevated border border-border-strong rounded text-xs hover:bg-surface-overlay flex items-center justify-center gap-1.5"
            >
              <Icon.Settings size={12}/> 在狀態頁編輯設定
              <Icon.ChevronRight size={12} className="text-ink-muted"/>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

// ---------- Volume Slider (for MuteDrawer) ----------

const VolumeSlider = ({ value, onChange, onVolumeChange }) => {
  // TODO(dashboard-audit-2026-07-15): wire onVolumeChange to audio player
  // in app.jsx. Right now this only calls Howler.volume() if the global is
  // present, and fires an optional onVolumeChange prop for the parent to
  // route to its <audio> element. Without one, the slider is a placebo:
  // muteState.volume is written but never read by any player.
  const emit = (v) => {
    onChange && onChange(v);
    onVolumeChange && onVolumeChange(v);
    // Optional: if the page bundles Howler for alert audio, drive it directly
    // so the slider works even before app.jsx wires the prop.
    if (window.Howler && typeof window.Howler.volume === 'function') {
      try { window.Howler.volume(Math.max(0, Math.min(1, v / 100))); } catch (_) {}
    }
  };
  return (
    <div className="flex items-center gap-2">
      <Icon.VolumeX size={14} className="text-ink-muted"/>
      <input
        type="range" min="0" max="100" value={value}
        onChange={e => emit(parseInt(e.target.value, 10))}
        aria-label="音量"
        className="flex-1 accent-sev-info h-1"
      />
      <Icon.Volume2 size={14} className="text-ink-muted"/>
      <span className="font-mono tnum text-xs text-ink-secondary w-8 text-right">{value}</span>
    </div>
  );
};

Object.assign(window, {
  OperatorsCluster, StaleAckPill, NewAlertBanner, ShiftBanner,
  CommandPalette, NodeSidePanel, VolumeSlider,
});
