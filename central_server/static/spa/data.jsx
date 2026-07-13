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
window.WEATHER = {
  available: false,
  typhoon: null,
  wind: { speed: 0, gust: 0, dir: '', degree: 0 },
  rain: { now: 0, hour: 0, day: 0 },
  temp: 0, humidity: 0, pressure: null, visibility: null,
  lightning: { count: 0, nearest: null },
  source: '—',
  forecast: [],
};
window.ALERT_RATE = new Array(16).fill(0);
window.HANDOVER = {
  current: '',
  pinned: { by: '—', at: '', text: '尚無交接備註', ageMin: 0 },
  history: [],
};
window.AUDIT = [];
window.OPERATOR = { name: window.SDPRS_USER || '', role: 'op', shiftStart: '', shiftRemaining: 0 };
window.NODE_HISTORY = {};
window.SHIFT_SUMMARY = {
  duration: '—', alertsHandled: 0, critical: 0, warn: 0, info: 0,
  ackMedian: '—', resolveMedian: '—', carryOver: 0, highlights: [],
};
window.OPERATORS_ONLINE = [];

// ---- Helpers ------------------------------------------------------------

const fmtAge = (sec) => {
  sec = Math.max(0, Math.round(sec || 0));
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.floor(sec / 60) + 'm ' + (sec % 60) + 's';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return h + 'h ' + m + 'm';
};
const ageColor = (sec) => {
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
const alertTypeLabel = (t) => ({
  glass_break: '玻璃破裂',
  flood_critical: '淹水告警',
  flood_warn: '水位警戒',
  pump_cycle: '抽水循環',
  temp_warn: '溫度警告',
  offline: '節點離線',
  upload_fail: '上傳失敗',
}[t] || t);
const stateMeta = {
  pending: { label: '待處理', cls: 'bg-sev-critical/15 text-sev-critical border-sev-critical/30' },
  acknowledged: { label: '已認領', cls: 'bg-sev-info/15 text-sev-info border-sev-info/30' },
  resolved: { label: '已解決', cls: 'bg-sev-ok/15 text-sev-ok border-sev-ok/30' },
  snoozed: { label: '已延期', cls: 'bg-ink-dim/15 text-ink-muted border-ink-dim/30' },
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

Object.assign(window, {
  RESOLVE_TEMPLATES, RUNBOOKS, STALE_ACK_THRESHOLD,
  fmtAge, ageColor, sevMeta, alertTypeLabel, stateMeta, detectorHealthMeta,
});
