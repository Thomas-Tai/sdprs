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

// Every loader key refreshLive()/loadInitial() fan out to, in the same order
// api.jsx declares them. Used as the "everything is unavailable" warning set
// when a refresh fails wholesale (SHL-12) rather than per-loader.
const _LOADER_KEYS = Object.freeze(['nodes', 'alerts', 'history', 'rate', 'weather', 'handover', 'audit']);

// SHL-11 (app-side half). api.jsx now attaches `.status` / `.detail` / `.timeout`
// to everything it throws precisely so callers stop string-matching `.message`
// — but the ack/resolve/snooze toasts still concatenated `(e.message || e)`
// straight into zh-TW copy. What an operator actually saw on failure:
//   • 401 → 「認領失敗: unauthorized」 — an English word, and the wrong advice:
//     the real remedy is the re-login modal, not retrying the ack.
//   • timeout → 「認領失敗: timeout after 10000ms on /api/alerts/318/ack」 —
//     an internal path in the operator's face, and actively misleading: an
//     aborted fetch does NOT mean the POST didn't execute server-side. Read as
//     "it failed", the operator re-sends and double-commands.
//   • 5xx → 「HTTP 502 on /api/…」.
// Branch on the structured fields, in most-specific-first order, and only fall
// back to raw `.message` when there is nothing better.
function actionErrorText(e) {
  if (!e) return '未知錯誤';
  // Checked before `.status`: api.jsx stamps aborted fetches with status 0.
  if (e.timeout || e.status === 0) return '連線逾時 — 指令可能已送出，請重新整理後確認狀態';
  if (e.status === 401) return '登入階段已逾時，請重新登入';
  if (e.status === 403) return '權限不足，無法執行此操作';
  // 409 detail is the useful part ("already resolved by alice at 14:32") and
  // is the one place we deliberately surface a backend string verbatim —
  // losing the peer's name and timestamp costs more than the language mismatch.
  if (e.status === 409) return e.detail ? String(e.detail) : '此警報已被其他操作員處理';
  if (e.detail) return Array.isArray(e.detail) ? e.detail.join('; ') : String(e.detail);
  if (e.status) return `伺服器錯誤 (HTTP ${e.status})`;
  return e.message ? String(e.message) : String(e);
}

function App({ initialError = null }) {
  const [tweaks, setTweak] = window.useTweaks(DEFAULTS);

  // Bootstrap error surface — populated by the boot effect below when
  // loadInitial rejects; renders the retry UI in place of the full app so
  // mount-time reads of window.ALERTS/NODES don't crash on undefined.
  const [bootstrapError, setBootstrapError] = useStateA(initialError);
  // SHL-10: true until the first data load settles. The shell renders
  // immediately underneath it; this only drives a thin "載入中" strip so an
  // operator can tell an empty queue apart from an unfinished load.
  const [booting, setBooting] = useStateA(true);

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
  //
  // SHL-10 changed the timing here: the app now mounts BEFORE the initial load
  // (see the boot effect below), so window.ALERTS is still the empty data.jsx
  // placeholder at this point and there is nothing to match a saved id
  // against. Resolution therefore happens in two steps — capture the saved
  // string now, resolve it to a live alert once the data lands.
  //
  // The capture has to happen during render, not in an effect: the
  // selectedId-persistence effect further down runs on mount with the initial
  // value and would removeItem() the very key we need to read. Lazy-init via
  // an `undefined` sentinel so it reads sessionStorage exactly once.
  const savedSelectedIdRef = useRefA(undefined);
  if (savedSelectedIdRef.current === undefined) {
    try { savedSelectedIdRef.current = sessionStorage.getItem('sdprs.selectedId'); }
    catch (_) { savedSelectedIdRef.current = null; /* sessionStorage unavailable */ }
  }
  const [selectedId, setSelectedId] = useStateA(() => {
    if (RESTORED_STATE?.selectedId != null) return RESTORED_STATE.selectedId;
    return null;
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
  // ALR-L5: `ackedIds` accumulates every alert id acked this session and was
  // never pruned — across a 24/7 shift it grows without bound. It is read only
  // to gate the one-time ack flash on a CURRENTLY-rendered alert
  // (alerts.jsx: `ackedIds.has(a.id)`), so any id no longer in the live `alerts`
  // list is dead weight. Prune to the intersection whenever the list changes.
  // Guards: (1) skip while `alerts` is empty so a transient empty snapshot
  // (pre-load / mid-refresh) can't wipe legitimately-acked ids; (2) functional
  // setState returns `prev` unchanged when nothing drops, so React bails the
  // re-render and this can't loop.
  useEffectA(() => {
    if (!alerts.length) return;
    const live = new Set(alerts.map(a => a.id));
    setAckedIds(prev => {
      let stale = false;
      for (const id of prev) { if (!live.has(id)) { stale = true; break; } }
      if (!stale) return prev;
      const next = new Set();
      for (const id of prev) if (live.has(id)) next.add(id);
      return next;
    });
    // ALR-L5 (other half): the ack-flash dedupe set was hoisted to
    // window.__SDPRS_FLASHED_ALERT_IDS (alerts.jsx) so an already-flashed alert
    // doesn't re-flash after AlertsPage remounts — correct, but that global is
    // never pruned either, so it re-grew the same unbounded set in a new place.
    // Its ids are exactly the acked ids (alerts.jsx seeds it from `ackedIds`),
    // so prune it against the same `live` set here in one pass. In-place delete:
    // alerts.jsx reads it by reference at render, and this runs post-commit.
    const flashed = window.__SDPRS_FLASHED_ALERT_IDS;
    if (flashed && flashed.size) {
      for (const id of Array.from(flashed)) if (!live.has(id)) flashed.delete(id);
    }
  }, [alerts]);
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
  // SHL-5: muteState.nodes is now reconciled against server snooze state on
  // every refresh (see runRefresh), so the old one-shot `muteHydratedRef`
  // guard — and the `muteStateRef` mirror that existed only to feed the
  // deleted lightning auto-mute branch (SHL-1) — are both gone.
  //
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
    // SHL-4: only act on history entries WE tagged with a `page`. Entries with
    // a null/foreign state (most commonly the "跳至主要內容" skip link, which
    // pushes a bare `#main-content` hash entry) are not ours; the old
    // `?? 'alerts'` fallback treated them as an explicit "go to Alerts"
    // navigation, so a keyboard user tabbing through the skip link and then
    // pressing Back got teleported off their page.
    //
    // The second guard matters just as much: skipNextUrlPushRef was armed
    // BEFORE we knew whether `page` would actually change. When popstate
    // resolved to the page already displayed, setPageRaw bailed as a no-op, the
    // push effect never ran, and the armed flag survived to swallow the NEXT
    // genuine navigation's pushState — wedging the history stack so Back
    // desynced from the visible page. Arm it only when we truly are changing
    // page in response to the browser's own navigation.
    const onPopState = (event) => {
      const nextPage = event.state && event.state.page;
      if (!nextPage) return;
      if (nextPage === prevPageRef.current) return;
      skipNextUrlPushRef.current = true;
      setPageRaw(nextPage);
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
        // SHL-5: reconcile muteState.nodes against server snooze state on EVERY
        // refresh, not just the first one. Per-node mute is entirely
        // server-owned — it is created by onSnooze (POST snoozeNode) and
        // cleared by MuteDrawer's 解除/解除所有 (POST unsnoozeNode), with no
        // local-only path — so `snoozeMin > 0` is the single source of truth
        // and mirroring it is safe. The old one-shot hydration (ref-guarded,
        // union-merged) meant the list only ever GREW: an expired 30-minute
        // snooze stayed visibly muted for the rest of the shift, and a snooze
        // a peer operator set never appeared at all. Both are the sort of
        // silent audio suppression that loses an alert on a typhoon night.
        //
        // Only write when the membership actually differs, otherwise every 20s
        // poll would mint a new muteState object and re-render MuteDrawer,
        // StatusStrip and every muteState consumer for no reason.
        const snoozedNodeIds = (r.nodes || []).filter(n => n.snoozeMin > 0).map(n => n.id);
        setMuteState(prev => {
          if (prev.nodes.length === snoozedNodeIds.length
              && prev.nodes.every(id => snoozedNodeIds.includes(id))) {
            return prev;
          }
          return { ...prev, nodes: snoozedNodeIds };
        });
        resetClockRef.current();
        // Surface partial failures from this refresh cycle (api.jsx populates
        // r.failures with the keys of loaders that rejected).
        //
        // SHL-12 (app-side half): a result object with no usable `failures`
        // array is not evidence that everything succeeded — treat it as a
        // total failure rather than clearing the stale-data banner. See the
        // catch below and the api.jsx handoff noted there.
        setDataWarnings(Array.isArray(r?.failures) ? r.failures : _LOADER_KEYS.slice());
      } catch (e) {
        // SHL-12: a wholesale refresh failure is precisely when the operator
        // most needs the "displaying cached data" banner — silently logging
        // and leaving the banner cleared let a dead backend look like a quiet
        // night. Mark every feed unavailable.
        console.warn('[SDPRS] refresh failed', e);
        setDataWarnings(_LOADER_KEYS.slice());
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
        // SHL-1: a `weather` branch used to live here, implementing lightning
        // auto-mute. It was dead on arrival in BOTH directions: `weather` is
        // not in api.jsx's _WS_EVENT_TYPES whitelist (so onEvent could never
        // be called with it) and no backend code path emits a `weather` frame
        // at all. The feature has therefore never once run in production.
        // Removed rather than left in place, because dormant code that looks
        // implemented is what let the MuteDrawer keep advertising a 雷擊自動靜音
        // toggle the system cannot honour. Re-implementing it needs a backend
        // emit + a whitelist entry + this branch, as one deliberate change.
        if (type === 'alert_updated' || type === 'alert_acknowledged' || type === 'alert_resolved') {
          scheduleRefresh();
        } else if (type === 'node_status' || type === 'pump_status' || type === 'node_deleted') {
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

  // API-F7: session keep-alive. The backend hard-expires a session at 24h and
  // /api/session/extend existed with ZERO callers, so an operator who came on
  // shift before the boundary got logged out mid-shift — potentially mid-typhoon,
  // which is exactly when nobody can afford to stop and re-authenticate.
  //
  // Deliberately activity-gated rather than a bare timer. A plain interval would
  // keep an abandoned browser authenticated forever, which defeats the point of
  // having an expiry at all. Instead we only extend when the operator has
  // actually interacted since the last extend: a worked console never dies
  // mid-shift, an abandoned one still expires on schedule.
  //
  // A NOC wall display counts as abandoned by this rule and WILL expire. That's
  // correct — a read-only wall panel showing a login screen is a visible,
  // fixable problem; a wall panel authenticated indefinitely is a standing
  // credential nobody is watching.
  const lastActivityRef = useRefA(0);
  useEffectA(() => {
    // `pointerdown`/`keydown` (not `mousemove`) so a cat on the keyboard or a
    // jittery trackpad doesn't count, but any real operator action does.
    const mark = () => { lastActivityRef.current = Date.now(); };
    window.addEventListener('pointerdown', mark, { passive: true });
    window.addEventListener('keydown', mark, { passive: true });
    return () => {
      window.removeEventListener('pointerdown', mark);
      window.removeEventListener('keydown', mark);
    };
  }, []);

  useEffectA(() => {
    const EXTEND_EVERY_MS = 15 * 60 * 1000;
    let lastExtend = Date.now();
    const id = setInterval(() => {
      // Nothing to keep alive if the session is already gone — extending here
      // would race the expiry modal's re-login flow.
      if (sessionExpired) return;
      if (lastActivityRef.current <= lastExtend) return;   // idle since last extend
      const api = window.SDPRS_API;
      if (!(api && typeof api.extendSession === 'function')) return;
      lastExtend = Date.now();
      // Best-effort: a failed extend is not worth interrupting the operator.
      // If the session really is dead, the existing 401 → __SDPRS_SESSION_EXPIRED
      // path above surfaces it through the normal modal.
      Promise.resolve(api.extendSession()).catch(() => {});
    }, EXTEND_EVERY_MS);
    return () => clearInterval(id);
  }, [sessionExpired]);

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

  // --- Contract B: the alert list the operator can actually SEE --------------
  // ALR-H2: app.jsx used to run every keyboard behaviour (↑/↓ nav, N
  // next-unack, ack/resolve auto-advance) against the raw `alerts` array while
  // AlertsPage rendered a tab/severity/search-filtered subset. The two lists
  // disagreed constantly: ↓ would "move" to an alert that isn't on screen, the
  // page would immediately snap the selection back to its own filtered[0], and
  // arrow triage froze solid under any filter.
  //
  // AlertsPage now reports its rendered order via onVisibleChange(visibleIds)
  // — ids as strings, in exactly the order painted, after tab + severity +
  // search are applied. We keep it in a REF, never state: the page emits from
  // an effect on every list change, so routing it through setState would make
  // App re-render → AlertsPage re-render → effect → setState … a render loop.
  // Nothing here needs to re-render on a visibility change either; the ref is
  // only ever read inside event handlers.
  //
  // Null/empty ref = "the page hasn't told us yet" (not on the alerts page, or
  // an older AlertsPage that doesn't implement the contract) → every consumer
  // below falls back to the previous unfiltered behaviour.
  const visibleAlertIdsRef = useRefA(null);
  const onVisibleAlertsChange = useCallbackA((visibleIds) => {
    visibleAlertIdsRef.current = Array.isArray(visibleIds) ? visibleIds : null;
  }, []);

  // Shared next-unacknowledged picker. `sourceAlerts` is passed explicitly
  // rather than closed over so callers can hand in FRESH post-refresh data
  // (SHL-8) instead of the render-time `alerts` snapshot; that also keeps this
  // callback's identity stable for the whole session.
  //
  // Membership (which alerts are candidates) comes from the visible list;
  // ORDER (which candidate wins) stays severity-first-then-newest, because
  // that's the triage priority the operator expects from N / auto-advance
  // regardless of how the table happens to be sorted.
  const pickNextUnack = useCallbackA((currentId, sourceAlerts) => {
    // String() compare on the exclusion too: `currentId` can arrive from a
    // path that stringified it (Contract B ids, sessionStorage), and a missed
    // exclusion would let auto-advance re-select the alert just acted on.
    let list = (sourceAlerts || [])
      .filter(a => a.state === 'pending' && String(a.id) !== String(currentId));
    const visible = visibleAlertIdsRef.current;
    if (Array.isArray(visible) && visible.length > 0) {
      // String() on both sides: ids cross a sessionStorage/DOM boundary in
      // places, so the page may hand us string ids for numeric alerts.
      const visibleSet = new Set(visible.map(String));
      list = list.filter(a => visibleSet.has(String(a.id)));
    }
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
  }, []);

  const findNextUnack = useCallbackA(
    (currentId) => pickNextUnack(currentId, alerts),
    [pickNextUnack, alerts]
  );

  const markSeen = useCallbackA((id) => {
    // Best-effort background write; a failure here shouldn't break the UI,
    // but the promise MUST be caught or React logs it as an unhandled rejection.
    Promise.resolve()
      .then(() => window.SDPRS_API.markSeen(id))
      .catch(err => console.warn('[app] markSeen failed', err));
    setAlerts(prev => {
      const next = prev.map(a => a.id === id ? { ...a, seen: true } : a);
      // Contract A attaches `.truncated` / `.totalAvailable` as PROPERTIES on
      // the alerts array (api.jsx loadAlerts). Array.prototype.map returns a
      // plain new array and silently drops them — and markSeen runs every time
      // an operator so much as views an alert, so the "list is capped" banner
      // would disappear on first read and not return until the next full
      // refresh. Carry the metadata across explicitly.
      if (prev.truncated !== undefined) next.truncated = prev.truncated;
      if (prev.totalAvailable !== undefined) next.totalAvailable = prev.totalAvailable;
      return next;
    });
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
        // Contract C: toast, then RETHROW. Returning normally here told the
        // caller "acknowledged" when nothing was acknowledged, which is what
        // made AlertDetail's failure branches dead code (ALR-H1/ALR-M8).
        showToast('認領失敗: ' + actionErrorText(e), 'warn');
        throw e;
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
      // SHL-8: pick the advance target AFTER the refresh, from the data the
      // refresh just landed. Choosing it beforehand meant a peer operator who
      // resolved that alert seconds earlier left us selecting a row that no
      // longer exists — the detail pane goes blank and every subsequent
      // keyboard action (A/R/↑/↓, all gated on `sel`) silently does nothing.
      // window.ALERTS is what refreshLive just wrote and is readable
      // synchronously; the `alerts` state variable still holds the pre-refresh
      // snapshot at this point (React hasn't re-rendered this closure).
      await refresh();
      const next = advance ? pickNextUnack(id, window.ALERTS ?? []) : null;
      if (next) setSelectedId(next);
    } finally {
      alertBusyRef.current = false;
      setAlertBusy(false);
    }
  }, [showToast, pickNextUnack, refresh, muteState.global]);

  const onResolve = useCallbackA(async (id, note) => {
    // ALR-M4: `!note` let a note of pure whitespace through the keyboard-R
    // gate — the backend stores it, the alert closes, and the audit trail
    // carries a blank disposition for an event someone will have to explain
    // later. Test the trimmed value, the same way the UI's own required-field
    // affordance reads to an operator.
    if (!note || !String(note).trim()) {
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
        // Contract C: toast, then RETHROW so AlertDetail's catch (which keeps
        // the drafted note) actually runs. Returning normally here is what
        // wiped a failed resolve's write-up (ALR-H1) — on a typhoon night
        // that is a paragraph of incident notes gone with no way back.
        showToast('解決失敗: ' + actionErrorText(e), 'warn');
        throw e;
      }
      // Operator engaged with the queue — clear the "N new" banner too.
      setNewAlertBannerCount(0);
      showToast('警報已解決', 'ok');
      // SHL-8: see onAck — advance target chosen from post-refresh data.
      await refresh();
      const next = pickNextUnack(id, window.ALERTS ?? []);
      if (next) setSelectedId(next);
    } finally {
      alertBusyRef.current = false;
      setAlertBusy(false);
    }
  }, [showToast, pickNextUnack, refresh]);

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
        // Contract C: toast, then RETHROW. Without it the snooze menu closed
        // on failure exactly as it does on success (ALR-M8) — the operator
        // walks away believing a node is muted while it is still armed to
        // wake them, or believing the snooze took when it never reached the
        // server.
        showToast('延期失敗: ' + actionErrorText(e), 'warn');
        throw e;
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
  // SHL-10: mount-then-load. The initial data fetch used to sit in front of
  // ReactDOM.render(), so the operator stared at index.html's (motionless —
  // see SHL-7) boot spinner until all SEVEN loaders settled; each carries a
  // 10s abort timeout, so a degraded backend meant ~20s of frozen page with no
  // nav, no clock and no way to tell a slow load from a hung tab. Nothing in
  // the shell needs the data to render — every consumer already reads the
  // empty data.jsx placeholders — so we mount first and fill in here.
  //
  // This effect also absorbs the two mount-time effects that used to read
  // window.* immediately (the partial-failure banner and the B8 shift banner);
  // both would now run before the data exists and see nothing.
  const bootRanRef = useRefA(false);
  useEffectA(() => {
    if (bootRanRef.current) return;
    bootRanRef.current = true;
    let cancelled = false;
    (async () => {
      try {
        await window.SDPRS_API.loadInitial();
        if (cancelled) return;
        setAlerts(window.ALERTS ?? []);
        setNodes(window.NODES ?? []);
        setNodeHistory(window.NODE_HISTORY ?? {});
        // Resolve the selection now that there are alerts to resolve against:
        // an explicit cross-login RESTORED_STATE id wins (already seeded), then
        // the sessionStorage id captured during first render, then the head of
        // the queue. Functional update so a selection the operator made while
        // the load was in flight is never yanked out from under them.
        setSelectedId(prev => {
          if (prev != null) return prev;
          const saved = savedSelectedIdRef.current;
          if (saved != null) {
            const match = (window.ALERTS ?? []).find(a => String(a.id) === saved);
            if (match) return match.id;
          }
          return window.ALERTS?.[0]?.id ?? null;
        });
        // B8: auto-open the shift banner when there's meaningful summary data.
        if (window.SHIFT_SUMMARY && window.SHIFT_SUMMARY.alertsHandled > 0) {
          setShiftBannerOpen(true);
        }
        // Partial-failure surface — loadInitial records which loaders rejected
        // on window.__SDPRS_LOAD_FAILURES.
        const failures = window.__SDPRS_LOAD_FAILURES;
        if (failures && failures.length > 0) {
          setDataWarnings(failures);
          const labels = failures.map(k => _FAILURE_LABELS[k] || k).join('、');
          showToast('部分資料載入失敗: ' + labels, 'warn');
        }
      } catch (e) {
        // Total failure — show the in-app retry UI rather than a bare shell
        // full of empty states that looks like a quiet night.
        console.error('[SDPRS] initial data load failed:', e);
        if (!cancelled) setBootstrapError(e || new Error('loadInitial failed'));
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint: mount-only by design — showToast is a stable useCallback and
    // _FAILURE_LABELS is a module-constant-shaped literal.
  }, []);

  // Command palette command dispatch
  const onCmdkCommand = useCallbackA((id) => {
    if (id === 'mute-all') setMuteDrawerOpen(true);
    else if (id === 'focus-mode') setFocusMode(f => !f);
    else if (id === 'density') setTweak('density', tweaks.density === 'compact' ? 'comfortable' : 'compact');
    else if (id === 'shortcuts') setShortcutsOpen(true);
    else if (id === 'audit-me') setPage('audit');
    // CMP-F11: palette node results dispatch `node:<id>` (see CommandPalette) so
    // picking a node opens that node's detail panel instead of dead-ending on the
    // generic status page. `'node:'.length === 5`. The panel is derived from
    // nodePanelNodeId via nodes.find (see line ~215), so the sliced id must match
    // a node.id exactly — the palette emits `node:` + the same n.id.
    else if (typeof id === 'string' && id.indexOf('node:') === 0) setNodePanelNodeId(id.slice(5));
  }, [tweaks.density, setTweak, setPage, setNodePanelNodeId]);

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
      // SHL-9: the session-expiry modal is blocking and action-required, but
      // it never blocked the single-key shortcuts underneath it. Keys 1-7 kept
      // switching the page BEHIND the modal, so the `page` snapshot encoded
      // into the re-login roundtrip (see the modal's button below) was
      // whatever the operator's stray keystrokes last landed on — they came
      // back from logging in on the wrong page. A / R / M / T were equally
      // live against a dead session, firing 401s and toasting failures. Every
      // shortcut below this line is now inert while the modal is up; Cmd/Ctrl+K
      // and Escape are handled above and already carry their own guards.
      if (sessionExpired) return;
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
        const templates = window.RESOLVE_TEMPLATES || [];
        const t = templates[idx];
        if (t) {
          // ALR-M3: this used to overwrite the note outright, so an operator
          // who had typed half a disposition and then reached for the "2"
          // shortcut lost it — while clicking the very same template as a chip
          // appends (see applyTemplate in alerts.jsx). Two paths to one action
          // must not differ on whether they destroy your work.
          //
          // The chip path appends only when its local `noteEdited` flag says
          // the operator typed. app.jsx can't see that flag, so we approximate
          // it with the one thing that's observable from here: a note that is
          // exactly some template is pure template application (replace, so
          // pressing 1 then 2 swaps rather than accumulates, matching chips);
          // anything else contains operator writing (append, never destroy).
          setResolveNote(prev => {
            const cur = prev || '';
            if (!cur.trim()) return t;
            if (templates.includes(cur.trim())) return t;
            return cur.replace(/\s+$/, '') + '\n' + t;
          });
          showToast(`已套用模板: ${t}`, 'info');
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
          // Contract C: onAck/onResolve now REJECT on failure. These two
          // call sites are fire-and-forget (a keydown handler can't await),
          // so they must swallow the rejection explicitly — otherwise every
          // failed keyboard ack becomes an unhandled promise rejection in the
          // console of a console that runs for weeks at a time. The operator
          // has already been told by the toast inside the handler.
          onAck(sel.id, !e.shiftKey).catch(() => { /* toasted in onAck */ });
        }
        return;
      }
      if (e.key === 'r' || e.key === 'R') {
        if (sel.state === 'acknowledged') {
          onResolve(sel.id, resolveNote).catch(() => { /* toasted in onResolve */ });
        }
        return;
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        // ALR-H2 (Contract B): walk the list the operator is actually looking
        // at. `alerts.filter(state !== 'resolved')` is the whole queue; under
        // any tab/severity/search filter it contains rows AlertsPage isn't
        // rendering, so ↓ would select an invisible alert and the page would
        // immediately snap the selection back — arrow triage just stopped
        // responding. The ref holds the page's own rendered order.
        const visible = visibleAlertIdsRef.current;
        const ids = (Array.isArray(visible) && visible.length > 0)
          ? visible
          : alerts.filter(a => a.state !== 'resolved').map(a => a.id);
        // C-9: when the list is empty, ids[nextIdx] is undefined and `.id`
        // throws. Bail before indexing.
        if (ids.length === 0) return;
        // String() compare: ids arriving over Contract B may be stringified
        // even when the underlying alert id is not. -1 (selection not in the
        // visible list) is intentionally left to fall through — ArrowDown then
        // lands on index 0, which is the right recovery when the operator's
        // selection has just been filtered out from under them.
        const idx = ids.findIndex(x => String(x) === String(selectedId));
        const nextIdx = e.key === 'ArrowDown' ? Math.min(ids.length - 1, idx + 1) : Math.max(0, idx - 1);
        const nextId = ids[nextIdx];
        // Hand back the LIVE alert's own id, not the possibly-stringified one,
        // so the `a.id === selectedId` strict-equality checks used throughout
        // this file keep matching.
        const liveAlert = alerts.find(a => String(a.id) === String(nextId));
        setSelectedId(liveAlert ? liveAlert.id : nextId);
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

  // B8's auto-open-shift-banner check moved into the SHL-10 boot effect above:
  // window.SHIFT_SUMMARY is only populated by loadInitial, which no longer
  // runs before mount, so a mount-time check here would always see the empty
  // data.jsx placeholder and the banner would never open.

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

  // CMP-F1 (defense in depth): stable `onClose` identities for the four
  // overlays. These were inline `() => setX(false)` lambdas, freshly allocated
  // on every App render — and each overlay's open/close effect in
  // components.jsx lists `onClose` in its dependency array. So every ~20s poll
  // and every inbound WS alert tore those effects down and re-ran them: the
  // cleanup restored focus to whatever sat behind the modal, and the re-run
  // grabbed it again (or, for CommandPalette/ShortcutsModal, didn't). Focus
  // moved out from under the operator mid-keystroke, and the stray keys landed
  // on the global single-key hotkeys — A acking an alert nobody chose. The
  // authoritative fix is the ref pattern inside components.jsx (owned by the
  // components agent); stabilising the props here removes the trigger from
  // this side too, and neither fix depends on the other.
  const onCloseShortcuts = useCallbackA(() => setShortcutsOpen(false), []);
  const onCloseMuteDrawer = useCallbackA(() => setMuteDrawerOpen(false), []);
  const onCloseCmdk = useCallbackA(() => setCmdkOpen(false), []);
  const onCloseNodePanel = useCallbackA(() => setNodePanelNodeId(null), []);

  // SHL-2 / WHA-H4: the way OUT of wall mode. Wall mode disables every hotkey
  // and replaces the entire shell — and its only toggle lives in the tweaks
  // panel, which nothing in production can open (no code posts
  // `__activate_edit_mode`). A `wallMode: true` persisted in the sdprs.tweaks
  // localStorage blob therefore booted the console into a display with no
  // controls and no exit: recovery meant opening devtools on a NOC machine
  // during whatever event put it there. This callback backs both the visible
  // button and the Escape handler below.
  const onExitWallMode = useCallbackA(() => setTweak('wallMode', false), [setTweak]);

  // Escape leaves wall mode. This needs its own listener because the main
  // shortcut handler deliberately bails on `tweaks.wallMode` (a wall display
  // has no operator context for hotkeys) — and that early return is exactly
  // what made the mode inescapable. Scoped to wall mode only, so it adds no
  // Escape behaviour to the normal shell, where Escape already has an
  // overlay-dismissal stack. IME guard mirrored from the main handler: a
  // zh-TW composition must never be able to tear down the wall display.
  useEffectA(() => {
    if (!tweaks.wallMode) return;
    const onKey = (e) => {
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        onExitWallMode();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [tweaks.wallMode, onExitWallMode]);

  // Contract B housekeeping: AlertsPage owns the visible-id ref while it is
  // mounted; once the operator navigates away, the last-reported list is a
  // stale snapshot of filters that are no longer on screen. Drop it so the
  // next visit starts from the fallback (unfiltered) behaviour until the page
  // reports afresh.
  useEffectA(() => {
    if (page !== 'alerts') visibleAlertIdsRef.current = null;
  }, [page]);

  // --- Memoized active alerts (non-resolved) ---
  // Previously `alerts.filter(a => a.state !== 'resolved')` ran inline in
  // renderPage() AND in the NodeSidePanel JSX — twice per render. Hoisting
  // to useMemo ensures it runs once and the reference is stable for
  // React.memo'd children (MonitorPage, NodeSidePanel).
  const activeAlerts = useMemoA(() => alerts.filter(a => a.state !== 'resolved'), [alerts]);

  const renderPage = () => {
    // SHL-10: the shell (status strip, nav rail, clock, footer) paints
    // immediately, but the PAGE must not render against data.jsx's empty
    // placeholders. AlertsPage's empty state is SystemOKState — a full-panel
    // 「系統正常」 all-clear — and an all-clear the console has not actually
    // verified is the single most dangerous thing this UI can display. Same
    // class of lie on the monitor wall (every node absent) and pumps. Show an
    // honest loading panel until the first load settles; the operator can
    // still navigate, open the palette and read the strip meanwhile, which is
    // the entire point of mounting first.
    if (booting) {
      return (
        <div className="h-full flex items-center justify-center" role="status">
          <div className="flex flex-col items-center gap-3 text-ink-muted">
            <span className="w-8 h-8 rounded-full border-[3px] border-border-subtle border-t-sev-info animate-spin" aria-hidden="true"></span>
            <div className="text-sm tracking-wide">正在載入即時資料…</div>
          </div>
        </div>
      );
    }
    const wrap = (el) => <ErrorBoundary key={page}>{el}</ErrorBoundary>;
    switch (page) {
      case 'alerts': return wrap(<window.AlertsPage density={tweaks.density} selectedId={selectedId} setSelectedId={setSelectedId} alerts={alerts} onAck={onAck} onResolve={onResolve} onSnooze={onSnooze} onRefresh={refresh} onVisibleChange={onVisibleAlertsChange} ackedIds={ackedIds} resolveNote={resolveNote} setResolveNote={setResolveNote} busy={alertBusy} nodes={nodes} nodeHistory={nodeHistory}/>);
      case 'monitor': return wrap(<window.MonitorPage nodes={nodes} activeAlerts={activeAlerts} onSelectNode={onSelectNode}/>);
      case 'status': return wrap(<window.StatusPage nodes={nodes} onSelectNode={onSelectNode} onRefresh={refresh}/>);
      case 'pumps': return wrap(<window.PumpsPage nodes={nodes} onSelectNode={onSelectNode} showToast={showToast}/>);
      case 'weather': return wrap(<window.WeatherPage showToast={showToast} onRefresh={refresh}/>);
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
      // SHL-10: re-enter the loading state for the duration of the retry, so
      // the shell shows the honest 「正在載入…」 panel instead of dropping
      // straight to empty pages that read as an all-clear.
      setBooting(true);
      try {
        await window.SDPRS_API.loadInitial();
        setAlerts(window.ALERTS ?? []);
        setNodes(window.NODES ?? []);
        setNodeHistory(window.NODE_HISTORY ?? {});
        setSelectedId(window.ALERTS?.[0]?.id ?? null);
      } catch (e) {
        console.error('[SDPRS] retry loadInitial failed:', e);
        setBootstrapError(e || err);
      } finally {
        setBooting(false);
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
    <div className="relative h-screen w-screen bg-black">
      {/* SHL-17: WallView was the one view rendered OUTSIDE an ErrorBoundary,
          so a render-time throw in it (a malformed node, a missing
          SnapshotImage) blanked the whole 4K display to a white screen with
          nothing but a console message — on the screen a room full of people
          is watching. renderPage() has wrapped every other page for a while;
          the wall branch simply never got the same treatment. */}
      <ErrorBoundary>
        {booting ? (
          // Same honesty rule as renderPage(): a wall showing zero unacked
          // alerts and an empty node grid is an all-clear the console has not
          // earned yet — and this one is being read across a room.
          <div className="h-screen w-screen flex flex-col items-center justify-center gap-4 bg-black text-ink-muted" role="status">
            <span className="w-12 h-12 rounded-full border-4 border-border-subtle border-t-sev-info animate-spin" aria-hidden="true"></span>
            <div className="text-xl tracking-widest">SDPRS · 正在載入即時資料…</div>
          </div>
        ) : (
          <WallView alerts={alerts} nodes={nodes} unackCount={unackCount}/>
        )}
      </ErrorBoundary>
      {/* SHL-2: the exit. Deliberately rendered OUTSIDE the ErrorBoundary
          above — if WallView itself crashes, the way out must still be on
          screen, which is precisely the case where an operator would
          otherwise be reaching for devtools. */}
      <button
        type="button"
        onClick={onExitWallMode}
        className="absolute bottom-10 right-3 z-50 inline-flex items-center gap-1.5 px-3 h-8 rounded border border-border-strong bg-surface-overlay/90 text-ink-secondary text-xs font-medium hover:text-ink-primary hover:border-ink-muted focus:outline-none focus:ring-2 focus:ring-sev-info"
        aria-label="離開牆面模式，返回操作介面"
      >
        <Icon.ChevronRight size={13} className="rotate-180" aria-hidden="true"/>
        離開牆面模式
        <span className="kbd !h-4 !text-[9px] !px-1 ml-0.5">Esc</span>
      </button>
    </div>
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

      <window.ShortcutsModal open={shortcutsOpen} onClose={onCloseShortcuts}/>
      <window.MuteDrawer open={muteDrawerOpen} onClose={onCloseMuteDrawer} muteState={muteState} setMuteState={setMuteState} nodes={nodes}/>
      <window.CommandPalette open={cmdkOpen} onClose={onCloseCmdk} alerts={alerts} nodes={nodes} onSelectAlert={setSelectedId} onNav={setPage} onCmd={onCmdkCommand}/>
      <window.NodeSidePanel
        node={nodePanelNode}
        history={nodePanelNode ? (nodeHistory[nodePanelNode.id] || []) : []}
        onClose={onCloseNodePanel}
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
        {/* `bg-brand-primary` used to be this button's background — a token
            that exists in NEITHER tailwind.config's `colors` block (only
            surface/ink/border/sev-* are declared) NOR styles.css. Tailwind
            Play emits nothing for an unknown utility, so the button rendered
            with no background at all: white text on the elevated panel, i.e.
            effectively invisible. It is the ONLY control on a blocking,
            action-required modal — the operator's single way back into a live
            session. Same failure mode as SHL-7's never-spinning spinner. */}
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
          className="w-full h-9 bg-sev-info text-white rounded font-medium hover:opacity-90"
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

// SHL-17 (second half): `SnapshotImage` is declared in components.jsx as a
// bare top-level `const` and is never assigned to `window`. It only resolves
// here because Babel's preset-env rewrites top-level `const` to `var`, which
// on a classic <script> becomes a property of the global object — an accident
// of the no-build-step setup, not an export. Any change to how these files are
// compiled (a real bundler, `type="module"`, preset-env targets that keep
// `const`) turns the wall display into a ReferenceError at render time.
//
// Resolve it defensively AT RENDER, preferring a real `window.SnapshotImage`
// export if components.jsx ever adds one (see the handoff note), falling back
// to the accidental global, and finally degrading to the same
// `snapshot-placeholder` box the component itself renders when a node has no
// frame — a wall with icon-less tiles beats a wall that is a blank screen.
function WallSnapshot(props) {
  const Impl = window.SnapshotImage
    || (typeof SnapshotImage !== 'undefined' ? SnapshotImage : null);
  if (!Impl) {
    return <div className="absolute inset-0 snapshot-placeholder" aria-hidden="true"></div>;
  }
  return <Impl {...props}/>;
}

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
          {/* SHL-14 (= WHA-M6): both of these rendered their unit with no
              number in front of it. `rain.now` is hardcoded null in mapWeather
              — the backend exposes only a 24h total, and api.jsx deliberately
              refuses to fabricate an instantaneous rate from it — so this line
              read a bare 「 mm/h」 forever, which on a wall parses as "0" at a
              glance. Wind had the same hole whenever a provider supplied no
              wind data (JSX renders null as nothing, so 「 km/h」). Quote the
              figure we genuinely have (the 24h accumulation, labelled as such)
              and render 「—」 for anything missing. */}
          <span className="font-mono tnum">{weather.wind?.dir || ''} {weather.wind?.speed ?? '—'} km/h</span>
          <span className="text-ink-dim">|</span>
          <span className="font-mono tnum text-sev-info">{weather.rain?.day ?? '—'} mm/24h</span>
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
                  <WallSnapshot node={n} iconSize={64}/>
                  <div className={`absolute top-2 left-2 w-4 h-4 rounded-full bg-sev-${n.status === 'offline' || n.status === 'critical' ? 'critical' : n.status === 'warn' ? 'warn' : 'ok'} ring-2 ring-black/50 ${n.status === 'offline' || n.status === 'critical' ? 'animate-live-blink' : ''}`}></div>
                  {n.status === 'offline' && (
                    <div className="absolute inset-0 flex items-center justify-center bg-black/40">
                      {/* Contract A: `heartbeat` is `number | null` — the old
                          `999` never-reported sentinel is gone. The previous
                          `Math.floor(n.heartbeat/60)` turned a null into
                          `Math.floor(0)` and painted 「離線 0m」 across the NOC
                          wall for a node that has NEVER checked in — the exact
                          opposite of the truth, and the reading a room full of
                          people acts on. Only quote a duration we actually
                          have; otherwise say so. */}
                      <div className="bg-sev-critical text-white text-base font-bold px-3 py-1 rounded">
                        {n.heartbeat != null ? `離線 ${window.fmtAge(n.heartbeat)}` : '離線 · 從未回報'}
                      </div>
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
            {/* Same missing-number-with-a-unit trap as the rain tile: a null
                `degree` left a bare 「°」 on the wall. */}
            <div className="text-sm text-ink-muted font-mono tnum mt-1">{weather.wind?.dir || '—'} {weather.wind?.degree != null ? `${weather.wind.degree}°` : ''}</div>
          </div>
          <div className="bg-surface-panel border border-sev-info/30 rounded p-4 flex-1">
            {/* SHL-14 (= WHA-M6): this hero tile advertised 「雨量 … mm/h」 with
                `rain.now`, which mapWeather hardcodes to null — so the wall's
                largest rain figure was a permanent 「—」 while the one real
                number (the 24h total) hid in 8px type underneath, and rendered
                as a confident 「0mm」 when it was actually absent. Promote the
                measurement that exists, label its true window, and say plainly
                that no instantaneous rate is available rather than leaving a
                dash the room will read as "no rain". */}
            <div className="text-xs uppercase tracking-wider text-sev-info font-bold flex items-center gap-2"><Icon.CloudRain size={14}/> 24 小時雨量</div>
            <div className="mt-2 flex items-baseline gap-1">
              <span className="text-7xl font-mono font-black tnum text-sev-info">{weather.rain?.day ?? '—'}</span>
              <span className="text-ink-muted text-xl">mm</span>
            </div>
            <div className="text-sm text-ink-muted font-mono tnum mt-1">即時雨率 — 資料來源未提供</div>
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

// SHL-10: mount FIRST, load after. This used to `await loadInitial()` before
// rendering anything, which put all seven loaders (10s abort timeout each) in
// front of first paint — up to ~20s of nothing but index.html's boot spinner
// on a degraded backend, indistinguishable from a hung tab.
//
// Mounting first is safe because data.jsx seeds every window.* global the
// shell reads with an empty placeholder of the correct shape, and App's own
// state initializers read those same placeholders. The load, its failure
// surfaces (partial-failure banner, bootstrap-error retry UI) and the
// selection/shift-banner resolution that depend on it all now live in App's
// boot effect, which runs immediately after this first paint.
(function bootstrap() {
  ReactDOM.createRoot(document.getElementById('root')).render(<App/>);
})();
