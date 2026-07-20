// SDPRS — Pumps Page

const { useMemo: useMemo_pump, useState: useState_pump, useEffect: useEffect_pump, useRef: useRef_pump } = React;

// HH:MM:SS clock formatter for a Date object. Two callers:
//  - fmtNowClock_pump(): a client-side UI timestamp for something that just
//    happened in THIS browser — not a wire timestamp — so it deliberately
//    does NOT go through api.jsx's parseTs (that contract is for
//    server-issued strings only).
//  - MSP-F5's lastPumpCommand display below: `at` is already a Date object
//    by the time it reaches this file (api.jsx's mapNode ran it through
//    parseTs) — this only formats it, never hand-rolls a new Date(...) from
//    a wire string itself.
const fmtClockTime_pump = (d) => {
  if (!d) return '—';
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};
const fmtNowClock_pump = () => fmtClockTime_pump(new Date());

// How long to hold a command "in flight" after the HTTP ack while waiting
// for the device's own next pump_status publish to confirm it actually
// happened. nodes.py documents ~2s device publish cadence; app.jsx's
// safety-net poll is 20s worst-case when WS is down — 25s covers one full
// poll cycle with margin so a healthy round-trip is never mistaken for a
// timeout.
const PUMP_CONFIRM_TIMEOUT_MS = 25000;
// Window an armed ⏹ (indefinite OFF hold) confirmation stays live before
// auto-disarming — a stale "confirm?" state from minutes ago should not
// still be one click away from firing.
const OFF_ARM_WINDOW_MS = 5000;

// Manual command control block for a single pump card. `pumpId` uniquely
// identifies the target for the fetch; `disabled` is set when the pump is
// offline (server can't reach it) — the button then reads as inert rather
// than fire-and-fail silently. Bench operators can pick a bounded pulse.
//
// Safety design (dashboard audit 2026-07-20, MSP-F1/F2/F4/F5/F16/F17):
//  - A command is "in flight" from click until the DEVICE confirms via the
//    next `pumpState` update, not until the HTTP request merely round-trips.
//    nodes.py: the device can silently DROP an ON command under dry-run /
//    sensor-conflict protection — an HTTP 200 only means "MQTT publish was
//    accepted", never "the pump did it". Buttons (and the armed ⏹ confirm)
//    stay disabled/hidden for the whole in-flight+awaiting-confirm window,
//    so a rapid re-click / held Enter cannot fire a second command while
//    the first is still unresolved.
//  - ⏹ 停機 is an INDEFINITE OFF hold (only cleared by another operator
//    command) — the single most consequential action on this card, so it
//    requires an explicit two-step arm/confirm naming the pump rather than
//    a bare click. The confirm button intentionally does NOT autofocus
//    (see WHA-M9/CMP-F8 — autofocusing the destructive action means a
//    stray Enter fires it); focus instead lands on Cancel.
//  - Errors surface the real HTTP status/detail instead of one generic
//    string, and a client-side timeout is worded as "unknown — may have
//    still gone through" rather than a flat failure, because re-sending
//    blind into an ambiguous timeout is how a pump gets double-commanded.
//    The dead `r.ok === false` branch from the old code is removed —
//    apiFetch throws on any non-2xx response, it never resolves with that
//    shape.
const PumpManualControls = ({ pumpId, pumpState, dryRunProtect, sensorConflict, manualOverride, lastPumpCommand, disabled, showToast }) => {
  const [busyLabel, setBusyLabel] = useState_pump(null); // label of the button currently sending or awaiting device confirmation
  const [phase, setPhase] = useState_pump(null); // 'sending' | 'awaiting' | null
  const [armOff, setArmOff] = useState_pump(false); // ⏹ two-step confirm armed
  const [lastOutcome, setLastOutcome] = useState_pump(null); // { text, tone } — persists on the card, not just a 3s toast
  const [releaseBusy, setReleaseBusy] = useState_pump(false); // MSP-F6: separate in-flight flag for the 恢復自動 release action
  const awaitRef = useRef_pump(null); // { expected: 'on'|'off', timer } while phase === 'awaiting'
  const armTimerRef = useRef_pump(null);
  const mountedRef = useRef_pump(true);

  useEffect_pump(() => () => {
    mountedRef.current = false;
    if (awaitRef.current && awaitRef.current.timer) clearTimeout(awaitRef.current.timer);
    if (armTimerRef.current) clearTimeout(armTimerRef.current);
  }, []);

  // MSP-F1/F5 fix: resolve "awaiting" only when the device's own reported
  // state (pumpState — 'on' | 'off' | null, from mapNode's pump_state)
  // actually matches what was commanded. Never resolve on the HTTP ack alone.
  useEffect_pump(() => {
    if (!awaitRef.current) return;
    if (pumpState === awaitRef.current.expected) {
      clearTimeout(awaitRef.current.timer);
      awaitRef.current = null;
      setPhase(null);
      setBusyLabel(null);
      const label = pumpState === 'on' ? '運轉中' : '已停機';
      setLastOutcome({ text: `裝置已確認：${pumpId} ${label}（${fmtNowClock_pump()}）`, tone: 'ok' });
      showToast && showToast(`${pumpId} 已確認${pumpState === 'on' ? '運轉' : '停機'}`, 'ok');
    }
  }, [pumpState]);

  // A pump that drops offline mid-command will never publish the
  // confirming pump_status — don't leave the UI silently "awaiting"
  // forever; say plainly that the outcome is unknown.
  useEffect_pump(() => {
    if (!disabled) return;
    setArmOff(false);
    if (armTimerRef.current) { clearTimeout(armTimerRef.current); armTimerRef.current = null; }
    if (awaitRef.current) {
      clearTimeout(awaitRef.current.timer);
      awaitRef.current = null;
      setPhase(null);
      setBusyLabel(null);
      setLastOutcome({ text: `${pumpId} 已離線，指令是否生效未知`, tone: 'warn' });
      showToast && showToast(`${pumpId} 離線，指令是否生效未知`, 'warn');
    }
  }, [disabled]);

  const clearArmTimer = () => { if (armTimerRef.current) { clearTimeout(armTimerRef.current); armTimerRef.current = null; } };
  const armOffConfirm = () => {
    if (busyLabel || releaseBusy) return;
    setArmOff(true);
    clearArmTimer();
    armTimerRef.current = setTimeout(() => { if (mountedRef.current) setArmOff(false); }, OFF_ARM_WINDOW_MS);
  };
  const cancelArmOff = () => { setArmOff(false); clearArmTimer(); };

  // MSP-F4 fix: surface the real HTTP status/detail (apiFetch attaches
  // .status/.detail/.timeout to every thrown error) instead of collapsing
  // every failure into one generic "網路或權限問題" string, and word a
  // client-side timeout as "unknown outcome" — the POST may have already
  // reached the broker even though this browser gave up waiting on it.
  const reportSendError = (e) => {
    const detail = e && e.detail ? String(e.detail) : '';
    let text;
    if (e && e.timeout) {
      text = `逾時（10 秒無回應）：${pumpId} 指令狀態不明，可能已送達，請先確認狀態再決定是否重送`;
    } else if (e && e.status === 401) {
      text = '登入逾時，請重新登入後再試一次';
    } else if (e && e.status) {
      text = `指令失敗（HTTP ${e.status}）${detail ? '：' + detail : ''}`;
    } else {
      text = `指令發送失敗${detail ? '：' + detail : '（網路或權限問題）'}`;
    }
    setLastOutcome({ text, tone: 'critical' });
    showToast && showToast(text, 'warn');
  };

  const send = async (action, durationS, label, expected) => {
    if (busyLabel) return; // in flight or awaiting device confirmation — never double-fire
    // MSP-F18 fix: mirror status.jsx SnoozeRowButton's API-existence guard —
    // don't let a command silently no-op or throw an unhandled rejection
    // when the API bundle hasn't loaded, and don't fire at a phantom pumpId
    // (this card unmounts the moment its node drops out of the `nodes` list,
    // but a stale/missing id should never reach a fetch call regardless).
    const api = window.SDPRS_API;
    if (!pumpId || !(api && typeof api.pumpCommand === 'function')) {
      reportSendError({ detail: '暫時無法連線後端，請重新整理頁面後再試' });
      return;
    }
    setArmOff(false); clearArmTimer();
    setLastOutcome(null);
    setBusyLabel(label);
    setPhase('sending');
    // Already in the commanded state (e.g. OFF re-sent while already off) —
    // there is nothing to wait for; don't hold the UI "in flight" for 25s.
    const alreadyThere = pumpState === expected;
    try {
      await api.pumpCommand(pumpId, action, durationS);
      if (!mountedRef.current) return;
      if (alreadyThere) {
        setPhase(null);
        setBusyLabel(null);
        setLastOutcome({ text: `已送出：${pumpId} 已符合狀態（${fmtNowClock_pump()}）`, tone: 'ok' });
        showToast && showToast(`${pumpId} 已在該狀態，指令已送出確認`, 'ok');
        return;
      }
      setPhase('awaiting');
      showToast && showToast(`已送出至 ${pumpId}：${action === 'ON' ? `運轉 ${durationS} 秒` : '停機'}，等待裝置回報…`, 'info');
      const timer = setTimeout(() => {
        if (!mountedRef.current) return;
        awaitRef.current = null;
        setPhase(null);
        setBusyLabel(null);
        const hint = (action === 'ON' && (dryRunProtect || sensorConflict))
          ? '（目前乾轉保護／感測器衝突中，指令可能遭裝置攔截）'
          : '';
        setLastOutcome({ text: `逾時：${pumpId} 在 ${PUMP_CONFIRM_TIMEOUT_MS / 1000} 秒內未回報新狀態，是否生效未知${hint}`, tone: 'warn' });
        showToast && showToast(`逾時：未收到 ${pumpId} 裝置回報，狀態未知${hint}`, 'warn');
      }, PUMP_CONFIRM_TIMEOUT_MS);
      awaitRef.current = { expected, timer };
    } catch (e) {
      if (!mountedRef.current) return;
      setPhase(null);
      setBusyLabel(null);
      reportSendError(e);
    }
  };

  // MSP-F6 fix: release a manual OFF hold or ON force back to automatic
  // control. Deliberately simpler than send() above — AUTO clears
  // manualOverride, not pumpState, so there is no device-reported target to
  // await; the finding's own guidance is that the existing refresh flow
  // updates node.manualOverride once the command lands, so this only needs
  // to track the HTTP round-trip. Same MSP-F18 API/pumpId existence guard.
  const releaseAuto = async () => {
    if (busyLabel || releaseBusy) return;
    const api = window.SDPRS_API;
    if (!pumpId || !(api && typeof api.pumpCommand === 'function')) {
      reportSendError({ detail: '暫時無法連線後端，請重新整理頁面後再試' });
      return;
    }
    setLastOutcome(null);
    setReleaseBusy(true);
    try {
      await api.pumpCommand(pumpId, 'AUTO');
      if (!mountedRef.current) return;
      setLastOutcome({ text: `已恢復自動：${pumpId}（${fmtNowClock_pump()}）`, tone: 'ok' });
      showToast && showToast(`${pumpId} 已恢復自動控制`, 'ok');
    } catch (e) {
      if (!mountedRef.current) return;
      reportSendError(e);
    } finally {
      if (mountedRef.current) setReleaseBusy(false);
    }
  };

  const busy = busyLabel != null || releaseBusy;
  // MSP-F16 fix: ~22px-tall buttons 6px apart → ≥32px targets, more breathing
  // room between the two bounded ON pulses and the indefinite OFF hold.
  const cls = 'text-[11px] px-3 py-2 min-h-[32px] rounded border font-mono transition-colors';
  const label10 = busyLabel === 'ON10' ? (phase === 'awaiting' ? '等待回報…' : '送出中…') : '▶ 10s';
  const label30 = busyLabel === 'ON30' ? (phase === 'awaiting' ? '等待回報…' : '送出中…') : '▶ 30s';
  const labelOff = busyLabel === 'OFF' ? (phase === 'awaiting' ? '等待回報…' : '送出中…') : '⏹ 停機';

  return (
    <div className="mt-3 pt-3 border-t border-border-subtle">
      {/* MSP-F6 fix (SAFETY-CRITICAL): a manual OFF hold silently overrides
          automatic control indefinitely — an operator who parked a pump OFF
          to service it, then went off-shift, left no on-screen trace that it
          would NOT resume automatically if rain returned. Same visibility +
          release path for a manual ON force. */}
      {(manualOverride === 'OFF' || manualOverride === 'ON') && (
        <div role="alert" className={`flex items-center justify-between gap-2 flex-wrap mb-2.5 px-2.5 py-1.5 rounded border text-xs font-semibold ${
          manualOverride === 'OFF' ? 'border-sev-critical/40 bg-sev-critical/15 text-sev-critical' : 'border-sev-warn/40 bg-sev-warn/15 text-sev-warn'
        }`}>
          <span className="flex items-center gap-1.5">
            <Icon.User size={12} className="flex-shrink-0"/>
            {manualOverride === 'OFF' ? '手動停機中 — 不會自動恢復' : '手動強制運行中 — 不會自動恢復'}
          </span>
          <button
            type="button"
            disabled={disabled || busy || armOff}
            onClick={releaseAuto}
            title="解除手動保持，交還自動控制"
            className={`text-[10px] px-2 py-1 min-h-[26px] rounded border font-mono transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
              manualOverride === 'OFF' ? 'bg-surface-panel border-sev-critical/40 hover:bg-sev-critical/20' : 'bg-surface-panel border-sev-warn/40 hover:bg-sev-warn/20'
            }`}
          >{releaseBusy ? '恢復中…' : '恢復自動'}</button>
        </div>
      )}
      {/* MSP-F5 fix: cross-operator visibility — an operator opening this
          card should see who last commanded this pump and when without
          checking the audit log, so Operator B doesn't re-issue a command
          Operator A already sent. Skips gracefully when null (never
          reported). */}
      {lastPumpCommand && (
        <div className="mb-2 text-[10px] leading-snug text-ink-dim">
          上次指令：{lastPumpCommand.action || '不明'} · 由 {lastPumpCommand.by || '不明'} · {lastPumpCommand.at ? fmtClockTime_pump(lastPumpCommand.at) : '時間不明'}
        </div>
      )}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] text-ink-muted">手動：</span>
        <button
          type="button"
          disabled={disabled || busy || armOff}
          onClick={() => send('ON', 10, 'ON10', 'on')}
          title="送出手動指令：運轉 10 秒後自動停機。乾轉保護仍會攔截。"
          className={`${cls} bg-sev-info/10 text-sev-info border-sev-info/30 hover:bg-sev-info/20 disabled:opacity-40 disabled:cursor-not-allowed`}
        >{label10}</button>
        <button
          type="button"
          disabled={disabled || busy || armOff}
          onClick={() => send('ON', 30, 'ON30', 'on')}
          title="送出手動指令：運轉 30 秒後自動停機。乾轉保護仍會攔截。"
          className={`${cls} bg-sev-info/10 text-sev-info border-sev-info/30 hover:bg-sev-info/20 disabled:opacity-40 disabled:cursor-not-allowed`}
        >{label30}</button>
        {/* MSP-F2 fix: ⏹ is an indefinite OFF hold — the most consequential
            action on this card — so it two-step arms rather than firing on
            a bare click, and the confirm names the specific pump. */}
        {armOff ? (
          <span className="inline-flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => send('OFF', null, 'OFF', 'off')}
              title={`確認：立即停機 ${pumpId}（無限期，直到下一個指令覆蓋或人工重設）`}
              className={`${cls} bg-sev-critical text-white border-sev-critical hover:bg-sev-critical/90 font-semibold`}
            >確認停機 {pumpId}？</button>
            <button
              type="button"
              autoFocus
              onClick={cancelArmOff}
              title="取消停機指令"
              className={`${cls} bg-surface-elevated text-ink-secondary border-border-strong hover:bg-surface-overlay`}
            >取消</button>
          </span>
        ) : (
          <button
            type="button"
            disabled={disabled || busy}
            onClick={armOffConfirm}
            title="停止抽水機：立即停機（無限期，直到下一個指令覆蓋或人工重設）— 需二次確認"
            className={`${cls} bg-sev-critical/10 text-sev-critical border-sev-critical/30 hover:bg-sev-critical/20 disabled:opacity-40 disabled:cursor-not-allowed`}
          >{labelOff}</button>
        )}
      </div>
      {phase === 'awaiting' && (
        <div className="mt-1.5 text-[10px] leading-snug text-sev-info flex items-center gap-1">
          <Icon.Clock size={10} className="animate-live-blink"/>
          等待 {pumpId} 裝置回報中…
        </div>
      )}
      {lastOutcome && (
        <div className={`mt-1.5 text-[10px] leading-snug text-sev-${lastOutcome.tone}`}>{lastOutcome.text}</div>
      )}
    </div>
  );
};

const PumpsPage = ({ nodes = [], onSelectNode, showToast }) => {
  const pumps = useMemo_pump(() => nodes.filter(n => n.type === 'pump'), [nodes]);
  return (
    <div className="h-full overflow-y-auto scroll-thin p-4">
      <div className="flex items-center gap-2 mb-4">
        <h1 className="text-sm font-semibold">抽水站</h1>
        <span className="text-xs text-ink-muted tnum">{pumps.length} 站</span>
      </div>
      {pumps.length === 0 ? (
        <div className="h-64 flex items-center justify-center">
          <EmptyState icon={Icon.Droplet} title="尚無泵浦資料"
            hint="伺服器尚未回報任何抽水站節點"/>
        </div>
      ) : null}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {pumps.map(p => {
          // Offline branches FIRST so a stale-but-high last-known level can't
          // render as a healthy or critical pump — telemetry has stopped and
          // the level is untrustworthy. Warn from status is honored even when
          // the level is below the water-threshold rules.
          const isOffline = p.status === 'offline';
          // Online but no water reading — sensor untrusted, must not read as healthy
          const isNoTelemetry = !isOffline && (p.level == null);
          const isCritical = !isOffline && !isNoTelemetry && (p.status === 'critical' || p.level > 85);
          const isWarn = !isOffline && !isNoTelemetry && !isCritical && (p.status === 'warn' || p.level > 70);
          const tone = isOffline ? 'stale' : isNoTelemetry ? 'warn' : isCritical ? 'critical' : isWarn ? 'warn' : 'ok';
          const statusLabel = isOffline ? '離線' : isNoTelemetry ? '無水位資料' : p.status === 'critical' ? '嚴重' : p.level > 85 ? '高水位' : isWarn ? '警戒' : '正常';
          // MSP-F1 fix: the actual relay/run state the device last reported
          // (mapNode's pumpState — 'on' | 'off' | null), distinct from the
          // water-level-derived `tone/statusLabel` above. This is the field
          // the audit found was never mapped anywhere: an operator could
          // click ▶30s, see "已送出", and never learn the pump never ran.
          // null means the device has never told us — including the
          // safety-critical case where the last ON command was silently
          // dropped by dry-run/sensor-conflict protection. Offline forces
          // this to "unknown" too: a last-known relay state read while
          // telemetry has stopped is exactly as untrustworthy as the level
          // gauge above, and must never be shown as a confident "已停機".
          const pumpRunUnknown = isOffline || p.pumpState == null;
          const pumpRunOn = !pumpRunUnknown && p.pumpState === 'on';
          const pumpRunTone = isOffline ? 'stale' : pumpRunUnknown ? 'warn' : pumpRunOn ? 'ok' : 'info';
          const pumpRunLabel = isOffline ? '狀態未知（離線）' : pumpRunUnknown ? '狀態未知' : pumpRunOn ? '運轉中' : '已停機';
          return (
            <div key={p.id}
              role="button"
              tabIndex={0}
              onClick={() => onSelectNode && onSelectNode(p)}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelectNode && onSelectNode(p); } }}
              // MSP-F22 fix: raw Tailwind slate-600 isn't one of this app's
              // theme-aware tokens — invisible against a light-theme panel.
              // border-border-strong is the token used for this exact
              // "stronger border on interaction" role elsewhere in the SPA.
              className={`bg-surface-panel border rounded p-4 cursor-pointer hover:border-border-strong transition-colors ${isOffline ? 'border-sev-stale/40 opacity-70' : isNoTelemetry ? 'border-sev-warn/40' : isCritical ? 'border-sev-critical/40' : isWarn ? 'border-sev-warn/40' : 'border-border-subtle'}`}>
              <div className="flex items-start justify-between mb-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-bold">{p.id}</span>
                    <span className="text-xs text-ink-secondary">{p.name}</span>
                  </div>
                  <div className="text-xs text-ink-muted mt-0.5">{p.location}</div>
                </div>
                <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border font-medium bg-sev-${tone}/15 text-sev-${tone} border-sev-${tone}/30`}>
                  <span className={`w-1.5 h-1.5 rounded-full bg-sev-${tone}`}></span>
                  {statusLabel}
                </span>
              </div>

              {/* MSP-F1 fix: prominent, unambiguous relay-state indicator —
                  separate from the water-level severity badge above. Tone
                  scheme is deliberately not a value judgment on the relay
                  state itself: ok=running, info=stopped (neutral fact),
                  warn=unknown (needs a human to check, never silently "off"). */}
              <div className="mb-3">
                <span className={`inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded border bg-sev-${pumpRunTone}/15 text-sev-${pumpRunTone} border-sev-${pumpRunTone}/30`}>
                  {pumpRunOn ? <Icon.Activity size={13} className="animate-live-blink"/> : pumpRunUnknown ? <Icon.HelpCircle size={13}/> : <Icon.Pause size={13}/>}
                  幫浦：{pumpRunLabel}
                </span>
              </div>

              {/* Sensor conflict — prominent critical banner, mirrors the glass-node critical alerts */}
              {p.sensorConflict && (
                <div role="alert" className="flex items-center gap-1.5 mb-3 px-2.5 py-1.5 rounded border border-sev-critical/40 bg-sev-critical/15 text-sev-critical text-xs font-semibold">
                  <Icon.AlertTriangle size={12} className="animate-live-blink flex-shrink-0"/>
                  <span>⚠ 感測器衝突 — 檢查浮球開關</span>
                </div>
              )}

              {/* API-F9 fix: node.cyclesAlert is the server's own short-cycling
                  verdict (count > PUMP_CYCLE_ALERT_THRESHOLD), computed
                  server-side but previously never surfaced anywhere in the
                  UI — buried at best in the 啟動頻率 stat's own local
                  magic-number coloring below. Give it its own banner so it
                  reads as an actionable warning. */}
              {p.cyclesAlert && (
                <div role="alert" className="flex items-center gap-1.5 mb-3 px-2.5 py-1.5 rounded border border-sev-warn/40 bg-sev-warn/15 text-sev-warn text-xs font-semibold">
                  <Icon.AlertTriangle size={12} className="flex-shrink-0"/>
                  <span>⚠ 短循環警告 — 啟動頻率過高，檢查浮球開關或管路</span>
                </div>
              )}

              {/* Rain / dry-run protect badges */}
              {(p.raining || p.dryRunProtect) && (
                <div className="flex items-center gap-1.5 mb-3 flex-wrap">
                  {p.raining && (
                    <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border font-medium bg-sev-info/15 text-sev-info border-sev-info/30">
                      🌧 降雨中
                    </span>
                  )}
                  {p.dryRunProtect && (
                    // MSP-F15 fix: was English-only in a zh-TW UI — this is
                    // the exact flag that explains "why didn't my ON run".
                    <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border font-medium bg-sev-warn/15 text-sev-warn border-sev-warn/30">
                      乾轉保護中（幫浦保持停機）
                    </span>
                  )}
                </div>
              )}

              {/* Water level visualization */}
              {/* Audit fix: offline cards previously rendered the last-known
                  `p.level` (potentially days stale) with stale-tone color +
                  fill. Treat offline the same as isNoTelemetry — dashed
                  border + em-dash + no fill — so a stale number never reads
                  as a live measurement. Only ok/warn/critical draw a fill. */}
              <div className={`relative h-32 bg-surface-base border rounded overflow-hidden ${(isOffline || isNoTelemetry) ? 'border-dashed border-sev-warn/40' : 'border-border-subtle'}`}>
                {!(isOffline || isNoTelemetry) && p.level != null && (
                  <div className={`absolute inset-x-0 bottom-0 transition-all duration-500 ${isCritical ? 'bg-sev-critical/40' : isWarn ? 'bg-sev-warn/40' : 'bg-sev-info/40'}`} style={{ height: p.level + '%' }}>
                    <div className={`h-1 ${isCritical ? 'bg-sev-critical' : isWarn ? 'bg-sev-warn' : 'bg-sev-info'}`}></div>
                  </div>
                )}
                {/* Threshold markers */}
                <div className="absolute right-2 top-1 text-[9px] font-mono text-ink-dim tnum">100</div>
                <div className="absolute right-2 bottom-1 text-[9px] font-mono text-ink-dim tnum">0</div>
                <div className="absolute left-0 right-0 border-t border-dashed border-sev-critical/40" style={{ bottom: '85%' }}>
                  <span className="absolute -top-2 right-2 text-[9px] font-mono text-sev-critical tnum bg-surface-panel px-1">85 嚴重</span>
                </div>
                <div className="absolute left-0 right-0 border-t border-dashed border-sev-warn/40" style={{ bottom: '70%' }}>
                  <span className="absolute -top-2 right-2 text-[9px] font-mono text-sev-warn tnum bg-surface-panel px-1">70 警戒</span>
                </div>
                {/* Center value */}
                <div className="absolute inset-0 flex items-center justify-center">
                  <span className={`text-4xl font-mono font-bold tnum ${isOffline ? 'text-sev-stale' : isNoTelemetry ? 'text-sev-warn' : isCritical ? 'text-sev-critical' : isWarn ? 'text-sev-warn' : 'text-ink-primary'}`}>{(isOffline || isNoTelemetry) ? '—' : <>{p.level}<span className="text-base text-ink-muted">%</span></>}</span>
                </div>
              </div>
              <div className="grid grid-cols-4 gap-2 mt-3 text-xs font-mono tnum">
                <div>
                  <div className="text-[10px] text-ink-muted">啟動頻率</div>
                  <div className={p.cycles > 20 ? 'text-sev-critical font-semibold' : p.cycles > 15 ? 'text-sev-warn' : 'text-ink-secondary'}>
                    每 {p.cycles > 0 ? (60/p.cycles).toFixed(1) : '—'} 分
                  </div>
                  <div className="text-[10px] text-ink-dim">本時 {p.cycles}×</div>
                </div>
                <div>
                  <div className="text-[10px] text-ink-muted">趨勢</div>
                  {/* MSP-F14 fix: api.jsx's `trend` is always null (never
                      computed server-side) — the old else-branch rendered a
                      confident flat "→ 平" arrow for every single card,
                      forever, which is indistinguishable from "we checked
                      and it's flat". Only claim up/down/flat when the field
                      actually says so; otherwise say plainly there's no
                      trend data. */}
                  <div className="text-ink-secondary inline-flex items-center gap-0.5">
                    {p.trend === 'up' ? <><Icon.ArrowUp size={10} className="text-sev-warn"/>升</>
                      : p.trend === 'down' ? <><Icon.ArrowDown size={10} className="text-sev-ok"/>降</>
                      : p.trend === 'flat' ? <><Icon.ArrowRight size={10}/>平</>
                      : <span className="text-ink-dim">—</span>}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-ink-muted">電壓</div>
                  <div className={p.voltage != null && p.voltage < 12 ? 'text-sev-warn' : 'text-ink-secondary'}>{p.voltage != null ? p.voltage + 'V' : '—'}</div>
                </div>
                <div>
                  <div className="text-[10px] text-ink-muted">電源</div>
                  <div className={p.power === 'mains' ? 'text-sev-ok' : p.power === 'ups' ? 'text-sev-warn' : 'text-sev-critical'}>
                    {p.power === 'mains' ? '市電' : p.power === 'ups' ? 'UPS' : '電池'}
                  </div>
                </div>
              </div>
              {/* Manual override controls — stopPropagation so clicking a
                  button doesn't also trigger the card's onSelectNode. */}
              <div onClick={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}>
                <PumpManualControls
                  pumpId={p.id}
                  pumpState={p.pumpState}
                  dryRunProtect={p.dryRunProtect}
                  sensorConflict={p.sensorConflict}
                  manualOverride={p.manualOverride}
                  lastPumpCommand={p.lastPumpCommand}
                  disabled={isOffline}
                  showToast={showToast}/>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

Object.assign(window, { PumpsPage });
