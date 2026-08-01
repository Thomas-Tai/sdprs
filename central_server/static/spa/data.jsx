// SDPRS — static UI config + helpers + live-data placeholders.
//
// The original mock data arrays were replaced by live data: api.jsx fetches
// from the central-server REST API and assigns the results to window.* before
// the React app mounts. The empty defaults below keep components from crashing
// if a fetch fails or a panel renders before the first load completes.

// ---- Static operator config (not backed by an API) ----------------------

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
  if (liveSec < 10) return 'Live \u00b7 ' + liveSec + 's';
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

// WAL-M9: count only unacked (pending) alerts for the wall ticker header.
function activeAlertCount(alerts) {
  return (alerts || []).filter(function (a) { return a.state !== 'acknowledged'; }).length;
}

// WAL-M9: sort wall alerts so unacked + higher severity float up before the
// 12-row slice. Primary: state (pending before acknowledged). Secondary:
// severity (critical > warn > info). Tertiary: recency (lower ageSec first).
// Returns a NEW array; does not mutate the input.
function orderWallAlerts(alerts) {
  var sevRank = { critical: 0, warn: 1, info: 2 };
  return (alerts || []).slice().sort(function (a, b) {
    var sa = a.state === 'acknowledged' ? 1 : 0;
    var sb = b.state === 'acknowledged' ? 1 : 0;
    if (sa !== sb) return sa - sb;
    var ra = sevRank[a.sev] != null ? sevRank[a.sev] : 99;
    var rb = sevRank[b.sev] != null ? sevRank[b.sev] : 99;
    if (ra !== rb) return ra - rb;
    return (a.ageSec || 0) - (b.ageSec || 0);
  });
}

Object.assign(window, {
  RESOLVE_TEMPLATES, RUNBOOKS, STALE_ACK_THRESHOLD,
  fmtAge, ageColor, sevMeta, alertTypeMap,
  stateMeta, detectorHealthMeta,
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
window.VALID_PAGES = VALID_PAGES;
window.sanitizePage = sanitizePage;
