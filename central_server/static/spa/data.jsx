// SDPRS — static UI config + helpers + live-data placeholders.
//
// The original mock data arrays were replaced by live data: api.jsx fetches
// from the central-server REST API and assigns the results to window.* before
// the React app mounts. The empty defaults below keep components from crashing
// if a fetch fails or a panel renders before the first load completes.

// ---- Static operator config (not backed by an API) ----------------------

// FIX-024: single source of truth for the build label displayed in the
// NavRail footer. Not auto-generated — update manually on each release
// so the operator can identify which build is running on the wall display.
window.__SDPRS_BUILD = 'build 2026.08.02';

const RESOLVE_TEMPLATES = [
  '誤報 — 環境因素',
  '已派員處理',
  '風雨引起 — 已加固',
  '系統自動恢復',
  '併入主告警',
  '硬體更換',
];

// Runbooks — static operator guidance keyed by alert type. The edge currently
// only emits glass-break events, so glass_break is the runbook normally shown;
// the others are kept for when the alert pipeline gains more event types.
const RUNBOOKS = {
  glass_break: {
    summary: '玻璃震動偵測 — 需確認實際破裂並啟動現場應變',
    actions: [
      { label: '檢視前 5 秒緩衝畫面',  hint: '常可分辨強風/物件/破壞', primary: true },
      { label: '通報保全現場巡查',     hint: 'CC: 大樓物管' },
      { label: '比對同節點近期警報',   hint: '參考此節點過去 7 天紀錄' },
      { label: '若誤報 → 調整閾值',    hint: '預設 0.8, 可調至 0.85' },
    ],
  },
  flood_critical: {
    summary: '水位達臨界值,需立即減壓並啟動備援系統',
    actions: [
      { label: '切換備援泵浦',     hint: '預期數分內水位下降', primary: true },
      { label: '通報土木組待命',   hint: '確認現場人員到位' },
      { label: '升級至 L2 主管',   hint: '水位持續上升時觸發', escalate: true },
    ],
  },
  flood_warn: {
    summary: '水位接近警戒 — 監看趨勢,準備減壓動作',
    actions: [
      { label: '加密水位回報頻率', hint: '縮短取樣間隔' },
      { label: '檢視泵浦循環頻率', hint: '若過高提前介入' },
      { label: '查看天氣預報',     hint: '若雨勢加劇預先升級' },
    ],
  },
  offline: {
    summary: '節點失聯 — 確認是網路或硬體',
    actions: [
      { label: '嘗試 SSH 遠端重啟',   hint: 'ssh pi@<node-ip>', primary: true },
      { label: '檢查網路交換器埠號',  hint: '確認 PoE 供電' },
      { label: '派員實地確認',        hint: '若遠端無回應' },
    ],
  },
};

// Stale ack threshold (seconds) — an acknowledged alert older than this is
// flagged as needing follow-up.
const STALE_ACK_THRESHOLD = 1500;

// ---- Live-data placeholders (populated by api.jsx before mount) ----------

window.NODES = [];
window.ALERTS = [];
window.HISTORY_ALERTS = [];
// SHL-13: this placeholder MUST stay shape-identical to api.jsx mapWeather's
// no-data branch, otherwise weather.jsx / WallView crash (or silently render
// `undefined`) on any field that only exists in one of the two shapes — the
// window between mount and the first successful /api/weather load, and every
// load failure thereafter. Fields kept in sync: sources (per-field provenance
// labels), stale, station, fetchedAt (epoch ms | null), and `wind.dir` as ''
// (not null) so string concat renders empty rather than "null".
window.WEATHER = {
  available: false,
  typhoon: null,
  wind: { speed: null, gust: null, dir: '', degree: null },
  rain: { now: null, hour: null, day: null },
  temp: null, humidity: null, pressure: null, visibility: null,
  lightning: { count: null, nearest: null },
  source: '—',
  sources: {},
  stale: true,
  station: '',
  forecast: [],
  fetchedAt: null,
};
window.ALERT_RATE = new Array(16).fill(0);
window.HANDOVER = {
  current: '',
  pinned: { by: '—', at: '', text: '尚無交接備註', ageMin: 0 },
  history: [],
};
window.AUDIT = [];
// TODO(dashboard-audit-2026-07-15): source role from server (window.SDPRS_USER)
//   — backend currently exposes SDPRS_USER as a bare string; if the login
//   payload later includes {name, role}, replace 'op' with SDPRS_USER.role.
window.OPERATOR = { name: window.SDPRS_USER || '', role: 'op', shiftStart: '', shiftRemaining: 0 };
window.NODE_HISTORY = {};
window.SHIFT_SUMMARY = {
  duration: '—', alertsHandled: 0, critical: 0, warn: 0, info: 0,
  ackMedian: '—', resolveMedian: '—', carryOver: 0, highlights: [],
};
// SHL-15: PERMANENTLY EMPTY — there is no presence feed. Nothing in api.jsx
// ever writes this, no backend route serves online-operator state, and no WS
// event carries it; app.jsx only reads it (`window.OPERATORS_ONLINE ??
// EMPTY_OPERATORS`) to hand StatusStrip its `operators` prop. So StatusStrip's
// presence cluster renders an empty roster on a 24/7 console where "who else
// is on shift right now" is exactly the question it appears to answer — an
// operator reads "nobody else online" and assumes sole responsibility during a
// typhoon. Deliberately left as `[]` rather than removed: app.jsx's read is
// null-safe either way, and deleting it would only move the same lie into a
// default. Closing this needs EITHER a backend presence endpoint + an api.jsx
// loader, OR StatusStrip suppressing the cluster when the roster is empty
// (components.jsx — see handoff). Do not seed fake operators here.
window.OPERATORS_ONLINE = [];

// ---- Helpers ------------------------------------------------------------

const fmtAge = (sec) => {
  if (sec == null) return '—';
  sec = Math.max(0, Math.round(sec || 0));
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ' + (sec % 60) + 's';
  if (sec < 86400) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    return h + 'h ' + m + 'm';
  }
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  return d + 'd ' + h + 'h';
};
// OPS-037: was defined/exported only from pages/monitor.jsx, with status.jsx
// reaching across files for it via a runtime `window.fmtAgeOrDash ? ... : ...`
// guard (see status.jsx's heartbeat/upload cells) — a fragile dependency on
// monitor.jsx happening to load first, silently falling back to a cruder
// inline formatter (no day/hour humanization) whenever it didn't (e.g. any
// test harness or future page that doesn't load monitor.jsx). It is a pure
// function with zero monitor.jsx-specific state, so it belongs in the shared
// helpers file every page already depends on. `sec == null` (never-reported
// node, MSP-F19/F8) renders '—' instead of fabricating "0s" via fmtAge's
// `sec || 0`.
const fmtAgeOrDash = (sec) => (sec == null ? '—' : fmtAge(sec));
const ageColor = (sec) => {
  if (sec == null) return 'text-ink-muted';
  if (sec < 300) return 'text-ink-secondary';
  if (sec < 900) return 'text-sev-warn';
  if (sec < 1800) return 'text-orange-400';
  return 'text-sev-critical font-semibold';
};
const sevMeta = {
  critical: { label: '嚴重', color: 'sev-critical', bar: 'sev-bar-critical', Icon: () => window.Icon.AlertTriangle({size:14}) },
  warn:     { label: '警告', color: 'sev-warn',     bar: 'sev-bar-warn',     Icon: () => window.Icon.AlertCircle({size:14}) },
  info:     { label: '資訊', color: 'sev-info',     bar: 'sev-bar-info',     Icon: () => window.Icon.Info({size:14}) },
  ok:       { label: '正常', color: 'sev-ok',       bar: 'sev-bar-ok',       Icon: () => window.Icon.CheckCircle({size:14}) },
  stale:    { label: '過期', color: 'sev-stale',    bar: 'sev-bar-stale',    Icon: () => window.Icon.Clock({size:14}) },
};
const alertTypeMap = {
  glass_break: '玻璃破裂',
  flood_critical: '淹水告警',
  flood_warn: '水位警戒',
  pump_cycle: '抽水循環',
  temp_warn: '溫度警告',
  offline: '節點離線',
  upload_fail: '上傳失敗',
};
function alertTypeLabel(type) {
  const map = window.alertTypeMap || alertTypeMap;
  if (type && map[type]) return map[type];
  if (type) return String(type);
  return '未知類型';
}
const stateMeta = {
  pending: { label: '待處理', cls: 'bg-sev-critical/15 text-sev-critical border-sev-critical/30' },
  acknowledged: { label: '已認領', cls: 'bg-sev-info/15 text-sev-info border-sev-info/30' },
  resolved: { label: '已解決', cls: 'bg-sev-ok/15 text-sev-ok border-sev-ok/30' },
  snoozed: { label: '通知已靜音', cls: 'bg-ink-dim/15 text-ink-muted border-ink-dim/30' },
};

// Detector health (camera nodes only) — maps the server-provided visual/audio
// detector status to a Chinese label + Pill tone. "paused"=thermal throttle,
// "blinded"=re-baseline in progress, "stale"=silent audio, "disabled"=mic
// failed to start. Missing/unrecognised values fall back to "unknown".
const detectorHealthMeta = {
  ok:       { label: '正常',         tone: 'ok' },
  paused:   { label: '已暫停(高溫)', tone: 'warn' },
  blinded:  { label: '已致盲',       tone: 'critical' },
  stale:    { label: '訊號停滯',     tone: 'warn' },
  disabled: { label: '未啟用',       tone: 'critical' },
  unknown:  { label: '未知',         tone: 'muted' },
};

function nodeStatusTone(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'offline' || s === 'critical') return 'critical';
  if (s === 'warning' || s === 'degraded') return 'warning';
  if (s === 'online' || s === 'ok' || s === 'active') return 'success';
  return 'muted';
}

const Z_LAYER = {
  base:      10,
  sticky:    20,
  overlay:   30,
  popover:   40,
  drawer:    50,
  modal:     60,
  toast:     90,
  critical: 100,
  confirm:  110,
};

function formatDurationShort(seconds) {
  if (seconds == null || !isFinite(seconds)) return '—';
  const s = Math.abs(Number(seconds));
  if (s < 60)    return `${Math.round(s)}秒`;
  if (s < 3600)  return `${Math.round(s / 60)}分`;
  if (s < 86400) return `${Math.round(s / 3600)}時`;
  return `${Math.round(s / 86400)}天`;
}

// UX-001: shared, date-aware wall-clock formatter. Every timestamp on this
// dashboard used to render as bare HH:MM:SS (fmtClock), so an event logged at
// 23:58 and one at 00:03 the NEXT day were indistinguishable in a handover
// review that spans midnight. fmtTs shows the date ONLY when the instant is
// not today, so the common (today) case stays terse while cross-midnight /
// multi-day timelines become unambiguous. Null-safe by contract: null,
// undefined, and unparseable inputs all render '—' (never "null" / "NaN" /
// "Invalid Date"). Accepts a Date OR epoch-ms (api.jsx's parseTsMs output) —
// deliberately NOT a raw wire string, which needs api.jsx's parseTs 'Z'
// repair first (THE ONE RULE); hand fmtTs an already-parsed instant.
function fmtTs(value) {
  if (value == null) return '—';
  const d = value instanceof Date ? value : new Date(value);
  if (isNaN(d.getTime())) return '—';
  const p = (n) => String(n).padStart(2, '0');
  const hms = p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  const now = new Date();
  const sameDay = d.getFullYear() === now.getFullYear() &&
                  d.getMonth() === now.getMonth() &&
                  d.getDate() === now.getDate();
  if (sameDay) return hms;
  const md = p(d.getMonth() + 1) + '-' + p(d.getDate());
  if (d.getFullYear() === now.getFullYear()) return md + ' ' + hms;
  return d.getFullYear() + '-' + md + ' ' + hms;
}

// FLOW-001: the dashboard used a single toast slot, so a burst of messages
// overwrote an action-critical failure toast ('認領失敗'/'解決失敗', tone 'warn')
// in under a second — the operator walked away believing an ack landed when it
// had failed. Toasts now stack; this pure reducer appends one and enforces the
// cap. Eviction preferentially drops the OLDEST NON-critical toast so failures
// survive contention, only falling back to the oldest overall when the whole
// stack is critical. Kept pure (no React) so the policy is unit-testable.
const _toastIsCritical = (t) => !!t && (t.tone === 'warn' || t.tone === 'error');

function nextToasts(list, toast, max = 4) {
  const next = (list || []).concat([toast]);
  if (next.length <= max) return next;
  const dropIdx = next.findIndex((t) => !_toastIsCritical(t));
  next.splice(dropIdx === -1 ? 0 : dropIdx, 1);
  return next;
}

// AUTH-003: while the blocking session-expiry modal is up, each open tab polls
// this once per interval. Tabs share one cookie, so when the operator
// re-authenticates in ANY tab the shared cookie goes live again and
// extendSession() (which requires a non-expired session) starts resolving —
// this reports true so the other tabs can self-dismiss their modals instead of
// forcing a separate re-login in each. Any rejection or throw means still
// expired, so the modal stays up and the tab keeps probing.
async function probeSessionOnce(extendSession) {
  try {
    await extendSession();
    return true;
  } catch (_) {
    return false;
  }
}

// WAL-H5: wall pill + StatusStrip share one label so the wall degrades its
// text during an outage (not just its color).
function liveClockLabel(liveSec) {
  if (liveSec < 10) return '\u5373\u6642 \u00b7 ' + liveSec + 's';
  if (liveSec < 30) return '\u91cd\u65b0\u9023\u7dda\u4e2d\u2026 ' + liveSec + 's';
  return '\u9023\u7dda\u4e2d\u65b7 ' + liveSec + 's';
}

// WAL-M8: a tile is frozen (grayscale) when offline OR when its frame is stale
// (upload > 60s). upload == null means "never had a snapshot" — not frozen.
function wallTileFrozen(node) {
  if (!node) return false;
  if (node.status === 'offline') return true;
  return node.upload != null && node.upload > 60;
}

// WAL-M10: wall mode always uses the dark palette regardless of user theme.
function effectiveTheme(theme, wallMode) {
  return wallMode ? 'dark' : theme;
}

// SHELL-001: the single authoritative set of pages app.jsx's renderPage()
// switch actually handles. `page` state can be seeded from RESTORED_STATE (a
// base64 blob decoded from a URL query param on the cross-login roundtrip),
// sessionStorage (tab-scoped, survives reloads), or a browser popstate event —
// all three are untrusted strings a stale bookmark, a corrupted blob, or a
// hand-edited URL could hand back as garbage. An unvalidated bad value used to
// fall through renderPage()'s `default: return null`, leaving the whole
// content area blank with the nav/status strip still showing (looked like a
// crash, wasn't one). sanitizePage is the single choke point every entry point
// funnels through; VALID_PAGES is published too so a caller can enumerate it.
const VALID_PAGES = ['alerts', 'monitor', 'status', 'pumps', 'weather', 'handover', 'audit'];
function sanitizePage(p) {
  return VALID_PAGES.indexOf(p) !== -1 ? p : 'alerts';
}

// SHELL-004: app.jsx's boot effect surfaces a partial-load warning banner +
// toast whenever loadInitial() reports SOME (but not all) loaders rejected
// via window.__SDPRS_LOAD_FAILURES. The bootstrap-error retry path re-runs
// loadInitial() but never re-checked that flag — a retry that recovered
// most-but-not-all loaders rendered as a full, silent success. Both call
// sites now funnel through this one pure decision so they can't drift again;
// `labels` is app.jsx's _FAILURE_LABELS map, passed in rather than duplicated
// here (data.jsx has no reason to know the zh-TW copy for each loader key).
function describeLoadFailures(failures, labels) {
  if (!Array.isArray(failures) || failures.length === 0) return null;
  const labelMap = labels || {};
  const names = failures.map(k => labelMap[k] || k).join('、');
  return { warnings: failures.slice(), toastMessage: '部分資料載入失敗: ' + names };
}

// SHELL-007: app.jsx used to adopt RESTORED_STATE.selectedId (decoded from a
// base64 URL param on the H-1 cross-login roundtrip) directly into initial
// state with no check that it names an alert actually in the queue — unlike
// the sessionStorage-saved id, which WAS already validated against the
// loaded alerts before being adopted. A stale id (resolved/aged out between
// the redirect and the next load), a corrupted blob, or a hand-edited URL
// therefore selected nothing real, with nothing to fall back to. Both
// untrusted sources now go through this one validation: restoredId wins if
// it names a live alert, else savedId if IT names one, else the head of the
// queue. String()-compared since ids cross a URL/sessionStorage boundary and
// may arrive as strings even when the underlying alert id is numeric.
function resolveSelectedId(restoredId, savedId, alerts) {
  const list = Array.isArray(alerts) ? alerts : [];
  if (restoredId != null) {
    const match = list.find(a => String(a.id) === String(restoredId));
    if (match) return match.id;
  }
  if (savedId != null) {
    const match = list.find(a => String(a.id) === String(savedId));
    if (match) return match.id;
  }
  return list.length > 0 ? list[0].id : null;
}

// Node hide/declutter (per-display). Hidden node ids live in localStorage
// under sdprs.hiddenNodes as a JSON array of node_id strings — a per-display
// convenience, deliberately NOT server-side (design spec §9). Every read and
// write is try/catch-wrapped so a private window, a cleared/blocked store, or
// a throwing accessor degrades to "nothing hidden" instead of taking the page
// down. loadHiddenNodes always returns a fresh array; a corrupt / non-array /
// non-string-element payload is treated as empty.
const HIDDEN_NODES_KEY = 'sdprs.hiddenNodes';
function loadHiddenNodes() {
  try {
    const raw = window.localStorage.getItem(HIDDEN_NODES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(id => typeof id === 'string');
  } catch (_) {
    return [];
  }
}
function saveHiddenNodes(ids) {
  try {
    const arr = Array.isArray(ids) ? ids.filter(id => typeof id === 'string') : [];
    window.localStorage.setItem(HIDDEN_NODES_KEY, JSON.stringify(arr));
  } catch (_) {
    /* best-effort: storage unavailable — hide is a per-display convenience */
  }
}
// The ONE filter both the wall (WallView) and the monitor grid apply, so a
// hidden node drops out of BOTH — and, for the wall, before its offline-first
// sort/slice. `hiddenIds` is a Set for O(1) membership; a null/absent/empty
// set means "hide nothing". Presentational only — callers keep the FULL list
// for fleet totals.
function filterVisibleNodes(nodes, hiddenIds) {
  const list = Array.isArray(nodes) ? nodes : [];
  if (!hiddenIds || typeof hiddenIds.has !== 'function' || hiddenIds.size === 0) return list;
  return list.filter(n => !hiddenIds.has(n.id));
}

// WAL-M9: count only unacked (pending) alerts for the wall ticker header.
function activeAlertCount(alerts) {
  return (alerts || []).filter(function (a) { return a.state !== 'acknowledged'; }).length;
}

// WAL-M9: sort wall alerts so unacked + higher severity float up before the
// 12-row slice. Primary: state (pending before acknowledged). Secondary:
// severity (critical > warn > info). Tertiary: recency (lower ageSec first).
// Returns a NEW array; does not mutate the input.
// SHELL-027: shared, NaN/null-safe ageSec comparator. Since DATA-011, ageSec
// is `null` (not 0) for an alert whose timestamp failed to parse — meaning
// "unknown", not "just now". A bare `a.ageSec - b.ageSec` coerces null to 0
// via subtraction, sorting an unknown age as the FRESHEST thing in the queue;
// `(a.ageSec || 0)` (orderWallAlerts' old form) has the same hole AND also
// conflates "unknown" with a legitimate ageSec===0 (a genuinely brand-new
// alert). Treat a non-finite/missing age as "oldest" (Infinity) so it sorts
// to the end — the conservative direction for something we don't actually
// know the urgency of — never poisoning the comparison with NaN the way the
// rest of this file already guards severity-rank fallbacks against.
function compareAgeSec(a, b) {
  var av = (typeof a === 'number' && isFinite(a)) ? a : Infinity;
  var bv = (typeof b === 'number' && isFinite(b)) ? b : Infinity;
  // Infinity - Infinity is NaN — guard the both-unknown case explicitly so
  // two unknown-age alerts tie instead of poisoning the sort.
  if (av === Infinity && bv === Infinity) return 0;
  return av - bv;
}

function orderWallAlerts(alerts) {
  var sevRank = { critical: 0, warn: 1, info: 2 };
  return (alerts || []).slice().sort(function (a, b) {
    var sa = a.state === 'acknowledged' ? 1 : 0;
    var sb = b.state === 'acknowledged' ? 1 : 0;
    if (sa !== sb) return sa - sb;
    var ra = sevRank[a.sev] != null ? sevRank[a.sev] : 99;
    var rb = sevRank[b.sev] != null ? sevRank[b.sev] : 99;
    if (ra !== rb) return ra - rb;
    return compareAgeSec(a.ageSec, b.ageSec);
  });
}

// OPS-015: shared error-text normalizer so every page's toast handler can
// route backend errors through zh-TW labels instead of raw English err.message.
// Previously defined only inside app.jsx's closure — unreachable from pages
// like status.jsx that also need it. Pure function, no app.jsx state.
function actionErrorText(e) {
  if (!e) return '未知錯誤';
  if (e.timeout || e.status === 0) return '連線逾時 — 指令可能已送出，請重新整理後確認狀態';
  if (e.status === 401) return '登入階段已逾時，請重新登入';
  if (e.status === 403) return '權限不足，無法執行此操作';
  if (e.status === 409) return e.detail ? String(e.detail) : '此警報已被其他操作員處理';
  if (e.detail) return Array.isArray(e.detail) ? e.detail.join('; ') : String(e.detail);
  // Prefer the error's own message over a generic HTTP-status label — apiFetch
  // stamps useful context into .message (e.g. "HTTP 403 on /api/audit"), while
  // "伺服器錯誤 (HTTP 500)" tells the operator nothing they can act on.
  if (e.message) return String(e.message);
  if (e.status) return '伺服器錯誤 (HTTP ' + e.status + ')';
  return String(e);
}

Object.assign(window, {
  RESOLVE_TEMPLATES, RUNBOOKS, STALE_ACK_THRESHOLD,
  fmtAge, fmtAgeOrDash, ageColor, sevMeta, alertTypeMap,
  stateMeta, detectorHealthMeta, actionErrorText,
});

window.alertTypeLabel = alertTypeLabel;
window.nodeStatusTone = nodeStatusTone;
window.Z_LAYER = Z_LAYER;
window.formatDurationShort = formatDurationShort;
window.fmtTs = fmtTs;
window.nextToasts = nextToasts;
window.probeSessionOnce = probeSessionOnce;
window.liveClockLabel = liveClockLabel;
window.wallTileFrozen = wallTileFrozen;
window.effectiveTheme = effectiveTheme;
window.activeAlertCount = activeAlertCount;
window.orderWallAlerts = orderWallAlerts;
window.compareAgeSec = compareAgeSec;
window.VALID_PAGES = VALID_PAGES;
window.sanitizePage = sanitizePage;
window.describeLoadFailures = describeLoadFailures;
window.resolveSelectedId = resolveSelectedId;
window.loadHiddenNodes = loadHiddenNodes;
window.saveHiddenNodes = saveHiddenNodes;
window.filterVisibleNodes = filterVisibleNodes;
