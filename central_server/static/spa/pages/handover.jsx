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
            autoFocus
          >取消</button>
          <button
            onClick={onConfirm}
            className={`px-4 py-1.5 rounded-lg text-white ${btnClass}`}
          >{confirmLabel || '確認'}</button>
        </div>
      </div>
    </div>
  );
}

// WHA-M8: shown when saveHandover() 409s — the operator's expected_updated_at
// didn't match the server's, meaning someone else saved in between. Unlike
// ConfirmDialog (a single message string), this needs to show BOTH the
// server's current text and the operator's draft side-by-side so they can
// make an informed choice instead of either silently clobbering the peer's
// note or silently discarding their own draft. Mirrors ConfirmDialog's
// overlay/focus-trap/Escape handling verbatim.
function ConflictDialog({ open, serverText, draftText, returnFocus, onCancel, onKeepServer, onKeepMine }) {
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
      aria-labelledby="handover-conflict-title"
      aria-describedby="handover-conflict-message"
    >
      <div
        className="bg-slate-900 border border-slate-700 rounded-2xl p-6 max-w-2xl w-full shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="handover-conflict-title" className="text-slate-100 text-lg font-semibold mb-2">儲存衝突</h3>
        <p id="handover-conflict-message" className="text-slate-300 text-sm mb-4">
          您編輯期間，其他操作員已儲存了新的交接備註。請選擇要保留哪個版本：
        </p>
        <div className="grid grid-cols-2 gap-3 mb-5">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-slate-400 font-semibold mb-1">伺服器目前版本</div>
            <div className="bg-slate-950 border border-slate-700 rounded p-2 text-xs text-slate-200 whitespace-pre-wrap max-h-56 overflow-y-auto scroll-thin">
              {serverText || '(空白)'}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-slate-400 font-semibold mb-1">您的草稿</div>
            <div className="bg-slate-950 border border-slate-700 rounded p-2 text-xs text-slate-200 whitespace-pre-wrap max-h-56 overflow-y-auto scroll-thin">
              {draftText || '(空白)'}
            </div>
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-4 py-1.5 rounded-lg text-slate-300 hover:bg-slate-800"
            autoFocus
          >稍後決定</button>
          <button
            onClick={onKeepServer}
            className="px-4 py-1.5 rounded-lg text-white bg-sky-600 hover:bg-sky-500"
          >保留伺服器版本</button>
          <button
            onClick={onKeepMine}
            className="px-4 py-1.5 rounded-lg text-white bg-red-600 hover:bg-red-500"
          >覆蓋伺服器版本</button>
        </div>
      </div>
    </div>
  );
}

// Draft key for crash-/close-/nav-recovery of an unsaved handover narrative
// (H-2 fix). Cleared on successful save and on peer-copy adoption.
// WHA-M11: scoped per logged-in user — an un-scoped key on a shared console
// leaked one operator's draft into the next login's textarea, risking a
// publish under the wrong operator's name. Computed per-call (not once at
// module load) so it always reflects window.SDPRS_USER as of that moment.
const HANDOVER_DRAFT_KEY_BASE = 'sdprs.handover.draft';
const handoverDraftKey = () => {
  const u = (window.SDPRS_USER && String(window.SDPRS_USER).trim()) || 'anon';
  return HANDOVER_DRAFT_KEY_BASE + '.' + u;
};
const readDraft = () => {
  try { return window.localStorage.getItem(handoverDraftKey()); } catch (_) { return null; }
};
const writeDraft = (v) => {
  try { window.localStorage.setItem(handoverDraftKey(), v); } catch (_) { /* quota / privacy mode */ }
};
const clearDraft = () => {
  try { window.localStorage.removeItem(handoverDraftKey()); } catch (_) { /* ignore */ }
};

// WHA-H3: mirrors the server-side cap in handover.py:32 (`max_length=2000`).
// Keep these two in sync if the backend limit ever changes.
const HANDOVER_MAX_LEN = 2000;
const HANDOVER_WARN_LEN = HANDOVER_MAX_LEN - 100;

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
  // WHA-M8: opaque precondition token (api.jsx loadHandover's raw
  // `updatedAt`, itself the server's raw `updated_at` — NOT parseTs'd)
  // echoed back on save as `expected_updated_at` so the server can detect a
  // lost-update race instead of silently last-write-wins clobbering. Kept
  // current by the effect further down, independent of `dirty` — see that
  // effect's comment for why.
  const [updatedAtToken, setUpdatedAtToken] = useState_p(() => (window.HANDOVER && window.HANDOVER.updatedAt) || null);
  const [saving, setSaving] = useState_p(false);
  const [dirty, setDirty] = useState_p(() => readDraft() != null);
  const [confirm, setConfirm] = useState_p(null);
  // WHA-M8: populated when saveHandover() 409s — carries the server's
  // current text/token plus the draft we tried to send, so the
  // conflict-resolution dialog can show both. Distinct from `confirm`
  // (generic yes/no message) because this renders two text panels.
  const [conflict, setConflict] = useState_p(null);
  const [pageToast, setPageToast] = useState_p(null);
  // Brief post-save grace period: after performSave succeeds, the app-level
  // 20s poll may round-trip the just-saved text back through window.HANDOVER
  // before React has reconciled baseline/dirty. Without this guard the
  // peer-changed banner flashes from the operator's OWN save — confusing
  // and prompting them to "adopt" a phantom peer version.
  const [saveGracePeriod, setSaveGracePeriod] = useState_p(false);
  // Track the in-flight grace-period timer so a rapid double-save clears the
  // previous timer before scheduling the new one. Without this the first
  // timer fires between saves, drops grace back to false, and the second
  // save's poll round-trip re-triggers the peer-changed banner.
  const graceTimerRef = React.useRef(null);
  // WHA-C1: always holds the LATEST `text`, even mid-await inside performSave
  // (whose own `text` closure is frozen at the moment that save started).
  // Updated synchronously on every render — see the "current" line just
  // below, not inside a useEffect, so it's never one render behind a
  // keystroke that lands while a save is in flight.
  const textRef = React.useRef(text);
  textRef.current = text;
  const openConfirm = (options) => {
    setConfirm({ ...options, returnFocus: document.activeElement });
  };
  // Ticks so we re-poll window.HANDOVER whenever the app-level refresh fires
  // (app.jsx bumps its own tick and re-renders — this re-runs on every render).
  const remoteCurrent = (window.HANDOVER && window.HANDOVER.current) || '';
  // WHA-M8: the raw token paired with remoteCurrent above (see api.jsx
  // loadHandover's `updatedAt` field).
  const remoteUpdatedAt = (window.HANDOVER && window.HANDOVER.updatedAt) || null;
  const peerChanged = !saveGracePeriod && dirty && remoteCurrent !== baseline;

  // On every render, if the remote has changed AND the user hasn't started
  // editing yet, silently adopt the new value. Once they've edited we leave
  // their draft alone and show the peer-updated banner instead.
  useEffect_p(() => {
    if (!dirty && remoteCurrent !== text) {
      setText(remoteCurrent);
      setBaseline(remoteCurrent);
    }
  }, [remoteCurrent, dirty]);

  // WHA-M8: mirror the server's latest known precondition token regardless
  // of `dirty` — unlike remoteCurrent (whose adoption is gated on a clean
  // draft so an in-progress edit is never clobbered), the token carries no
  // visible content; it only feeds the NEXT save's expected_updated_at.
  // Keeping it live even while dirty means an operator who explicitly picks
  // "仍要儲存" in the peer-changed dialog below (which already does its own
  // refreshLive() + compare) saves against the CURRENT token and succeeds
  // outright, instead of bouncing through a second, redundant 409 conflict
  // prompt for a divergence they already agreed to overwrite.
  useEffect_p(() => {
    setUpdatedAtToken(remoteUpdatedAt);
  }, [remoteUpdatedAt]);

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

  useEffect_p(() => {
    if (!pageToast) return undefined;
    const t = setTimeout(() => setPageToast(null), 4000);
    return () => clearTimeout(t);
  }, [pageToast]);

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
  // WHA-M12: toISOString() is UTC — during the entire 00:00-08:00 Macau
  // night shift (the shift this page exists for) that read as "yesterday".
  // Use the browser's local date instead (operators are physically in Macau).
  const today = (() => {
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  })();

  // WHA-H3: the backend hard-caps the note at 2000 chars (handover.py:32)
  // with zero prior client-side signal, so a long typhoon-night note used to
  // fail save with a cryptic HTTP 422 and no indication why. `overLimit` can
  // still occur despite the textarea's maxLength below, because
  // writeGeneratedSummary() sets text programmatically (not user keystrokes).
  const overLimit = text.length > HANDOVER_MAX_LEN;
  const nearLimit = !overLimit && text.length >= HANDOVER_WARN_LEN;

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
  // WHA-M8: `overrideNote`/`overrideExpected` let the conflict-resolution
  // "覆蓋伺服器版本" action re-issue a save with the server's NEW token
  // (from the 409 body) without going through the normal `text` state.
  // Both are optional; the plain `performSave()` call (used by the "儲存"
  // button and the peer-changed "仍要儲存" confirm) falls back to the live
  // `text`/`updatedAtToken`. `!= null` / `!== undefined` distinguish "not
  // provided" from a legitimate empty-string note or null token.
  const performSave = async (overrideNote, overrideExpected) => {
    // WHA-C1: snapshot exactly what we're sending. `text` keeps changing via
    // keystrokes during the await below (setTextTracked runs freely while
    // saving), but this closured value stays fixed for this save's lifetime.
    const sentText = overrideNote != null ? overrideNote : text;
    const expected = overrideExpected !== undefined ? overrideExpected : updatedAtToken;
    setSaving(true);
    try {
      const result = await window.SDPRS_API.saveHandover(sentText, expected);
      const newToken = (result && result.updated_at) || null;
      const now = new Date();
      const p = (n) => String(n).padStart(2, '0');
      setSavedAt(p(now.getHours()) + ':' + p(now.getMinutes()) + ':' + p(now.getSeconds()));
      // The server now holds `sentText` — sync window.HANDOVER.current/
      // updatedAt to match. Without this the silent-adoption effect below
      // (still reading the stale pre-save server copy from window.HANDOVER)
      // stomps the textarea back to the OLD note the instant `dirty` clears,
      // destroying the just-saved text and any keystrokes typed during the
      // await. Also without syncing updatedAt, the NEXT save would echo a
      // now-stale token and false-conflict against our own successful save.
      // Defensive create: window.HANDOVER can be undefined if the initial
      // page load's handover fetch hard-failed (see api.jsx loadHandover) —
      // don't let a bare property-set throw here, since it would fall into
      // the catch below and report "儲存失敗" for a save that actually
      // succeeded on the server.
      if (window.HANDOVER) { window.HANDOVER.current = sentText; window.HANDOVER.updatedAt = newToken; }
      else window.HANDOVER = { current: sentText, pinned: null, history: [], updatedAt: newToken };
      setUpdatedAtToken(newToken);
      setBaseline(sentText);
      // Only clear `dirty` if the operator hasn't kept typing past what we
      // just sent. If textRef.current has moved on, leave dirty=true so
      // those trailing keystrokes stay protected — the localStorage draft
      // keeps being written and the silent-adoption effect stays blocked —
      // until the NEXT save picks them up. baseline/window.HANDOVER are
      // already correctly synced to sentText above, so that next save's
      // conflict check diffs against the right snapshot either way.
      if (textRef.current === sentText) {
        setDirty(false);
      }
      // Activate the post-save grace period so a poll round-trip of the
      // just-saved text doesn't trigger the peer-changed banner.
      setSaveGracePeriod(true);
      if (graceTimerRef.current) clearTimeout(graceTimerRef.current);
      graceTimerRef.current = setTimeout(() => {
        graceTimerRef.current = null;
        setSaveGracePeriod(false);
      }, 2000);
      // A successful save fully resolves any conflict this text was part of
      // (relevant for the "覆蓋伺服器版本" re-issue path).
      setConflict(null);
    } catch (e) {
      const status = e && e.status;
      if (e && e.conflict) {
        // WHA-M8: the server rejected our expected_updated_at — someone else
        // saved first. Surface the compare-and-choose dialog instead of a
        // generic toast; do NOT clobber the server note or discard the
        // draft here — that decision belongs to the operator (see
        // keepServerVersion / overwriteServerVersion below).
        setConflict({
          serverText: e.current || '',
          serverUpdatedAt: e.updatedAt || null,
          draftText: sentText,
          returnFocus: document.activeElement,
        });
      } else if (status === 422) {
        // WHA-H3: server-side 2000-char cap (handover.py:32). Client-side
        // maxLength/disable below should normally prevent this, but the
        // auto-generated summary path (writeGeneratedSummary) sets text
        // programmatically and isn't bounded by the textarea's maxLength —
        // give an explicit zh-TW reason instead of a cryptic generic toast
        // (previously this looped forever with no clue what was wrong).
        setPageToast({ tone: 'error', msg: '儲存失敗：備註內容超過系統上限（2000 字），請刪減後再試。' });
      } else {
        setPageToast({ tone: 'error', msg: '儲存失敗: ' + (e.message || e) });
      }
    }
    setSaving(false);
  };

  // WHA-M8 conflict resolution: adopt the server's version, discarding the
  // operator's draft. An explicit, operator-chosen action — never automatic.
  const keepServerVersion = () => {
    if (!conflict) return;
    const serverText = conflict.serverText;
    const serverToken = conflict.serverUpdatedAt;
    setText(serverText);
    setBaseline(serverText);
    setUpdatedAtToken(serverToken);
    if (window.HANDOVER) { window.HANDOVER.current = serverText; window.HANDOVER.updatedAt = serverToken; }
    setDirty(false);
    setConflict(null);
    setPageToast({ tone: 'warn', msg: '已捨棄本機草稿，改用伺服器版本。' });
  };

  // WHA-M8 conflict resolution: force-save the operator's draft by
  // re-issuing with the server's NEW updated_at (from the 409 body) as the
  // expected token, so this retry matches and the save proceeds.
  const overwriteServerVersion = async () => {
    if (!conflict) return;
    const noteToForce = conflict.draftText;
    const tokenToUse = conflict.serverUpdatedAt;
    setConflict(null);
    await performSave(noteToForce, tokenToUse);
  };
  const save = async () => {
    if (saving) return;
    setSaving(true);
    // WHA-M8 mitigation: window.HANDOVER can be up to ~20s stale (the poll
    // interval), so diffing only against it let two operators silently
    // last-write-wins clobber each other. Force a fresh fetch right before
    // the conflict check so we compare against the freshest known server
    // copy — this narrows the race a lot but doesn't close it entirely; the
    // robust fix needs a backend `updated_at` precondition / 409, which is a
    // backend change (see handoff note). refreshLive() is the only refresh
    // primitive this page has access to (HandoverPage isn't given an
    // onRefresh prop by app.jsx, unlike Alerts/Status/Weather), so this
    // refreshes more than just the handover note — an accepted trade-off.
    try {
      await window.SDPRS_API.refreshLive();
    } catch (_) {
      // refreshLive() already resolves-with-fallback rather than rejecting,
      // but guard anyway — fall back to whatever window.HANDOVER already
      // holds rather than blocking the save entirely.
    }
    const latest = (window.HANDOVER && window.HANDOVER.current) || '';
    if (latest !== baseline && latest !== text) {
      setSaving(false);
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
    await performSave();
  };
  return (
    <div className="h-full overflow-y-auto scroll-thin p-6 grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6">
      {pageToast && (
        <div role="status" aria-live="polite"
          className={`fixed bottom-4 left-1/2 -translate-x-1/2 z-40 px-3 py-2 rounded shadow-lg text-xs border ${
            pageToast.tone === 'error' ? 'bg-sev-critical/20 border-sev-critical text-sev-critical'
            : pageToast.tone === 'warn' ? 'bg-sev-warn/20 border-sev-warn text-sev-warn'
            : 'bg-surface-elevated border-border-strong text-ink-primary'
          }`}>
          {pageToast.msg}
        </div>
      )}
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
          maxLength={HANDOVER_MAX_LEN}
          aria-label="班次交接備註（單筆全域備註，24 小時後自動失效，上限 2000 字）"
          className="w-full bg-surface-panel border border-border-strong rounded p-3 text-sm font-mono leading-relaxed focus:border-sev-info focus:outline-none resize-none"
        />
        <div className={`mt-1 text-[11px] font-mono tnum text-right ${
          overLimit ? 'text-sev-critical' : nearLimit ? 'text-sev-warn' : 'text-ink-muted'
        }`}>
          {overLimit
            ? `已超過上限 ${text.length}/${HANDOVER_MAX_LEN}，請刪減 ${text.length - HANDOVER_MAX_LEN} 字後才能儲存`
            : `${text.length}/${HANDOVER_MAX_LEN}`}
        </div>
        <div className="mt-2 flex items-center gap-2">
          <button onClick={save} disabled={saving || overLimit} title={overLimit ? '內容超過 2000 字上限，請先刪減' : undefined} className="px-3 h-9 bg-sev-info hover:bg-blue-600 disabled:opacity-50 text-white rounded text-sm font-semibold flex items-center gap-2">
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
        {/* WHA-M10: window.HANDOVER.history is permanently [] — api.jsx's
            loadHandover() hardcodes it and no backend endpoint exists to
            populate it. The old "尚無歷史備註" ("no history yet") copy
            falsely implied the feature works and is simply empty. Report
            this as a real gap (hide-or-be-honest) rather than building a
            fake history list against data that will never arrive. */}
        <div className="text-xs text-ink-muted bg-surface-panel border border-border-subtle rounded p-3">
          歷史備註功能尚未提供
        </div>
      </div>
      <ConfirmDialog
        open={!!confirm}
        {...(confirm || {})}
        onCancel={() => setConfirm(null)}
      />
      <ConflictDialog
        open={!!conflict}
        {...(conflict || {})}
        onCancel={() => setConflict(null)}
        onKeepServer={keepServerVersion}
        onKeepMine={overwriteServerVersion}
      />
    </div>
  );
};

Object.assign(window, { HandoverPage });
