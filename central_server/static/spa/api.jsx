// SDPRS — live data layer.
//
// Fetches from the central-server REST API (FastAPI) and maps responses into
// the shapes the UI components consume. The browser sends the session cookie
// automatically (same-origin), so every /api/* call is authenticated as the
// logged-in dashboard user. On 401 we bounce to /login.
//
// Exposed as window.SDPRS_API. app.jsx calls loadInitial() once before mount,
// then refreshLive() on a timer and on every relevant WebSocket event.

(function () {
  // ---- low-level fetch ---------------------------------------------------

  async function apiFetch(path, opts = {}) {
    const res = await fetch(path, { credentials: 'same-origin', ...opts });
    if (res.status === 401) {
      window.location.href = '/login';
      throw new Error('unauthorized');
    }
    if (!res.ok) {
      const err = new Error('HTTP ' + res.status + ' on ' + path);
      err.status = res.status;
      throw err;
    }
    if (res.status === 204) return null;
    const ct = res.headers.get('content-type') || '';
    return ct.includes('application/json') ? res.json() : res.text();
  }

  const jsonBody = (method, obj) => ({
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(obj),
  });

  // ---- time / formatting helpers ----------------------------------------

  // DB timestamps are UTC. SQLite CURRENT_TIMESTAMP and Python isoformat()
  // both omit the zone, which JS would otherwise read as local time — so we
  // append 'Z' unless an explicit zone is already present.
  function parseTs(s) {
    if (!s) return null;
    let str = String(s).trim().replace(' ', 'T');
    if (!/([zZ])$|([+-]\d\d:?\d\d)$/.test(str)) str += 'Z';
    const d = new Date(str);
    return isNaN(d.getTime()) ? null : d;
  }
  const secsSince = (s) => {
    const d = parseTs(s);
    return d ? Math.max(0, (Date.now() - d.getTime()) / 1000) : null;
  };
  const fmtClock = (d) => {
    if (!d) return '—';
    const p = (n) => String(n).padStart(2, '0');
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  };
  const compass = (deg) => {
    if (deg == null) return '';
    const dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
    return dirs[Math.round(((deg % 360) / 45)) % 8];
  };
  const round = (n) => (n == null ? null : Math.round(n));

  // Tracks which pending alerts the operator has already looked at; survives
  // the periodic re-fetch (which rebuilds the alert objects from scratch).
  const _seen = new Set();

  // ---- mappers -----------------------------------------------------------

  const STATE_MAP = {
    PENDING_VIDEO: 'pending',
    PENDING: 'pending',
    ACKNOWLEDGED: 'acknowledged',
    RESOLVED: 'resolved',
  };

  function deriveSeverity(vc) {
    if (vc == null) return 'warn';
    if (vc >= 0.9) return 'critical';
    if (vc >= 0.7) return 'warn';
    return 'info';
  }

  function mapAlert(e) {
    const vc = e.visual_confidence;
    const db = e.audio_db_peak;
    const created = e.created_at || e.timestamp;
    const state = STATE_MAP[e.status] || 'pending';
    const ageSec = secsSince(created) || 0;

    const timeline = [];
    timeline.push({
      t: fmtClock(parseTs(e.timestamp || created)),
      label: 'EDGE_CREATED',
      detail: 'conf=' + (vc != null ? vc.toFixed(2) : '—') +
              (db != null ? ' db=' + Math.round(db) : ''),
    });
    if (e.status !== 'PENDING_VIDEO') {
      timeline.push({ t: fmtClock(parseTs(created)), label: 'JSON_SENT', detail: 'mqtt→central' });
    }
    if (e.mp4_path) {
      timeline.push({
        t: '—', label: 'UPLOADED',
        detail: String(e.mp4_path).split(/[\\/]/).pop(),
      });
    }
    if (e.acknowledged_at) {
      timeline.push({ t: fmtClock(parseTs(e.acknowledged_at)), label: 'ACKNOWLEDGED', detail: 'by ' + (e.acknowledged_by || '—') });
    }
    if (e.resolved_at) {
      timeline.push({ t: fmtClock(parseTs(e.resolved_at)), label: 'RESOLVED', detail: 'by ' + (e.resolved_by || '—') });
    }

    let message = '玻璃震動偵測';
    if (vc != null) message += ' · 視覺信心 ' + vc.toFixed(2);
    if (db != null) message += ' · 音量 ' + Math.round(db) + 'dB';

    return {
      id: e.id,
      node: e.node_id,
      type: 'glass_break',
      sev: deriveSeverity(vc),
      state,
      ageSec,
      seen: _seen.has(e.id) || state !== 'pending',
      message,
      timeline,
      ackBy: e.acknowledged_by || null,
      ackAt: e.acknowledged_at ? fmtClock(parseTs(e.acknowledged_at)) : null,
      ackAgeSec: e.acknowledged_at ? (secsSince(e.acknowledged_at) || 0) : 0,
      resBy: e.resolved_by || null,
      resAt: e.resolved_at ? fmtClock(parseTs(e.resolved_at)) : null,
      note: e.notes || null,
    };
  }

  function mapNode(n) {
    const type = n.node_type === 'pump' ? 'pump' : 'camera';
    const loc = n.location || '';
    let floor = '', area = '', name = n.node_id;
    if (loc.includes('·')) {
      const parts = loc.split('·').map((s) => s.trim());
      floor = parts[0] || '';
      area = parts.slice(1).join(' · ');
      name = area || n.node_id;
    } else if (loc) {
      name = loc;
    }

    const offline = n.status !== 'ONLINE';
    const level = n.water_level != null ? Math.round(n.water_level) : null;
    let status = 'online';
    if (offline) status = 'offline';
    else if (type === 'pump' && level != null) {
      status = level >= 85 ? 'critical' : level >= 70 ? 'warn' : 'online';
    } else if (type === 'camera' && n.is_stale) {
      status = 'warn';
    }

    const hb = secsSince(n.last_heartbeat);
    const up = type === 'camera'
      ? (n.snapshot_timestamp ? secsSince(n.snapshot_timestamp) : hb)
      : hb;
    const ss = n.stream_status || {};

    return {
      id: n.node_id,
      type,
      name,
      location: loc || '—',
      floor, area,
      status,
      heartbeat: hb != null ? Math.round(hb) : 999,
      upload: up != null ? Math.round(up) : 999,
      temp: n.cpu_temp != null ? Math.round(n.cpu_temp) : null,
      bitrate: ss.bitrate_mbps != null ? ss.bitrate_mbps
             : ss.bitrate != null ? ss.bitrate : 0,
      drops: ss.dropped_frames != null ? ss.dropped_frames
           : ss.drops != null ? ss.drops : 0,
      level: level != null ? level : 0,
      cycles: n._cycles != null ? n._cycles : 0,
      cycleHistory: null,
      raining: n.raining,
      sensorConflict: n.sensor_conflict,
      dryRunProtect: n.dry_run_protect,
      voltage: n.battery_voltage != null ? n.battery_voltage : null,
      power: n.power_source || 'mains',
      trend: null,
      flow: null,
      snoozeMin: n.snoozed_until ? Math.max(0, Math.round((parseTs(n.snoozed_until) - Date.now()) / 60000)) : 0,
    };
  }

  function mapWeather(current, forecast, typhoon) {
    if (!current) {
      return { ...window.WEATHER, available: false };
    }
    const fc = (forecast || []).slice(0, 16).map((b) => {
      const d = parseTs(b.start_time);
      return {
        h: d ? String(d.getHours()).padStart(2, '0') : '--',
        wind: round((b.wind_speed_ms || 0) * 3.6) || 0,
        rain: round(b.rainfall_mm) || 0,
      };
    });
    return {
      available: true,
      typhoon: typhoon ? {
        name: typhoon.name,
        level: typhoon.category || '颱風',
        distance: round(typhoon.distance_to_site_km),
        direction: compass(typhoon.bearing_to_site_deg),
        bearing: typhoon.bearing_to_site_deg,
      } : null,
      wind: {
        speed: round((current.wind_speed_ms || 0) * 3.6) || 0,
        gust: 0,
        dir: compass(current.wind_direction_deg),
        degree: current.wind_direction_deg || 0,
      },
      rain: {
        now: round(current.rainfall_24h_mm) || 0,
        hour: round(current.rainfall_24h_mm) || 0,
        day: round(current.rainfall_24h_mm) || 0,
      },
      temp: round(current.temperature_c) || 0,
      humidity: current.humidity_pct || 0,
      pressure: null,
      visibility: null,
      lightning: { count: 0, nearest: null },
      source: current.source || 'SMG',
      stale: !!current.is_stale,
      station: current.station_name || '',
      forecast: fc,
    };
  }

  function mapAuditRow(r) {
    let detail = r.details != null ? r.details : (r.detail != null ? r.detail : {});
    if (typeof detail === 'string') {
      try { detail = JSON.parse(detail); } catch (e) { detail = { value: detail }; }
    }
    const ts = parseTs(r.timestamp || r.created_at || r.t);
    return {
      t: ts ? fmtClock(ts) : (r.timestamp || r.t || '—'),
      by: r.operator || r.by || 'system',
      action: r.action || r.action_type || '—',
      target: r.target != null ? r.target : (r.target_id != null ? r.target_id : '—'),
      detail: detail || {},
    };
  }

  // ---- loaders -----------------------------------------------------------

  async function loadNodes() {
    const rows = await apiFetch('/api/nodes');
    const list = Array.isArray(rows) ? rows : (rows.nodes || []);
    // Cycle counts for pumps come from a separate endpoint; fetch in parallel.
    const pumps = list.filter((n) => n.node_type === 'pump');
    await Promise.all(pumps.map(async (n) => {
      try {
        const c = await apiFetch('/api/pump/' + encodeURIComponent(n.node_id) + '/cycles?window=1h');
        n._cycles = c && c.count != null ? c.count : 0;
      } catch (e) { n._cycles = 0; }
    }));
    return list.map(mapNode);
  }

  async function loadAlerts() {
    const rows = await apiFetch('/api/alerts?status_filter=PENDING_VIDEO,PENDING,ACKNOWLEDGED&limit=200');
    return (Array.isArray(rows) ? rows : []).map(mapAlert);
  }

  async function loadHistory() {
    const rows = await apiFetch('/api/alerts?status_filter=RESOLVED&limit=80');
    return (Array.isArray(rows) ? rows : []).map(mapAlert);
  }

  async function loadRate() {
    try {
      const r = await apiFetch('/api/alerts/rate?bucket=15m&window=4h');
      const buckets = (r && r.buckets) || [];
      return buckets.map((b) => b.count || 0);
    } catch (e) { return new Array(16).fill(0); }
  }

  async function loadWeather() {
    let current = null, forecast = [], typhoon = null;
    try { current = await apiFetch('/api/weather/current'); } catch (e) { /* 503 = disabled */ }
    try {
      const f = await apiFetch('/api/weather/forecast');
      forecast = (f && f.buckets) || [];
    } catch (e) { /* ignore */ }
    try { typhoon = await apiFetch('/api/weather/typhoon'); } catch (e) { /* ignore */ }
    return mapWeather(current, forecast, typhoon);
  }

  async function loadHandover() {
    try {
      const h = await apiFetch('/api/handover/note');
      const note = (h && h.note) || '';
      const upd = parseTs(h && h.updated_at);
      return {
        current: note,
        pinned: {
          by: (h && h.author) || '—',
          at: upd ? fmtClock(upd) : '',
          text: note ? note.slice(0, 80) : '尚無交接備註',
          ageMin: upd ? Math.round((Date.now() - upd.getTime()) / 60000) : 0,
        },
        history: [],
      };
    } catch (e) {
      return { current: '', pinned: { by: '—', at: '', text: '尚無交接備註', ageMin: 0 }, history: [] };
    }
  }

  async function loadAudit() {
    try {
      const a = await apiFetch('/api/audit?limit=200');
      const rows = (a && a.rows) || [];
      return rows.map(mapAuditRow);
    } catch (e) {
      return []; // 403 for non-admin — audit page just shows empty
    }
  }

  // ---- derived data ------------------------------------------------------

  function buildNodeHistory(history) {
    const byNode = {};
    history.forEach((a) => {
      (byNode[a.node] = byNode[a.node] || []).push({
        t: a.resAt || a.ackAt || '—',
        type: a.type,
        sev: a.sev,
        resolution: a.note || '已解決',
      });
    });
    Object.keys(byNode).forEach((k) => { byNode[k] = byNode[k].slice(0, 6); });
    return byNode;
  }

  function buildShiftSummary(history, alerts) {
    return {
      duration: '—',
      alertsHandled: history.length,
      critical: history.filter((a) => a.sev === 'critical').length,
      warn: history.filter((a) => a.sev === 'warn').length,
      info: history.filter((a) => a.sev === 'info').length,
      ackMedian: '—',
      resolveMedian: '—',
      carryOver: alerts.filter((a) => a.state === 'acknowledged').length,
      highlights: [],
    };
  }

  // ---- public surface ----------------------------------------------------

  // Loads everything and publishes onto window.* before the React app mounts.
  async function loadInitial() {
    const results = await Promise.allSettled([
      loadNodes(), loadAlerts(), loadHistory(),
      loadRate(), loadWeather(), loadHandover(), loadAudit(),
    ]);
    const [nodes, alerts, history, rate, weather, handover, audit] =
      results.map((r) => (r.status === 'fulfilled' ? r.value : null));

    if (nodes) window.NODES = nodes;
    if (alerts) window.ALERTS = alerts;
    if (history) window.HISTORY_ALERTS = history;
    if (rate) window.ALERT_RATE = rate;
    if (weather) window.WEATHER = weather;
    if (handover) window.HANDOVER = handover;
    if (audit) window.AUDIT = audit;
    window.NODE_HISTORY = buildNodeHistory(window.HISTORY_ALERTS);
    window.SHIFT_SUMMARY = buildShiftSummary(window.HISTORY_ALERTS, window.ALERTS);
    window.OPERATOR = { name: window.SDPRS_USER || '', role: 'op', shiftStart: '', shiftRemaining: 0 };

    const failed = results.filter((r) => r.status === 'rejected');
    if (failed.length) console.warn('[SDPRS] some data failed to load:', failed.map((f) => f.reason));
  }

  // Re-fetches the volatile data (alerts, nodes, rate). Returns the new
  // arrays so app.jsx can push them into React state.
  async function refreshLive() {
    const results = await Promise.allSettled([loadNodes(), loadAlerts(), loadHistory(), loadRate()]);
    const [nodes, alerts, history, rate] =
      results.map((r) => (r.status === 'fulfilled' ? r.value : null));
    if (nodes) window.NODES = nodes;
    if (alerts) window.ALERTS = alerts;
    if (history) {
      window.HISTORY_ALERTS = history;
      window.NODE_HISTORY = buildNodeHistory(history);
    }
    if (rate) window.ALERT_RATE = rate;
    window.SHIFT_SUMMARY = buildShiftSummary(window.HISTORY_ALERTS, window.ALERTS);
    return {
      nodes: window.NODES,
      alerts: window.ALERTS,
      history: window.HISTORY_ALERTS,
      rate: window.ALERT_RATE,
    };
  }

  function markSeen(id) { _seen.add(id); }

  // ---- mutations ---------------------------------------------------------

  const ackAlert = (id) => apiFetch('/api/alerts/' + id + '/acknowledge', { method: 'PATCH' });

  const resolveAlert = (id, note) => apiFetch('/api/alerts/' + id + '/resolve',
    jsonBody('PATCH', { resolved_by: window.SDPRS_USER || 'operator', notes: note || null }));

  const snoozeNode = (nodeId, minutes, reason) => apiFetch('/api/nodes/' + encodeURIComponent(nodeId) + '/snooze',
    jsonBody('POST', { minutes, reason: reason || '操作員延期' }));

  const saveHandover = (note) => apiFetch('/api/handover/note', jsonBody('PUT', { note: note || '' }));

  const updateNodeLocation = (id, location) => apiFetch('/api/nodes/' + encodeURIComponent(id),
    jsonBody('PATCH', { location }));

  // ---- websocket ---------------------------------------------------------

  // Opens the live event socket. `onEvent({type,data})` fires for every
  // server message; it auto-reconnects with a short backoff.
  function openSocket(onEvent) {
    let ws = null, closed = false, retry = 1000;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const connect = () => {
      if (closed) return;
      try { ws = new WebSocket(proto + '//' + location.host + '/ws'); }
      catch (e) { setTimeout(connect, retry); return; }
      ws.onopen = () => { retry = 1000; };
      ws.onmessage = (ev) => {
        let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
        try { onEvent(msg); } catch (e) { console.warn('[SDPRS] ws handler error', e); }
      };
      ws.onclose = () => {
        if (closed) return;
        setTimeout(connect, retry);
        retry = Math.min(retry * 2, 15000);
      };
      ws.onerror = () => { if (ws) ws.close(); };
    };
    connect();
    return () => { closed = true; if (ws) ws.close(); };
  }

  window.SDPRS_API = {
    loadInitial, refreshLive, markSeen,
    ackAlert, resolveAlert, snoozeNode, saveHandover, updateNodeLocation,
    openSocket,
  };
})();
