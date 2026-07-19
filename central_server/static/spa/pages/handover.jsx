// SDPRS — Handover Page

const { useState: useState_p, useEffect: useEffect_p } = React;

function ConfirmDialog({ open, title, message, confirmLabel, tone, returnFocus, onCancel, onConfirm }) {
  const dialogRef = React.useRef(null);
  const onCancelRef = React.useRef(onCancel);
  onCancelRef.current = onCancel;
  useEffect_p(() => {
    if (!open) return undefined;
    const handleKeyDown = (e) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopImmediatePropagation();
      onCancelRef.current();
    };
    window.addEventListener('keydown', handleKeyDown, true);
    return () => {
      window.removeEventListener('keydown', handleKeyDown, true);
      if (returnFocus?.isConnected) returnFocus.focus();
    };
  }, [open, returnFocus]);

  if (!open) return null;
  const btnClass = tone === 'danger'
    ? 'bg-red-600 hover:bg-red-500'
    : 'bg-sky-600 hover:bg-sky-500';
  const handleDialogKeyDown = (e) => {
    e.stopPropagation();
    if (e.key !== 'Tab') return;
    const buttons = Array.from(dialogRef.current?.querySelectorAll('button:not([disabled])') || []);
    if (!buttons.length) return;
    const first = buttons[0];
    const last = buttons[buttons.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
  return (
    <div
      ref={dialogRef}
      className="fixed inset-0 z-[110] bg-slate-950/70 flex items-center justify-center p-4"
      onClick={onCancel}
      onKeyDown={handleDialogKeyDown}
      role="dialog"
      aria-modal="true"
      aria-labelledby="handover-confirm-title"
      aria-describedby="handover-confirm-message"
    >
      <div
        className="bg-slate-900 border border-slate-700 rounded-2xl p-6 max-w-md w-full shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="handover-confirm-title" className="text-slate-100 text-lg font-semibold mb-2">{title}</h3>
        <p id="handover-confirm-message" className="text-slate-300 text-sm mb-5 whitespace-pre-line">{message}</p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-4 py-1.5 rounded-lg text-slate-300 hover:bg-slate-800"
          >取消</button>
          <button
            onClick={onConfirm}
            className={`px-4 py-1.5 rounded-lg text-white ${btnClass}`}
            autoFocus
          >{confirmLabel || '確認'}</button>
        </div>
      </div>
    </div>
  );
}

// Draft key for crash-/close-/nav-recovery of an unsaved handover narrative
// (H-2 fix). Cleared on successful save and on peer-copy adoption.
const HANDOVER_DRAFT_KEY = 'sdprs.handover.draft';
const readDraft = () => {
  try { return window.localStorage.getItem(HANDOVER_DRAFT_KEY); } catch (_) { return null; }
};
const writeDraft = (v) => {
  try { window.localStorage.setItem(HANDOVER_DRAFT_KEY, v); } catch (_) { /* quota / privacy mode */ }
};
const clearDraft = () => {
  try { window.localStorage.removeItem(HANDOVER_DRAFT_KEY); } catch (_) { /* ignore */ }
};

const HandoverPage = () => {
  // Lazy init — mustn't crash if the loader hasn't populated HANDOVER yet.
  // If a locally-saved draft exists (tab was closed / crashed mid-edit), restore
  // it and mark dirty so beforeunload + peer-diff keep working.
  const [text, setText] = useState_p(() => {
    const draft = readDraft();
    if (draft != null) return draft;
    return (window.HANDOVER && window.HANDOVER.current) || '';
  });
  // Snapshot of what the server had when we started editing — for the divergence check.
  const [baseline, setBaseline] = useState_p(() => (window.HANDOVER && window.HANDOVER.current) || '');
  const [savedAt, setSavedAt] = useState_p(() => window.HANDOVER && window.HANDOVER.pinned && window.HANDOVER.pinned.at);
  const [saving, setSaving] = useState_p(false);
  const [dirty, setDirty] = useState_p(() => readDraft() != null);
  const [confirm, setConfirm] = useState_p(null);
  const openConfirm = (options) => {
    setConfirm({ ...options, returnFocus: document.activeElement });
  };
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

  // H-2: persist dirty drafts to localStorage on every keystroke so tab close,
  // browser crash, or in-app nav don't destroy a long narrative. Cleared when
  // dirty flips false (saved or peer-adopted).
  useEffect_p(() => {
    if (!dirty) { clearDraft(); return undefined; }
    writeDraft(text);
    return undefined;
  }, [text, dirty]);

  // H-2: browser-level "unsaved changes" prompt on tab close / navigation while
  // the draft is dirty. localStorage recovery handles in-app page switches too.
  useEffect_p(() => {
    if (!dirty) return undefined;
    const handler = (e) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  const setTextTracked = (v) => { setText(v); setDirty(true); };
  const replaceWithPeerCopy = () => {
    const peerCopy = (window.HANDOVER && window.HANDOVER.current) || '';
    setText(peerCopy);
    setBaseline(peerCopy);
    setDirty(false);
    setConfirm(null);
  };
  const adoptPeerCopy = () => {
    openConfirm({
      title: '覆蓋現有草稿？',
      message: '目前草稿內容將被替換為對方版本，無法還原。',
      confirmLabel: '覆蓋',
      tone: 'danger',
      onConfirm: replaceWithPeerCopy,
    });
  };

  const s = window.SHIFT_SUMMARY || {};
  const today = new Date().toISOString().slice(0, 10);

  // H-3: surface 24h TTL expiry so the incoming operator isn't blindsided by
  // a note that vanishes minutes into their shift. ageMin is captured at
  // fetch time (api.jsx loadHandover) — only trust it when the on-screen text
  // still matches what was fetched, otherwise a just-saved draft would show
  // a stale "expiring" badge from the previous note's age.
  const pinnedRef = window.HANDOVER && window.HANDOVER.pinned;
  const pinnedText = (window.HANDOVER && window.HANDOVER.current) || '';
  let expiryBadge = null;
  if (pinnedRef && !dirty && text && text === pinnedText) {
    const ageMinFromServer = Number(pinnedRef.ageMin) || 0;
    const hoursLeft = 24 - ageMinFromServer / 60;
    if (hoursLeft < 1) {
      // < 1h: escalate to red so the incoming operator plainly sees the note
      // is about to disappear and can copy anything critical elsewhere.
      expiryBadge = { tone: 'critical', label: '1 小時內過期' };
    } else if (hoursLeft < 4) {
      // 1–4h: amber warning window — enough runway to save a refreshed copy.
      expiryBadge = { tone: 'warn', label: `即將過期 (${Math.floor(hoursLeft)}h)` };
    }
  }

  const writeGeneratedSummary = () => {
    // Audit fix: template literals interpolate `undefined` literally when
    // a shift-summary field is missing (cold-start race, backend hiccup),
    // producing garbage like "本班次摘要 (undefined)" and "處理警報 undefined 筆"
    // in the operator's saved note. Normalise every field to '—' first.
    const d = (v) => (v == null ? '—' : v);
    const lines = [
      `本班次摘要 (${d(s.duration)})`,
      `處理警報 ${d(s.alertsHandled)} 筆 — 嚴重 ${d(s.critical)} · 警告 ${d(s.warn)} · 資訊 ${d(s.info)}`,
      `中位認領時間 ${d(s.ackMedian)} · 中位解決時間 ${d(s.resolveMedian)}`,
      `仍未解決承接 ${d(s.carryOver)} 筆`,
    ];
    if (s.highlights && s.highlights.length) {
      lines.push('', '主要事件:');
      s.highlights.forEach(h => lines.push(`· ${h.node} ${h.label} (${h.count}×)`));
    }
    setTextTracked(lines.join('\n'));
    setConfirm(null);
  };
  const generateSummary = () => {
    if (!text) {
      writeGeneratedSummary();
      return;
    }
    openConfirm({
      title: '覆蓋現有內容？',
      message: '目前內容將被自動產生的本班次摘要取代，無法還原。',
      confirmLabel: '覆蓋',
      tone: 'danger',
      onConfirm: writeGeneratedSummary,
    });
  };
  const performSave = async () => {
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
  const save = () => {
    // Save-time diff check — if the global changed since we captured baseline,
    // warn before clobbering the peer's edit.
    const latest = (window.HANDOVER && window.HANDOVER.current) || '';
    if (latest !== baseline && latest !== text) {
      openConfirm({
        title: '覆蓋對方版本？',
        message: '伺服器上的備註在您編輯期間已被其他操作員更新。\n\n確定要以您的版本覆蓋嗎？取消可先預覽對方版本。',
        confirmLabel: '仍要儲存',
        tone: 'danger',
        onConfirm: () => {
          setConfirm(null);
          performSave();
        },
      });
      return;
    }
    performSave();
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
              <div className="text-lg font-mono font-bold tnum">{s.alertsHandled ?? '—'}</div>
              <div className="text-[10px] text-ink-muted">警報處理</div>
            </div>
            <div>
              <div className="text-lg font-mono font-bold tnum text-sev-critical">{s.critical ?? '—'}</div>
              <div className="text-[10px] text-ink-muted">嚴重</div>
            </div>
            <div>
              <div className="text-lg font-mono font-bold tnum">{s.ackMedian ?? '—'}</div>
              <div className="text-[10px] text-ink-muted">中位認領</div>
            </div>
            <div>
              <div className="text-lg font-mono font-bold tnum">{s.resolveMedian ?? '—'}</div>
              <div className="text-[10px] text-ink-muted">中位解決</div>
            </div>
            <div>
              <div className="text-lg font-mono font-bold tnum text-sev-warn">{s.carryOver ?? '—'}</div>
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
          aria-label="班次交接備註（單筆全域備註，24 小時後自動失效）"
          className="w-full bg-surface-panel border border-border-strong rounded p-3 text-sm font-mono leading-relaxed focus:border-sev-info focus:outline-none resize-none"
        />
        <div className="mt-3 flex items-center gap-2">
          <button onClick={save} disabled={saving} className="px-3 h-9 bg-sev-info hover:bg-blue-600 disabled:opacity-50 text-white rounded text-sm font-semibold flex items-center gap-2">
            <Icon.Check size={14}/> 儲存
          </button>
          <span className="text-xs text-ink-muted ml-2">
            {savedAt ? <>最後儲存: <span className="font-mono tnum">{savedAt}</span></> : '尚未儲存'}
            {dirty && <span className="ml-2 text-sev-warn">· 未儲存變更</span>}
            {expiryBadge && (
              <span
                className={`ml-2 inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-semibold bg-sev-${expiryBadge.tone}/15 text-sev-${expiryBadge.tone} border-sev-${expiryBadge.tone}/40`}
                title="交接備註 24 小時後會自動失效"
              >
                {expiryBadge.label}
              </span>
            )}
          </span>
        </div>
      </div>
      <div>
        <h2 className="text-sm font-semibold mb-3 text-ink-secondary">歷史備註</h2>
        <div className="space-y-2">
          {(window.HANDOVER && window.HANDOVER.history && window.HANDOVER.history.length)
            ? window.HANDOVER.history.map((h, i) => (
              <div key={i} className="bg-surface-panel border border-border-subtle rounded p-3">
                <div className="flex items-center gap-2 text-xs text-ink-muted mb-1.5 font-mono tnum">
                  <Icon.User size={12}/> <span className="text-ink-secondary">{h.by}</span>
                  <span className="text-ink-dim">·</span>
                  <span>{h.at}</span>
                </div>
                <p className="text-xs text-ink-secondary leading-relaxed">{h.text}</p>
              </div>
            ))
            : <div className="text-xs text-ink-muted">尚無歷史備註</div>
          }
        </div>
      </div>
      <ConfirmDialog
        open={!!confirm}
        {...(confirm || {})}
        onCancel={() => setConfirm(null)}
      />
    </div>
  );
};

Object.assign(window, { HandoverPage });
