// SDPRS — App shell + state management

const { useState: useStateA, useEffect: useEffectA, useMemo: useMemoA, useCallback: useCallbackA, useRef: useRefA } = React;

const DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "dark",
  "density": "compact",
  "muted": false,
  "wallMode": false,
  "accent": "blue"
}/*EDITMODE-END*/;

// H-1: cross-login state carrier. Session-expiry redirect encodes {page,
// selectedId, hadDraft} into `?sdprs_state=<base64>` on the post-login target
// URL; on the fresh mount we decode, seed initial state, and strip the query
// param so a reload doesn't re-apply stale state. Runs once at script load
// (module IIFE) so the values are ready before App's useState initializers.
// resolveNote is intentionally NOT preserved — round-tripping a free-form
// operator note through the URL is a size + XSS risk not worth the payoff.
const RESTORED_STATE = (function () {
  try {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get('sdprs_state');
    if (!raw) return null;
    const parsed = JSON.parse(atob(raw));
    params.delete('sdprs_state');
    const newSearch = params.toString();
    const newUrl = window.location.pathname + (newSearch ? '?' + newSearch : '') + window.location.hash;
    window.history.replaceState(null, '', newUrl);
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch (_) {
    return null;
  }
})();

// Error boundary — catches render-time errors in page components so a single
// page crash doesn't unmount the entire app shell (nav, toasts, overlays).
// The retry button resets the boundary's error state, which re-renders the
// crashed child from scratch (picking up fresh props/state on the next pass).
class ErrorBoundary extends React.Component {
  state = { error: null };
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error, info) {
    console.error('[SDPRS] Page error:', error, info);
  }
  render() {
    if (this.state.error) {
      return (
        <div className="h-full flex items-center justify-center p-6">
          <div className="max-w-md text-center space-y-3">
            <div className="text-lg font-bold text-sev-critical">頁面發生錯誤</div>
            <div className="text-sm text-ink-secondary">
              此頁面在渲染時發生未預期的錯誤。請重試或聯絡系統管理員。
            </div>
            <div className="text-xs font-mono text-ink-muted break-all">
              {String(this.state.error?.message || this.state.error)}
            </div>
            <button
              onClick={() => this.setState({ error: null })}
              className="px-4 py-2 rounded bg-sev-info text-white text-sm font-bold hover:bg-sev-info/80"
            >
              重試
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// =================================================================
// LIVE CLOCK PROVIDER — isolates 1Hz tick from App
// =================================================================
// The liveSec counter increments every second and resets to 0 on each
// successful server contact (refresh or WS ping). By owning this state in a
// dedicated provider, only context consumers (StatusStrip, DriftMeter,
// WallView) re-render on the tick — the rest of the app tree is unaffected.
const LiveClockContext = window.LiveClockContext;

function LiveClockProvider({ children, registerReset }) {
  const [liveSec, setLiveSec] = useStateA(0);

  useEffectA(() => {
    const id = setInterval(() => setLiveSec(s => s + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const resetClock = useCallbackA(() => setLiveSec(0), []);

  // Register the reset function with the parent so it can call it imperatively
  // from runRefresh / onPing without needing to be a context consumer.
  useEffectA(() => {
    if (typeof registerReset === 'function') registerReset(resetClock);
    return () => { if (typeof registerReset === 'function') registerReset(null); };
  }, [registerReset, resetClock]);

  const value = useMemoA(() => ({ liveSec, resetClock }), [liveSec, resetClock]);

  return <LiveClockContext.Provider value={value}>{children}</LiveClockContext.Provider>;
}

// Shared frozen empty operators reference — used as the `??` fallback for
// `window.OPERATORS_ONLINE` so App's render doesn't hand StatusStrip a fresh
// `[]` on every tick (which would defeat the React.memo shallow-compare and
// force StatusStrip to re-render every parent update).
const EMPTY_OPERATORS = Object.freeze([]);

function App({ initialError = null }) {
  const [tweaks, setTweak] = window.useTweaks(DEFAULTS);

  // Bootstrap error surface — populated by bootstrap() below when loadInitial
  // rejects; renders the retry UI in place of the full app so mount-time
  // reads of window.ALERTS/NODES don't crash on undefined.
  const [bootstrapError, setBootstrapError] = useStateA(initialError);

  // F2: RESTORED_STATE (H-1 cross-login roundtrip) wins if present; otherwise
  // fall back to the page persisted in sessionStorage (see the persistence
  // effect below) so an F5 / tab reload doesn't dump the operator back to
  // Alerts. sessionStorage is tab-scoped and survives reloads but not tab
  // close, which is the right lifetime for "where was I" — try/catch covers
  // sessionStorage-disabled contexts (e.g. some locked-down kiosk browsers).
  const [page, setPageRaw] = useStateA(() => {
    if (RESTORED_STATE?.page) return RESTORED_STATE.page;
    try { return sessionStorage.getItem('sdprs.page') ?? 'alerts'; }
    catch (_) { return 'alerts'; }
  });
  const [pageHistory, setPageHistory] = useStateA([]); // for Alt+← back
  const [alerts, setAlerts] = useStateA(window.ALERTS ?? []);
  // load-bearing: setNodes() forces re-render so window.NODES reads pick up new data. DO NOT remove.
  const [nodes, setNodes] = useStateA(window.NODES ?? []);
  // C-8: NodeSidePanel history was previously read directly from
  // window.NODE_HISTORY, which is non-reactive — the panel showed stale
  // events until re-opened. Hoist history into React state so refresh() /
  // WS events propagate to the panel automatically.
  const [nodeHistory, setNodeHistory] = useStateA(window.NODE_HISTORY ?? {});
  // F2 (nice-to-have): same idea as `page` above, for the selected alert.
  // Matched by String() comparison since sessionStorage only stores strings
  // but alert ids may not be — on match we return the LIVE alert's `.id` (not
  // the raw string) so downstream `a.id === selectedId` strict-equality
  // checks throughout this file keep working against the original type.
  const [selectedId, setSelectedId] = useStateA(() => {
    if (RESTORED_STATE?.selectedId != null) return RESTORED_STATE.selectedId;
    try {
      const savedId = sessionStorage.getItem('sdprs.selectedId');
      if (savedId != null) {
        const match = (window.ALERTS ?? []).find(a => String(a.id) === savedId);
        if (match) return match.id;
      }
    } catch (_) { /* sessionStorage unavailable */ }
    return window.ALERTS?.[0]?.id ?? null;
  });
  // LiveClock ref — LiveClockProvider registers its reset function here so
  // runRefresh and onPing can reset the drift counter without App owning the state.
  const resetClockRef = useRefA(() => {});
  const registerClockReset = useCallbackA((fn) => { resetClockRef.current = fn || (() => {}); }, []);
  const [shortcutsOpen, setShortcutsOpen] = useStateA(false);
  const [muteDrawerOpen, setMuteDrawerOpen] = useStateA(false);
  const [cmdkOpen, setCmdkOpen] = useStateA(false);
  const [shiftBannerOpen, setShiftBannerOpen] = useStateA(false);
  // F3: store only the id, not a snapshot of the node object — the old
  // `useStateA(null)`-with-whole-node approach froze the panel's data at the
  // moment it opened, never re-syncing with live `nodes` updates (refresh /
  // WS events). Deriving the node from current `nodes` below keeps it live.
  const [nodePanelNodeId, setNodePanelNodeId] = useStateA(null);
  // Reactive lookup — recomputes whenever `nodes` refreshes (poll/WS) or the
  // selected id changes, instead of freezing a snapshot from open-time.
  // NodeSidePanel itself already treats a null `node` prop as "closed"
  // (`if (!node) return null;` in components.jsx), so a momentary miss here
  // (id set but the matching node hasn't landed in `nodes` yet/was removed)
  // safely renders nothing rather than crashing.
  const nodePanelNode = useMemoA(
    () => (nodePanelNodeId == null ? null : (nodes.find(n => n.id === nodePanelNodeId) ?? null)),
    [nodes, nodePanelNodeId]
  );
  // B4: persist focus mode across reloads (try/catch for Safari private mode).
  const [focusMode, setFocusMode] = useStateA(() => {
    try { return window.localStorage.getItem('sdprs.focusMode') === '1'; }
    catch (_) { return false; }
  });
  const [newAlertBannerCount, setNewAlertBannerCount] = useStateA(0);
  // Backend-signalled session expiry. Flips true when the /ws handler sends
  // {type: 'auth_expired'} immediately before its 1008 close (see
  // services/websocket_service.py). Renders a blocking modal + halts the
  // openSocket reconnect loop so we don't 1008-thrash forever.
  const [sessionExpired, setSessionExpired] = useStateA(false);
  const [muteState, setMuteState] = useStateA(() => {
    // Read persisted volume from localStorage so the slider survives reloads.
    // VolumeSlider itself already pushes to Howler on change; app.jsx owns
    // the persistence side (see the muteState.volume effect below).
    let persistedVolume = 70;
    try {
      const raw = localStorage.getItem('sdprs.volume');
      const v = parseInt(raw, 10);
      if (Number.isFinite(v) && v >= 0 && v <= 100) persistedVolume = v;
    } catch (_) { /* localStorage may be unavailable (private mode) */ }
    return {
      // Seed from persisted tweaks.muted so the mount-time sync effect below
      // (setTweak('muted', muteState.global)) never clobbers a persisted true
      // with our default false.
      global: !!tweaks.muted,
      nodes: [],
      lightning: false,
      volume: persistedVolume,
    };
  });
  const [ackedIds, setAckedIds] = useStateA(new Set());
  const [toast, setToast] = useStateA(null);
  // Partial data-load failures — populated when loadInitial() or refreshLive()
  // has some (but not all) loaders reject. Drives the warning banner below the
  // status strip so the operator knows which feeds are stale or unavailable.
  const [dataWarnings, setDataWarnings] = useStateA([]);
  const [audioReplayIn, setAudioReplayIn] = useStateA(30);

  // Refs used by callbacks below (declared here so JSX/hooks can reference
  // them). See setPage/goBack for the history flow, showToast for the timer,
  // and the wallMode-aware keyboard handler for the ref-based skip guard.
  const toastTimerRef = useRefA(null);
  const prevPageRef = useRefA('alerts');
  const skipNextHistoryPushRef = useRefA(false);
  const pageHistoryRef = useRefA([]);
  // Holds openSocket's teardown thunk so the auth_expired branch (below) can
  // cancel the reconnect loop from OUTSIDE the useEffect cleanup path.
  const wsStopRef = useRefA(null);
  // B5: last-ping wall-clock. A >30s gap between onPing calls means the
  // socket was down and just came back (pings are ~10s); we then trigger a
  // refresh + toast so the operator knows they may have missed events.
  const lastPingRef = useRefA(Date.now());
  // B3: ref for the session-expiry modal's sole focusable button — used by
  // the focus-trap effect below to keep Tab from escaping the modal.
  const sessionModalButtonRef = useRefA(null);
  // B5: guard so muteState.nodes is seeded from backend snooze data only on
  // the FIRST successful refresh — avoids clobbering user-driven mute toggles
  // on subsequent refreshes.
  const muteHydratedRef = useRefA(false);
  // Bug #1 fix: ref mirror so the WebSocket onEvent callback (which is
  // memoized once) reads the CURRENT muteState instead of the stale closure
  // captured at mount time. Without this, lightning auto-mute breaks after
  // the first render because muteState.lightning / muteState.global are
  // frozen at their initial values inside the callback.
  const muteStateRef = useRefA(muteState);
  muteStateRef.current = muteState;
  // F4: ref mirror of `sessionExpired` so the 20s poll interval and the
  // `online` recovery handler (both set up in effects with stable/unrelated
  // dep arrays — see below) can read the CURRENT modal-open state without
  // needing `sessionExpired` in their deps (which would tear down/rebuild
  // the WebSocket or the online/offline listeners on every expiry toggle).
  // Deliberately NOT window.__SDPRS_SESSION_EXPIRED: that flag is a one-shot
  // signal api.jsx/the 401-poll effect below reset to `false` the instant
  // it's consumed (see the `window.__SDPRS_SESSION_EXPIRED = false` line),
  // so it does not represent "the modal is currently up" — `sessionExpired`
  // state (also what gates the modal's own JSX and the Escape/Cmd+K guards
  // above) is the actual source of truth for that.
  const sessionExpiredRef = useRefA(sessionExpired);
  sessionExpiredRef.current = sessionExpired;
  // F5: guards the browser-history pushState effect below so it doesn't
  // double-push — once on mount (the initial entry is already seeded via
  // history.replaceState in the mount effect) and once whenever a `page`
  // change originates FROM a popstate event (which must only sync `page`,
  // never push ANOTHER entry on top of the one the user just navigated to).
  // Starts true to skip the mount run. Same idiom as skipNextHistoryPushRef
  // above, just for the browser's history stack instead of the Alt+← stack.
  const skipNextUrlPushRef = useRefA(true);

  // StrictMode-safe setPage: no side effects inside the state updater.
  // History push happens in a follow-up effect that reads prev via ref.
  const setPage = useCallbackA((p) => {
    setPageRaw(p);
  }, []);

  // Mirror pageHistory into a ref so goBack can read the current value
  // without depending on the state (keeps the callback identity stable).
  useEffectA(() => { pageHistoryRef.current = pageHistory; }, [pageHistory]);

  // History push on page change. Skipped once immediately after goBack() so
  // navigating backwards doesn't re-push the page we just left.
  useEffectA(() => {
    if (skipNextHistoryPushRef.current) {
      skipNextHistoryPushRef.current = false;
    } else if (prevPageRef.current !== page) {
      setPageHistory(h => [...h.slice(-9), prevPageRef.current]);
    }
    prevPageRef.current = page;
  }, [page]);

  const goBack = useCallbackA(() => {
    const h = pageHistoryRef.current;
    if (h.length === 0) return;
    const prev = h[h.length - 1];
    skipNextHistoryPushRef.current = true;
    setPageHistory(h.slice(0, -1));
    setPageRaw(prev);
  }, []);

  // F2: persist the current page to sessionStorage on every change, whatever
  // the source (setPage, goBack(), or the popstate listener below) — so an
  // F5 / tab reload restores the page the operator was actually on instead
  // of dumping them back to Alerts.
  useEffectA(() => {
    try { sessionStorage.setItem('sdprs.page', page); }
    catch (_) { /* sessionStorage unavailable (private mode) */ }
  }, [page]);

  // F2 (nice-to-have): same for the selected alert.
  useEffectA(() => {
    try {
      if (selectedId != null) sessionStorage.setItem('sdprs.selectedId', String(selectedId));
      else sessionStorage.removeItem('sdprs.selectedId');
    } catch (_) { /* sessionStorage unavailable (private mode) */ }
  }, [selectedId]);

  // F5: push a browser-history entry on every `page` change so the Back
  // button navigates in-app pages instead of leaving the SPA entirely.
  // Skipped once right after mount (that entry is seeded by the
  // replaceState in the mount effect below) and once right after a
  // popstate-driven change (see onPopState) — both via skipNextUrlPushRef —
  // so we never push a duplicate entry on top of the one already reflecting
  // reality. Deliberately keyed on `page` (not folded into setPage) so it
  // catches goBack() too, same reasoning as the sessionStorage effect above.
  useEffectA(() => {
    if (skipNextUrlPushRef.current) {
      skipNextUrlPushRef.current = false;
      return;
    }
    try { window.history.pushState({ page }, '', window.location.pathname + window.location.search); }
    catch (_) { /* history API unavailable (e.g. sandboxed iframe) */ }
  }, [page]);

  // F5: seed the initial history entry (so the very first Back press has
  // somewhere in-app to land) and install the popstate listener that takes
  // over from there. Runs once on mount — AFTER RESTORED_STATE/sessionStorage
  // have already produced the initial `page` value above, so this only tags
  // that existing entry (replaceState, not pushState) rather than
  // re-deriving it; the H-1 cross-login roundtrip is untouched.
  useEffectA(() => {
    try { window.history.replaceState({ page }, '', window.location.pathname + window.location.search); }
    catch (_) { /* history API unavailable */ }
    const onPopState = (event) => {
      skipNextUrlPushRef.current = true;
      setPageRaw(event.state?.page ?? 'alerts');
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
    // eslint: intentionally mount-only — `page` here is only the initial
    // seed value; subsequent navigation is driven by the effect above.
  }, []);

  // Apply theme/wall/focus classes
  useEffectA(() => {
    document.documentElement.classList.toggle('dark', tweaks.theme === 'dark');
    document.documentElement.classList.toggle('light', tweaks.theme === 'light');
    document.documentElement.classList.toggle('wall-mode', !!tweaks.wallMode);
    document.documentElement.classList.toggle('focus-mode', !!focusMode);
  }, [tweaks.theme, tweaks.wallMode, focusMode]);

  // B4: write focusMode back to localStorage on change.
  useEffectA(() => {
    try { window.localStorage.setItem('sdprs.focusMode', focusMode ? '1' : '0'); }
    catch (_) { /* localStorage unavailable (private mode) */ }
  }, [focusMode]);

  // Persist volume + push to the AudioController whenever muteState.volume
  // changes. Covers persistence AND callers that mutate muteState.volume
  // directly (e.g. bulk reset) — VolumeSlider's own onChange already fires
  // SDPRS_AUDIO.setVolume for the slider-driven path.
  useEffectA(() => {
    try {
      localStorage.setItem('sdprs.volume', String(muteState.volume));
      if (window.SDPRS_AUDIO && typeof window.SDPRS_AUDIO.setVolume === 'function') {
        window.SDPRS_AUDIO.setVolume(muteState.volume);
      }
    } catch (_) { /* localStorage / audio pipeline unavailable — safe to swallow */ }
  }, [muteState.volume]);

  const unackCount = useMemoA(() => alerts.filter(a => a.state === 'pending').length, [alerts]);
  // Read from local `nodes` state (which mirrors window.NODES via setNodes)
  // so this recomputes on refresh alongside its neighbours.
  const offlineCount = useMemoA(() => nodes.filter(n => n.status === 'offline').length, [nodes]);
  const staleAckCount = useMemoA(() => alerts.filter(a => a.state === 'acknowledged' && a.ackAgeSec > (window.STALE_ACK_THRESHOLD ?? 1500)).length, [alerts]);

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
      // Audio replay countdown intentionally cycles 30 → 1 → 30 without ever
      // displaying "0". The reset happens at s === 1 so the operator never
      // sees a stale zero between the wrap and the next tick.
      setAudioReplayIn(s => s <= 1 ? 30 : s - 1);
    }, 1000);
    return () => clearInterval(id);
  }, [unackCount, muteState.global, tweaks.muted]);

  const showToast = useCallbackA((message, tone = 'info') => {
    setToast({ message, tone, id: Date.now() });
    // Cancel any in-flight auto-hide so a rapid second toast isn't wiped
    // by the first one's expiring timer.
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => {
      setToast(null);
      toastTimerRef.current = null;
    }, 3000);
  }, []);

  // --- Live-refresh coalescing + in-flight guard ----------------------------
  // Every inbound WS event wants fresh data, but a bare refresh() per event
  // multiplies into a request storm during an alert burst (refreshLive() = 4+
  // GETs plus one /cycles per pump). Three entry points now share ONE guarded
  // runner so refreshLive() NEVER runs concurrently and bursts collapse:
  //   • refresh()         — forced + awaitable, for user actions (ack/resolve/…)
  //   • scheduleRefresh() — 300ms trailing-debounce, for WS events + 20s poll
  // The mutable guard lives in refs (not state) so it neither triggers a
  // re-render nor goes stale inside the memoized callbacks below.
  const refreshInFlight = useRefA(null);  // Promise of the active run, else null
  const refreshPending = useRefA(false);  // work arrived mid-flight → run once more
  const refreshDebounce = useRefA(null);  // scheduleRefresh() trailing-debounce timer

  // The single guarded runner. In-flight guard: if a run is already active we do
  // NOT start a parallel one — we mark the run "dirty" (refreshPending) and hand
  // back the in-flight promise, so awaiters still wait for fresh data. Exactly
  // ONE trailing run then fires after the active run settles, guaranteeing the
  // latest server state is eventually fetched with no missed updates.
  const runRefresh = useCallbackA(() => {
    if (refreshInFlight.current) {
      refreshPending.current = true;
      return refreshInFlight.current;
    }
    const p = (async () => {
      try {
        const r = await window.SDPRS_API.refreshLive();
        setAlerts(r.alerts);
        setNodes(r.nodes);
        // Push NODE_HISTORY into React state so NodeSidePanel re-renders
        // when history changes (C-8). buildNodeHistory ran in api.jsx and
        // wrote window.NODE_HISTORY — mirror the fresh copy here.
        setNodeHistory(window.NODE_HISTORY ?? {});
        // B5: seed muteState.nodes from backend snooze data on first refresh.
        // Nodes with snoozeMin > 0 are actively snoozed server-side; mirror
        // that into muteState so the UI reflects it without requiring the
        // operator to manually re-snooze. Only runs once (ref-guarded).
        if (!muteHydratedRef.current) {
          muteHydratedRef.current = true;
          const snoozedNodeIds = r.nodes.filter(n => n.snoozeMin > 0).map(n => n.id);
          if (snoozedNodeIds.length > 0) {
            setMuteState(prev => ({ ...prev, nodes: [...new Set([...prev.nodes, ...snoozedNodeIds])] }));
          }
        }
        resetClockRef.current();
        // Surface partial failures from this refresh cycle (api.jsx populates
        // r.failures with the keys of loaders that rejected).
        setDataWarnings(r.failures || []);
      } catch (e) {
        console.warn('[SDPRS] refresh failed', e);
      } finally {
        refreshInFlight.current = null;
        if (refreshPending.current) {
          refreshPending.current = false;
          await runRefresh(); // trailing run: pick up state that changed mid-flight
        }
      }
    })();
    refreshInFlight.current = p;
    return p;
  }, []);

  // Forced refresh for USER ACTIONS. runRefresh already reflects state AT/AFTER
  // this call: if idle it runs immediately; if a run is in flight (possibly
  // started before the user's mutation) it queues exactly one trailing run and
  // returns a promise that resolves only after that trailing run completes — so
  // `await refresh()` in onAck/onResolve/onSnooze always sees post-mutation data.
  const refresh = runRefresh;

  // Coalesced refresh for WS events + the safety-net poll. A burst of calls
  // within 300ms collapses to a single runRefresh() (imperceptible to operators,
  // but it removes the per-event storm multiplication).
  const scheduleRefresh = useCallbackA(() => {
    if (refreshDebounce.current) clearTimeout(refreshDebounce.current);
    refreshDebounce.current = setTimeout(() => {
      refreshDebounce.current = null;
      runRefresh();
    }, 300);
  }, [runRefresh]);

  // Live updates: coalesced refresh on every relevant WebSocket event, with a
  // slow poll as a safety net (it also keeps alert ages current between events).
  //
  // api.jsx openSocket exposes an options-object contract:
  //   onNewAlert()          — new-alert banner + refresh
  //   onEvent(type, data)   — everything else that matters to the operator.
  //                           `ping` and `new_alert` do NOT reach onEvent.
  // We coalesce every event into scheduleRefresh() (300ms debounced). This is
  // what closes the "peer acks invisible until next 20s poll" gap.
  useEffectA(() => {
    const stop = window.SDPRS_API.openSocket({
      onNewAlert: () => {
        setNewAlertBannerCount(c => c + 1);
        scheduleRefresh();
      },
      // Keepalive pings must reset liveSec so a healthy WS doesn't drift into
      // the 10s "Reconnecting…" range between 20s poll edges (audit P1 #2).
      // api.jsx surfaces ping via onPing so we don't leak the keepalive into
      // the general onEvent whitelist.
      // B5: a >30s gap between pings unambiguously means the socket dropped
      // and reconnected (pings are ~10s apart on a healthy WS), so we sync
      // any events missed while offline and notify the operator.
      onPing: () => {
        const now = Date.now();
        if (now - lastPingRef.current > 30000) {
          scheduleRefresh();
          showToast('連線恢復 — 已重新同步資料', 'info');
        }
        lastPingRef.current = now;
        resetClockRef.current();
      },
      onEvent: (type, _data) => {
        if (type === 'auth_expired') {
          // Server sent {type:'auth_expired'} right before its 1008 close.
          // Do NOT scheduleRefresh() — the socket is about to close and any
          // fetch will 401. Show the blocking modal and halt reconnect thrash.
          setSessionExpired(true);
          if (wsStopRef.current) { wsStopRef.current(); wsStopRef.current = null; }
          return;
        }
        if (type === 'alert_updated' || type === 'alert_acknowledged' || type === 'alert_resolved') {
          scheduleRefresh();
        } else if (type === 'node_status' || type === 'pump_status' || type === 'node_deleted') {
          scheduleRefresh();
        } else if (type === 'weather') {
          // Auto-mute on lightning when lightning auto-mute is enabled.
          // Bug #1 fix: read from muteStateRef.current instead of the stale
          // muteState closure so lightning auto-mute works after mount.
          const lightningCount = _data?.lightning?.count || 0;
          if (lightningCount > 0 && muteStateRef.current.lightning && !muteStateRef.current.global) {
            try { window.SDPRS_AUDIO?.setMuted(true); } catch (_) {}
          } else if (lightningCount === 0 && muteStateRef.current.lightning && window.SDPRS_AUDIO?.isMuted()) {
            // Unmute when lightning clears (only if we muted it)
            try { window.SDPRS_AUDIO?.setMuted(false); } catch (_) {}
          }
          scheduleRefresh();
        }
      },
    });
    wsStopRef.current = stop;
    // F4: don't poll while the session-expiry modal is blocking — the
    // session is dead, so the refresh would just be a wasted 401 round-trip
    // (and briefly flash "stale" state before the next 401 re-triggers the
    // modal-open path).
    const poll = setInterval(() => {
      if (sessionExpiredRef.current) return;
      scheduleRefresh();
    }, 20000);
    return () => {
      stop();
      wsStopRef.current = null;
      clearInterval(poll);
      if (refreshDebounce.current) { clearTimeout(refreshDebounce.current); refreshDebounce.current = null; }
    };
  }, [scheduleRefresh, showToast]);

  // REST 401 soft-redirect: api.jsx sets window.__SDPRS_SESSION_EXPIRED instead
  // of hard-redirecting to /login, so unsaved state (handover drafts, resolve
  // notes) is preserved through the session-expiry modal's H-1 cross-login flow.
  // Poll the flag every 2s — covers both initial-load 401s (before WS connects)
  // and any REST call that 401s outside the WS event path.
  useEffectA(() => {
    const id = setInterval(() => {
      if (window.__SDPRS_SESSION_EXPIRED) {
        window.__SDPRS_SESSION_EXPIRED = false;
        setSessionExpired(true);
        if (wsStopRef.current) { wsStopRef.current(); wsStopRef.current = null; }
      }
    }, 2000);
    return () => clearInterval(id);
  }, []);

  // Offline / online detection — toast the operator when connectivity changes
  // and trigger a refresh when the connection comes back so stale data is
  // replaced immediately.
  useEffectA(() => {
    const handleOffline = () => showToast('網路連線中斷', 'warn');
    // F4: skip the recovery toast + refresh while the session-expiry modal
    // is up — same reasoning as the poll guard above (session is dead, the
    // refresh would just 401 again).
    const handleOnline = () => {
      if (sessionExpiredRef.current) return;
      showToast('網路已恢復', 'ok');
      refresh();
    };
    window.addEventListener('offline', handleOffline);
    window.addEventListener('online', handleOnline);
    return () => {
      window.removeEventListener('offline', handleOffline);
      window.removeEventListener('online', handleOnline);
    };
  }, [showToast, refresh]);

  const findNextUnack = useCallbackA((currentId) => {
    const list = alerts.filter(a => a.state === 'pending' && a.id !== currentId);
    if (list.length === 0) return null;
    // Severity-first, then RECENCY (newest first) — critical+new always wins.
    // Unknown severities (schema drift, new backend enum) get rank 99 so
    // they sort to the end rather than poisoning the comparison with NaN
    // (undefined - number === NaN → sort order becomes engine-dependent).
    const sorted = list.sort((a, b) => {
      const rank = { critical: 0, warn: 1, info: 2 };
      const rankA = rank[a.sev] ?? 99;
      const rankB = rank[b.sev] ?? 99;
      if (rankA !== rankB) return rankA - rankB;
      return a.ageSec - b.ageSec; // smaller ageSec = newer
    });
    return sorted[0].id;
  }, [alerts]);

  const markSeen = useCallbackA((id) => {
    // Best-effort background write; a failure here shouldn't break the UI,
    // but the promise MUST be caught or React logs it as an unhandled rejection.
    Promise.resolve()
      .then(() => window.SDPRS_API.markSeen(id))
      .catch(err => console.warn('[app] markSeen failed', err));
    setAlerts(prev => prev.map(a => a.id === id ? { ...a, seen: true } : a));
  }, []);

  // Shared in-flight guard for Ack/Resolve/Snooze dispatch. Hoisted from
  // AlertDetail so the keyboard shortcuts A/R (app-level, this file) and the
  // detail-panel buttons (alerts.jsx) share one source of truth — a rapid
  // A-A double-tap or an "Ack button click during in-flight keyboard-A" both
  // resolve to the same guard. The ref is the correctness gate (checked
  // synchronously to close the double-fire race that a state-only guard would
  // lose across the setState scheduling boundary); the state is only the
  // visual signal to disable buttons in AlertDetail.
  const alertBusyRef = useRefA(false);
  const [alertBusy, setAlertBusy] = useStateA(false);

  const onAck = useCallbackA(async (id, advance = true) => {
    if (alertBusyRef.current) return;
    alertBusyRef.current = true;
    setAlertBusy(true);
    try {
      try {
        await window.SDPRS_API.ackAlert(id);
      } catch (e) {
        showToast('認領失敗: ' + (e.message || e), 'warn');
        return;
      }
      // Play confirmation sound on successful ack. muteState.global gates it
      // because the StatusStrip mute toggle doesn't mirror to SDPRS_AUDIO.setMuted.
      // Wrapped in try/catch — WebAudio failure must never block the ack.
      try {
        if (window.SDPRS_AUDIO && !muteState.global) window.SDPRS_AUDIO.playAck();
      } catch (_) { /* audio pipeline failure — never block the ack */ }
      setAckedIds(prev => new Set(prev).add(id));
      // Operator engaged with the queue — clear the "N new" banner so a stale
      // count doesn't linger after everything's been touched.
      setNewAlertBannerCount(0);
      showToast('已認領' + (advance ? ' → 下一筆' : ''), 'info');
      const next = advance ? findNextUnack(id) : null;
      await refresh();
      if (next) setSelectedId(next);
    } finally {
      alertBusyRef.current = false;
      setAlertBusy(false);
    }
  }, [showToast, findNextUnack, refresh, muteState.global]);

  const onResolve = useCallbackA(async (id, note) => {
    if (!note) {
      showToast('需備註才能解決', 'warn');
      return;
    }
    if (alertBusyRef.current) return;
    alertBusyRef.current = true;
    setAlertBusy(true);
    try {
      try {
        await window.SDPRS_API.resolveAlert(id, note);
      } catch (e) {
        showToast('解決失敗: ' + (e.message || e), 'warn');
        return;
      }
      // Operator engaged with the queue — clear the "N new" banner too.
      setNewAlertBannerCount(0);
      showToast('警報已解決', 'ok');
      const next = findNextUnack(id);
      await refresh();
      if (next) setSelectedId(next);
    } finally {
      alertBusyRef.current = false;
      setAlertBusy(false);
    }
  }, [showToast, findNextUnack, refresh]);

  const onSnooze = useCallbackA(async (id, mins) => {
    const a = alerts.find(x => x.id === id);
    if (!a) return;
    if (alertBusyRef.current) return;
    alertBusyRef.current = true;
    setAlertBusy(true);
    try {
      try {
        await window.SDPRS_API.snoozeNode(a.node, mins);
      } catch (e) {
        showToast('延期失敗: ' + (e.message || e), 'warn');
        return;
      }
      setMuteState(prev => ({ ...prev, nodes: prev.nodes.includes(a.node) ? prev.nodes : [...prev.nodes, a.node] }));
      showToast(`${a.node} 已延期 ${mins} 分鐘`, 'warn');
      await refresh();
    } finally {
      alertBusyRef.current = false;
      setAlertBusy(false);
    }
  }, [showToast, alerts, refresh]);

  useEffectA(() => {
    if (selectedId) markSeen(selectedId);
  }, [selectedId, markSeen]);

  // Landing on the Alerts page counts as "operator saw the new-alert banner";
  // clear the counter so the pill doesn't linger forever after they navigated in.
  useEffectA(() => {
    if (page === 'alerts') setNewAlertBannerCount(0);
  }, [page]);

  const [resolveNote, setResolveNote] = useStateA('');
  useEffectA(() => { setResolveNote(''); }, [selectedId]);

  // H-1: surface the restored-across-login state to the operator. Runs once
  // on mount (deps are the stable showToast identity). If they had a resolve
  // note in flight before the redirect, tell them explicitly — the draft was
  // dropped on purpose (see RESTORED_STATE comment above).
  useEffectA(() => {
    if (!RESTORED_STATE) return;
    if (RESTORED_STATE.hadDraft) {
      showToast('登入前的草稿未保存', 'warn');
    } else {
      showToast('已回復先前頁面', 'info');
    }
  }, [showToast]);

  // Partial data-load failures — loadInitial() stores which loaders failed on
  // window.__SDPRS_LOAD_FAILURES; surface them as a warning banner so the
  // operator knows which feeds are stale or unavailable.
  const _FAILURE_LABELS = {
    nodes: '節點資料', alerts: '警報', history: '歷史紀錄',
    rate: '警報頻率', weather: '天氣資訊', handover: '交接備註', audit: '稽核紀錄',
  };
  useEffectA(() => {
    const failures = window.__SDPRS_LOAD_FAILURES;
    if (failures && failures.length > 0) {
      setDataWarnings(failures);
      const labels = failures.map(k => _FAILURE_LABELS[k] || k).join('、');
      showToast('部分資料載入失敗: ' + labels, 'warn');
    }
  }, [showToast]);

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
      // Wall mode is a read-only NOC display; the operator has no context
      // for hotkeys to act against, and stray keystrokes on the wall
      // shouldn't teleport people around. Bail early instead of unmounting
      // the effect so we don't tear/rebuild listeners on every mode toggle.
      if (tweaks.wallMode) return;

      // H-4: IME composition guard. Bopomofo/Zhuyin/Cangjie/Pinyin deliver
      // keydown during composition; without this, the first stroke of a
      // Chinese sequence can trigger a shortcut mid-word. `isComposing` is
      // the modern spec; `keyCode === 229` is Firefox's consumed-key sentinel
      // (Firefox delivers keydown BEFORE the IME layer swallows it), so both
      // are required for full coverage in a zh-TW deployment.
      if (e.isComposing || e.keyCode === 229) return;

      const tag = e.target.tagName;
      // H-2: `isContentEditable` is the canonical DOM predicate — catches
      // any element (e.g. a future handover rich-text editor); SELECT is
      // added so browser type-to-jump inside a dropdown isn't hijacked.
      // Shadow-DOM inputs are NOT caught here — that would need
      // composedPath() traversal and we don't host web components today.
      const inField = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target.isContentEditable;

      // Cmd+K / Ctrl+K opens the palette from ANYWHERE, including inside inputs.
      // This is the one shortcut that must survive the inField guard below.
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        // H-3: the sessionExpired modal is blocking — palette-driven actions
        // would fire 401s against the dead session, and the palette would
        // paint on top of the login modal, producing a confusing UI state.
        if (sessionExpired) return;
        // B2: idempotent toggle — a second Cmd+K closes the palette.
        setCmdkOpen(v => !v);
        return;
      }
      // H-4: inside inputs, no shortcuts fire — Escape just blurs so typing
      // "?" / "/" / "m" / "1" in the search box doesn't teleport the operator.
      // Cmd/Ctrl+K is the only exception and was handled above.
      if (inField) {
        if (e.key === 'Escape') {
          document.activeElement?.blur();
        }
        return;
      }
      // Ctrl+. focus mode
      if ((e.ctrlKey || e.metaKey) && e.key === '.') {
        e.preventDefault();
        setFocusMode(f => !f);
        showToast(focusMode ? '已關閉專注模式' : '已啟用專注模式 — 隱藏資訊級警報', 'info');
        return;
      }
      // F1: every shortcut below this point is a single-key (or Shift+key)
      // binding with no Ctrl/Cmd involvement. Without this guard, Ctrl/Cmd+A,
      // +R, +N, +M, +T, and even Ctrl+1..7 collide with browser-native
      // Select-All / Reload / New-Window / tab-switch, firing OUR handler
      // (ack/resolve/next-unack/etc.) on top of — or instead of — the
      // browser's own action. Cmd/Ctrl+K and Cmd/Ctrl+. are the only
      // combos meant to work everywhere and are both handled above this line.
      if (e.ctrlKey || e.metaKey) return;
      // Alt+← back navigation
      // B1: only consume the event when we have app-internal history to pop;
      // otherwise let the browser handle its native back so a first-load user
      // can still leave the page.
      if (e.altKey && e.key === 'ArrowLeft') {
        if (pageHistoryRef.current.length > 0) {
          e.preventDefault();
          goBack();
        }
        return;
      }

      // H-2: Escape closes ONLY the top-of-stack overlay so a nested modal
      // (e.g. ShortcutsModal opened from within MuteDrawer) doesn't collapse
      // its parent too. Priority (top-most first):
      //   sessionExpired (non-dismissible) → cmdk → shortcuts → nodePanel →
      //   mute → shift.
      if (e.key === 'Escape') {
        if (sessionExpired) return; // blocking modal — never Esc-dismissible
        if (cmdkOpen) { setCmdkOpen(false); return; }
        if (shortcutsOpen) { setShortcutsOpen(false); return; }
        if (nodePanelNode) { setNodePanelNodeId(null); return; }
        if (muteDrawerOpen) { setMuteDrawerOpen(false); return; }
        if (shiftBannerOpen) { setShiftBannerOpen(false); return; }
        return;
      }

      if (e.key === '?') { e.preventDefault(); setShortcutsOpen(true); return; }
      if (e.key === '/') {
        e.preventDefault();
        // H-5: when a modal owns the foreground, "/" would otherwise steal
        // focus into that modal's own search input (e.g. the command palette).
        // Bail so the shortcut stays a header-search shortcut only.
        if (nodePanelNode || shortcutsOpen || muteDrawerOpen || cmdkOpen || sessionExpired) return;
        // Alerts page carries the header search input at
        // `pages/alerts.jsx` with `id="global-search"`; getElementById is
        // the stable lookup. querySelector fallback stays as defence for
        // future search-input additions on other pages.
        const el = document.getElementById('global-search')
          || document.querySelector('input[type="text"][placeholder*="搜尋"]');
        el?.focus();
        return;
      }
      if (e.key === 'm' || e.key === 'M') { setMuteDrawerOpen(true); return; }
      if (e.key === 't' || e.key === 'T') { setTweak('theme', tweaks.theme === 'dark' ? 'light' : 'dark'); return; }
      if (e.shiftKey && (e.key === 'D' || e.key === 'd')) { setTweak('density', tweaks.density === 'compact' ? 'comfortable' : 'compact'); return; }

      const navMap = { '1': 'alerts', '2': 'monitor', '3': 'status', '4': 'pumps', '5': 'weather', '6': 'handover', '7': 'audit' };
      const sel = alerts.find(a => a.id === selectedId);
      const inResolveTemplateFlow = page === 'alerts' && sel && sel.state === 'acknowledged';
      // Suppress ALL number-key nav (1-7) while an acknowledged alert has
      // focus: keys 1-6 apply resolve templates, and 7 is intentionally
      // swallowed (no template exists) rather than teleporting the operator
      // to Audit mid-resolve and losing their in-progress resolve-note.
      // Feedback for 7 is handled below (toast "無此模板").
      if (navMap[e.key] && !(inResolveTemplateFlow && /^[1-7]$/.test(e.key))) {
        setPage(navMap[e.key]);
        return;
      }
      if (inResolveTemplateFlow && e.key === '7') {
        showToast('無此模板', 'info');
        return;
      }
      if (inResolveTemplateFlow && /^[1-6]$/.test(e.key)) {
        const idx = parseInt(e.key, 10) - 1;
        if (window.RESOLVE_TEMPLATES && window.RESOLVE_TEMPLATES[idx]) {
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
        if (sel.state === 'pending') {
          // BUG 1: mirror AlertRow/AlertDetail's PENDING_VIDEO heuristic so
          // keyboard A doesn't fire an ack the backend will 409.
          const waiting = !(sel.timeline || []).some(t => t.label === 'UPLOADED');
          if (waiting) { showToast('等待影像上傳中 — 尚未可認領', 'warn'); return; }
          onAck(sel.id, !e.shiftKey);
        }
        return;
      }
      if (e.key === 'r' || e.key === 'R') {
        if (sel.state === 'acknowledged') onResolve(sel.id, resolveNote);
        return;
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const list = alerts.filter(a => a.state !== 'resolved');
        // C-9: when every alert is resolved, list[nextIdx] is undefined and
        // `.id` throws. Bail before indexing.
        if (list.length === 0) return;
        const idx = list.findIndex(a => a.id === selectedId);
        const nextIdx = e.key === 'ArrowDown' ? Math.min(list.length - 1, idx + 1) : Math.max(0, idx - 1);
        setSelectedId(list[nextIdx].id);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [page, selectedId, alerts, tweaks.theme, tweaks.density, tweaks.wallMode, setTweak, setPage, goBack, onAck, onResolve, findNextUnack, resolveNote, showToast, focusMode, sessionExpired, cmdkOpen, shortcutsOpen, nodePanelNode, muteDrawerOpen, shiftBannerOpen]);

  // B3: session-expiry modal focus trap. The modal has ONE focusable element
  // (autoFocused via autoFocus), so any Tab/Shift+Tab must bounce back to it —
  // otherwise focus can escape to controls behind the backdrop. We also save
  // and restore document.activeElement, though in practice the modal is
  // dismissed by navigation, so the restore branch usually never runs.
  useEffectA(() => {
    if (!sessionExpired) return;
    const savedActive = document.activeElement;
    const trap = (e) => {
      if (e.key !== 'Tab') return;
      e.preventDefault();
      sessionModalButtonRef.current?.focus();
    };
    window.addEventListener('keydown', trap);
    return () => {
      window.removeEventListener('keydown', trap);
      try { savedActive && savedActive.focus && savedActive.focus(); } catch (_) { /* element gone */ }
    };
  }, [sessionExpired]);

  // B8: auto-open shift banner on mount when there's meaningful shift summary
  // data. Empty dependency array so it runs exactly once; the guard prevents
  // opening a blank banner when no shift activity has been recorded yet.
  useEffectA(() => {
    if (window.SHIFT_SUMMARY && window.SHIFT_SUMMARY.alertsHandled > 0) {
      setShiftBannerOpen(true);
    }
  }, []);

  // Sync the in-app "global mute" toggle back to persisted tweaks.muted.
  // Skip the first invocation: the initial value was seeded FROM tweaks.muted
  // above, so writing it back on mount is a no-op at best and, if the initial
  // seed ever changes shape, a clobber. Ref-guard keeps the intent explicit.
  const muteInitializedRef = useRefA(false);
  useEffectA(() => {
    if (!muteInitializedRef.current) {
      muteInitializedRef.current = true;
      return;
    }
    setTweak('muted', muteState.global);
  }, [muteState.global, setTweak]);

  const onUpdateNode = useCallbackA(async (id, patch) => {
    if (patch.location) await window.SDPRS_API.updateNodeLocation(id, patch.location);
    // F3: no more local-state patch needed here — nodePanelNode is now
    // derived from `nodes`, so the `await refresh()` below (which replaces
    // `nodes` with fresh server data) is what makes the panel pick up the
    // edit. The optimistic setNodePanelNode(...) patch this used to do is
    // gone along with the state it patched.
    showToast(`${id} 配置已更新`, 'ok');
    await refresh();
  }, [showToast, refresh]);

  const onSelectNode = useCallbackA((n) => setNodePanelNodeId(n?.id ?? null), []);
  const onJumpAlert = useCallbackA((id) => {
    setNodePanelNodeId(null);
    setPage('alerts');
    setSelectedId(id);
  }, [setPage]);

  // --- Stabilized callbacks for StatusStrip / NavRail props ---
  // These were previously inline arrow functions recreated every render, which
  // defeated React.memo on the child components. useCallback gives them stable
  // identity so shallow-compare memoization actually skips re-renders.
  const onSetMuted = useCallbackA((v) => setMuteState(prev => ({ ...prev, global: v })), []);
  const onSetTheme = useCallbackA((v) => setTweak('theme', v), [setTweak]);
  const onOpenShortcuts = useCallbackA(() => setShortcutsOpen(true), []);
  const onOpenMuteDrawer = useCallbackA(() => setMuteDrawerOpen(true), []);
  const onOpenCmdK = useCallbackA(() => setCmdkOpen(true), []);
  const onToggleFocus = useCallbackA(() => setFocusMode(f => !f), []);
  const onSetDensity = useCallbackA((v) => setTweak('density', v), [setTweak]);

  // --- Memoized active alerts (non-resolved) ---
  // Previously `alerts.filter(a => a.state !== 'resolved')` ran inline in
  // renderPage() AND in the NodeSidePanel JSX — twice per render. Hoisting
  // to useMemo ensures it runs once and the reference is stable for
  // React.memo'd children (MonitorPage, NodeSidePanel).
  const activeAlerts = useMemoA(() => alerts.filter(a => a.state !== 'resolved'), [alerts]);

  const renderPage = () => {
    const wrap = (el) => <ErrorBoundary key={page}>{el}</ErrorBoundary>;
    switch (page) {
      case 'alerts': return wrap(<window.AlertsPage density={tweaks.density} selectedId={selectedId} setSelectedId={setSelectedId} alerts={alerts} onAck={onAck} onResolve={onResolve} onSnooze={onSnooze} onRefresh={refresh} ackedIds={ackedIds} resolveNote={resolveNote} setResolveNote={setResolveNote} busy={alertBusy} nodes={nodes} nodeHistory={nodeHistory}/>);
      case 'monitor': return wrap(<window.MonitorPage nodes={nodes} activeAlerts={activeAlerts} onSelectNode={onSelectNode}/>);
      case 'status': return wrap(<window.StatusPage nodes={nodes} onSelectNode={onSelectNode} onRefresh={refresh}/>);
      case 'pumps': return wrap(<window.PumpsPage nodes={nodes} onSelectNode={onSelectNode}/>);
      case 'weather': return wrap(<window.WeatherPage/>);
      case 'handover': return wrap(<window.HandoverPage/>);
      case 'audit': return wrap(<window.AuditPage auditLog={window.AUDIT ?? []}/>);
      default: return null;
    }
  };

  // Bootstrap error fallback. loadInitial() populates the window.* globals
  // that downstream JSX reads; if it failed we render a retry UI instead of
  // the full app so mount-time `.filter(...)` calls on undefined don't crash.
  let bootstrapErrorUI = null;
  if (bootstrapError) {
    const retry = async () => {
      const err = bootstrapError;
      setBootstrapError(null);
      window.__SDPRS_LOAD_FAILURES = [];
      setDataWarnings([]);
      try {
        await window.SDPRS_API.loadInitial();
        setAlerts(window.ALERTS ?? []);
        setNodes(window.NODES ?? []);
        setNodeHistory(window.NODE_HISTORY ?? {});
        setSelectedId(window.ALERTS?.[0]?.id ?? null);
      } catch (e) {
        console.error('[SDPRS] retry loadInitial failed:', e);
        setBootstrapError(e || err);
      }
    };
    bootstrapErrorUI = (
      <div className="h-screen w-screen flex items-center justify-center bg-surface-base text-ink-primary p-6">
        <div className="max-w-md text-center space-y-4">
          <div className="text-2xl font-bold text-sev-critical">無法載入初始資料</div>
          <div className="text-sm text-ink-secondary">
            伺服器暫時無法回應。請確認網路連線後重試。
          </div>
          <div className="text-xs font-mono text-ink-muted break-all">
            {String(bootstrapError && (bootstrapError.message || bootstrapError))}
          </div>
          <button
            onClick={retry}
            className="px-4 py-2 rounded bg-sev-info text-white text-sm font-bold hover:bg-sev-info/80"
          >
            重試
          </button>
        </div>
      </div>
    );
  }

  return (<LiveClockProvider registerReset={registerClockReset}>
  {bootstrapError ? bootstrapErrorUI : tweaks.wallMode ? (
    <WallView alerts={alerts} nodes={nodes} unackCount={unackCount}/>
  ) : (
    <div className="h-screen w-screen overflow-hidden text-ink-primary">
      <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[200] focus:bg-sev-info focus:text-white focus:px-4 focus:py-2 focus:rounded">
        跳至主要內容
      </a>
      <window.StatusStrip
        unackCount={unackCount}
        muted={muteState.global}
        setMuted={onSetMuted}
        theme={tweaks.theme}
        setTheme={onSetTheme}
        onOpenShortcuts={onOpenShortcuts}
        page={page}
        setPage={setPage}
        onOpenMuteDrawer={onOpenMuteDrawer}
        audioReplayIn={audioReplayIn}
        muteState={muteState}
        operators={window.OPERATORS_ONLINE ?? EMPTY_OPERATORS}
        staleAckCount={staleAckCount}
        onOpenCmdK={onOpenCmdK}
        focusMode={focusMode}
        onToggleFocus={onToggleFocus}
      />
      <window.NavRail
        page={page}
        setPage={setPage}
        density={tweaks.density} setDensity={onSetDensity}
        unackCount={unackCount}
        offlineCount={offlineCount}
      />
      {dataWarnings.length > 0 && (
        <div className="fixed top-12 left-0 right-0 z-40 bg-sev-warn/10 border-b border-sev-warn/30 px-4 py-1.5 text-xs text-sev-warn flex items-center gap-2" role="alert">
          <Icon.AlertCircle size={14} className="flex-shrink-0"/>
          <span>
            {dataWarnings.map(k => _FAILURE_LABELS[k] || k).join('、')} 無法載入 — 顯示快取資料
          </span>
          <button onClick={() => setDataWarnings([])} className="ml-auto text-ink-muted hover:text-ink-primary">×</button>
        </div>
      )}
      <main id="main-content" className="ml-0 md:ml-56 mt-12 mb-10 h-[calc(100vh-88px)] overflow-hidden">
        {renderPage()}
      </main>
      <window.Footer data={window.ALERT_RATE ?? []} handover={window.HANDOVER?.pinned ?? null}/>

      {/* Floating new-alert banner */}
      {page === 'alerts' && newAlertBannerCount > 0 && (
        <window.NewAlertBanner count={newAlertBannerCount} onClick={() => {
          setNewAlertBannerCount(0);
          const firstUnseen = alerts.find(a => !a.seen);
          if (firstUnseen) setSelectedId(firstUnseen.id);
        }}/>
      )}

      {/* Shift onboarding banner */}
      {shiftBannerOpen && <window.ShiftBanner shiftSummary={window.SHIFT_SUMMARY} onDismiss={() => setShiftBannerOpen(false)} onViewHandover={() => { setShiftBannerOpen(false); setPage('handover'); }}/>}

      <window.ShortcutsModal open={shortcutsOpen} onClose={() => setShortcutsOpen(false)}/>
      <window.MuteDrawer open={muteDrawerOpen} onClose={() => setMuteDrawerOpen(false)} muteState={muteState} setMuteState={setMuteState} nodes={nodes}/>
      <window.CommandPalette open={cmdkOpen} onClose={() => setCmdkOpen(false)} alerts={alerts} nodes={nodes} onSelectAlert={setSelectedId} onNav={setPage} onCmd={onCmdkCommand}/>
      <window.NodeSidePanel
        node={nodePanelNode}
        history={nodePanelNode ? (nodeHistory[nodePanelNode.id] || []) : []}
        onClose={() => setNodePanelNodeId(null)}
        onJumpAlert={onJumpAlert}
        onSelectAlert={onJumpAlert}
        onNavigate={setPage}
        openAlerts={activeAlerts}
        onUpdateNode={onUpdateNode}/>

      {/* Toast — a11y: polite for info/ok, assertive for warn.
          role="status" + aria-live announce silently to screen readers. */}
      <div
        aria-live={toast?.tone === 'warn' ? 'assertive' : 'polite'}
        aria-atomic="true"
        role={toast?.tone === 'warn' ? 'alert' : 'status'}
        className="sr-only-live"
      >
        {toast?.message || ''}
      </div>
      {toast && (
        <div className="fixed bottom-14 right-4 z-50 animate-in" aria-hidden="true">
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
          {(window.NAV_ITEMS ?? []).map(item => (
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
  )}

  {/* Session-expiry modal — blocking, action-required. Rendered outside the
      bootstrap-error / wallMode / main-app branches so it appears as a single
      instance regardless of which view is active. Its z-[100] sits above every
      other overlay (drawer/palette/toast are z-40..50). No Escape handler by
      design: modal is required action, and we don't want Esc to accidentally
      dismiss any parent overlay while it's up. */}
  {sessionExpired && (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="session-expiry-title"
      className="fixed inset-0 z-[100] bg-black/70 flex items-center justify-center p-4"
    >
      <div className="bg-surface-elevated border border-border-strong rounded-lg p-6 max-w-sm w-full shadow-2xl">
        <h2 id="session-expiry-title" className="text-lg font-semibold text-ink-primary mb-2">
          連線階段已逾時
        </h2>
        <p className="text-sm text-ink-secondary mb-4">
          您的登入階段已過期，請重新登入以繼續操作。目前顯示的資料可能已過時。
        </p>
        <button
          ref={sessionModalButtonRef}
          onClick={() => {
            // H-1: preserve page + selectedId across the login roundtrip.
            // Encoded into the post-login destination URL (not just the
            // /login URL) so the redirect back carries it into the fresh
            // app mount, where RESTORED_STATE picks it up.
            let target = window.location.pathname;
            try {
              const blob = btoa(JSON.stringify({
                page,
                selectedId,
                hadDraft: !!(resolveNote || '').trim(),
              }));
              target = window.location.pathname + '?sdprs_state=' + encodeURIComponent(blob);
            } catch (_) { /* fall through with pathname only */ }
            window.location.href = '/login?next=' + encodeURIComponent(target);
          }}
          className="w-full h-9 bg-brand-primary text-white rounded font-medium hover:opacity-90"
          autoFocus
        >
          前往登入頁
        </button>
      </div>
    </div>
  )}
  </LiveClockProvider>);
}

// =================================================================
// WALL VIEW — 4K NOC display
// =================================================================

function WallView({ alerts, nodes, unackCount }) {
  const { liveSec } = React.useContext(LiveClockContext);
  const liveState = liveSec < 10 ? 'ok' : liveSec < 30 ? 'warn' : 'critical';
  // C1: ticking wall clock — updates every second so the NOC display shows
  // real time instead of a frozen mount-time snapshot.
  const [wallClock, setWallClock] = useStateA(() => Date.now());
  useEffectA(() => {
    const id = setInterval(() => setWallClock(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const sorted = [...(nodes ?? [])].sort((a, b) => {
    const rank = { offline: 0, critical: 1, warn: 2, online: 3 };
    // Unknown/new status codes shouldn't NaN-sort themselves to random positions.
    return (rank[a.status] ?? 99) - (rank[b.status] ?? 99);
  });
  const weather = window.WEATHER ?? { typhoon: null, wind: {}, rain: {} };
  const handoverPin = window.HANDOVER?.pinned ?? null;
  const alertRate = window.ALERT_RATE ?? [];
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
          {weather.typhoon && (
            <>
              <span className="flex items-center gap-2 text-sev-warn font-bold"><Icon.Typhoon size={20}/> 颱風 {weather.typhoon.name} · {weather.typhoon.level}</span>
              <span className="text-ink-dim">|</span>
            </>
          )}
          <span className="font-mono tnum">{weather.wind?.dir} {weather.wind?.speed} km/h</span>
          <span className="text-ink-dim">|</span>
          <span className="font-mono tnum text-sev-info">{weather.rain?.now} mm/h</span>
        </div>
        <div className="w-px h-10 bg-border-subtle"></div>
        <div className="font-mono tnum text-base text-ink-secondary">{new Date(wallClock).toLocaleTimeString('zh-TW', { hour12: false })}</div>
      </div>

      {/* Body: 3-column wall layout */}
      <div className="flex-1 grid grid-cols-[2fr_1fr_1fr] gap-3 p-3 min-h-0">
        {/* Monitor wall */}
        <div className="flex flex-col min-h-0">
          <div className="grid grid-cols-3 gap-3 min-h-0 overflow-hidden flex-1">
            {sorted.slice(0, 9).map(n => (
              <div key={n.id} className="bg-surface-panel rounded border border-border-subtle overflow-hidden relative">
                <div className={`relative h-full snapshot-placeholder ${n.status === 'offline' ? 'snapshot-frozen' : ''}`}>
                  <SnapshotImage node={n} iconSize={64}/>
                  <div className={`absolute top-2 left-2 w-4 h-4 rounded-full bg-sev-${n.status === 'offline' || n.status === 'critical' ? 'critical' : n.status === 'warn' ? 'warn' : 'ok'} ring-2 ring-black/50 ${n.status === 'offline' || n.status === 'critical' ? 'animate-live-blink' : ''}`}></div>
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
          {sorted.length > 9 && (
            <div className="flex justify-center pt-2 flex-shrink-0">
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-surface-panel/80 border border-border-subtle text-xs font-mono text-ink-muted tracking-wide">
                <span className="w-1.5 h-1.5 rounded-full bg-ink-muted/60"></span>
                +{sorted.length - 9} more
              </span>
            </div>
          )}
        </div>

        {/* Center: live alert ticker */}
        <div className="bg-surface-panel border border-border-subtle rounded flex flex-col min-h-0">
          <div className="px-4 py-2.5 border-b border-border-subtle flex items-center justify-between flex-shrink-0">
            <h2 className="text-base font-bold uppercase tracking-wider">即時警報</h2>
            <span className="text-xs font-mono text-ink-muted tnum">{alerts.length} 筆</span>
          </div>
          <div className="flex-1 overflow-y-auto scroll-thin">
            {alerts.slice(0, 12).map(a => {
              const m = window.safeSevMeta(a.sev);
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
            {alerts.length > 12 && (
              <div className="px-3 py-2 flex justify-center">
                <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-black/30 border border-border-subtle text-xs font-mono text-ink-muted tracking-wide">
                  <span className="w-1.5 h-1.5 rounded-full bg-sev-critical/60 animate-live-blink"></span>
                  +{alerts.length - 12} more alerts
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Right: weather + system health */}
        <div className="flex flex-col gap-3 min-h-0">
          <div className="bg-surface-panel border border-sev-warn/30 rounded p-4 flex-1">
            <div className="text-xs uppercase tracking-wider text-sev-warn font-bold flex items-center gap-2"><Icon.Wind size={14}/> 風速</div>
            <div className="mt-2 flex items-baseline gap-1">
              <span className="text-7xl font-mono font-black tnum text-sev-warn">{weather.wind?.speed ?? '—'}</span>
              <span className="text-ink-muted text-xl">km/h</span>
            </div>
            <div className="text-sm text-ink-muted font-mono tnum mt-1">{weather.wind?.dir || '—'} {weather.wind?.degree}°</div>
          </div>
          <div className="bg-surface-panel border border-sev-info/30 rounded p-4 flex-1">
            <div className="text-xs uppercase tracking-wider text-sev-info font-bold flex items-center gap-2"><Icon.CloudRain size={14}/> 雨量</div>
            <div className="mt-2 flex items-baseline gap-1">
              <span className="text-7xl font-mono font-black tnum text-sev-info">{weather.rain?.now ?? '—'}</span>
              <span className="text-ink-muted text-xl">mm/h</span>
            </div>
            <div className="text-sm text-ink-muted font-mono tnum mt-1">日累計 {weather.rain?.day ?? 0}mm</div>
          </div>
          <div className="bg-surface-panel border border-border-subtle rounded p-3">
            <div className="text-xs uppercase tracking-wider text-ink-muted font-bold mb-2">系統健康</div>
            <div className="grid grid-cols-2 gap-2 text-xs font-mono tnum">
              <div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-sev-ok"></span><span className="text-ink-secondary">線上</span><span className="ml-auto font-bold">{sorted.filter(n=>n.status==='online').length}</span></div>
              <div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-sev-warn"></span><span className="text-ink-secondary">警告</span><span className="ml-auto font-bold">{sorted.filter(n=>n.status==='warn').length}</span></div>
              <div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-sev-critical"></span><span className="text-ink-secondary">嚴重</span><span className="ml-auto font-bold">{sorted.filter(n=>n.status==='critical').length}</span></div>
              <div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-sev-critical animate-live-blink"></span><span className="text-ink-secondary">離線</span><span className="ml-auto font-bold">{sorted.filter(n=>n.status==='offline').length}</span></div>
            </div>
          </div>
        </div>
      </div>

      {/* Footer ticker */}
      <div className="h-8 bg-surface-panel border-t border-border-strong flex items-center px-4 gap-4 text-xs flex-shrink-0">
        <Icon.Activity size={12} className="text-ink-muted"/>
        <window.Sparkline data={alertRate} width={180} height={20}/>
        <span className="text-ink-muted font-mono tnum">警報率 · 15min × 16</span>
        <div className="flex-1"></div>
        {handoverPin && (
          <span className="text-ink-muted">上一班: <span className="text-ink-secondary font-mono tnum">{handoverPin.by} @ {handoverPin.at}</span> "<span className="text-ink-secondary">{handoverPin.text}</span>"</span>
        )}
      </div>
    </div>
  );
}

// Load the first batch of live data, then mount. The loading spinner in
// index.html stays visible until render() replaces #root.
//
// If loadInitial() rejects we STILL mount (with the failure passed down as
// initialError) so the operator sees an in-app retry UI instead of the bare
// spinner. Previously a rejection just logged and left mount to crash on
// undefined `window.NODES.filter(...)` etc.
(async function bootstrap() {
  let initialError = null;
  try {
    await window.SDPRS_API.loadInitial();
  } catch (e) {
    console.error('[SDPRS] initial data load failed:', e);
    initialError = e || new Error('loadInitial failed');
  }
  ReactDOM.createRoot(document.getElementById('root')).render(<App initialError={initialError}/>);
})();
