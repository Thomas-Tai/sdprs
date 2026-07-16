// SDPRS — Handover Page

const { useState: useState_p, useEffect: useEffect_p } = React;

const HandoverPage = () => {
  // Lazy init — mustn't crash if the loader hasn't populated HANDOVER yet.
  const [text, setText] = useState_p(() => (window.HANDOVER && window.HANDOVER.current) || '');
  // Snapshot of what the server had when we started editing — for the divergence check.
  const [baseline, setBaseline] = useState_p(() => (window.HANDOVER && window.HANDOVER.current) || '');
  const [savedAt, setSavedAt] = useState_p(() => window.HANDOVER && window.HANDOVER.pinned && window.HANDOVER.pinned.at);
  const [saving, setSaving] = useState_p(false);
  const [dirty, setDirty] = useState_p(false);
  // Ticks so we re-poll window.HANDOVER whenever the app-level refresh fires
  // (app.jsx bumps its own tick and re-renders — this re-runs on every render).
  const remoteCurrent = (window.HANDOVER && window.HANDOVER.current) || '';
  const peerChanged = dirty && remoteCurrent !== baseline;

  // On every render, if the remote has changed AND the user hasn't started
  // editing yet, silently adopt the new value. Once they've edited we leave
  // their draft alone and show the peer-updated banner instead.
  useEffect_p(() => {
    if (!dirty && remoteCurrent !== text) {
      setText(remoteCurrent);
      setBaseline(remoteCurrent);
    }
  }, [remoteCurrent, dirty]);

  const setTextTracked = (v) => { setText(v); setDirty(true); };
  const adoptPeerCopy = () => {
    setText(remoteCurrent);
    setBaseline(remoteCurrent);
    setDirty(false);
  };

  const s = window.SHIFT_SUMMARY;
  const today = new Date().toISOString().slice(0, 10);
  const generateSummary = () => {
    const lines = [
      `本班次摘要 (${s.duration})`,
      `處理警報 ${s.alertsHandled} 筆 — 嚴重 ${s.critical} · 警告 ${s.warn} · 資訊 ${s.info}`,
      `中位認領時間 ${s.ackMedian} · 中位解決時間 ${s.resolveMedian}`,
      `仍未解決承接 ${s.carryOver} 筆`,
    ];
    if (s.highlights && s.highlights.length) {
      lines.push('', '主要事件:');
      s.highlights.forEach(h => lines.push(`· ${h.node} ${h.label} (${h.count}×)`));
    }
    setTextTracked(lines.join('\n'));
  };
  const save = async () => {
    // Save-time diff check — if the global changed since we captured baseline,
    // warn before clobbering the peer's edit.
    const latest = (window.HANDOVER && window.HANDOVER.current) || '';
    if (latest !== baseline && latest !== text) {
      const ok = window.confirm('伺服器上的備註在您編輯期間已被其他操作員更新。\n\n確定要以您的版本覆蓋嗎? (取消可先預覽對方版本)');
      if (!ok) return;
    }
    setSaving(true);
    try {
      await window.SDPRS_API.saveHandover(text);
      const now = new Date();
      const p = (n) => String(n).padStart(2, '0');
      setSavedAt(p(now.getHours()) + ':' + p(now.getMinutes()) + ':' + p(now.getSeconds()));
      setBaseline(text);
      setDirty(false);
    } catch (e) {
      alert('儲存失敗: ' + (e.message || e));
    }
    setSaving(false);
  };
  return (
    <div className="h-full overflow-y-auto scroll-thin p-6 grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6">
      <div>
        <div className="flex items-center gap-2 mb-3">
          <h1 className="text-base font-semibold">班次交接備註</h1>
          <span className="text-xs text-ink-muted font-mono tnum">{today} · {window.SDPRS_USER || ''}</span>
          <div className="flex-1"></div>
          <button onClick={generateSummary}
            className="text-xs px-3 h-7 bg-sev-info/15 border border-sev-info/40 text-sev-info rounded hover:bg-sev-info/25 inline-flex items-center gap-1.5">
            <Icon.Activity size={12}/> 自動產生本班次摘要
          </button>
        </div>

        {/* Shift summary card — pre-loaded */}
        <div className="mb-3 bg-surface-panel border border-border-subtle rounded p-3">
          <div className="text-[10px] uppercase tracking-wider text-ink-muted font-semibold mb-2">本班次數據</div>
          <div className="grid grid-cols-5 gap-2">
            <div>
              <div className="text-lg font-mono font-bold tnum">{s.alertsHandled}</div>
              <div className="text-[10px] text-ink-muted">警報處理</div>
            </div>
            <div>
              <div className="text-lg font-mono font-bold tnum text-sev-critical">{s.critical}</div>
              <div className="text-[10px] text-ink-muted">嚴重</div>
            </div>
            <div>
              <div className="text-lg font-mono font-bold tnum">{s.ackMedian}</div>
              <div className="text-[10px] text-ink-muted">中位認領</div>
            </div>
            <div>
              <div className="text-lg font-mono font-bold tnum">{s.resolveMedian}</div>
              <div className="text-[10px] text-ink-muted">中位解決</div>
            </div>
            <div>
              <div className="text-lg font-mono font-bold tnum text-sev-warn">{s.carryOver}</div>
              <div className="text-[10px] text-ink-muted">承接</div>
            </div>
          </div>
        </div>

        {peerChanged && (
          <div className="mb-2 flex items-center gap-2 text-xs bg-sev-warn/10 border border-sev-warn/40 rounded px-3 py-2">
            <Icon.AlertCircle size={12} className="text-sev-warn flex-shrink-0"/>
            <span className="text-sev-warn font-medium">其他操作員已更新交接備註 — 儲存會覆蓋對方的版本</span>
            <div className="flex-1"></div>
            <button onClick={adoptPeerCopy}
              className="text-[11px] font-mono text-sev-warn hover:text-ink-primary underline">
              以對方版本重載
            </button>
          </div>
        )}
        <textarea
          value={text}
          onChange={e => setTextTracked(e.target.value)}
          rows="14"
          className="w-full bg-surface-panel border border-border-strong rounded p-3 text-sm font-mono leading-relaxed focus:border-sev-info focus:outline-none resize-none"
        />
        <div className="mt-3 flex items-center gap-2">
          <button onClick={save} disabled={saving} className="px-3 h-9 bg-sev-info hover:bg-blue-600 disabled:opacity-50 text-white rounded text-sm font-semibold flex items-center gap-2">
            <Icon.Check size={14}/> 儲存
          </button>
          <span className="text-xs text-ink-muted ml-2">
            {savedAt ? <>最後儲存: <span className="font-mono tnum">{savedAt}</span></> : '尚未儲存'}
            {dirty && <span className="ml-2 text-sev-warn">· 未儲存變更</span>}
          </span>
        </div>
      </div>
      <div>
        <h2 className="text-sm font-semibold mb-3 text-ink-secondary">歷史備註</h2>
        <div className="space-y-2">
          {window.HANDOVER.history.map((h, i) => (
            <div key={i} className="bg-surface-panel border border-border-subtle rounded p-3">
              <div className="flex items-center gap-2 text-xs text-ink-muted mb-1.5 font-mono tnum">
                <Icon.User size={12}/> <span className="text-ink-secondary">{h.by}</span>
                <span className="text-ink-dim">·</span>
                <span>{h.at}</span>
              </div>
              <p className="text-xs text-ink-secondary leading-relaxed">{h.text}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

Object.assign(window, { HandoverPage });
