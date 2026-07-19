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

  const FETCH_TIMEOUT_MS = 10_000;

  async function apiFetch(path, opts = {}) {
    // Fetch has no built-in timeout — a stuck TCP handshake would hang a request
    // (and any downstream Promise.all) forever. 10s is generous vs. our
    // ~1s p99 API latency; adjust per-call via opts.timeoutMs if needed.
    const ac = new AbortController();
    const timeoutMs = opts.timeoutMs != null ? opts.timeoutMs : FETCH_TIMEOUT_MS;
    const t = setTimeout(() => ac.abort(), timeoutMs);
    // Compose the built-in timeout signal with any caller-supplied signal so
    // a page-level AbortController (e.g. cancel-on-navigate) does NOT defeat
    // the timeout. If the runtime lacks AbortSignal.any (all evergreen
    // browsers ship it since 2024), fall back to letting the timeout win —
    // i.e. spread opts BEFORE signal so opts.signal can't override it.
    const callerSignal = opts && opts.signal;
    const signal = (callerSignal && typeof AbortSignal !== 'undefined' && typeof AbortSignal.any === 'function')
      ? AbortSignal.any([ac.signal, callerSignal])
      : ac.signal;
    let res;
    try {
      res = await fetch(path, { credentials: 'same-origin', ...opts, signal });
    } catch (e) {
      if (e && e.name === 'AbortError') {
        const err = new Error('timeout after ' + timeoutMs + 'ms on ' + path);
        err.status = 0;
        err.timeout = true;
        throw err;
      }
      throw e;
    } finally {
      clearTimeout(t);
    }
    if (res.status === 401) {
      window.location.href = '/login';
      throw new Error('unauthorized');
    }
    if (!res.ok) {
      // Read the response body so FastAPI's structured `{ detail: '...' }`
      // (e.g. "already resolved by alice at 14:32" on a 409 race) reaches the
      // operator's toast instead of the opaque "HTTP 409 on /api/…" default.
      // Guard for non-JSON error pages (proxy 502/504 HTML) via .text().
      const ct2 = res.headers.get('content-type') || '';
      let body = null;
      if (ct2.includes('application/json')) {
        body = await res.json().catch(() => null);
      } else {
        body = await res.text().catch(() => null);
      }
      const detail = (body && typeof body === 'object' && typeof body.detail === 'string')
        ? body.detail : null;
      const err = new Error('HTTP ' + res.status + ' on ' + path + (detail ? ': ' + detail : ''));
      err.status = res.status;
      err.detail = detail;
      err.body = body;
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
    // Match trailing zone: 'Z', ±HH:MM, ±HHMM, or the bare 2-digit ±HH
    // (Postgres' `+00` shorthand). Without the last alternative, `2026-07-15T12:00:00+00`
    // would get an extra 'Z' tacked on and parse as "12:00:00+00Z" = NaN.
    if (!/([zZ])$|([+-]\d\d:?\d\d)$|([+-]\d\d)$/.test(str)) str += 'Z';
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
  // Like round(), but distinguishes "no data" from "legit zero". Sensor down
  // must show as null (→ "—" in UI), NOT as 0 (which would falsely mean
  // "reading is zero"). `n || 0` and `round(n) || 0` both collapsed those.
  const roundOrNull = (v) => {
    if (v == null) return null;
    const n = Number(v);
    if (!Number.isFinite(n)) return null;
    return Math.round(n);
  };

  // Tracks which pending alerts the operator has already looked at; survives
  // the periodic re-fetch (which rebuilds the alert objects from scratch).
  // Capped FIFO so a long-running shift can't leak unboundedly.
  const _SEEN_CAP = 1000;
  const _seen = new Set();
  const _seenAdd = (id) => {
    if (id == null) return;
    if (_seen.has(id)) return;
    _seen.add(id);
    if (_seen.size > _SEEN_CAP) {
      // Set iteration order is insertion order → first value is oldest.
      const oldest = _seen.values().next().value;
      _seen.delete(oldest);
    }
  };

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
    timeline.push({ t: fmtClock(parseTs(created)), label: 'JSON_SENT', detail: 'mqtt→central' });
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
    const level = roundOrNull(n.water_level);
    const visualHealth = n.visual_health || 'unknown';
    const audioHealth = n.audio_health || 'unknown';
    let status = 'online';
    if (offline) status = 'offline';
    else if (type === 'pump' && level != null) {
      status = level >= 85 ? 'critical' : level >= 70 ? 'warn' : 'online';
    } else if (type === 'camera' && n.is_stale) {
      status = 'warn';
    }
    // A camera that is online but cannot reliably alert — blinded/paused vision
    // or a dead/stale mic — is degraded; surface it as a warning (never downgrade
    // an already critical/offline state).
    if (type === 'camera' && status === 'online' &&
        (visualHealth === 'blinded' || visualHealth === 'paused' ||
         audioHealth === 'disabled' || audioHealth === 'stale')) {
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
      temp: roundOrNull(n.cpu_temp),
      bitrate: ss.bitrate_mbps != null ? ss.bitrate_mbps
             : ss.bitrate != null ? ss.bitrate : 0,
      drops: ss.dropped_frames != null ? ss.dropped_frames
           : ss.drops != null ? ss.drops : 0,
      level, // null = sensor down / no reading (was previously coerced to 0 → misleading)
      cycles: n._cycles != null ? n._cycles : 0,
      cycleHistory: null,
      raining: n.raining,
      sensorConflict: n.sensor_conflict,
      dryRunProtect: n.dry_run_protect,
      voltage: n.battery_voltage != null ? n.battery_voltage : null,
      power: n.power_source || 'mains',
      trend: null,
      flow: null,
      snoozeMin: (() => {
        if (!n.snoozed_until) return 0;
        const t = parseTs(n.snoozed_until);
        if (t == null || Number.isNaN(t)) return 0; // malformed ISO → don't render "NaNm"
        return Math.max(0, Math.round((t - Date.now()) / 60000));
      })(),
      snoozedBy: n.snoozed_by != null ? n.snoozed_by : null,
      snoozedAt: n.snoozed_at != null ? n.snoozed_at : null,
      visualHealth,
      audioHealth,
      // Used by the tile view as an <img> cache-buster — changes each time the
      // edge pushes a new snapshot, so the browser refetches only then. Null
      // when the node has never uploaded a snapshot (offline / pump / new node).
      snapshotTimestamp: n.snapshot_timestamp || null,
    };
  }

  function mapWeather(current, forecast, typhoon) {
    if (!current) {
      // Same shape as the populated return so destructuring (`w.wind.speed`
      // etc.) never crashes. All leaves null so consumers can render "—"
      // instead of quoting a stale value carried over from the last poll.
      return {
        available: false,
        typhoon: null,
        wind: { speed: null, gust: null, dir: '', degree: null },
        rain: { now: null, hour: null, day: null },
        temp: null,
        humidity: null,
        pressure: null,
        visibility: null,
        lightning: { count: 0, nearest: null },
        source: '—',
        stale: true,
        station: '',
        forecast: [],
      };
    }
    const fc = (forecast || []).slice(0, 16).map((b) => {
      const d = parseTs(b.start_time);
      return {
        h: d ? String(d.getHours()).padStart(2, '0') : '--',
        wind: roundOrNull(b.wind_speed_ms != null ? b.wind_speed_ms * 3.6 : null),
        rain: roundOrNull(b.rainfall_mm),
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
        speed: roundOrNull(current.wind_speed_ms != null ? current.wind_speed_ms * 3.6 : null),
        // TODO(dashboard-audit-2026-07-15): backend has no gust field yet
        // (weather_service.CurrentWeather has wind_speed_ms only). Bind
        // when Open-Meteo/CWA gust ingestion lands. Do NOT default to 0 —
        // that reads as "no gust during a typhoon" which is a safety lie.
        gust: null,
        dir: compass(current.wind_direction_deg),
        degree: current.wind_direction_deg != null ? current.wind_direction_deg : null,
      },
      rain: {
        // Backend only exposes rainfall_24h_mm (see services/weather_service.py
        // CurrentWeather). The "10min" and "1h" buckets don't exist yet, so
        // leave now/hour null rather than reusing the 24h value under other
        // labels (the audit specifically flagged that lie).
        // TODO(dashboard-audit-2026-07-15): bind now/hour when backend
        // exposes rainfall_10min_mm / rainfall_1h_mm.
        now: null,
        hour: null,
        day: roundOrNull(current.rainfall_24h_mm),
      },
      temp: roundOrNull(current.temperature_c),
      humidity: current.humidity_pct != null ? current.humidity_pct : null,
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
      // Full timestamp (ms since epoch) used by the audit date filter.
      // Null if the row didn't carry a parseable timestamp.
      ts: ts != null && !Number.isNaN(ts) ? ts : null,
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
    const pumps = list.filter((n) => n.node_type === 'pump');
    if (pumps.length) {
      // One batch call for every pump's cycle-count instead of one request per
      // pump (was N+1). Degrades gracefully to all-zeros if the endpoint is
      // missing (older server 404s) or errors.
      let cycles = {};
      try {
        const resp = await apiFetch('/api/pumps/cycles?window=1h');
        cycles = (resp && resp.nodes) || {};
      } catch (e) { cycles = {}; }
      pumps.forEach((n) => {
        const c = cycles[n.node_id];
        n._cycles = (c && c.count != null) ? c.count : 0;
      });
    }
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

  // 503 = feature disabled server-side (no CWA_API_KEY); expected in dev.
  // Anything else is a real error and worth a console warn so operators
  // see it while /api/weather/* is silently returning stub data.
  function _weatherLog(endpoint, err) {
    const s = err && err.status;
    if (s === 503) return; // expected when the weather integration is off
    console.warn('[api] weather ' + endpoint + ' failed:', s || err.message);
  }

  async function loadWeather() {
    // Parallel — the 3 endpoints are independent, and the slowest one (typhoon
    // hits an upstream API) was gating the other two under sequential await.
    const results = await Promise.allSettled([
      apiFetch('/api/weather/current'),
      apiFetch('/api/weather/forecast'),
      apiFetch('/api/weather/typhoon'),
    ]);
    const endpoints = ['current', 'forecast', 'typhoon'];
    results.forEach((r, i) => {
      if (r.status === 'rejected') _weatherLog(endpoints[i], r.reason);
    });
    const current = results[0].status === 'fulfilled' ? results[0].value : null;
    const fRes = results[1].status === 'fulfilled' ? results[1].value : null;
    const forecast = (fRes && fRes.buckets) || [];
    const typhoon = results[2].status === 'fulfilled' ? results[2].value : null;
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
    // 403 = non-admin session; page renders a "無權限" empty state driven by
    // window.AUDIT.forbidden. apiFetch throws Error('HTTP 403 on ...') with
    // .status = 403 (see apiFetch above) — we tolerate both shapes just in
    // case a caller re-wraps the error before it reaches here.
    window.AUDIT = window.AUDIT || [];
    try {
      const a = await apiFetch('/api/audit?limit=200');
      const rows = (a && a.rows) || [];
      const entries = rows.map(mapAuditRow);
      entries.forbidden = false;
      window.AUDIT = entries;
      return entries;
    } catch (e) {
      const status = e && e.status;
      const msg = e && e.message ? String(e.message) : '';
      const is403 = status === 403 || msg.indexOf('403') !== -1;
      const entries = [];
      entries.forbidden = !!is403;
      window.AUDIT = entries;
      return entries;
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
  //
  // In-flight guard: if a WS event and the poll timer both call refreshLive
  // in the same tick (or a slow request outlasts the interval), we return
  // the pending promise instead of stacking a second identical fan-out.
  //
  // Trailing debounce: without it, a burst of 10 node_status events arriving
  // during one in-flight refresh would settle → then trigger 10 back-to-back
  // refetches from any callers that had queued between settle and event drain.
  // The pending flag collapses the entire tail into ONE follow-up refresh
  // scheduled 300ms after settle (bounded — never unbounded delay).
  let refreshLiveInFlight = null;
  let refreshLivePending = false;
  async function refreshLive() {
    if (refreshLiveInFlight) {
      refreshLivePending = true;
      return refreshLiveInFlight;
    }
    refreshLiveInFlight = (async () => {
      // weather + handover + audit added 2026-07-16: previously initial-load
      // only, so post-503 weather recovery, peer-operator handover edits, and
      // audit rows stayed stale until page reload.
      const results = await Promise.allSettled([
        loadNodes(), loadAlerts(), loadHistory(), loadRate(),
        loadWeather(), loadHandover(), loadAudit(),
      ]);
      const [nodes, alerts, history, rate, weather, handover, audit] =
        results.map((r) => (r.status === 'fulfilled' ? r.value : null));
      if (nodes) window.NODES = nodes;
      if (alerts) window.ALERTS = alerts;
      if (history) {
        window.HISTORY_ALERTS = history;
        window.NODE_HISTORY = buildNodeHistory(history);
      }
      if (rate) window.ALERT_RATE = rate;
      if (weather) window.WEATHER = weather;
      if (handover) window.HANDOVER = handover;
      if (audit) window.AUDIT = audit;
      window.SHIFT_SUMMARY = buildShiftSummary(window.HISTORY_ALERTS, window.ALERTS);
      return {
        nodes: window.NODES,
        alerts: window.ALERTS,
        history: window.HISTORY_ALERTS,
        rate: window.ALERT_RATE,
        weather: window.WEATHER,
        handover: window.HANDOVER,
        audit: window.AUDIT,
      };
    })()
      .catch((err) => {
        // Symmetry with loadInitial — log but don't propagate an unhandled
        // rejection to the poll timer / WS handler.
        console.warn('[api] refreshLive failed', err);
        return {
          nodes: window.NODES,
          alerts: window.ALERTS,
          history: window.HISTORY_ALERTS,
          rate: window.ALERT_RATE,
          weather: window.WEATHER,
          handover: window.HANDOVER,
          audit: window.AUDIT,
        };
      })
      .finally(() => {
        refreshLiveInFlight = null;
        // Trailing debounce — if anyone queued while we were in-flight,
        // schedule ONE follow-up refresh 300ms after settle. Reset the flag
        // FIRST so the follow-up's own callers can flip it again if a new
        // burst arrives while the follow-up runs.
        if (refreshLivePending) {
          refreshLivePending = false;
          setTimeout(() => { refreshLive().catch(() => {}); }, 300);
        }
      });
    return refreshLiveInFlight;
  }

  function markSeen(id) { _seenAdd(id); }

  // ---- mutations ---------------------------------------------------------

  const ackAlert = (id) => apiFetch('/api/alerts/' + id + '/acknowledge', { method: 'PATCH' });

  // Attribution is derived server-side from the authenticated session
  // (see central_server/api/alerts.py resolve_alert) — do not send
  // resolved_by from the client, it would be spoofable.
  const resolveAlert = (id, note) => apiFetch('/api/alerts/' + id + '/resolve',
    jsonBody('PATCH', { notes: note || null }));

  // Bulk operations — server iterates the id list under one session, so
  // attribution is still server-derived. Returns `{acked}` / `{resolved}`
  // with the count actually mutated (server skips already-terminal rows).
  const bulkAckAlerts = (ids, note) => apiFetch('/api/alerts/bulk-ack',
    jsonBody('POST', { ids: ids || [], note: note || null }));

  const bulkResolveAlerts = (ids, note) => apiFetch('/api/alerts/bulk-resolve',
    jsonBody('POST', { ids: ids || [], note: note || null }));

  // Stream control — MQTT-driven start/stop on an edge camera. Backend
  // (api/stream.py) 404s on unknown nodes and 503s when the node is offline
  // or MQTT is down; apiFetch surfaces the FastAPI `detail` in the error
  // message so the caller's toast reads "串流啟動失敗: Node offline" etc.
  const startStream = (nodeId) => apiFetch('/api/stream/' + encodeURIComponent(nodeId) + '/start',
    { method: 'POST' });
  const stopStream = (nodeId) => apiFetch('/api/stream/' + encodeURIComponent(nodeId) + '/stop',
    { method: 'POST' });

  // Audit CSV export — preflight against the sibling GET /api/audit endpoint
  // (same admin-only gate as export.csv, see api/audit.py) to detect 401/403
  // BEFORE the anchor-click download, otherwise the browser would silently
  // "download" the JSON error body as a .csv file and the caller's `await`
  // would resolve with success. HEAD is unusable here because FastAPI's
  // APIRouter.get does not auto-implement HEAD and returns 405. On success
  // we hand off to a plain anchor click: keeps the session cookie, lets the
  // browser handle the save dialog, and avoids the memory cost of
  // fetch→blob→createObjectURL for large exports.
  async function exportAuditCsv(opts) {
    const o = opts || {};
    const params = new URLSearchParams();
    if (o.limit != null) params.set('limit', String(o.limit));
    if (o.type) params.set('type', o.type);
    // operator + sinceMs added 2026-07-16 (C2 fix) so the CSV mirrors the
    // on-screen operator/date/meOnly filters instead of only forwarding
    // action-type. sinceMs is Unix milliseconds; backend converts to
    // naive-UTC via datetime.fromtimestamp(...).replace(tzinfo=None).
    if (o.operator) params.set('operator', o.operator);
    if (o.sinceMs != null) params.set('since_ms', String(Math.floor(o.sinceMs)));
    const qs = params.toString();
    const url = '/api/audit/export.csv' + (qs ? '?' + qs : '');

    // apiFetch handles 401 (redirects to /login) and re-throws non-2xx with
    // `.status` attached, so a 403 non-admin surfaces to the caller's toast.
    try {
      await apiFetch('/api/audit?limit=1');
    } catch (e) {
      const err = new Error('audit export failed: ' + (e && e.status != null ? e.status : (e && e.message) || 'unknown'));
      err.status = (e && e.status) || 0;
      err.cause = e;
      throw err;
    }

    // YYYYMMDD from the ISO string (naive-UTC safe — no Date locale drift).
    const ymd = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    const a = document.createElement('a');
    a.href = url;
    a.download = 'audit_' + ymd + '.csv';
    // Some browsers (Firefox pre-93) require the anchor to be in the DOM.
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  const snoozeNode = (nodeId, minutes, reason) => apiFetch('/api/nodes/' + encodeURIComponent(nodeId) + '/snooze',
    jsonBody('POST', { minutes, reason: reason || '操作員延期' }));

  // Cancel an active snooze. Backend DELETE handler at nodes.py logs
  // ACTION_UNSNOOZE for audit; no request body needed.
  const unsnoozeNode = (nodeId) => apiFetch('/api/nodes/' + encodeURIComponent(nodeId) + '/snooze',
    { method: 'DELETE' });

  const saveHandover = (note) => apiFetch('/api/handover/note', jsonBody('PUT', { note: note || '' }));

  const updateNodeLocation = (id, location) => apiFetch('/api/nodes/' + encodeURIComponent(id),
    jsonBody('PATCH', { location }));

  // ---- websocket ---------------------------------------------------------

  // Backend event types dispatched to `onEvent(type, data)`. `new_alert` is
  // routed to `onNewAlert(alertObj)` (mapped through mapAlert). `ping` is
  // internal — pure keepalive, never surfaced.
  const _WS_EVENT_TYPES = new Set([
    'alert_updated', 'alert_acknowledged', 'alert_resolved',
    'node_status', 'pump_status',
    'auth_expired',
  ]);
  // A server that accepts the socket then closes it (auth-drop, upstream
  // proxy hiccup) would trigger a 1 req/s reconnect flood if we reset the
  // backoff on `onopen`. Only reset after this many ms of stable connection.
  const _WS_STABLE_MS = 30_000;

  // Opens the live event socket.
  //
  // Two call shapes for backward compat:
  //   openSocket({ onNewAlert, onEvent, onPing })   — new contract
  //   openSocket(fn)                                — legacy; fn treated as onEvent
  //
  // `onNewAlert(alertObj)` fires for `new_alert` (alert is mapAlert-normalised).
  // `onEvent(type, data)` fires for the 5 telemetry types above. `onPing()`
  // fires on every keepalive so callers (e.g. app.jsx liveSec) can reset a
  // staleness timer without leaking `ping` into the general event whitelist.
  // Auto-reconnects with exponential backoff (max 15s).
  function openSocket(arg) {
    // Legacy positional callers passed a single fn for telemetry (onEvent).
    // Routing it to onEvent — not onNewAlert — is what preserves ack/resolve
    // echoes, node-status flips, and auth_expired (drops here caused mystery
    // /login redirects). new_alert reaches legacy pages via the DOM bridge.
    if (typeof arg === 'function') {
      arg = { onEvent: arg };
    }
    let onNewAlert = null, onEvent = null, onPing = null;
    if (arg && typeof arg === 'object') {
      onNewAlert = typeof arg.onNewAlert === 'function' ? arg.onNewAlert : null;
      onEvent = typeof arg.onEvent === 'function' ? arg.onEvent : null;
      onPing = typeof arg.onPing === 'function' ? arg.onPing : null;
    }

    let ws = null, closed = false, retry = 1000;
    let stableTimer = null;
    // Track the pending reconnect setTimeout id so teardown (auth_expired)
    // can cancel it — otherwise a scheduled connect() would still fire and,
    // while it early-returns on `closed`, leaks the timer and lets any
    // future teardown-then-refire miss the cancel window (MED F2).
    let reconnectTimer = null;
    const clearStable = () => { if (stableTimer) { clearTimeout(stableTimer); stableTimer = null; } };
    const clearReconnect = () => { if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; } };
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const connect = () => {
      if (closed) return;
      reconnectTimer = null;
      try { ws = new WebSocket(proto + '//' + location.host + '/ws'); }
      catch (e) { reconnectTimer = setTimeout(connect, retry); return; }
      ws.onopen = () => {
        // Delay backoff reset — see _WS_STABLE_MS comment.
        clearStable();
        stableTimer = setTimeout(() => { retry = 1000; stableTimer = null; }, _WS_STABLE_MS);
      };
      ws.onmessage = (ev) => {
        let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
        const type = msg && msg.type;
        if (!type) return;
        if (type === 'ping') { if (onPing) { try { onPing(); } catch (_) {} } return; } // keepalive; surfaced only via onPing so callers can reset liveSec without whitelisting 'ping'
        try {
          if (type === 'new_alert') {
            if (onNewAlert) {
              // Backend sends a thin notification (see api/alerts.py):
              //   { type: "new_alert", data: { alert_id, node_id, timestamp, status } }
              // It's a signal to refetch, NOT a full event — mapAlert would
              // return an object full of nulls. Pass the payload as-is so
              // app.jsx can bump banner counters / trigger refreshLive.
              onNewAlert(msg.data != null ? msg.data : msg);
            }
          } else if (_WS_EVENT_TYPES.has(type)) {
            if (onEvent) {
              const data = (msg.data != null ? msg.data : msg);
              onEvent(type, data);
            }
          }
          // else: unknown type — ignore silently for forward-compat
        } catch (e) {
          console.warn('[SDPRS] ws handler error for', type, e);
        }
      };
      ws.onclose = () => {
        clearStable();
        if (closed) return;
        reconnectTimer = setTimeout(connect, retry);
        retry = Math.min(retry * 2, 15000);
      };
      ws.onerror = () => { if (ws) ws.close(); };
    };
    connect();
    return () => { closed = true; clearStable(); clearReconnect(); if (ws) ws.close(); };
  }

  window.SDPRS_API = {
    loadInitial, refreshLive, markSeen,
    ackAlert, resolveAlert, bulkAckAlerts, bulkResolveAlerts,
    snoozeNode, unsnoozeNode, saveHandover, updateNodeLocation,
    exportAuditCsv,
    startStream, stopStream,
    openSocket,
  };
})();
