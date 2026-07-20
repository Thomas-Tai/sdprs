// SDPRS — live data layer.
//
// Fetches from the central-server REST API (FastAPI) and maps responses into
// the shapes the UI components consume. The browser sends the session cookie
// automatically (same-origin), so every /api/* call is authenticated as the
// logged-in dashboard user. On 401 we do NOT hard-redirect — apiFetch sets
// window.__SDPRS_SESSION_EXPIRED so app.jsx can show a re-login modal that
// preserves in-progress operator state (see apiFetch below).
//
// Exposed as window.SDPRS_API. app.jsx calls loadInitial() once before mount,
// then refreshLive() on a timer and on every relevant WebSocket event.

(function () {
  // ---- low-level fetch ---------------------------------------------------

  const FETCH_TIMEOUT_MS = 10_000;

  // Normalizes FastAPI's error `detail` into a human-readable string.
  // Two shapes reach us:
  //   - a plain string (HTTPException(detail="...")) — the common case.
  //   - a list of {loc, msg, type} objects — FastAPI's automatic 422
  //     request-validation error. Previously dropped entirely (returned
  //     null), so the operator saw a bare "HTTP 422 on /api/…" with no
  //     indication of which field/why (API-F10 / SHL-11).
  function _extractDetail(body) {
    if (!body || typeof body !== 'object') return null;
    const d = body.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
      const msgs = d.map((item) => {
        if (item && typeof item === 'object' && typeof item.msg === 'string') {
          const loc = Array.isArray(item.loc) ? item.loc.filter((p) => p !== 'body').join('.') : '';
          return loc ? loc + ': ' + item.msg : item.msg;
        }
        return typeof item === 'string' ? item : null;
      }).filter((s) => s != null);
      return msgs.length ? msgs.join('; ') : null;
    }
    return null;
  }

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
      // Set a flag instead of hard-redirecting so app.jsx can show the
      // session-expiry modal (which preserves handover drafts, resolve notes,
      // and page state through the H-1 cross-login flow).
      window.__SDPRS_SESSION_EXPIRED = true;
      // .status/.detail carried like every other thrown error (contract:
      // callers must be able to branch on `.status === 401` instead of
      // string-matching `.message`, which is the English word "unauthorized"
      // and must never reach a zh-TW toast directly).
      const err = new Error('unauthorized');
      err.status = 401;
      err.detail = null;
      throw err;
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
      const detail = _extractDetail(body);
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

  // THE ONE RULE (API-F12): every timestamp this file receives over the wire
  // is naive-UTC (no zone marker) — SQLite CURRENT_TIMESTAMP and Python
  // isoformat() both omit the zone, which JS would otherwise misread as
  // local time. parseTs() is the single place that repairs this by
  // appending 'Z' when no zone is present. Every field derived from a wire
  // timestamp MUST go through parseTs (or secsSince/parseTsMs, which wrap
  // it) — never `new Date(rawString)` directly, and never assume a raw
  // string is already safe to hand to a consumer.
  //
  // The weather service used to be an exception — it serialized forecast and
  // fetched_at as naive *Asia/Macau local* time (API-F1), so parseTs's
  // append-Z repair shifted them +8h. That is FIXED at the source: the
  // Open-Meteo request now asks for timezone=UTC, so the naive strings are
  // genuinely UTC and parseTs is unconditionally correct here.
  // DO NOT add a compensating ±8h offset anywhere on this path. The
  // correction exists in exactly ONE place (the backend request parameter);
  // a second one here would silently put every forecast timestamp 8 hours
  // out during a typhoon.
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
  // Epoch-ms variant of parseTs, for fields consumers compare against
  // Date.now() (e.g. a "shown as of N seconds ago" ticker) without needing
  // a Date object. null when unparseable/absent — never NaN or 0.
  const parseTsMs = (s) => {
    const d = parseTs(s);
    return d ? d.getTime() : null;
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
    // MSP-F9 fix: a camera with no snapshot_timestamp has never uploaded —
    // that is a distinct, more serious fact than "heartbeat is N seconds
    // old" (heartbeat-only means the pipeline is up but produced nothing;
    // substituting hb here silently hid a dead upload pipeline behind a
    // healthy-looking heartbeat number). null means "no snapshot ever" and
    // must render as 「—」, not be papered over with the heartbeat age.
    // Pumps have no snapshot concept at all, so their "upload" age is
    // simply their heartbeat age (unchanged).
    const up = type === 'camera'
      ? (n.snapshot_timestamp ? secsSince(n.snapshot_timestamp) : null)
      : hb;
    const ss = n.stream_status || {};

    return {
      id: n.node_id,
      type,
      name,
      location: loc || '—',
      floor, area,
      status,
      // MSP-F8 fix: null means "device has never reported a heartbeat /
      // upload" — was previously coerced to the sentinel 999, which
      // rendered as a fabricated "心跳 999s" / "16m" for a node the server
      // has literally never heard from. Consumers must render null as 「—」.
      heartbeat: hb != null ? Math.round(hb) : null,
      upload: up != null ? Math.round(up) : null,
      temp: roundOrNull(n.cpu_temp),
      bitrate: ss.bitrate_mbps != null ? ss.bitrate_mbps
             : ss.bitrate_kbps != null ? ss.bitrate_kbps / 1000
             : ss.bitrate != null ? ss.bitrate : 0,
      drops: ss.dropped_frames != null ? ss.dropped_frames
           : ss.dropped != null ? ss.dropped
           : ss.drops != null ? ss.drops : 0,
      level, // null = sensor down / no reading (was previously coerced to 0 → misleading)
      // MSP-F1 fix: the actual reported pump state, distinct from `status`
      // (which is derived from water level / online-ness, not the relay).
      // 'on' | 'off' from the device's last pump_status publish; null means
      // the device has never reported (brand-new node, or — the critical
      // case per nodes.py:497-499 — the device silently DROPPED an ON
      // command under dry-run/sensor-conflict protection and the last known
      // state is still whatever it was before, which may itself be null on
      // a fresh node). A stray non-ON/OFF value (e.g. mqtt_service.py's
      // "UNKNOWN" fallback for a garbled payload) also maps to null — we
      // only assert 'on'/'off' when the device told us so unambiguously.
      pumpState: n.pump_state === 'ON' ? 'on' : n.pump_state === 'OFF' ? 'off' : null,
      // MSP-F5 fix: frozen contract NodeStatus.manual_override: "ON"|"OFF"|null,
      // added server-side in parallel with this change — may not exist on the
      // wire yet (older server omits the field entirely). Coerce anything
      // else (undefined, absent, a stray value) to null rather than crash or
      // fabricate a state.
      manualOverride: n.manual_override === 'ON' ? 'ON' : n.manual_override === 'OFF' ? 'OFF' : null,
      // MSP-F6 fix: frozen contract NodeStatus.last_pump_command:
      // {action, by, at} | null, `at` naive-UTC ISO — same parallel-backend
      // caveat as manualOverride above. `at` MUST go through parseTs (never
      // hand-rolled `new Date(...)`) per the THE ONE RULE header comment, so
      // it gets the same 'Z'-repair every other wire timestamp in this file
      // gets. Any shape mismatch (missing object, missing/garbled `at`)
      // degrades to null fields rather than throwing.
      lastPumpCommand: (n.last_pump_command && typeof n.last_pump_command === 'object') ? {
        action: n.last_pump_command.action != null ? n.last_pump_command.action : null,
        by: n.last_pump_command.by != null ? n.last_pump_command.by : null,
        at: parseTs(n.last_pump_command.at),
      } : null,
      // API-F9 fix: n._cycles now carries the whole {count, alert} object
      // from /api/pumps/cycles (see loadNodes below), not just the bare
      // count — `alert` is the server's own count > PUMP_CYCLE_ALERT_THRESHOLD
      // verdict (nodes.py:662/705). `cycles` keeps its existing external
      // contract (a plain number — monitor.jsx/pumps.jsx/status.jsx all do
      // arithmetic like `60/node.cycles` and `node.cycles > 20` on it).
      // `cyclesAlert` is new: surfaces the server's threshold call so those
      // pages CAN switch off their own hardcoded >20/>15 magic numbers
      // (which silently drift if PUMP_CYCLE_ALERT_THRESHOLD ever changes)
      // without recomputing anything client-side.
      cycles: (n._cycles && n._cycles.count != null) ? n._cycles.count : 0,
      cyclesAlert: !!(n._cycles && n._cycles.alert),
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
      // SHL-3 fix: was the raw wire string (naive-UTC, no 'Z'), which JS
      // would misread as local time — the provenance chip showed a time 8h
      // off in Macau (UTC+8). Now epoch ms via parseTs, matching the
      // snoozedAt contract (epoch ms | null); null when never snoozed or
      // unparseable.
      snoozedAt: parseTsMs(n.snoozed_at),
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
        lightning: { count: null, nearest: null },
        source: '—',
        sources: {}, // consumer safe-access: w.sources[field] returns undefined
        stale: true,
        station: '',
        forecast: [],
        fetchedAt: null,  // "N seconds ago" ticker treats this as never
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
    // Backend Phase 1 (2026-07-19) added `sources` — per-field labels
    // (e.g. sources.temperature_c === "SMG 外港"). Pre-Phase-1 backends
    // omit the key entirely; default to {} so weather.jsx can safely do
    // `w.sources[field] || w.source` without an undefined-crash.
    const backendSources = (current.sources && typeof current.sources === 'object') ? current.sources : {};
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
        gust: roundOrNull(current.gust_speed_ms != null ? current.gust_speed_ms * 3.6 : null),
        dir: compass(current.wind_direction_deg),
        degree: current.wind_direction_deg != null ? current.wind_direction_deg : null,
      },
      rain: {
        // Backend only exposes rainfall_24h_mm (see services/weather_service.py
        // CurrentWeather). There are NO sub-24h buckets (no rainfall_10min_mm,
        // no rainfall_1h_mm). Deriving `now` as rainfall_24h_mm / 24 would
        // fabricate an instantaneous rate from a daily total — dishonest during
        // a typhoon where rain is anything but uniform. Keep now/hour null.
        // The weather.jsx display MUST render null as "—" / "N/A" (honest
        // labeling), never substitute the 24h value under a different label.
        // TODO(dashboard-audit-2026-07-15): bind now/hour only when backend
        // exposes true rainfall_10min_mm / rainfall_1h_mm fields.
        now: null,
        hour: null,
        day: roundOrNull(current.rainfall_24h_mm),
      },
      temp: roundOrNull(current.temperature_c),
      humidity: current.humidity_pct != null ? current.humidity_pct : null,
      // Option D (2026-07-19): pressure_hpa and visibility_km now flow
      // from backend when any provider supplies them (SMG's fixed
      // stations for pressure; Open-Meteo for both). Round on the way
      // through so tile renders "1005 hPa · 17km" not "1005.4 hPa".
      pressure: current.pressure_hpa != null ? Math.round(current.pressure_hpa) : null,
      visibility: current.visibility_km != null ? Math.round(current.visibility_km * 10) / 10 : null,
      lightning: { count: null, nearest: null },
      source: current.source || 'SMG',
      // Per-field sources dict from backend Phase 1 multi-source merge.
      // Keys match CurrentWeather dataclass field names (temperature_c,
      // humidity_pct, wind_speed_ms, wind_direction_deg, gust_speed_ms,
      // rainfall_24h_mm). Missing key = that field wasn't supplied by any
      // provider — the tile must render '—' rather than showing the raw
      // default (e.g. 0.0 wind from HKO rhrread which has no wind data).
      sources: backendSources,
      stale: !!current.is_stale,
      station: current.station_name || '',
      forecast: fc,
      // SHL-20 fix: now epoch ms via parseTs (was the raw wire string,
      // bypassing parseTs entirely). API-F1 is now fixed backend-side, so
      // this is correct for both shapes fetched_at can arrive in: a naive
      // string (true UTC → parseTs appends Z) or an offset-bearing string
      // (parseTs leaves it alone). No compensating offset belongs here —
      // see the parseTs header comment. Consumed by the WeatherPage's live-updating
      // "更新於 N 秒前" ticker (`Date.now() - fetchedAt`); null when the
      // backend never supplied a fetch time.
      fetchedAt: parseTsMs(current.fetched_at),
    };
  }

  function mapAuditRow(r) {
    let detail = r.details != null ? r.details : (r.detail != null ? r.detail : {});
    if (typeof detail === 'string') {
      try { detail = JSON.parse(detail); } catch (e) { detail = { value: detail }; }
    }
    const tsDate = parseTs(r.timestamp || r.created_at || r.t);
    // SHL-18 fix: `ts` is documented (and consumed — audit.jsx's `now - a.ts`
    // shift-window math, `a.ts < _shiftFloor`, and formatAuditTs's
    // `new Date(ts)`) as ms-since-epoch, but this used to store the parseTs()
    // Date object itself. Arithmetic on a Date auto-coerces via valueOf() so
    // those call sites happened not to crash, but `Number.isNaN(ts)` below
    // was dead code — Number.isNaN returns false for any non-number
    // (including a Date), so a malformed timestamp could never trip the
    // guard. Convert to a real ms number here so the guard is meaningful and
    // the field matches its documented/consumed type.
    const tsMs = tsDate ? tsDate.getTime() : null;
    return {
      t: tsDate ? fmtClock(tsDate) : (r.timestamp || r.t || '—'),
      // Full timestamp (ms since epoch) used by the audit date filter.
      // Null if the row didn't carry a parseable timestamp.
      ts: tsMs != null && !Number.isNaN(tsMs) ? tsMs : null,
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
        // API-F9 fix: was `n._cycles = c.count` — kept only the raw count and
        // threw away `c.alert` (computed server-side at nodes.py:704 against
        // PUMP_CYCLE_ALERT_THRESHOLD), so the server's alert verdict never
        // reached the UI at all. Carry the whole {count, alert} object
        // through; mapNode splits it back into `cycles` (number, unchanged
        // contract) + `cyclesAlert` (new, boolean).
        n._cycles = c || { count: 0, alert: false };
      });
    }
    return list.map(mapNode);
  }

  // ALR-M7 fix: the backend silently caps this list at LIMIT (oldest/
  // longest-waiting alerts fall off the end with no signal). The endpoint
  // returns a bare array with no total-count field, so we can't report a
  // true grand-total without a backend change (out of scope here — I only
  // own api.jsx). What we CAN prove from the response alone: if we got back
  // exactly LIMIT rows, the true count is >= LIMIT (truncated); otherwise
  // we got everything that matched. Consumers must render `totalAvailable`
  // as a floor ("至少 N 筆"), never assert it is the exact total when
  // `truncated` is true.
  const _ACTIVE_ALERTS_LIMIT = 200;

  async function loadAlerts() {
    const rows = await apiFetch('/api/alerts?status_filter=PENDING_VIDEO,PENDING,ACKNOWLEDGED&limit=' + _ACTIVE_ALERTS_LIMIT);
    const arr = Array.isArray(rows) ? rows : [];
    const list = arr.map(mapAlert);
    list.truncated = arr.length >= _ACTIVE_ALERTS_LIMIT;
    list.totalAvailable = list.truncated ? _ACTIVE_ALERTS_LIMIT : arr.length;
    return list;
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
    } catch (e) {
      const status = e && e.status;
      const msg = e && e.message ? String(e.message) : '';

      // 401: session expired (soft-401 flag already set by apiFetch)
      // Return placeholder so sparkline stays interactive
      if (msg === 'unauthorized' || status === 401) {
        return new Array(16).fill(0);
      }

      // 403: should not happen for rate, but handle gracefully
      const is403 = status === 403 || msg.indexOf('403') !== -1;
      if (is403) {
        return new Array(16).fill(0);
      }

      // Any other error (network, 5xx, parse) → re-throw so
      // Promise.allSettled marks it rejected and dataWarnings banner shows
      throw e;
    }
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
        // WHA-M8: opaque precondition token for saveHandover's
        // expected_updated_at — the RAW server string, deliberately NOT
        // routed through parseTs/reformatted. It's compared for equality
        // against what the server holds at PUT time, never displayed
        // (pinned.at above remains the parseTs'd, human-readable rendering).
        updatedAt: (h && h.updated_at) || null,
      };
    } catch (e) {
      const status = e && e.status;
      const msg = e && e.message ? String(e.message) : '';

      // 401: session expired (soft-401 flag already set by apiFetch)
      // Return stub so UI stays interactive
      if (msg === 'unauthorized' || status === 401) {
        return { current: '', pinned: { by: '—', at: '', text: '尚無交接備註', ageMin: 0 }, history: [] };
      }

      // 403: should not happen for handover, but handle gracefully
      const is403 = status === 403 || msg.indexOf('403') !== -1;
      if (is403) {
        return { current: '', pinned: { by: '—', at: '', text: '尚無交接備註', ageMin: 0 }, history: [] };
      }

      // Any other error (network, 5xx, parse) → re-throw so
      // Promise.allSettled marks it rejected and dataWarnings banner shows
      throw e;
    }
  }

  // WHA-M14 fix: same silent-cap issue as ALR-M7 above — /api/audit has no
  // total-count field, so `totalAvailable` is a floor, not a verified exact
  // total (see _ACTIVE_ALERTS_LIMIT comment for the full reasoning).
  const _AUDIT_LIMIT = 200;

  async function loadAudit() {
    // 403 = non-admin session; page renders a "無權限" empty state driven by
    // window.AUDIT.forbidden. apiFetch throws Error('HTTP 403 on ...') with
    // .status = 403 (see apiFetch above) — we tolerate both shapes just in
    // case a caller re-wraps the error before it reaches here.
    window.AUDIT = window.AUDIT || [];
    try {
      const a = await apiFetch('/api/audit?limit=' + _AUDIT_LIMIT);
      const rows = (a && a.rows) || [];
      const entries = rows.map(mapAuditRow);
      entries.forbidden = false;
      entries.truncated = rows.length >= _AUDIT_LIMIT;
      entries.totalAvailable = entries.truncated ? _AUDIT_LIMIT : rows.length;
      window.AUDIT = entries;
      return entries;
    } catch (e) {
      const status = e && e.status;
      const msg = e && e.message ? String(e.message) : '';

      // 401: session expired (soft-401 flag already set by apiFetch)
      // Keep prior state so audit rows don't disappear mid-shift
      if (msg === 'unauthorized' || status === 401) {
        return window.AUDIT;
      }

      // 403: non-admin operator — expected, silence without banner
      const is403 = status === 403 || msg.indexOf('403') !== -1;
      if (is403) {
        const entries = [];
        entries.forbidden = true;
        entries.truncated = false;
        entries.totalAvailable = 0;
        window.AUDIT = entries;
        return entries;
      }

      // Any other error (network, 5xx, parse) → re-throw so
      // Promise.allSettled marks it rejected and dataWarnings banner shows
      throw e;
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

    const _loaderKeys = ['nodes', 'alerts', 'history', 'rate', 'weather', 'handover', 'audit'];
    const failedKeys = [];
    results.forEach((r, i) => {
      if (r.status === 'rejected') {
        failedKeys.push(_loaderKeys[i]);
        console.warn('[SDPRS] loader "' + _loaderKeys[i] + '" failed:', r.reason);
      }
    });
    // Expose failed loader names so app.jsx can show a partial-failure banner
    // ("Alert feed unavailable — displaying cached data") instead of silently
    // serving stale data from a previous successful load.
    window.__SDPRS_LOAD_FAILURES = failedKeys;
    if (failedKeys.length) console.warn('[SDPRS] some data failed to load:', failedKeys);
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
  // Loader keys, in the same order as the Promise.allSettled below. Hoisted
  // out of the IIFE so the outer .catch can report a total failure honestly.
  const _RL_KEYS = ['nodes', 'alerts', 'history', 'rate', 'weather', 'handover', 'audit'];
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
      // Track which loaders failed so app.jsx can surface partial-failure
      // warnings (mirrors loadInitial's __SDPRS_LOAD_FAILURES contract).
      const rlFailed = [];
      results.forEach((r, i) => { if (r.status === 'rejected') rlFailed.push(_RL_KEYS[i]); });
      return {
        nodes: window.NODES,
        alerts: window.ALERTS,
        history: window.HISTORY_ALERTS,
        rate: window.ALERT_RATE,
        weather: window.WEATHER,
        handover: window.HANDOVER,
        audit: window.AUDIT,
        failures: rlFailed,
      };
    })()
      .catch((err) => {
        // Symmetry with loadInitial — log but don't propagate an unhandled
        // rejection to the poll timer / WS handler.
        //
        // SHL-12 residual: reaching here means the IIFE itself threw, which
        // Promise.allSettled rules out for the loaders — so the thrower is a
        // mapper (buildNodeHistory / buildShiftSummary). Every window.* value
        // below is therefore whatever the PREVIOUS refresh left behind, i.e.
        // wholly stale. Reporting `failures: []` here told app.jsx everything
        // was fine and silently cleared the stale-data banner, which on a
        // 24/7 console means an operator reads a frozen board as live. Report
        // all keys as failed instead — the banner must stay up.
        console.warn('[api] refreshLive failed', err);
        return {
          nodes: window.NODES,
          alerts: window.ALERTS,
          history: window.HISTORY_ALERTS,
          rate: window.ALERT_RATE,
          weather: window.WEATHER,
          handover: window.HANDOVER,
          audit: window.AUDIT,
          failures: _RL_KEYS.slice(),
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

  // API-F3 fix: /api/stream/health (central_server/api/stream.py:222) scrapes
  // mediamtx's Prometheus /metrics endpoint and computes real per-node
  // bitrate/drops/viewers — but had zero callers anywhere in the SPA, so
  // status.jsx fell back to mapNode's `bitrate`/`drops` fields, which read
  // from `stream_status.bitrate_mbps`/`dropped_frames` keys the edge never
  // publishes and are therefore always 0 (see mapNode's `ss.bitrate_mbps ...`
  // fallback chain above).
  //
  // Response shape (no node_id query param — the endpoint scrapes ALL
  // streams in one round-trip; we filter to the requested node client-side):
  //   { enabled: false }                                          — MEDIAMTX_METRICS_URL unset (feature off)
  //   { enabled: true, reachable: false, status_code, nodes: {} }  — mediamtx responded non-200
  //   { enabled: true, reachable: false, error, nodes: {} }        — scrape threw (network/timeout)
  //   { enabled: true, reachable: true,
  //     nodes: { <node_id>: { viewers: int, dropped: int, bitrate_kbps: int } } }
  // `nodes` keys are the mediamtx stream path, which IS the node_id (server
  // comment: "mediamtx labels the path with the stream name ... that becomes
  // our node_id"). A node with no entry in `nodes` is NOT an error — it just
  // means mediamtx hasn't scraped a byte-counter sample for that path yet
  // (stream never started since server boot), so its fields come back null
  // rather than a fabricated 0 (roundOrNull convention, same reasoning as
  // elsewhere in this file: "no data" must never look like "reading is zero").
  //
  // Math done here that the endpoint doesn't do: bitrate_kbps -> Mbps, to
  // match the existing `node.bitrate` convention (Mbps) that status.jsx's
  // thresholds and "…Mbps" label already assume — the endpoint itself only
  // computes kbps (first derivative of the mediamtx byte counter).
  //
  // NOT wrapped in try/catch here (unlike the loadXxx() functions) — this is
  // an on-demand per-node query, not a Promise.allSettled fan-out member, so
  // errors propagate to the caller exactly like startStream/stopStream do.
  async function getStreamHealth(nodeId) {
    const r = await apiFetch('/api/stream/health');
    const enabled = !!(r && r.enabled);
    const reachable = !!(r && r.reachable);
    const entry = (r && r.nodes && r.nodes[nodeId]) || null;
    return {
      enabled,
      reachable,
      bitrateMbps: (entry && entry.bitrate_kbps != null) ? entry.bitrate_kbps / 1000 : null,
      drops: (entry && entry.dropped != null) ? entry.dropped : null,
      viewers: (entry && entry.viewers != null) ? entry.viewers : null,
    };
  }

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

    // apiFetch handles 401 (sets window.__SDPRS_SESSION_EXPIRED for the
    // soft-401 modal flow — no hard redirect) and re-throws non-2xx with
    // `.status` attached, so a 403 non-admin surfaces to the caller's toast.
    try {
      await apiFetch('/api/audit?limit=1');
    } catch (e) {
      const err = new Error('audit export failed: ' + (e && e.status != null ? e.status : (e && e.message) || 'unknown'));
      err.status = (e && e.status) || 0;
      err.cause = e;
      throw err;
    }

    // SHL-16: this used to be `new Date().toISOString().slice(0,10)`, i.e. the
    // UTC date. Macau is UTC+8, so for the whole 00:00–08:00 local night shift
    // the UTC date is still YESTERDAY — an operator exporting at 02:00 on the
    // 21st got a file named `audit_20260720.csv`, which is exactly the kind of
    // off-by-one-day that makes an incident export get filed against the wrong
    // date. Use LOCAL date parts: every other timestamp the operator sees on
    // this dashboard is browser-local (see fmtClock), so the filename now
    // agrees with the console rather than with the wire format.
    const _now = new Date();
    const _p = (n) => String(n).padStart(2, '0');
    const ymd = String(_now.getFullYear()) + _p(_now.getMonth() + 1) + _p(_now.getDate());
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

  // Manual pump ON/OFF command. ON requires a positive duration_s (1..600);
  // OFF may omit it (holds indefinitely). Fire-and-forget from the SPA — the
  // truth about whether the pump actually turned on lives on the device and
  // reaches back via the next pump_status publish (~2s cadence).
  const pumpCommand = (nodeId, action, durationS) => apiFetch(
    '/api/nodes/' + encodeURIComponent(nodeId) + '/pump',
    jsonBody('POST', { action, duration_s: durationS != null ? durationS : null }));

  // Weather multi-source settings (Option C, 2026-07-19) — hits the
  // config API landed alongside the SPA settings pane. All 4 helpers
  // gracefully swallow 503 (weather service disabled) so the SPA can
  // hide the settings pane in that case without a console error storm.
  const getWeatherConfig = () => apiFetch('/api/weather/config');
  const setWeatherConfig = (cfg) => apiFetch('/api/weather/config', jsonBody('PUT', cfg));
  const listSmgStations = () => apiFetch('/api/weather/smg/stations');
  const listHkoStations = () => apiFetch('/api/weather/hko/stations');
  // Force an immediate weather re-tick — server's cache is otherwise
  // only refreshed every WEATHER_REFRESH_SECONDS (600s / 10 min default).
  // Called after setWeatherConfig so the operator sees the new source
  // selection reflected on the tiles within seconds, not minutes.
  //
  // API-F4 / WHA-H1 fix: the endpoint always answers HTTP 200 — even when
  // every upstream provider failed — and signals that failure only via a
  // body flag (`{ok: false, message: '...'}`, see weather.py:189-193).
  // apiFetch has no way to know this is semantically an error (it only
  // looks at the HTTP status), so it resolved successfully and every
  // caller's toast claimed success while the tiles stayed stale mid-typhoon.
  // We now inspect the body here and reject so callers' existing try/catch
  // (written for the old "network error" case) also catches "upstream
  // refresh failed" — same .status/.detail shape as every other thrown
  // error in this file.
  async function refreshWeather() {
    const r = await apiFetch('/api/weather/refresh', { method: 'POST' });
    if (!r || r.ok !== true) {
      const msg = (r && typeof r.message === 'string') ? r.message : null;
      const err = new Error('weather refresh failed' + (msg ? ': ' + msg : ''));
      err.status = 200; // HTTP succeeded; the failure is in the response body
      err.detail = msg;
      throw err;
    }
    return r;
  }

  const deleteNode = (nodeId) => apiFetch('/api/nodes/' + encodeURIComponent(nodeId),
    { method: 'DELETE' });

  // WHA-M8: `expectedUpdatedAt` is the opaque token loadHandover() returned
  // as `updatedAt` (raw server `updated_at` string, unparsed). Sending it
  // lets the server detect a lost-update race (see handover.py's
  // expected_updated_at precondition) instead of silent last-write-wins.
  // Omitted/null caller → `expected_updated_at: null`, which the backend
  // treats the same as the field being absent (old, pre-precondition
  // behaviour) — so callers that haven't been updated yet still work.
  //
  // On a 409 the server means "your expected_updated_at didn't match — someone
  // else saved first" and its JSON body carries the server's CURRENT note
  // text + updated_at (roughly {detail, current, updated_at}). apiFetch
  // already parses that body onto the thrown Error as `.body` (see apiFetch's
  // `!res.ok` branch above) — we just lift it onto first-class `.conflict` /
  // `.current` / `.updatedAt` properties so handover.jsx can render a
  // compare-and-choose UI instead of the generic error toast every other
  // saveHandover failure gets. Non-409 errors are rethrown untouched.
  async function saveHandover(note, expectedUpdatedAt) {
    try {
      return await apiFetch('/api/handover/note', jsonBody('PUT', {
        note: note || '',
        expected_updated_at: expectedUpdatedAt != null ? expectedUpdatedAt : null,
      }));
    } catch (e) {
      if (e && e.status === 409) {
        const body = e.body;
        e.conflict = true;
        e.current = (body && typeof body.current === 'string') ? body.current : '';
        e.updatedAt = (body && body.updated_at) || null;
      }
      throw e;
    }
  }

  const updateNodeLocation = (id, location) => apiFetch('/api/nodes/' + encodeURIComponent(id),
    jsonBody('PATCH', { location }));

  // API-F7 fix: /api/session/extend (main.py:447-458) re-stamps the
  // server-side session's `login_at`, which is what the session lifetime is
  // measured against — but had zero callers, so an operator active for a
  // full 24h typhoon shift got hard-logged-out mid-shift regardless of how
  // recently they clicked anything. This wrapper only issues the call;
  // deciding WHEN to call it is deliberately left to app.jsx (which owns the
  // session-expiry modal / polling timers already), not implemented here.
  //
  // Suggested wiring (for the app.jsx owner): call on a safety-net interval
  // — e.g. every 30 min via setInterval — while the tab is visible/focused
  // (skip when document.hidden to avoid extending a shift nobody is actually
  // working), OR piggyback on top of any successful mutating call the
  // operator already makes (ack/resolve/snooze/pump command/handover save)
  // so real activity resets the clock immediately and the timer is purely a
  // backstop for a quiet "just watching" shift. Either way this should be a
  // fire-and-forget call: on failure (401 = already expired, network blip)
  // there is nothing useful to do beyond letting the existing soft-401 modal
  // flow (window.__SDPRS_SESSION_EXPIRED, set by apiFetch above) take over.
  //
  // Response body is `{ ok: true, login_at: <naive-UTC ISO> }` — returned
  // as-is; callers don't need `login_at` today but it's there if a future
  // "session extended, N hours remaining" toast wants it (would need
  // parseTs, per THE ONE RULE, not a raw new Date()).
  const extendSession = () => apiFetch('/api/session/extend', { method: 'POST' });

  // ---- websocket ---------------------------------------------------------

  // Backend event types dispatched to `onEvent(type, data)`. `new_alert` is
  // routed to `onNewAlert(alertObj)` (mapped through mapAlert). `ping` is
  // internal — pure keepalive, never surfaced.
  const _WS_EVENT_TYPES = new Set([
    'alert_updated', 'alert_acknowledged', 'alert_resolved',
    'node_status', 'pump_status', 'node_deleted',
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
    snoozeNode, unsnoozeNode, pumpCommand, deleteNode, saveHandover, updateNodeLocation,
    getWeatherConfig, setWeatherConfig, listSmgStations, listHkoStations, refreshWeather,
    exportAuditCsv,
    startStream, stopStream, getStreamHealth,
    extendSession,
    openSocket,
  };
})();
