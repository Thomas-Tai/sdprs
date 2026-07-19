// Shared UI components

const { useState, useEffect, useRef, useMemo, useCallback } = React;

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

// ---------- LiveClockContext ----------
// Shared context for the 1-second drift timer. LiveClockProvider (app.jsx)
// owns the state; StatusStrip and DriftMeter are the only consumers. This
// prevents the entire App tree from re-rendering every second.
const LiveClockContext = React.createContext({ liveSec: 0, resetClock: () => {} });
window.LiveClockContext = LiveClockContext;

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

  // Beep-overlap guard: rapid playCritical/playWarning calls (e.g. two
  // unacked alerts arriving within a second) stack oscillators on top of
  // each other and produce ugly distortion. Track the last fire time per
  // category and skip if the previous sequence hasn't finished playing.
  let lastCriticalTime = 0;
  let lastWarningTime = 0;

  const api = {
    arm: () => {
      if (armed) return;
      armed = true;
      const c = ensure();
      if (c && typeof c.resume === 'function') { try { c.resume(); } catch (_) {} }
      notify();
    },
    isArmed: () => armed,
    playCritical: () => {
      const now = Date.now();
      if (now - lastCriticalTime < 1000) return;
      lastCriticalTime = now;
      beep(880, 0.25); beep(660, 0.25, 'sine', 0.30); beep(880, 0.40, 'sine', 0.60);
    },
    playWarning: () => {
      const now = Date.now();
      if (now - lastWarningTime < 600) return;
      lastWarningTime = now;
      beep(660, 0.20); beep(880, 0.30, 'sine', 0.25);
    },
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

const Kbd = React.memo(({ children }) => <kbd className="kbd noselect">{children}</kbd>);

const SeverityBadge = React.memo(({ sev, withLabel = true, size = 'sm' }) => {
  const m = safeSevMeta(sev);
  const Ico = m.Icon;
  const sz = size === 'md' ? 'text-sm px-2 py-0.5' : 'text-[10px] px-1.5 py-0.5';
  return (
    <span className={`inline-flex items-center gap-1 rounded border font-medium tnum bg-${m.color}/15 text-${m.color} border-${m.color}/30 ${sz}`}>
      {Ico && <Ico />}
      {withLabel && <span>{m.label}</span>}
    </span>
  );
}, (prev, next) => prev.sev === next.sev && prev.withLabel === next.withLabel && prev.size === next.size);

const StateBadge = React.memo(({ state }) => {
  const m = safeStateMeta(state);
  return <span className={`inline-flex items-center text-[10px] px-1.5 py-0.5 rounded border font-medium ${m.cls}`}>{m.label}</span>;
}, (prev, next) => prev.state === next.state);

const AgeCell = React.memo(({ sec }) => (
  <span className={`font-mono text-xs tnum ${window.ageColor(sec)}`}>{window.fmtAge(sec)}</span>
), (prev, next) => prev.sec === next.sec);

const Pill = React.memo(({ tone = 'neutral', children, dot, pulse, className = '' }) => {
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
}, (prev, next) => prev.tone === next.tone && prev.children === next.children && prev.dot === next.dot && prev.pulse === next.pulse && prev.className === next.className);

// ---------- SnapshotImage — live camera frame or icon fallback ----------
// Used by NodeCard tile (pages/monitor.jsx), the big monitor wall (app.jsx), and
// the node detail side panel (components.jsx). Each slot needs the same
// behaviour: show a live JPEG for cameras that have uploaded a snapshot,
// fall back to an icon otherwise.
//
// Cache-buster: `node.snapshotTimestamp` (populated by api.jsx mapNode) only
// changes when the edge actually pushes a new frame. Using it as `?t=` keeps
// the URL stable across parent re-renders → browser 304 fast-path works and
// the monitor wall stops hammering the edge cams. Fall back to `node.id` when
// the timestamp is missing (never Date.now(), which would defeat the fix).
// Server encoding: picamera2's misnamed "RGB888" numpy array is already
// B,G,R; the edge adapter passes it straight through to cv2.imencode.
// If colours ever look magenta again, check edge_glass/utils/camera.py.
const SnapshotImage = ({ node, iconSize = 48 }) => {
  const frozen = node.status === 'offline' || node.upload > 60;
  const wantsLiveImg = node.type === 'camera' && !frozen;
  if (wantsLiveImg && node.snapshotTimestamp) {
    return (
      <img
        src={`/api/edge/${node.id}/snapshot/latest?t=${node.snapshotTimestamp}`}
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

const DriftMeter = React.memo(({ sec, max = 30 }) => {
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
}, (prev, next) => prev.sec === next.sec && prev.max === next.max);

// ---------- Overlay stack (Escape-key top-of-stack precedence) ----------
// Every overlay in this file (ShortcutsModal, MuteDrawer, CommandPalette,
// NodeSidePanel, the logout confirmation dialog, the mobile nav drawer)
// registers its own Escape-key listener. Without coordination, opening a
// second overlay on top of a first means BOTH close on a single Escape press
// (every listener fires independently — they're all attached to
// window/document, not nested inside one another, so stopPropagation between
// them does nothing). window.__SDPRS_OVERLAY_STACK tracks currently-open
// overlays in open-order; each overlay calls useOverlayTop(isOpen) and only
// acts on Escape when it reports true (i.e. it is the most-recently-opened
// overlay still open).
window.__SDPRS_OVERLAY_STACK = window.__SDPRS_OVERLAY_STACK || [];
let __sdprsOverlaySeq = 0;
function useOverlayTop(active) {
  const idRef = useRef(null);
  useEffect(() => {
    if (!active) return;
    const id = ++__sdprsOverlaySeq;
    idRef.current = id;
    window.__SDPRS_OVERLAY_STACK.push(id);
    return () => {
      window.__SDPRS_OVERLAY_STACK = window.__SDPRS_OVERLAY_STACK.filter(x => x !== id);
      idRef.current = null;
    };
  }, [active]);
  return () => {
    const stack = window.__SDPRS_OVERLAY_STACK;
    return stack.length > 0 && stack[stack.length - 1] === idRef.current;
  };
}

// ---------- Status Strip ----------

const StatusStrip = React.memo(({ unackCount, muted, setMuted, theme, setTheme, onOpenShortcuts, page, setPage, onOpenMuteDrawer, audioReplayIn, muteState, operators, staleAckCount, onOpenCmdK, focusMode, onToggleFocus }) => {
  const { liveSec } = React.useContext(LiveClockContext);
  const liveState = liveSec < 10 ? 'ok' : liveSec < 30 ? 'warn' : 'critical';
  const liveLabel = liveSec < 10 ? `Live · ${liveSec}s` : liveSec < 30 ? `Reconnecting… ${liveSec}s` : `Disconnected ${liveSec}s`;
  const tones = { ok: 'bg-sev-ok/15 text-sev-ok border-sev-ok/40', warn: 'bg-sev-warn/15 text-sev-warn border-sev-warn/40', critical: 'bg-sev-critical/15 text-sev-critical border-sev-critical/40' };
  const activeMutes = (muted ? 1 : 0) + (muteState?.nodes?.length || 0) + (muteState?.lightning ? 1 : 0);
  const [logoutConfirmOpen, setLogoutConfirmOpen] = useState(false);
  const logoutDialogRef = useRef(null);
  const logoutTriggerRef = useRef(null);
  const logoutConfirmBtnRef = useRef(null);
  const isLogoutTop = useOverlayTop(logoutConfirmOpen);

  // Focus trap for logout confirmation dialog. Captures the trigger element
  // on open so focus can be restored on close. Traps Tab/Shift+Tab within
  // the dialog's focusable elements. On open, focus moves to the primary
  // (destructive) button so keyboard users land directly on it. Escape closes
  // — guarded by useOverlayTop so it only fires when this dialog is the
  // topmost open overlay (see F4 / useOverlayTop above).
  useEffect(() => {
    if (!logoutConfirmOpen) return;
    logoutTriggerRef.current = document.activeElement;
    if (logoutConfirmBtnRef.current) {
      try { logoutConfirmBtnRef.current.focus(); } catch (_) {}
    }
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        if (!isLogoutTop()) return;
        e.stopPropagation();
        setLogoutConfirmOpen(false);
        return;
      }
      if (e.key === 'Tab' && logoutDialogRef.current) {
        const focusable = logoutDialogRef.current.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      if (logoutTriggerRef.current && typeof logoutTriggerRef.current.focus === 'function') {
        try { logoutTriggerRef.current.focus(); } catch (_) {}
      }
    };
  }, [logoutConfirmOpen]);

  // Reactive audio-armed state — flips true after the first user gesture
  // anywhere on the page (see AudioController's one-shot listener). The
  // subscribe() hook keeps the pill in sync without prop-drilling.
  const [audioArmed, setAudioArmed] = useState(() => !!(window.SDPRS_AUDIO && window.SDPRS_AUDIO.isArmed()));
  useEffect(() => {
    if (!window.SDPRS_AUDIO || typeof window.SDPRS_AUDIO.subscribe !== 'function') return;
    const off = window.SDPRS_AUDIO.subscribe(() => setAudioArmed(window.SDPRS_AUDIO.isArmed()));
    return () => { off && off(); };
  }, []);

  // Play a tone the moment a NEW unack alert appears. Uses a ref to hold
  // the previous count so unrelated re-renders don't retrigger. Reads
  // window.ALERTS at effect-time to honor per-alert severity and per-node snooze.
  const prevUnackRef = useRef(unackCount);
  useEffect(() => {
    const prev = prevUnackRef.current;
    prevUnackRef.current = unackCount;
    if (muted || !window.SDPRS_AUDIO) return;
    // Operator-armed lightning suppression: skip the alert tone so the
    // app.jsx `weather` WS handler owns the auto-mute during a strike
    // (setMuted(true) on count>0, setMuted(false) on clear).
    if (muteState?.lightning) return;
    if (unackCount > prev) {
      const newest = (window.ALERTS || []).find(a => a.state === 'pending' && !(a.acknowledged_by || a.ackBy));
      if (newest && muteState?.nodes?.includes(newest.node)) return;
      const sev = newest?.sev || 'critical';
      const method = sev === 'critical' ? 'playCritical' : sev === 'warn' ? 'playWarning' : null;
      if (method) { try { window.SDPRS_AUDIO[method](); } catch (_) {} }
    }
  }, [unackCount, muted, muteState]);

  // Replay tone when the audio countdown resets (transitions from a small
  // value UP to a larger one, e.g. 1 → 30). app.jsx owns the countdown ticker;
  // we just react to the reset edge, which happens exactly when the
  // "replay every 30s while unacked" loop cycles.
  const prevReplayRef = useRef(audioReplayIn);
  useEffect(() => {
    const prev = prevReplayRef.current;
    prevReplayRef.current = audioReplayIn;
    if (muted || !window.SDPRS_AUDIO) return;
    if (muteState?.lightning) return;
    if (unackCount > 0 && prev != null && audioReplayIn != null && audioReplayIn > prev) {
      const newest = (window.ALERTS || []).find(a => a.state === 'pending' && !(a.acknowledged_by || a.ackBy));
      if (newest && muteState?.nodes?.includes(newest.node)) return;
      const sev = newest?.sev || 'critical';
      const method = sev === 'critical' ? 'playCritical' : sev === 'warn' ? 'playWarning' : null;
      if (method) { try { window.SDPRS_AUDIO[method](); } catch (_) {} }
    }
  }, [audioReplayIn, unackCount, muted, muteState]);

  return (
    <div className="h-12 fixed inset-x-0 top-0 z-40 bg-surface-panel border-b border-border-subtle flex items-center pl-14 md:pl-4 pr-4 gap-3 noselect">
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

      {/* Right cluster. On <md this row can exceed the viewport width (all
          controls render at every breakpoint save for the three explicitly
          `hidden md:*`'d below); the body has global overflow:hidden so any
          overflow here would be invisible AND unreachable. overflow-x-auto +
          flex-shrink-0 on each child keeps every control reachable via a
          horizontal scroll gesture instead of being clipped (F1). */}
      <div className="flex items-center gap-2 md:gap-1 min-w-0 overflow-x-auto scroll-thin">
        {operators && operators.length > 1 && <OperatorsCluster operators={operators} currentUser={window.SDPRS_USER || ''}/>}
        <button onClick={onOpenCmdK} title="命令面板 (⌘K / Ctrl+K)" className="hidden md:flex flex-shrink-0 items-center gap-1 h-7 px-2 ml-1 rounded border border-border-subtle bg-surface-elevated hover:bg-surface-overlay text-xs text-ink-muted transition-colors">
          <Icon.Search size={12}/> <span>跳轉...</span> <Kbd>⌘K</Kbd>
        </button>
        {/* Touch targets bumped to 44×44 on mobile (F9); desktop keeps the
            original 32×32 visual size via the md: override. */}
        <button onClick={onToggleFocus} title="夜深 / 專注模式 (Ctrl+.)"
          aria-pressed={!!focusMode}
          aria-label={focusMode ? '關閉專注模式' : '啟用專注模式（隱藏資訊級警報）'}
          className={`flex-shrink-0 w-11 h-11 md:w-8 md:h-8 rounded flex items-center justify-center transition-colors ${focusMode ? 'text-sev-info bg-sev-info/10 ring-1 ring-sev-info/60' : 'text-ink-muted hover:text-ink-primary hover:bg-surface-elevated'}`}>
          <Icon.Moon size={16} aria-hidden="true"/>
        </button>
        <button onClick={onOpenShortcuts} title="鍵盤捷徑 (?)" aria-label="開啟鍵盤捷徑說明" className="flex-shrink-0 w-11 h-11 md:w-8 md:h-8 rounded flex items-center justify-center text-ink-muted hover:text-ink-primary hover:bg-surface-elevated transition-colors">
          <Icon.Keyboard size={16} aria-hidden="true"/>
        </button>
        <button onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} title="Theme (T)"
          aria-label={theme === 'dark' ? '切換為淺色主題' : '切換為深色主題'}
          className="flex-shrink-0 w-11 h-11 md:w-8 md:h-8 rounded flex items-center justify-center text-ink-muted hover:text-ink-primary hover:bg-surface-elevated transition-colors">
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
            className={`hidden md:inline-flex flex-shrink-0 items-center gap-1 h-6 px-2 rounded border text-[10px] font-medium tnum whitespace-nowrap transition-colors ${audioArmed ? 'border-sev-ok/40 bg-sev-ok/10 text-sev-ok cursor-default' : 'border-sev-warn/40 bg-sev-warn/10 text-sev-warn hover:bg-sev-warn/20 animate-live-blink'}`}
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
          className={`relative flex-shrink-0 w-11 h-11 md:w-8 md:h-8 rounded flex items-center justify-center transition-colors ${activeMutes > 0 ? 'text-sev-warn hover:bg-sev-warn/10 ring-1 ring-sev-warn/60' : 'text-ink-muted hover:text-ink-primary hover:bg-surface-elevated'}`}
        >
          {muted ? <Icon.VolumeX size={16} aria-hidden="true"/> : <Icon.Volume2 size={16} aria-hidden="true"/>}
          {activeMutes > 0 && <span aria-hidden="true" className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-sev-warn text-[9px] font-bold text-black flex items-center justify-center tnum">{activeMutes}</span>}
        </button>
        <div className="flex-shrink-0 w-px h-6 bg-border-subtle mx-1"></div>
        <button onClick={() => setLogoutConfirmOpen(true)} className="flex-shrink-0 flex items-center gap-2 h-8 pl-1 pr-2 rounded hover:bg-surface-elevated transition-colors" title="點擊登出">
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
      {logoutConfirmOpen && (
        <div ref={logoutDialogRef} role="dialog" aria-modal="true" aria-labelledby="logout-confirm-title" className="fixed inset-0 z-[80] bg-black/60 flex items-center justify-center p-4" onClick={() => setLogoutConfirmOpen(false)}>
          <div className="bg-surface-elevated border border-border-strong rounded-lg p-5 max-w-xs w-full shadow-2xl" onClick={e => e.stopPropagation()}>
            <div id="logout-confirm-title" className="text-sm font-semibold text-ink-primary mb-3">確定要登出嗎？</div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setLogoutConfirmOpen(false)} className="px-3 h-8 rounded text-sm text-ink-secondary hover:bg-surface-overlay">取消</button>
              <button ref={logoutConfirmBtnRef} onClick={() => { window.location.href = '/logout'; }} className="px-3 h-8 rounded text-sm bg-sev-critical text-white font-medium hover:bg-red-700">登出</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
});

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

const NavRail = React.memo(({ page, setPage, density, setDensity, unackCount, offlineCount }) => {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const drawerRef = useRef(null);
  const hamburgerRef = useRef(null);
  // Element that had focus before the drawer opened; restored on close so
  // keyboard/screen-reader users don't get dumped back at <body>. WCAG 2.4.3.
  const lastFocusedRef = useRef(null);

  const closeDrawer = () => setMobileNavOpen(false);
  const isNavDrawerTop = useOverlayTop(mobileNavOpen);

  // Navigate and close drawer on nav item click.
  const handleNavClick = (id) => {
    setPage(id);
    setMobileNavOpen(false);
  };

  // Focus trap + Escape + focus restore. On open, capture the currently-focused
  // element then move focus into the drawer. On close, restore focus. Tab and
  // Shift+Tab are trapped within the drawer's focusable elements. Escape is
  // guarded by useOverlayTop so it only closes this drawer when it's the
  // topmost open overlay (see F4 / useOverlayTop above) — otherwise a
  // MuteDrawer/CommandPalette/etc. opened on top of it would also disappear.
  useEffect(() => {
    if (!mobileNavOpen) return;
    lastFocusedRef.current = (typeof document !== 'undefined') ? document.activeElement : null;
    // Focus the first nav button inside the drawer after the slide-in begins.
    const focusTimer = setTimeout(() => {
      const firstBtn = drawerRef.current && drawerRef.current.querySelector('button');
      if (firstBtn) { try { firstBtn.focus(); } catch (_) {} }
    }, 50);
    const onKey = (e) => {
      if (e.key === 'Escape') {
        if (!isNavDrawerTop()) return;
        e.stopPropagation();
        closeDrawer();
        return;
      }
      if (e.key === 'Tab' && drawerRef.current) {
        const focusable = drawerRef.current.querySelectorAll(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey) {
          if (document.activeElement === first) { e.preventDefault(); last.focus(); }
        } else {
          if (document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      clearTimeout(focusTimer);
      window.removeEventListener('keydown', onKey);
      const el = lastFocusedRef.current;
      lastFocusedRef.current = null;
      if (el && typeof el.focus === 'function') {
        try { el.focus(); } catch (_) {}
      }
    };
  }, [mobileNavOpen]);

  // Shared nav content rendered inside both the desktop rail and mobile drawer.
  const navContent = (
    <>
      <div className="px-3 py-2 text-[10px] uppercase tracking-widest text-ink-muted font-semibold">操作站</div>
      <div className="flex-1 px-2 space-y-0.5 overflow-y-auto scroll-thin">
        {NAV_ITEMS.map(item => {
          const active = page === item.id;
          const Ico = item.Icon;
          const badgeVal = item.badge === 'unack' ? unackCount : item.badge === 'offline' ? offlineCount : null;
          return (
            <button
              key={item.id}
              onClick={() => handleNavClick(item.id)}
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
    </>
  );

  return (
    <>
      {/* Mobile hamburger — floats over the top-left of StatusStrip on <md */}
      <button
        ref={hamburgerRef}
        type="button"
        onClick={() => setMobileNavOpen(v => !v)}
        aria-label={mobileNavOpen ? '關閉導覽' : '開啟導覽'}
        aria-expanded={mobileNavOpen}
        className="md:hidden fixed top-1.5 left-2 z-[60] w-9 h-9 rounded bg-surface-elevated border border-border-subtle text-ink-primary text-lg leading-none flex items-center justify-center hover:bg-surface-overlay"
      >
        {mobileNavOpen ? '✕' : '☰'}
      </button>

      {/* Mobile backdrop — semi-transparent overlay, click to dismiss */}
      {mobileNavOpen && (
        <div
          className="md:hidden fixed inset-0 z-40 bg-black/50"
          onClick={closeDrawer}
          aria-hidden="true"
        />
      )}

      {/* Mobile slide-out drawer — always rendered, off-screen when closed so
          the CSS transition can animate the slide-in. aria-hidden when closed
          prevents screen readers from reading the off-screen content, and
          `inert` (F11) additionally removes its buttons from the tab order
          and blocks interaction — aria-hidden alone doesn't stop a sighted
          keyboard user from tabbing into off-screen content. Empty-string
          value (rather than a boolean) is intentional: this React/react-dom
          build (18.3.1) has no built-in prop mapping for `inert`, so it falls
          through the generic custom-attribute path, where a boolean `true`
          is dropped entirely but a string value is passed through verbatim
          via setAttribute, and `undefined` removes the attribute. */}
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal={mobileNavOpen}
        aria-label="導覽選單"
        aria-hidden={!mobileNavOpen}
        inert={!mobileNavOpen ? '' : undefined}
        className={`md:hidden fixed left-0 top-12 bottom-10 w-56 z-50 bg-surface-panel border-r border-border-subtle flex flex-col noselect transform transition-transform duration-300 ease-in-out ${mobileNavOpen ? 'translate-x-0' : '-translate-x-full'}`}
      >
        {navContent}
      </div>

      {/* Desktop nav rail */}
      <nav className="hidden md:flex w-56 fixed left-0 top-12 bottom-10 bg-surface-panel border-r border-border-subtle flex-col noselect">
        {navContent}
      </nav>
    </>
  );
});

// ---------- Footer ----------

const Sparkline = React.memo(({ data, width = 240, height = 28 }) => {
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
}, (prev, next) => prev.data === next.data && prev.width === next.width && prev.height === next.height);

const Footer = React.memo(({ data, handover }) => {
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
});

// ---------- Shortcuts Modal ----------

const SHORTCUTS = [
  { keys: ['/'], label: '搜尋', cat: '導覽' },
  { keys: ['1','2','3','4','5','6','7'], label: '切換頁面', cat: '導覽' },
  { keys: ['A'], label: '認領並前往下一筆', cat: '警報處置' },
  { keys: ['Shift','A'], label: '認領但停留', cat: '警報處置' },
  { keys: ['R'], label: '解決選取的警報', cat: '警報處置' },
  { keys: ['N'], label: '跳至下一筆未認領', cat: '警報處置' },
  { keys: ['1','...','6'], label: '套用解決模板', cat: '警報處置' },
  { keys: ['M'], label: '開啟音效抑制面板', cat: '全域' },
  { keys: ['T'], label: '切換主題', cat: '全域' },
  { keys: ['Shift','D'], label: '切換密度', cat: '全域' },
  { keys: ['Esc'], label: '關閉詳情/對話框', cat: '全域' },
  { keys: ['↑','↓'], label: '上下移動列表', cat: '警報處置' },
  { keys: ['?'], label: '顯示此說明', cat: '全域' },
];

const ShortcutsModal = ({ open, onClose }) => {
  const [q, setQ] = useState('');
  const dialogRef = useRef(null);
  const lastFocusedRef = useRef(null);
  const isTop = useOverlayTop(open);
  React.useEffect(() => {
    if (!open) return;
    lastFocusedRef.current = document.activeElement;
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        if (!isTop()) return;
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === 'Tab' && dialogRef.current) {
        const focusable = dialogRef.current.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      if (lastFocusedRef.current && typeof lastFocusedRef.current.focus === 'function') {
        try { lastFocusedRef.current.focus(); } catch (_) {}
      }
    };
  }, [open, onClose]);
  if (!open) return null;
  const matches = SHORTCUTS.filter(s =>
    !q || s.label.toLowerCase().includes(q.toLowerCase()) || s.keys.some(k => k.toLowerCase().includes(q.toLowerCase())) || s.cat.toLowerCase().includes(q.toLowerCase())
  );
  const byCat = matches.reduce((acc, s) => { (acc[s.cat] = acc[s.cat] || []).push(s); return acc; }, {});
  return (
    <div className="fixed inset-0 z-[70] bg-surface-base/80 backdrop-blur-sm flex items-center justify-center p-6" onClick={onClose}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="鍵盤捷徑"
        className="bg-surface-panel border border-border-strong rounded-lg max-w-2xl w-full"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-border-subtle">
          <h2 className="text-base font-semibold flex items-center gap-2"><Icon.Keyboard size={18}/> 鍵盤捷徑</h2>
          <button onClick={onClose} aria-label="關閉" title="關閉 (Esc)" className="text-ink-muted hover:text-ink-primary"><Icon.X size={18}/></button>
        </div>
        <div className="px-5 pt-3 pb-2 border-b border-border-subtle">
          <div className="relative">
            <Icon.Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-muted"/>
            <input
              autoFocus
              value={q} onChange={e => setQ(e.target.value)}
              placeholder="搜尋捷徑或動作..."
              aria-label="搜尋快捷鍵"
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
  // Element that had focus before the drawer opened; restored on close so
  // keyboard/screen-reader users don't get dumped back at <body>. WCAG 2.4.3.
  const lastFocusedRef = useRef(null);
  // Inline error for the "解除" unsnooze API call; keyed by node id.
  const [unsnoozeErr, setUnsnoozeErr] = useState(null);
  const isTop = useOverlayTop(open);
  // No local countdown ticker: n.snoozeMin is already a whole-minute value
  // from api.jsx mapNode and only refreshes on the parent poll (~20s). A
  // sub-minute setInterval would just re-render without changing the number,
  // making the "剩餘 X 分鐘" text appear to jump on the poll edge instead of
  // ticking smoothly. Live smooth countdown would need snoozeUntil (ms epoch)
  // exposed on the node — deferred to api.jsx.
  // Focus + Escape trap. On open, capture the currently-focused element then
  // focus lands on the panel's heading (not the ✕ close button) so screen
  // readers announce the panel's purpose. On close, focus is restored to the
  // element that opened the drawer. See H-9. Tab/Shift+Tab are trapped within
  // the drawer's focusable elements (F8); Escape is guarded by useOverlayTop
  // so it only closes this drawer when it's the topmost open overlay (F4).
  useEffect(() => {
    if (!open) return;
    lastFocusedRef.current = (typeof document !== 'undefined') ? document.activeElement : null;
    if (headingRef.current) {
      try { headingRef.current.focus(); } catch (_) {}
    }
    const onKey = (e) => {
      if (e.key === 'Escape') {
        if (!isTop()) return;
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === 'Tab' && drawerRef.current) {
        const focusable = drawerRef.current.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      const el = lastFocusedRef.current;
      lastFocusedRef.current = null;
      if (el && typeof el.focus === 'function') {
        // Trigger may have unmounted (e.g. row removed by a refresh) — swallow.
        try { el.focus(); } catch (_) {}
      }
    };
  }, [open, onClose]);
  if (!open) return null;
  // Use prop-supplied nodes (React state from caller — always fresh).
  const nodeList = Array.isArray(nodes) ? nodes : [];
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
      setMuteState(prev => ({ ...prev, nodes: prev.nodes.filter(x => x !== nid) }));
    } catch (err) {
      console.error('unsnooze failed', err);
      setUnsnoozeErr({ nid, msg: '伺服器解除失敗，請重試' });
    }
  };
  // Unsnooze all snoozed nodes. Preserve global + lightning flags so an
  // operator clearing stale per-node snoozes during a planned drill doesn't
  // silently drop their intentional global mute. Errors on any node are
  // collected and surfaced without wiping the remaining flags.
  const unsnoozeAll = async () => {
    setUnsnoozeErr(null);
    const targets = [...muteState.nodes];
    if (window.SDPRS_API && typeof window.SDPRS_API.unsnoozeNode === 'function' && targets.length > 0) {
      const results = await Promise.allSettled(targets.map(nid => window.SDPRS_API.unsnoozeNode(nid)));
      // Only remove nodes whose API call actually succeeded — a blanket
      // clear on partial failure silently drops still-snoozed nodes from
      // the UI until the next 20s poll re-surfaces them (operator thinks
      // "解除" worked when it didn't).
      const succeededNodes = targets.filter((nodeId, index) =>
        results[index].status === 'fulfilled'
      );
      const failedCount = results.filter(r => r.status === 'rejected').length;
      setMuteState(prev => ({
        ...prev,
        nodes: prev.nodes.filter(nodeId => !succeededNodes.includes(nodeId))
      }));
      if (failedCount > 0) {
        setUnsnoozeErr({ nid: null, msg: `${failedCount} 個節點解除失敗` });
      }
    } else {
      setMuteState(prev => ({ ...prev, nodes: [] }));
    }
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
          <button onClick={onClose} aria-label="關閉" title="關閉 (Esc)" className="text-ink-muted hover:text-ink-primary"><Icon.X size={18}/></button>
        </div>

        <div className="p-5 space-y-4">
          {/* Volume slider */}
          <div>
            <div className="text-[10px] uppercase tracking-widest text-ink-muted font-semibold mb-2">音量</div>
            <div className="bg-surface-elevated border border-border-subtle rounded p-3">
              <VolumeSlider
                value={muteState.volume ?? 70}
                onChange={(v) => {
                  setMuteState(prev => ({ ...prev, volume: v }));
                  if (window.SDPRS_AUDIO) { try { window.SDPRS_AUDIO.setVolume(v); } catch (_) {} }
                }}
              />
              {/* Disable test buttons when global mute is on: beep() short-circuits
                  on muted, so a live-looking button that emits no sound reads as
                  "audio broken" to operators. The disabled+tooltip surface tells
                  them to unmute instead of filing a hardware ticket. */}
              <div className="flex items-center justify-between mt-3 text-xs">
                <span className="text-ink-muted">測試音效:</span>
                <div className="flex gap-1">
                  <button onClick={() => playTest('critical', '嚴重')} disabled={muteState.global} title={muteState.global ? '已靜音 — 請先取消靜音' : undefined} className="px-2 h-6 bg-sev-critical/15 text-sev-critical border border-sev-critical/30 rounded text-[10px] font-medium hover:bg-sev-critical/25 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-sev-critical/15">嚴重</button>
                  <button onClick={() => playTest('warning', '警告')} disabled={muteState.global} title={muteState.global ? '已靜音 — 請先取消靜音' : undefined} className="px-2 h-6 bg-sev-warn/15 text-sev-warn border border-sev-warn/30 rounded text-[10px] font-medium hover:bg-sev-warn/25 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-sev-warn/15">警告</button>
                  <button onClick={() => playTest('ack', '確認')} disabled={muteState.global} title={muteState.global ? '已靜音 — 請先取消靜音' : undefined} className="px-2 h-6 bg-sev-info/15 text-sev-info border border-sev-info/30 rounded text-[10px] font-medium hover:bg-sev-info/25 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-sev-info/15">確認</button>
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
                  setMuteState(prev => ({ ...prev, global: next }));
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
                  // snoozed_until − now() and only refreshes on the parent
                  // poll (~20s). snoozedBy / snoozedAt are surfaced by
                  // api.jsx mapNode — only render provenance when snoozedBy
                  // is truthy (never fake).
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
                onClick={() => setMuteState(prev => ({ ...prev, lightning: !prev.lightning }))}
                className={`px-2.5 h-6 rounded text-xs font-medium ${muteState.lightning ? 'bg-sev-warn text-black' : 'bg-surface-overlay text-ink-muted'}`}
              >
                {muteState.lightning ? '啟用' : '停用'}
              </button>
            </div>
          </div>

          <button
            onClick={unsnoozeAll}
            disabled={muteState.nodes.length === 0}
            className="w-full mt-2 h-9 bg-sev-info hover:bg-blue-600 text-white rounded text-sm font-semibold disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-sev-info"
          >
            解除所有節點延期
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

// Optional `onClear` renders a × affordance inside the chip and enables
// Delete/Backspace when the chip has keyboard focus. When `onClear` is not
// provided, the chip is byte-for-byte the original toggle button — existing
// callers keep working unchanged.
const FilterChip = ({ active, onClick, children, count, onClear }) => {
  const handleKeyDown = (e) => {
    if (onClear && (e.key === 'Delete' || e.key === 'Backspace')) {
      e.preventDefault();
      onClear();
    }
  };
  // The × sits inside the outer <button>; we avoid a nested real <button>
  // (invalid HTML content model) by using a plain <span> whose click stops
  // propagation so the chip toggle doesn't also fire. Keyboard users clear
  // via Delete/Backspace on the focused chip (handled above), so the span
  // doesn't need its own tab stop.
  const handleClearClick = (e) => {
    e.stopPropagation();
    onClear && onClear();
  };
  return (
    <button onClick={onClick}
      onKeyDown={onClear ? handleKeyDown : undefined}
      aria-pressed={!!active}
      className={`inline-flex items-center gap-1 px-2 h-6 rounded text-xs border transition-colors ${active ? 'bg-sev-info/15 text-sev-info border-sev-info/40' : 'bg-surface-elevated text-ink-secondary border-border-subtle hover:border-border-strong'}`}>
      {children}
      {count != null && <span className="font-mono tnum text-[10px] text-ink-muted">{count}</span>}
      {onClear && (
        <span
          onClick={handleClearClick}
          title="清除 (Delete)"
          aria-label="清除此篩選"
          className="ml-0.5 inline-flex items-center justify-center w-3.5 h-3.5 rounded hover:bg-ink-muted/20 text-ink-muted hover:text-ink-primary leading-none cursor-pointer"
        >
          ×
        </span>
      )}
    </button>
  );
};

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
    <div className="flex-shrink-0 flex items-center gap-1 h-6 px-1.5 rounded border border-border-subtle bg-surface-elevated">
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
  // Field-name flex: try the audit-suggested name first, then fall back to
  // '—'. Previously fell back to s.handled / s.alertsHandled (total resolved
  // count) which is semantically wrong — carryOver means "still unresolved",
  // so substituting the handled count tells the operator the opposite of
  // reality (audit fix).
  const carryOver = s.carryOver ?? '—';
  const snoozed   = s.snoozed   ?? '—';
  // Same audit fix: `pending` (unhandled-new count) is not `s.critical`
  // (critical-severity count) — dropping the semantically-wrong fallback.
  const pending   = s.pending   ?? '—';
  const recent = s.recentIncident
    ?? (Array.isArray(s.highlights) && s.highlights.length ? s.highlights.join(' · ') : '尚無交接事項');
  return (
    <div className="fixed top-14 right-4 z-40 w-[360px] bg-surface-panel border border-sev-info/40 rounded-lg shadow-2xl overflow-hidden">
      <div className="px-4 py-2.5 bg-sev-info/15 border-b border-sev-info/30 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sev-info">
          <Icon.ClipboardList size={14}/>
          <span className="text-sm font-semibold">班次接班摘要 · {operator}</span>
        </div>
        <button onClick={onDismiss} aria-label="關閉" title="關閉" className="text-ink-muted hover:text-ink-primary"><Icon.X size={14}/></button>
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
        <button
          onClick={onViewHandover ?? (() => {})}
          className="w-full h-8 bg-sev-info text-white rounded text-xs font-semibold hover:bg-blue-600"
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
  const lastFocusedRef = useRef(null);
  const isTop = useOverlayTop(open);

  React.useEffect(() => {
    if (open) { setQ(''); setHi(0); }
  }, [open]);

  // Escape closes + focus trap + focus restore on close. Escape is guarded
  // by useOverlayTop so it only fires when this palette is the topmost open
  // overlay (F4 / useOverlayTop above).
  React.useEffect(() => {
    if (!open) return;
    lastFocusedRef.current = document.activeElement;
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        if (!isTop()) return;
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === 'Tab' && paletteRef.current) {
        const focusable = paletteRef.current.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      if (lastFocusedRef.current && typeof lastFocusedRef.current.focus === 'function') {
        try { lastFocusedRef.current.focus(); } catch (_) {}
      }
    };
  }, [open, onClose]);

  if (!open) return null;

  // Use React-state nodes from the caller.
  const nodeList = Array.isArray(nodes) ? nodes : [];

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
    <div className="fixed inset-0 z-[70] bg-surface-base/60 backdrop-blur-sm flex items-start justify-center pt-24" onClick={onClose}>
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
            aria-label="搜尋指令、警報和頁面"
            className="flex-1 h-11 px-3 bg-transparent text-sm placeholder-ink-muted focus:outline-none"
            role="combobox"
            aria-expanded="true"
            aria-controls="cmdk-listbox"
            aria-autocomplete="list"
            aria-activedescendant={matches.length > 0 ? `cmd-item-${hi}` : undefined}
          />
          <Kbd>Esc</Kbd>
        </div>
        <div
          id="cmdk-listbox"
          role="listbox"
          aria-label="命令搜尋結果"
          className="max-h-[60vh] overflow-y-auto scroll-thin py-1"
        >
          {matches.length === 0 ? (
            <div className="text-center text-sm text-ink-muted py-8">找不到符合的項目</div>
          ) : (
            matches.map((it, i) => {
              const Ico = it.icon;
              const selected = hi === i;
              return (
                <button key={`${it.kind}-${it.id}-${i}`}
                  id={`cmd-item-${i}`}
                  role="option"
                  aria-selected={selected}
                  onClick={() => fire(it)}
                  onMouseEnter={() => setHi(i)}
                  className={`w-full px-4 py-2 flex items-center gap-3 text-left ${selected ? 'bg-sev-info/10' : ''}`}
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
  const [saving, setSaving] = useState(false);
  const panelRef = useRef(null);
  const headingRef = useRef(null);
  // Element that had focus before the panel opened; restored on close so
  // keyboard/screen-reader users don't get dumped back at <body>. WCAG 2.4.3.
  const lastFocusedRef = useRef(null);
  const isTop = useOverlayTop(!!node);
  React.useEffect(() => {
    if (node) {
      setDraft({ location: node.location || '' });
      setEditing(false);
      setLocationError(null);
      setSaving(false);
    }
  }, [node?.id]);

  // A11y: Escape closes (guarded by useOverlayTop so it only fires when this
  // panel is the topmost open overlay — F4); Tab/Shift+Tab are trapped within
  // the panel's focusable elements (F8). On open, capture the
  // previously-focused element then focus lands on the panel heading (not
  // the ✕ close button) so screen readers announce the node id/name first.
  // On close, focus is restored to the element that opened the panel. See H-9.
  React.useEffect(() => {
    if (!node) return;
    lastFocusedRef.current = (typeof document !== 'undefined') ? document.activeElement : null;
    if (headingRef.current) {
      try { headingRef.current.focus(); } catch (_) {}
    }
    const onKey = (e) => {
      if (e.key === 'Escape') {
        if (!isTop()) return;
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === 'Tab' && panelRef.current) {
        const focusable = panelRef.current.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      const el = lastFocusedRef.current;
      lastFocusedRef.current = null;
      if (el && typeof el.focus === 'function') {
        // Trigger (e.g. NodeCard tile) may have unmounted between opens — swallow.
        try { el.focus(); } catch (_) {}
      }
    };
  }, [node?.id, onClose]);

  if (!node) return null;
  const nodeAlerts = openAlerts.filter(a => a.node === node.id);
  // Prefer prop-supplied history (fresh from caller's useState); fall back to
  // the window global for legacy call sites that haven't been updated yet.
  // See C-8 — direct window reads were non-reactive so panel history looked
  // stale until the panel was re-opened.
  const items = history || (window.NODE_HISTORY?.[node?.id] || []);

  const saveEdits = async () => {
    const newLocation = (draft?.location || '').trim();
    if (!newLocation) {
      setLocationError('位置為必填');
      return;
    }
    setLocationError(null);
    setSaving(true);
    try {
      await onUpdateNode?.(node.id, { location: newLocation });
      setEditing(false);
    } catch (e) {
      setLocationError('儲存失敗，請重試');
    } finally {
      setSaving(false);
    }
  };

  // F3: Cancel must discard the in-progress edit, not just close the editor.
  // Previously this only flipped `editing` back to false — `draft` (and any
  // `locationError`) were left as-is, so reopening the editor on the same
  // node showed the abandoned edit instead of the saved node.location.
  const cancelEdits = () => {
    setDraft({ location: node.location || '' });
    setLocationError(null);
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
          <button onClick={onClose} aria-label="關閉" title="關閉 (Esc)" className="text-ink-muted hover:text-ink-primary"><Icon.X size={18}/></button>
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
                  <button onClick={cancelEdits} disabled={saving} className="text-[10px] text-ink-muted hover:text-ink-primary px-1.5 h-5 rounded bg-surface-elevated disabled:opacity-50">取消</button>
                  <button onClick={saveEdits} disabled={saving} aria-busy={saving} className="text-[10px] text-white px-1.5 h-5 rounded bg-sev-info hover:bg-blue-600 disabled:opacity-50">{saving ? '儲存中…' : '儲存'}</button>
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
                  <label htmlFor="node-location-input" className="text-[10px] text-ink-muted block mb-0.5">
                    位置 <span className="text-ink-dim">(格式: 樓層 · 區域)</span>
                  </label>
                  <input
                    id="node-location-input"
                    value={draft?.location ?? ''}
                    onChange={e => {
                      setDraft({ ...(draft || {}), location: e.target.value });
                      if (locationError) setLocationError(null);
                    }}
                    placeholder="例: 3F · 西側走廊"
                    aria-invalid={!!locationError}
                    aria-describedby={locationError ? 'node-location-error' : undefined}
                    className={`w-full h-7 px-2 text-xs bg-surface-base border rounded focus:outline-none ${locationError ? 'border-sev-critical focus:border-sev-critical' : 'border-border-strong focus:border-sev-info'}`}
                  />
                  {locationError && (
                    <div id="node-location-error" className="text-sev-critical text-xs mt-1">{locationError}</div>
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
                  <div className="font-mono tnum">{node.cycles > 0 ? '每 ' + (60 / node.cycles).toFixed(1) + 'm' : '—'}</div>
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
  const emit = (v) => {
    onChange && onChange(v);
    onVolumeChange && onVolumeChange(v);
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
