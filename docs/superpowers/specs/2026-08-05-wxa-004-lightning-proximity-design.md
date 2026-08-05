# Design Proposal — WXA-004 Lightning Proximity (Blitzortung integration)

**Status:** Approved for spec review · **Author:** dashboard-audit workstream · **Date:** 2026-08-05
**Scope:** `central_server/services/lightning_service.py` (NEW), `central_server/main.py` (lifespan wiring), `central_server/api/weather.py` (stitch lightning into the payload outside the throttle cache), `central_server/static/spa/api.jsx` (replace the two hardcoded nulls), `central_server/static/spa/pages/weather.jsx` (drop the stale "no source" comment; no logic change), `central_server/requirements.txt` (declare `websockets` directly), `central_server/config.py` (five new `LIGHTNING_*` settings on the `Settings` class), `central_server/tests/test_lightning_service.py` (NEW).
**Relates to:** `docs/audits/full_dashboard_audit_2026-07-26.md` finding **WXA-004** ("Lightning hero tile permanently dead (hardcoded null)"). Companion to the shipped weather stack (`weather_service.py` + `api/weather.py`).

> Requirement + source + deployment status confirmed with the user: the tile must show **real per-strike proximity** (distance drives the `<20 km` 警戒), the source is **Blitzortung.org** (community lightning network), and SDPRS is a **non-commercial / educational** deployment — which satisfies Blitzortung's ToS (non-commercial + attribution + server-side aggregation). Architecture confirmed as **Approach A** (dedicated `lightning_service.py` module mirroring `weather_service.py`), not folding lightning into the weather poller. This document is the reviewable spec that precedes the implementation plan.

---

## 1. Problem

`weather.jsx` renders a "雷擊" (lightning) hero tile: a strike count ("次/hr") and a proximity readout that flips to a `<20 km` 警戒 (alert) state. The tile has always been dead:

- `api.jsx:474` (fallback path) and `api.jsx:533` (the real `mapWeather` path) both **hardcode** `lightning: { count: null, nearest: null }`.
- `data.jsx:85` sets the same null shape as the default.
- `weather.jsx:684-686` carries a comment: *"No source label — lightning has no backend source yet … Reserved for a future Blitzortung / HKO thunderstorm-warning integration."*

There is **no backend lightning source at all** — the field is a permanent placeholder, so the tile always renders "—" (unknown) and never reaches either the "0 次/hr / 無偵測" quiet state or the `<20 km` 警戒 state.

**Root cause:** the tile UI was built ahead of a data source. Wiring it requires (a) a real-time strike feed, (b) a place to aggregate strikes server-side into `{count, nearest}`, and (c) stitching that into the existing `/api/weather` payload. The weather stack is **poll-based** (httpx, periodic fetch); lightning is **push-based** (a persistent stream of per-strike events), so it does not fit the weather poller — it needs its own long-lived listener.

---

## 2. Goals / non-goals

**Goals**
- The tile shows **real, live** proximity: `nearest` = the great-circle distance (km) to the closest recent strike, and the `<20 km` 警戒 fires on real strikes.
- **Weather-grade robustness:** a lightning failure (never-connected, socket dropped, decode error, stale feed) **MUST NOT** propagate. The getter always returns a safe shape; the alert pipeline and the rest of the weather payload are unaffected. This mirrors the weather stack's hard invariant.
- **Two honest empty states**, distinguishable in the UI:
  - *connected but quiet* → `{count: 0, nearest: null}` → "0 次/hr" + "無偵測" (we are listening; nothing is out there).
  - *disconnected / stale / never-connected* → `{count: null, nearest: null}` → "—" (we don't know).
- Non-commercial ToS compliance: SDPRS **aggregates** strikes server-side and exposes only `{count, nearest}` — it never proxies the raw Blitzortung stream to browsers. Source attributed as `"Blitzortung.org"`.
- **Self-degrading and reversible:** a feature flag (default on) plus tuning knobs, all env-driven, no redeploy needed to disable.

**Non-goals (explicitly out of scope)**
- A strike map, historical strike storage, or a strikes-over-time chart. Aggregate `{count, nearest}` only.
- Any change to the alert/notification pipeline, MQTT, or node telemetry. Lightning is **weather-tile decoration**, exactly like the rest of `weather_service`.
- HKO thunderstorm-warning text integration (the comment's other half) — a separate future item.
- Streaming raw strikes to the SPA, or any client-side WebSocket to Blitzortung (ToS-prohibited and unnecessary).
- Persisting strikes across restarts. The in-memory window is rebuilt from the live feed within minutes of startup; a cold tile shows "—" until then, which is correct.

---

## 3. Data model & semantics

The `/api/weather/current` payload (served by `api/weather.py::get_current_weather`, mounted at `/api`) gains a `lightning` object and a `sources.lightning` label:

```jsonc
{
  "lightning": {
    "count":   0,        // int  — strikes in the trailing COUNT window within COUNT_RADIUS_KM
    "nearest": null       // float km — min great-circle distance in the trailing NEAREST window, or null
  },
  "sources": { "...": "...", "lightning": "Blitzortung.org" }
}
```

**Definitions (approved numbers):**

| Field | Meaning | Window | Radius |
| --- | --- | --- | --- |
| `count` | strikes counted for the "次/hr" readout | trailing **60 min** | within **`LIGHTNING_COUNT_RADIUS_KM = 50`** km |
| `nearest` | min great-circle km, drives the `<20 km` 警戒 | trailing **`LIGHTNING_NEAREST_WINDOW_MIN = 30`** min | any strike in the retained buffer |

The `nearest` window (30 min) is deliberately **shorter** than the `count` window (60 min): "how busy has the last hour been" is a different question from "is there a strike *near me right now*." A strike from 45 minutes ago should not keep the 警戒 latched.

**Empty-state contract (the two states the UI must tell apart):**

| Backend state | Payload | Tile |
| --- | --- | --- |
| Listening, no strikes in window | `{count: 0, nearest: null}` | "0 次/hr", "無偵測" (quiet) |
| Never connected / socket closed / feed stale | `{count: null, nearest: null}` | "—" (unknown) |

"Stale" = no strike message received for `LIGHTNING_STALE_AFTER_S = 300` s (5 min). Because real skies can be genuinely quiet for 5+ minutes, staleness is judged on **message receipt** (did the socket deliver *anything*, including keep-alives), not on strike count — a live-but-quiet feed stays in the "0 次/hr" state, only a dead socket flips to "—". (See §5 for how the listener distinguishes these.)

`nearest` is `null` whenever there is no strike inside the nearest-window, even when `count > 0` (e.g. strikes 40 min ago count toward the hour but are outside the 30-min proximity window) — the UI already treats `nearest == null` as "無偵測", which is correct.

---

## 4. New module — `services/lightning_service.py`

Mirrors `weather_service.py`'s shape: a module-level singleton created by `init_lightning_service(settings)`, fetched by `get_lightning_service()`, with `async start()` / `async stop()` lifecycle and safe-default public getters.

### 4a. Background listener
- On `start()`, spawn one `asyncio.Task` (`self._task`) holding a **persistent WebSocket** to Blitzortung, with **exponential-backoff reconnect** (capped; jittered by attempt index — `Math.random`/`Date.now` are not needed, backoff is deterministic on attempt count).
- Each inbound message is decoded to a strike `(lat, lon, epoch_utc)`. Blitzortung frames use a custom LZW-style encoding; decoding is a pure function (`_decode_strike(raw) -> Optional[Strike]`) so it can be unit-tested against a captured sample with **no live socket**.
- A strike is kept only if it falls inside a **bounding box** (~±1° ≈ 111 km) around the configured site — a cheap pre-filter so the retained buffer stays tiny. Kept strikes append to a `collections.deque`.
- On every append (and on read), prune entries older than the **longest** window we need (60 min) using `timeutil.utcnow()`. Naive-UTC throughout — never `datetime.utcnow()` / never tz-aware.
- **The listener body is wrapped so no exception escapes:** decode errors, malformed frames, and socket drops are caught, logged, and lead to reconnect. The task never dies silently and never propagates.
- Track `self._last_msg_at` (naive-UTC) on **every** received message (strike or keep-alive) for the staleness check, and `self._connected` for the never-connected check.

### 4b. Pure getter
```python
def get_lightning(self, site_lat, site_lon) -> dict:
    # Returns {"count": int|None, "nearest": float|None,
    #          "source": "Blitzortung.org", "is_stale": bool}
    # NEVER raises. Never-connected or stale -> {count: None, nearest: None}.
```
- Never connected, or `utcnow() - self._last_msg_at > LIGHTNING_STALE_AFTER_S` → return the unknown shape `{count: None, nearest: None}` (with `source` still set so the UI can attribute the *intended* source, and `is_stale: True`).
- Otherwise compute `count` (haversine ≤ `COUNT_RADIUS_KM`, within 60 min) and `nearest` (min haversine within `NEAREST_WINDOW_MIN`), returning `nearest: None` when the nearest-window is empty.
- `get_lightning` is a **pure read** over the in-memory deque — cheap enough to call on every `/api/weather` request (see §5, it runs *outside* the throttle cache).
- Haversine is a small local helper (no new dependency); great-circle km is accurate enough at these ranges.

### 4c. Lifecycle wiring in `main.py`
Immediately after the weather-service block in `lifespan()` (mirrors it exactly):
```python
try:
    lightning_svc = init_lightning_service(settings)
    await lightning_svc.start()          # no-op when LIGHTNING_ENABLED is false
    app.state.lightning_service = lightning_svc
except Exception as e:
    logger.warning(f"Failed to start lightning service: {e}")
    app.state.lightning_service = None
```
And on shutdown, `await lightning_svc.stop()` inside a try/except, next to the weather-service stop. `stop()` cancels the task and closes the socket with a short timeout, exactly like `WeatherService.stop()`.

---

## 5. Endpoint & SPA integration

> **Correction (2026-08-05, during plan grounding):** the tile is fed by three separate routes — `GET /api/weather/current|forecast|typhoon` — which `api.jsx::loadWeather` fetches in parallel and combines via `mapWeather`. There is **no** single `/api/weather` route, and `/api/weather/current` is **not** `_ThrottleGate`-gated (only the two station-list routes and `POST /weather/refresh` are). So there is no throttle cache to work around: `get_current_weather` already returns a **fresh** per-request read of the in-memory weather cache. The lightning overlay is likewise a fresh per-request in-memory read — the "outside the throttle cache" intent is satisfied for free. §5a below is the corrected wiring.

### 5a. `api/weather.py::get_current_weather` — overlay a fresh lightning read
The `/api/weather/current` handler serializes `svc.get_current()` (the weather cache, refreshed by the background poll) on every request. Overlay lightning onto that dict before returning:
- After `_serialize(cur)`, read `get_lightning_service()`; if present, call `get_lightning(site_lat, site_lon)` (a fresh in-memory deque read — microseconds) and set `payload["lightning"] = {"count": …, "nearest": …}` + `payload.setdefault("sources", {})["lightning"] = <source>`.
- `site_lat` / `site_lon` come from `get_weather_config()` (keys `site_lat` / `site_lon`); when unset, fall back to `settings.SITE_LAT` / `settings.SITE_LON` (the same Macau default the weather poller uses).
- If `get_lightning_service()` is `None` (flag off / init failed), set `payload["lightning"] = {"count": None, "nearest": None}` and **do not** set `sources.lightning` — the tile degrades to a bare "—", exactly today's behavior. (A *labelled* "—" therefore means "service on, no data / stale"; a *bare* "—" means "service off" — a free honest signal.)

### 5b. `api.jsx` — replace the hardcoded null + expose the loader for testability
- `api.jsx:533` (real `mapWeather` path): `lightning: current.lightning || { count: null, nearest: null }`. `sources` already flows through untouched (`sources: backendSources` at L541 carries `current.sources.lightning`), so no separate sources wiring is needed here.
- `api.jsx:474` (fallback path): unchanged. When `/api/weather/current` is unreachable/503, `current` is null and "—" is the correct tile state, so the literal null default stays.
- **Add `loadWeather` to the `window.SDPRS_API` export object** (L1440-1451), mirroring `refreshLive`. `mapWeather`/`loadWeather` live inside api.jsx's IIFE and are only observable through that public surface — the render-test harness concatenates the target after the IIFE has already returned, so the SPA render test drives `loadWeather` over a stubbed `window.fetch` (it cannot call `mapWeather` directly).

### 5c. `weather.jsx` — show the source chip like every sibling tile
- The tile logic (`count`/`nearest`/`alarming = near < 20`) is **unchanged**.
- Replace the stale `weather.jsx:684-686` comment ("no backend source yet…") with `<SourceChip label={sources.lightning}/>` — the same component every other tile uses (e.g. `sources.rainfall_24h_mm` at L665). `SourceChip` returns `null` on a falsy label, so it stays invisible until the backend supplies `sources.lightning`.

### 5d. Dependency
- `websockets` is already installed transitively via `uvicorn[standard]`. Declare it **directly** in `requirements.txt` (`websockets>=12.0`) so the new module's import is an explicit, honest dependency rather than an accidental transitive one. No new install.

---

## 6. Error handling & degradation

Every failure mode maps to a safe tile state — nothing throws to the request path:

| Failure | Handling | Tile |
| --- | --- | --- |
| Never connected yet (cold start) | `get_lightning` sees `self._connected == False` → unknown shape | "—" |
| Socket drops mid-run | listener catches, logs, backs off + reconnects; getter goes stale after 300 s | "—" after 5 min, live again on reconnect |
| Malformed / undecodable frame | `_decode_strike` returns `None`; message skipped; feed stays live | unaffected |
| Feed silent > 300 s | `is_stale` true → unknown shape | "—" |
| Flag off / init raised | `get_lightning_service()` is `None`; endpoint uses literal null default | "—" |
| Genuinely quiet sky (live feed, no strikes) | not stale; count 0, nearest null | "0 次/hr", "無偵測" |

The invariant, stated once: **`get_lightning` never raises and never blocks; a lightning fault can only ever make the tile show "—".**

---

## 7. Configuration (all env-driven, no new credentials)

| Setting | Default | Purpose |
| --- | --- | --- |
| `LIGHTNING_ENABLED` | `true` | Master switch. `start()` is a no-op when false; endpoint degrades to "—". Self-degrading, so "on" is safe. |
| `LIGHTNING_COUNT_RADIUS_KM` | `50` | Radius for the "次/hr" count. |
| `LIGHTNING_NEAREST_WINDOW_MIN` | `30` | Trailing window for the proximity / 警戒 readout. |
| `LIGHTNING_STALE_AFTER_S` | `300` | No message for this long ⇒ feed stale ⇒ "—". |
| `LIGHTNING_COUNT_WINDOW_MIN` | `60` | Trailing window for the count (the "/hr" basis). |

No API key, no credentials — Blitzortung's community feed is unauthenticated. The banned strings (`Msc@2333`, `MSC-Person`, `broker.emqx.io`) appear nowhere. The five knobs are added to the `Settings` class in `central_server/config.py`, beside the existing `WEATHER_*` settings.

---

## 8. Testing (TDD, mirrors `test_weather_service.py` / `test_smg_xml_parser.py`)

Strict RED→GREEN; **no live WebSocket in CI** (the socket and decoder are injected/mocked). Test list:

1. **Decoder (deterministic, no live frame)** — split into two pure functions: `_decompress(raw)` (the community LZW port) tested with an ASCII-identity vector plus two hand-computed dictionary-branch vectors (`"AB"+chr(256) → "ABAB"`, `"A"+chr(256) → "AAA"`); and `_parse_strike(msg_dict)` tested with a plain `{"time","lat","lon"}` dict → `Strike` with naive-UTC ts. A real captured frame is committed as an **optional, non-CI** regression fixture during the manual smoke test (spec §10 Q1) — the CI-blocking tests never need a live socket.
2. **`count` radius edge** — strikes just inside / just outside `COUNT_RADIUS_KM`; assert only the inside ones count.
3. **`count` window edge** — strikes just inside / just outside the 60-min window; assert pruning.
4. **`nearest` window edge** — a strike 40 min ago (counts toward hour, outside 30-min proximity) ⇒ `count ≥ 1` but `nearest is None`.
5. **`nearest` value** — nearest of several strikes returns the minimum haversine km.
6. **Empty-state: quiet** — connected, `_last_msg_at` recent, no strikes ⇒ `{count: 0, nearest: None}`.
7. **Empty-state: unknown** — never connected ⇒ `{count: None, nearest: None}`; and stale (`_last_msg_at` old) ⇒ same.
8. **Never raises** — feed a decode exception / malformed frame; assert `get_lightning` still returns a safe shape and the task survives.
9. **Endpoint merge** — `get_current_weather` overlays `lightning` + `sources.lightning` onto the serialized weather dict (monkeypatch `get_lightning_service`/`get_weather_service`/`get_weather_config`); assert the overlay is a fresh read each call (mutate the stub's return, second call reflects it).
10. **Flag off** — `LIGHTNING_ENABLED=false` ⇒ `start()` no-op, `get_lightning_service()` is `None`, endpoint returns bare null-shape with no `sources.lightning`.
11. **SPA render** (`tools/spa/render_extra/wxa004-lightning.js`, `target: 'api.jsx'`) — drive `SDPRS_API.loadWeather()` over a stubbed `window.fetch` whose `/api/weather/current` payload carries `lightning:{count:3,nearest:5}` + `sources.lightning`; assert the mapped `w.lightning` and `w.sources.lightning` flow through (RED before the L533 fix). Run via `node tools/spa/render_tests.js`, gate via `node tools/spa/run_all.js`.

Optional manual smoke test (not in CI): run the service locally against the live feed for a few minutes and eyeball the tile.

---

## 9. Rollout / reversibility

- Ships behind `LIGHTNING_ENABLED` (default on, self-degrading). If the live feed misbehaves in production, set it false — the tile reverts to "—", nothing else changes.
- Zero schema/DB changes; zero new credentials; one directly-declared dependency already present transitively.
- Backward compatible: an old SPA against a new backend sees a real `lightning` object (fine); a new SPA against an old backend sees the null default (tile "—", as today).

---

## 10. Open questions for spec review

1. **Decoder sourcing.** The Blitzortung frame decoder is community-reverse-engineered (custom LZW variant). The plan will capture one real frame as a fixture and implement the decoder from the documented algorithm; if the live format has drifted, item 1 of §8 turns RED and we adjust. Acceptable to discover during implementation?
2. **Bounding-box vs. exact filter.** §4a pre-filters strikes to ~±1° before the exact haversine. At 50 km count radius that's comfortably inside ±1° (~111 km); confirm the box doesn't need to widen for any future larger radius.
3. **`count` window knob.** §7 adds `LIGHTNING_COUNT_WINDOW_MIN=60` to make the "/hr" basis explicit/tunable. Keep it configurable, or hardcode 60?
