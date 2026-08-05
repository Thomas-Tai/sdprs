# WXA-004 Lightning Proximity (Blitzortung) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the already-built "雷擊" (lightning) weather tile to a real Blitzortung.org data source so it shows live strike count + nearest-strike proximity (the `<20 km` 警戒), replacing the permanently-hardcoded `null`.

**Architecture:** A new `services/lightning_service.py` runs a background asyncio task holding a persistent Blitzortung WebSocket, decodes each strike, and keeps recent strikes in an in-memory bounding-box-filtered `deque`. A pure `get_lightning(site_lat, site_lon)` getter (never raises) computes `{count, nearest}` over that deque on demand. The `/api/weather/current` endpoint overlays a fresh lightning read onto its response; the SPA maps it through to the existing tile. This mirrors the shipped `weather_service.py` shape exactly (singleton + `start()`/`stop()` lifecycle + safe-default getters), keeping lightning fully independent of the weather poller.

**Tech Stack:** Python 3.14, FastAPI, asyncio, `websockets` (already transitive via `uvicorn[standard]`), the no-build-step in-browser-Babel React SPA.

**Source spec:** `docs/superpowers/specs/2026-08-05-wxa-004-lightning-proximity-design.md` (approved 2026-08-05).

## Global Constraints

*Every task's requirements implicitly include this section. Values are verbatim from the spec.*

- **Never raises:** a lightning fault (never-connected / socket drop / decode error / stale feed) MUST NOT propagate. `get_lightning` always returns a safe shape; the weather payload and alert pipeline are unaffected.
- **Naive-UTC only:** all datetimes via `from ..timeutil import utcnow` (naive UTC). NEVER `datetime.utcnow()`, NEVER tz-aware in app code. Epoch→naive-UTC conversion uses `datetime.fromtimestamp(sec, tz=timezone.utc).replace(tzinfo=None)`.
- **Non-commercial ToS:** SDPRS aggregates strikes server-side and exposes only `{count, nearest}`. NEVER proxy the raw Blitzortung stream to browsers. Source attributed as the literal string `"Blitzortung.org"`.
- **No new credentials.** Blitzortung's feed is unauthenticated. The literal strings `Msc@2333`, `MSC-Person`, `broker.emqx.io` must NEVER appear in any diff.
- **SPA rules:** no new npm deps, no `import`/`require`, no build step. All user-facing strings zh-TW.
- **Dependency:** declare `websockets>=12.0` directly in `requirements.txt` (already installed transitively — no new install).
- **Feature flag:** everything gated by `LIGHTNING_ENABLED` (default `true`, self-degrading).
- **TDD:** strict RED→GREEN. See every test FAIL for the stated reason before implementing. No live WebSocket in CI.
- **Commits:** commit after each task. NOTHING merges to / pushes to `origin/main` without the user typing the literal word **"approved"**.
- **Test runners:** Python — `/c/Python314/python -m pytest central_server/tests/<file>.py -v` run from the `sdprs/` root (per-suite, not the whole dir). SPA — fast: `node tools/spa/render_tests.js`; blocking gate: `node tools/spa/run_all.js` (must end "All blocking SPA checks passed.").

## File Structure

| File | Responsibility |
| --- | --- |
| `central_server/config.py` | +5 `LIGHTNING_*` settings on **both** `Settings` classes (pydantic `BaseSettings` block ~L106-116 and plain fallback class + its `__init__` ~L197-237). |
| `central_server/services/lightning_service.py` | **NEW.** Decoder (`_decompress`/`_parse_strike`/`_decode_message`), `_haversine_km`, `Strike` dataclass, `LightningService` (deque + `_on_message`/`_prune`/`_in_bbox`/`get_lightning`/`start`/`stop`/`_run`), module singleton `init_lightning_service`/`get_lightning_service`. |
| `central_server/main.py` | Lifespan: `init_lightning_service` + `start()` on startup (after weather block), `stop()` on shutdown. |
| `central_server/api/weather.py` | `_overlay_lightning(payload)` helper + call it in `get_current_weather`. |
| `central_server/static/spa/api.jsx` | Replace hardcoded null at L533; add `loadWeather` to the `SDPRS_API` export. |
| `central_server/static/spa/pages/weather.jsx` | Replace the L684-686 comment with `<SourceChip label={sources.lightning}/>`. |
| `central_server/requirements.txt` | Declare `websockets>=12.0`. |
| `central_server/tests/test_lightning_service.py` | **NEW.** Decoder, haversine, getter windows/radius/empty-states, `_on_message` bbox, lifecycle, singleton, config defaults. |
| `central_server/tests/test_lightning_endpoint.py` | **NEW.** `_overlay_lightning` + endpoint overlay + flag-off. |
| `central_server/tests/test_lightning_lifespan.py` | **NEW.** App-boot lifespan wiring (TestClient, `init_lightning_service` stubbed). |
| `tools/spa/render_extra/wxa004-lightning.js` | **NEW.** SPA mapping test through `SDPRS_API.loadWeather` over stubbed fetch. |

---

## Task 1: Config knobs (`LIGHTNING_*`)

**Files:**
- Modify: `central_server/config.py` (pydantic block ~L106-116; fallback class attr block ~L197-208; fallback `__init__` ~L231-237)
- Test: `central_server/tests/test_lightning_service.py`

**Interfaces:**
- Produces: `settings.LIGHTNING_ENABLED: bool`, `settings.LIGHTNING_COUNT_RADIUS_KM: float`, `settings.LIGHTNING_NEAREST_WINDOW_MIN: int`, `settings.LIGHTNING_COUNT_WINDOW_MIN: int`, `settings.LIGHTNING_STALE_AFTER_S: int`.

- [ ] **Step 1: Write the failing test**

Create `central_server/tests/test_lightning_service.py`:

```python
# -*- coding: utf-8 -*-
"""WXA-004 lightning service unit tests. No live WebSocket — the socket and
decoder are exercised through pure functions / injected state."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server.config import get_settings


def test_lightning_settings_defaults():
    s = get_settings()
    assert s.LIGHTNING_ENABLED is True
    assert s.LIGHTNING_COUNT_RADIUS_KM == 50.0
    assert s.LIGHTNING_NEAREST_WINDOW_MIN == 30
    assert s.LIGHTNING_COUNT_WINDOW_MIN == 60
    assert s.LIGHTNING_STALE_AFTER_S == 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest central_server/tests/test_lightning_service.py::test_lightning_settings_defaults -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'LIGHTNING_ENABLED'`.

- [ ] **Step 3: Add the settings to BOTH Settings classes**

In the pydantic `BaseSettings` block, immediately after `WEATHER_CACHE_STALE_SECONDS: int = 3600` (~L116):

```python
        # WXA-004 lightning (Blitzortung.org). Self-degrading; on is safe.
        LIGHTNING_ENABLED: bool = True
        LIGHTNING_COUNT_RADIUS_KM: float = 50.0     # radius for the "次/hr" count
        LIGHTNING_NEAREST_WINDOW_MIN: int = 30      # trailing window for proximity / 警戒
        LIGHTNING_COUNT_WINDOW_MIN: int = 60        # trailing window for the "/hr" count
        LIGHTNING_STALE_AFTER_S: int = 300          # no message this long => feed stale => "—"
```

In the fallback plain-class type-annotation block, after `WEATHER_CACHE_STALE_SECONDS: int` (~L203):

```python
        LIGHTNING_ENABLED: bool
        LIGHTNING_COUNT_RADIUS_KM: float
        LIGHTNING_NEAREST_WINDOW_MIN: int
        LIGHTNING_COUNT_WINDOW_MIN: int
        LIGHTNING_STALE_AFTER_S: int
```

In the fallback `__init__`, after `self.WEATHER_CACHE_STALE_SECONDS = _get_env_int(...)` (~L237):

```python
            self.LIGHTNING_ENABLED = _get_env_bool("LIGHTNING_ENABLED", True)
            self.LIGHTNING_COUNT_RADIUS_KM = _get_env_float("LIGHTNING_COUNT_RADIUS_KM", 50.0)
            self.LIGHTNING_NEAREST_WINDOW_MIN = _get_env_int("LIGHTNING_NEAREST_WINDOW_MIN", 30)
            self.LIGHTNING_COUNT_WINDOW_MIN = _get_env_int("LIGHTNING_COUNT_WINDOW_MIN", 60)
            self.LIGHTNING_STALE_AFTER_S = _get_env_int("LIGHTNING_STALE_AFTER_S", 300)
```

(`_get_env_bool` and `_get_env_float` already exist — used by `COOKIE_SECURE` and `SITE_LAT`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Python314/python -m pytest central_server/tests/test_lightning_service.py::test_lightning_settings_defaults -v`
Expected: PASS. (Note: `get_settings` is `lru_cache`d — this test only reads defaults, no env is set for `LIGHTNING_*`, so the cached instance is fine.)

- [ ] **Step 5: Commit**

```bash
git add central_server/config.py central_server/tests/test_lightning_service.py
git commit -m "feat(wxa-004): add LIGHTNING_* config knobs (both Settings classes)"
```

---

## Task 2: Decoder + haversine primitives

**Files:**
- Create: `central_server/services/lightning_service.py`
- Test: `central_server/tests/test_lightning_service.py`

**Interfaces:**
- Produces:
  - `Strike` dataclass with `lat: float`, `lon: float`, `ts: datetime` (naive UTC).
  - `_decompress(b: str) -> str` — community Blitzortung LZW port.
  - `_parse_strike(msg: dict) -> Optional[Strike]` — reads `time` (ns), `lat`, `lon`; `None` on missing/bad fields.
  - `_decode_message(raw: str) -> Optional[Strike]` — `_parse_strike(json.loads(_decompress(raw)))`, `None` on any failure.
  - `_haversine_km(lat1, lon1, lat2, lon2) -> float`.
  - Module constant `SOURCE_LABEL = "Blitzortung.org"`.

- [ ] **Step 1: Write the failing tests**

Append to `central_server/tests/test_lightning_service.py`:

```python
from central_server.services import lightning_service as L


def test_decompress_ascii_identity():
    # All-ASCII input has no back-references -> identity (the common small-JSON case).
    assert L._decompress("hello") == "hello"


def test_decompress_dictionary_branch():
    # "AB" + code-256 char -> dictionary[256] == "AB" -> "AB" + "AB" == "ABAB".
    assert L._decompress("AB" + chr(256)) == "ABAB"


def test_decompress_else_branch():
    # "A" + code-256 char, dict empty -> entry = prev+prev[0] = "AA" -> "A"+"AA" == "AAA".
    assert L._decompress("A" + chr(256)) == "AAA"


def test_parse_strike_ok():
    s = L._parse_strike({"time": 1_700_000_000_000_000_000, "lat": 22.2, "lon": 113.5})
    assert s is not None
    assert abs(s.lat - 22.2) < 1e-9 and abs(s.lon - 113.5) < 1e-9
    assert s.ts.tzinfo is None  # naive UTC


def test_parse_strike_missing_fields_returns_none():
    assert L._parse_strike({"lat": 1.0}) is None
    assert L._parse_strike({"time": 1, "lat": "x", "lon": 2}) is None


def test_decode_message_roundtrip():
    import json
    raw = json.dumps({"time": 1_700_000_000_000_000_000, "lat": 22.2, "lon": 113.5})  # ASCII -> identity
    s = L._decode_message(raw)
    assert s is not None and abs(s.lat - 22.2) < 1e-9


def test_decode_message_garbage_returns_none():
    assert L._decode_message("{not json") is None


def test_haversine_zero_and_known():
    assert L._haversine_km(22.2, 113.5, 22.2, 113.5) < 1e-6
    d = L._haversine_km(22.0, 113.5, 23.0, 113.5)  # ~1 deg latitude
    assert 110 < d < 112
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/c/Python314/python -m pytest central_server/tests/test_lightning_service.py -v -k "decompress or parse_strike or decode_message or haversine"`
Expected: FAIL — `ModuleNotFoundError: No module named 'central_server.services.lightning_service'`.

- [ ] **Step 3: Create the module with the primitives**

Create `central_server/services/lightning_service.py`:

```python
# -*- coding: utf-8 -*-
"""SDPRS Central Server - Lightning proximity service (WXA-004).

A background asyncio task holds a persistent Blitzortung.org WebSocket,
decodes each strike, and keeps recent strikes in an in-memory bounding-box-
filtered deque. `get_lightning()` computes {count, nearest} on demand.

INVARIANT (mirrors weather_service.py): any failure here MUST NOT propagate.
`get_lightning` never raises; the listener never dies silently. A lightning
fault can only ever make the tile show "—".

ToS: SDPRS is a non-commercial / educational deployment. We AGGREGATE strikes
server-side and expose only {count, nearest} — the raw stream is never proxied
to browsers. Source attributed as "Blitzortung.org".
"""
import asyncio
import json
import logging
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..timeutil import utcnow

logger = logging.getLogger("services.lightning")

SOURCE_LABEL = "Blitzortung.org"

# Blitzortung real-time WebSocket hosts (community feed, unauthenticated). The
# subscribe frame starts the strike stream. Hosts are rotated on reconnect.
BLITZORTUNG_WS_HOSTS = [
    "ws1.blitzortung.org",
    "ws3.blitzortung.org",
    "ws7.blitzortung.org",
    "ws8.blitzortung.org",
]
SUBSCRIBE_MSG = '{"a":111}'


@dataclass
class Strike:
    lat: float
    lon: float
    ts: datetime  # naive UTC


def _decompress(b: str) -> str:
    """Community Blitzortung message decompressor (LZW variant over char codes).

    Ported from the widely-circulated reference decoder. All-ASCII input (no
    code >= 256) decompresses to itself, which is the common small-message case.
    """
    if not b:
        return ""
    data = list(b)
    dictionary = {}
    prev = data[0]
    result = [prev]
    code = 256
    for i in range(1, len(data)):
        cc = ord(data[i])
        if cc < 256:
            entry = data[i]
        elif cc in dictionary:
            entry = dictionary[cc]
        else:
            entry = prev + prev[0]
        result.append(entry)
        dictionary[code] = prev + entry[0]
        code += 1
        prev = entry
    return "".join(result)


def _epoch_ns_to_naive_utc(time_ns) -> datetime:
    sec = float(time_ns) / 1e9
    # Naive-UTC per the project contract (never datetime.utcfromtimestamp, which
    # is deprecated on 3.12+; never tz-aware).
    return datetime.fromtimestamp(sec, tz=timezone.utc).replace(tzinfo=None)


def _parse_strike(msg: dict) -> Optional[Strike]:
    """Decoded Blitzortung strike dict -> Strike, or None on missing/bad fields."""
    try:
        lat = float(msg["lat"])
        lon = float(msg["lon"])
        ts = _epoch_ns_to_naive_utc(msg["time"])
        return Strike(lat=lat, lon=lon, ts=ts)
    except (KeyError, TypeError, ValueError):
        return None


def _decode_message(raw: str) -> Optional[Strike]:
    try:
        data = json.loads(_decompress(raw))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return _parse_strike(data)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/c/Python314/python -m pytest central_server/tests/test_lightning_service.py -v -k "decompress or parse_strike or decode_message or haversine"`
Expected: PASS (all 8).

- [ ] **Step 5: Commit**

```bash
git add central_server/services/lightning_service.py central_server/tests/test_lightning_service.py
git commit -m "feat(wxa-004): lightning decoder + haversine primitives"
```

---

## Task 3: `LightningService.get_lightning` — windows, radius, empty states

**Files:**
- Modify: `central_server/services/lightning_service.py`
- Test: `central_server/tests/test_lightning_service.py`

**Interfaces:**
- Consumes: `Strike`, `_haversine_km`, `SOURCE_LABEL`, `utcnow`, `settings.LIGHTNING_*`, `settings.SITE_LAT/SITE_LON`.
- Produces:
  - `LightningService(settings)` with instance state `_strikes: deque`, `_last_msg_at: Optional[datetime]`, `_connected: bool`, `_site_lat/_site_lon/_bbox_deg` and the `LIGHTNING_*` values.
  - `get_lightning(site_lat, site_lon) -> dict` returning `{"count": int|None, "nearest": float|None, "source": "Blitzortung.org"}`. NEVER raises. Unknown (never-connected / stale) -> `{None, None, source}`. Connected-quiet -> `{0, None, source}`. `nearest` rounded to 1 dp.

- [ ] **Step 1: Write the failing tests**

Append to `central_server/tests/test_lightning_service.py`:

```python
import types
from datetime import timedelta
from central_server.timeutil import utcnow


def _svc(**over):
    s = types.SimpleNamespace(
        LIGHTNING_ENABLED=True, LIGHTNING_COUNT_RADIUS_KM=50.0,
        LIGHTNING_NEAREST_WINDOW_MIN=30, LIGHTNING_COUNT_WINDOW_MIN=60,
        LIGHTNING_STALE_AFTER_S=300, SITE_LAT=22.19, SITE_LON=113.55,
    )
    for k, v in over.items():
        setattr(s, k, v)
    return L.LightningService(s)


def _add(svc, lat, lon, age_min):
    svc._strikes.append(L.Strike(lat=lat, lon=lon, ts=utcnow() - timedelta(minutes=age_min)))


def test_getter_unknown_when_never_connected():
    svc = _svc()
    assert svc.get_lightning(22.19, 113.55) == {"count": None, "nearest": None, "source": "Blitzortung.org"}


def test_getter_quiet_when_connected_no_strikes():
    svc = _svc()
    svc._connected = True
    svc._last_msg_at = utcnow()
    assert svc.get_lightning(22.19, 113.55) == {"count": 0, "nearest": None, "source": "Blitzortung.org"}


def test_getter_stale_returns_unknown():
    svc = _svc()
    svc._connected = True
    svc._last_msg_at = utcnow() - timedelta(seconds=400)  # > 300s
    r = svc.get_lightning(22.19, 113.55)
    assert r["count"] is None and r["nearest"] is None


def test_count_radius_edge():
    svc = _svc()
    svc._connected = True
    svc._last_msg_at = utcnow()
    _add(svc, 22.29, 113.55, 5)   # ~11 km  -> inside 50
    _add(svc, 22.79, 113.55, 5)   # ~66 km  -> outside 50 (still inside 1-deg bbox)
    assert svc.get_lightning(22.19, 113.55)["count"] == 1


def test_count_window_edge():
    svc = _svc()
    svc._connected = True
    svc._last_msg_at = utcnow()
    _add(svc, 22.29, 113.55, 30)  # within 60 min
    _add(svc, 22.29, 113.55, 90)  # outside 60 min
    assert svc.get_lightning(22.19, 113.55)["count"] == 1


def test_nearest_window_excludes_old_but_count_includes():
    svc = _svc()
    svc._connected = True
    svc._last_msg_at = utcnow()
    _add(svc, 22.24, 113.55, 40)  # ~5.5 km, 40 min old: counts to hour, outside 30-min proximity
    r = svc.get_lightning(22.19, 113.55)
    assert r["count"] == 1
    assert r["nearest"] is None


def test_nearest_returns_min_distance():
    svc = _svc()
    svc._connected = True
    svc._last_msg_at = utcnow()
    _add(svc, 22.29, 113.55, 5)   # ~11 km
    _add(svc, 22.22, 113.55, 5)   # ~3.3 km
    r = svc.get_lightning(22.19, 113.55)
    assert r["nearest"] is not None and r["nearest"] < 5.0


def test_getter_never_raises_on_corrupt_state():
    svc = _svc()
    svc._connected = True
    svc._last_msg_at = utcnow()
    svc._strikes.append("not-a-strike")  # corrupt entry must not crash the request path
    r = svc.get_lightning(22.19, 113.55)
    assert r["count"] is None  # safe default on internal error
    assert r["source"] == "Blitzortung.org"


def test_getter_falls_back_to_configured_site_when_args_none():
    svc = _svc()
    svc._connected = True
    svc._last_msg_at = utcnow()
    _add(svc, 22.20, 113.55, 5)   # ~1 km from the 22.19/113.55 default
    r = svc.get_lightning(None, None)  # None args -> use configured site
    assert r["count"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/c/Python314/python -m pytest central_server/tests/test_lightning_service.py -v -k "getter or count_ or nearest_"`
Expected: FAIL — `AttributeError: module ... has no attribute 'LightningService'`.

- [ ] **Step 3: Add the class constructor + getter**

Append to `central_server/services/lightning_service.py`:

```python
class LightningService:
    """Singleton-style; the module-level instance is created by
    init_lightning_service()."""

    def __init__(self, settings) -> None:
        self._settings = settings
        self._enabled = getattr(settings, "LIGHTNING_ENABLED", True)
        self._count_radius_km = float(getattr(settings, "LIGHTNING_COUNT_RADIUS_KM", 50.0))
        self._nearest_window_min = int(getattr(settings, "LIGHTNING_NEAREST_WINDOW_MIN", 30))
        self._count_window_min = int(getattr(settings, "LIGHTNING_COUNT_WINDOW_MIN", 60))
        self._stale_after_s = int(getattr(settings, "LIGHTNING_STALE_AFTER_S", 300))
        # Bounding-box center: the configured site. The deque is a cheap
        # pre-filter (~1 deg ~ 111 km); get_lightning uses the request's exact
        # site for correctness. A site relocated >100 km from this center would
        # fall outside the retained box — out of scope for this deployment.
        self._site_lat = float(getattr(settings, "SITE_LAT", 22.19))
        self._site_lon = float(getattr(settings, "SITE_LON", 113.55))
        self._bbox_deg = 1.0
        self._strikes = deque()
        self._last_msg_at: Optional[datetime] = None
        self._connected = False
        self._task: Optional[asyncio.Task] = None
        self._stop: Optional[asyncio.Event] = None

    # ---- public surface ----------------------------------------------------
    def get_lightning(self, site_lat, site_lon) -> dict:
        """Fresh in-memory read; NEVER raises. Returns
        {"count", "nearest", "source"}."""
        try:
            if site_lat is None or site_lon is None:
                site_lat, site_lon = self._site_lat, self._site_lon
            now = utcnow()
            if self._last_msg_at is None or \
                    (now - self._last_msg_at).total_seconds() > self._stale_after_s:
                return {"count": None, "nearest": None, "source": SOURCE_LABEL}
            count_cutoff = now - timedelta(minutes=self._count_window_min)
            near_cutoff = now - timedelta(minutes=self._nearest_window_min)
            count = 0
            nearest = None
            for s in list(self._strikes):
                d = _haversine_km(site_lat, site_lon, s.lat, s.lon)
                if s.ts >= count_cutoff and d <= self._count_radius_km:
                    count += 1
                if s.ts >= near_cutoff and (nearest is None or d < nearest):
                    nearest = d
            return {
                "count": count,
                "nearest": round(nearest, 1) if nearest is not None else None,
                "source": SOURCE_LABEL,
            }
        except Exception:
            logger.exception("get_lightning failed; returning safe default")
            return {"count": None, "nearest": None, "source": SOURCE_LABEL}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/c/Python314/python -m pytest central_server/tests/test_lightning_service.py -v -k "getter or count_ or nearest_"`
Expected: PASS (all 9).

- [ ] **Step 5: Commit**

```bash
git add central_server/services/lightning_service.py central_server/tests/test_lightning_service.py
git commit -m "feat(wxa-004): LightningService.get_lightning windows/radius/empty-states"
```

---

## Task 4: Listener (`_on_message`/`_prune`/`_in_bbox`), lifecycle, singleton, dependency

**Files:**
- Modify: `central_server/services/lightning_service.py`
- Modify: `central_server/requirements.txt`
- Test: `central_server/tests/test_lightning_service.py`

**Interfaces:**
- Consumes: `_decode_message`, the deque/state from Task 3.
- Produces:
  - `LightningService._in_bbox(lat, lon) -> bool`, `_prune(now=None) -> None`, `_on_message(raw: str) -> None` (updates `_last_msg_at`/`_connected` for EVERY message, then decode→bbox-filter→append→prune), `async start()`, `async stop()`, `async _run()` (socket loop; not CI-tested).
  - `init_lightning_service(settings) -> LightningService`, `get_lightning_service() -> Optional[LightningService]`.
  - `requirements.txt` line `websockets>=12.0`.

- [ ] **Step 1: Write the failing tests**

Append to `central_server/tests/test_lightning_service.py`:

```python
import asyncio
from datetime import datetime, timezone


def test_on_message_updates_liveness_even_when_filtered():
    svc = _svc()
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)  # tz-aware in TEST is fine
    far = json.dumps({"time": now_ns, "lat": 40.0, "lon": 113.5})  # outside 1-deg bbox
    svc._on_message(far)
    assert len(svc._strikes) == 0            # filtered out
    assert svc._connected is True            # but the message still marks the feed live
    assert svc._last_msg_at is not None


def test_on_message_appends_in_bbox_strike():
    svc = _svc()
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    near = json.dumps({"time": now_ns, "lat": 22.2, "lon": 113.5})  # ~1 km from site
    svc._on_message(near)
    assert len(svc._strikes) == 1


def test_on_message_swallows_garbage():
    svc = _svc()
    svc._on_message("{bad json")   # must not raise; still marks liveness
    assert len(svc._strikes) == 0
    assert svc._connected is True


def test_prune_drops_strikes_older_than_count_window():
    svc = _svc()
    _add(svc, 22.2, 113.5, 90)   # 90 min old -> beyond 60-min window
    _add(svc, 22.2, 113.5, 10)   # fresh
    svc._prune()
    assert len(svc._strikes) == 1


def test_start_is_noop_when_disabled():
    svc = _svc(LIGHTNING_ENABLED=False)
    asyncio.run(svc.start())
    assert svc._task is None


def test_singleton_init_and_get():
    s = types.SimpleNamespace(
        LIGHTNING_ENABLED=True, LIGHTNING_COUNT_RADIUS_KM=50.0,
        LIGHTNING_NEAREST_WINDOW_MIN=30, LIGHTNING_COUNT_WINDOW_MIN=60,
        LIGHTNING_STALE_AFTER_S=300, SITE_LAT=22.19, SITE_LON=113.55,
    )
    svc = L.init_lightning_service(s)
    assert L.get_lightning_service() is svc
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/c/Python314/python -m pytest central_server/tests/test_lightning_service.py -v -k "on_message or prune or start_is_noop or singleton"`
Expected: FAIL — `AttributeError: 'LightningService' object has no attribute '_on_message'` (and no `init_lightning_service`).

- [ ] **Step 3: Add listener, lifecycle, and singleton**

Append the listener/lifecycle methods inside `class LightningService` (after `get_lightning`):

```python
    # ---- listener ----------------------------------------------------------
    def _in_bbox(self, lat: float, lon: float) -> bool:
        return (abs(lat - self._site_lat) <= self._bbox_deg and
                abs(lon - self._site_lon) <= self._bbox_deg)

    def _prune(self, now: Optional[datetime] = None) -> None:
        now = now or utcnow()
        cutoff = now - timedelta(minutes=self._count_window_min)
        while self._strikes and isinstance(self._strikes[0], Strike) and self._strikes[0].ts < cutoff:
            self._strikes.popleft()

    def _on_message(self, raw: str) -> None:
        """Handle one received frame. Marks the feed live for EVERY message
        (strike or keep-alive) so a genuinely quiet sky stays 'connected';
        only decoded, in-box strikes are retained. Never raises."""
        self._last_msg_at = utcnow()
        self._connected = True
        try:
            strike = _decode_message(raw)
            if strike is None:
                return
            if not self._in_bbox(strike.lat, strike.lon):
                return
            self._strikes.append(strike)
            self._prune()
        except Exception:
            logger.exception("lightning _on_message failed")

    # ---- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if not self._enabled:
            logger.info("Lightning service disabled (LIGHTNING_ENABLED=false)")
            return
        if self._task is not None:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run())
        logger.info("Lightning service started (Blitzortung.org)")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._task = None

    async def _run(self) -> None:
        """Persistent WebSocket with exponential-backoff reconnect. Not
        exercised in CI (no live socket); every failure is caught + retried so
        nothing propagates."""
        import websockets  # local import: keeps module importable if the dep is absent
        attempt = 0
        while self._stop is None or not self._stop.is_set():
            host = BLITZORTUNG_WS_HOSTS[attempt % len(BLITZORTUNG_WS_HOSTS)]
            url = f"wss://{host}/"
            try:
                async with websockets.connect(url, open_timeout=10) as ws:
                    await ws.send(SUBSCRIBE_MSG)
                    attempt = 0
                    self._connected = True
                    self._last_msg_at = utcnow()
                    async for raw in ws:
                        if self._stop is not None and self._stop.is_set():
                            break
                        self._on_message(raw if isinstance(raw, str) else raw.decode("utf-8", "ignore"))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Lightning WS disconnected ({host}): {e}")
            attempt += 1
            backoff = min(60, 2 ** min(attempt, 6))
            if self._stop is None:
                await asyncio.sleep(backoff)
            else:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass


_service: Optional[LightningService] = None


def init_lightning_service(settings) -> LightningService:
    global _service
    _service = LightningService(settings)
    return _service


def get_lightning_service() -> Optional[LightningService]:
    return _service
```

- [ ] **Step 4: Declare the dependency**

In `central_server/requirements.txt`, under the Web Framework section (after `fastapi>=0.104.0`), add:

```
# WXA-004 lightning: Blitzortung WebSocket client. Already present transitively
# via uvicorn[standard]; declared directly so the import is an honest dependency.
websockets>=12.0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/c/Python314/python -m pytest central_server/tests/test_lightning_service.py -v`
Expected: PASS (entire file — Tasks 1-4 tests, ~25 assertions).

- [ ] **Step 6: Commit**

```bash
git add central_server/services/lightning_service.py central_server/requirements.txt central_server/tests/test_lightning_service.py
git commit -m "feat(wxa-004): lightning listener, lifecycle, singleton + websockets dep"
```

---

## Task 5: Wire the service into the app lifespan

**Files:**
- Modify: `central_server/main.py` (import ~L36; startup block after the weather block ~L127; shutdown block after the weather stop ~L142)
- Test: `central_server/tests/test_lightning_lifespan.py` (**NEW** — its own file, no ordering coupling with Task 6)

**Interfaces:**
- Consumes: `init_lightning_service`, `get_lightning_service` from Task 4.
- Produces: `app.state.lightning_service` set on startup; `stop()` awaited on shutdown.

- [ ] **Step 1: Write the failing lifespan-wiring test**

The wiring is verified through a real app boot (TestClient triggers the lifespan — the established pattern in this suite, e.g. `test_alerts_api.py`). `init_lightning_service` is monkeypatched to a stub so NO live WebSocket is opened in CI. Create `central_server/tests/test_lightning_lifespan.py`:

```python
# -*- coding: utf-8 -*-
"""WXA-004 lifespan-wiring test. Boots the app via TestClient (triggers the
lifespan) with init_lightning_service stubbed so no live socket opens in CI."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def test_lifespan_wires_lightning(monkeypatch):
    import main
    from fastapi.testclient import TestClient

    started = {"n": 0}

    class _StubSvc:
        def __init__(self):
            self._task = None
        async def start(self):
            started["n"] += 1
        async def stop(self):
            pass

    # Patch the name bound INTO main (main did `from ...lightning_service import
    # init_lightning_service`), so the lifespan builds our stub — no real socket.
    monkeypatch.setattr(main, "init_lightning_service", lambda settings: _StubSvc())

    with TestClient(main.app):
        assert main.app.state.lightning_service is not None
    assert started["n"] == 1  # start() was awaited exactly once during startup
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest central_server/tests/test_lightning_lifespan.py -v`
Expected: FAIL — `AttributeError: 'State' object has no attribute 'lightning_service'` (lifespan doesn't set it yet), or `AttributeError: module 'main' has no attribute 'init_lightning_service'` (import not added).

- [ ] **Step 3: Add the import**

In `central_server/main.py`, beside the weather-service import (~L36):

```python
from .services.lightning_service import init_lightning_service, get_lightning_service
```

- [ ] **Step 4: Add the startup block**

Immediately after the weather-service `try/except` block (after `app.state.weather_service = None`, ~L127):

```python
    # Lightning service (WXA-004). Self-degrading; start() is a no-op when
    # LIGHTNING_ENABLED is false. A failure here must never block startup.
    try:
        lightning_svc = init_lightning_service(settings)
        await lightning_svc.start()
        app.state.lightning_service = lightning_svc
    except Exception as e:
        logger.warning(f"Failed to start lightning service: {e}")
        app.state.lightning_service = None
```

- [ ] **Step 5: Add the shutdown block**

Immediately after the weather-service `stop()` block (~L142), before the retention-scheduler shutdown:

```python
    # Stop lightning service
    lightning_svc = get_lightning_service()
    if lightning_svc is not None:
        try:
            await lightning_svc.stop()
        except Exception as e:
            logger.warning(f"Lightning service shutdown error: {e}")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `/c/Python314/python -m pytest central_server/tests/test_lightning_lifespan.py -v`
Expected: PASS. (`app.state.lightning_service` is the stub; `start()` awaited once. The shutdown block calls the real `get_lightning_service()`, which is `None` here since init was stubbed — the `is not None` guard makes that a safe no-op.)

- [ ] **Step 7: Commit**

```bash
git add central_server/main.py central_server/tests/test_lightning_lifespan.py
git commit -m "feat(wxa-004): wire lightning service into app lifespan"
```

---

## Task 6: Endpoint overlay (`/api/weather/current`)

**Files:**
- Modify: `central_server/api/weather.py` (imports ~L18-23; new `_overlay_lightning` helper; call it in `get_current_weather` ~L259-267)
- Test: `central_server/tests/test_lightning_endpoint.py`

**Interfaces:**
- Consumes: `get_lightning_service` (Task 4), `get_weather_config` (`..database`), `get_settings` (`..config`).
- Produces: `_overlay_lightning(payload: dict) -> None` — mutates `payload` in place, setting `payload["lightning"] = {"count", "nearest"}` and, when the service exists, `payload["sources"]["lightning"] = <source>`. `get_current_weather` calls it after `_serialize(cur)`.

- [ ] **Step 1: Write the failing tests**

Create `central_server/tests/test_lightning_endpoint.py`:

```python
# -*- coding: utf-8 -*-
"""WXA-004 endpoint overlay tests. Mirrors the direct-coroutine-call idiom of
test_weather_throttle_newapi002.py (no TestClient)."""
import sys
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server.api import weather


class _StubLightning:
    def __init__(self, payload):
        self._payload = payload
    def get_lightning(self, lat, lon):
        return dict(self._payload)  # fresh copy each call


def test_overlay_sets_lightning_and_source(monkeypatch):
    monkeypatch.setattr(weather, "get_lightning_service",
                        lambda: _StubLightning({"count": 3, "nearest": 5.0, "source": "Blitzortung.org"}))
    monkeypatch.setattr(weather, "get_weather_config", lambda: {"site_lat": 22.19, "site_lon": 113.55})
    payload = {"sources": {"temperature_c": "SMG"}}
    weather._overlay_lightning(payload)
    assert payload["lightning"] == {"count": 3, "nearest": 5.0}
    assert payload["sources"]["lightning"] == "Blitzortung.org"


def test_overlay_bare_null_when_service_absent(monkeypatch):
    monkeypatch.setattr(weather, "get_lightning_service", lambda: None)
    payload = {"sources": {"temperature_c": "SMG"}}
    weather._overlay_lightning(payload)
    assert payload["lightning"] == {"count": None, "nearest": None}
    assert "lightning" not in payload["sources"]  # bare "—", no source label


def test_overlay_is_fresh_each_call(monkeypatch):
    stub = _StubLightning({"count": 1, "nearest": 9.0, "source": "Blitzortung.org"})
    monkeypatch.setattr(weather, "get_lightning_service", lambda: stub)
    monkeypatch.setattr(weather, "get_weather_config", lambda: {"site_lat": 22.19, "site_lon": 113.55})
    p1 = {}
    weather._overlay_lightning(p1)
    stub._payload = {"count": 2, "nearest": 4.0, "source": "Blitzortung.org"}  # deque changed
    p2 = {}
    weather._overlay_lightning(p2)
    assert p1["lightning"]["count"] == 1 and p2["lightning"]["count"] == 2  # not frozen


def test_overlay_falls_back_to_settings_site(monkeypatch):
    seen = {}
    class _Rec:
        def get_lightning(self, lat, lon):
            seen["lat"], seen["lon"] = lat, lon
            return {"count": 0, "nearest": None, "source": "Blitzortung.org"}
    monkeypatch.setattr(weather, "get_lightning_service", lambda: _Rec())
    monkeypatch.setattr(weather, "get_weather_config", lambda: {"site_lat": None, "site_lon": None})
    weather._overlay_lightning({})
    s = weather.get_settings()
    assert seen["lat"] == s.SITE_LAT and seen["lon"] == s.SITE_LON
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/c/Python314/python -m pytest central_server/tests/test_lightning_endpoint.py -v`
Expected: FAIL — `AttributeError: module 'central_server.api.weather' has no attribute '_overlay_lightning'` (and no `get_lightning_service`/`get_settings` on the module).

- [ ] **Step 3: Add imports + helper + wire into the handler**

In `central_server/api/weather.py`, extend the imports (~L18-23):

```python
from ..auth import get_current_user
from ..config import get_settings
from ..database import get_weather_config, set_weather_config
from ..services.weather_service import (
    get_weather_service, update_weather_location, refresh_weather_now,
    _list_hko_temp_stations, SMG_XML_URL, HTTP_TIMEOUT_S,
)
from ..services.lightning_service import get_lightning_service
```

Add the helper above `get_current_weather` (near ~L259):

```python
def _overlay_lightning(payload: Dict[str, Any]) -> None:
    """Overlay a FRESH lightning read onto a serialized weather payload.

    Never raises (get_lightning is safe; this only adds dict keys). Service
    absent -> bare null-shape with NO sources.lightning (tile shows a bare
    "—"); service present -> {count, nearest} + sources.lightning label."""
    lsvc = get_lightning_service()
    if lsvc is None:
        payload["lightning"] = {"count": None, "nearest": None}
        return
    cfg = get_weather_config() or {}
    settings = get_settings()
    lat = cfg.get("site_lat") if cfg.get("site_lat") is not None else settings.SITE_LAT
    lon = cfg.get("site_lon") if cfg.get("site_lon") is not None else settings.SITE_LON
    ll = lsvc.get_lightning(lat, lon)
    payload["lightning"] = {"count": ll.get("count"), "nearest": ll.get("nearest")}
    payload.setdefault("sources", {})["lightning"] = ll.get("source", "Blitzortung.org")
```

In `get_current_weather`, overlay before returning:

```python
@router.get("/weather/current")
async def get_current_weather(request: Request, user: str = Depends(get_current_user)) -> Dict[str, Any]:
    svc = get_weather_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Weather service not enabled")
    cur = svc.get_current()
    if cur is None:
        raise HTTPException(status_code=503, detail="Weather data not available yet")
    payload = _serialize(cur)
    _overlay_lightning(payload)
    return payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/c/Python314/python -m pytest central_server/tests/test_lightning_endpoint.py -v`
Expected: PASS (all 4).

- [ ] **Step 5: Regression — weather suites still green**

Run: `/c/Python314/python -m pytest central_server/tests/test_weather_throttle_newapi002.py central_server/tests/test_weather_config_persistence.py -v`
Expected: PASS (the overlay only adds keys; existing weather behavior unchanged).

- [ ] **Step 6: Commit**

```bash
git add central_server/api/weather.py central_server/tests/test_lightning_endpoint.py
git commit -m "feat(wxa-004): overlay fresh lightning read on /api/weather/current"
```

---

## Task 7: SPA wiring + render test

**Files:**
- Modify: `central_server/static/spa/api.jsx` (L533 hardcoded null; `SDPRS_API` export ~L1440-1451)
- Modify: `central_server/static/spa/pages/weather.jsx` (comment L684-686 -> `SourceChip`)
- Create: `tools/spa/render_extra/wxa004-lightning.js`

**Interfaces:**
- Consumes: the backend `/api/weather/current` payload's `lightning` + `sources.lightning` (Task 6).
- Produces: `window.SDPRS_API.loadWeather` exposed; the tile renders the mapped `lightning`.

- [ ] **Step 1: Write the failing render test**

Create `tools/spa/render_extra/wxa004-lightning.js`:

```js
// WXA-004: lightning data flows from the backend through mapWeather to the tile.
// Owner: feat/wxa-004-lightning. api.jsx is an IIFE, so mapWeather/loadWeather
// are only observable via the published window.SDPRS_API surface — this suite
// drives SDPRS_API.loadWeather over a stubbed window.fetch.
module.exports = [
  {
    name: 'WXA-004                     api.jsx lightning maps through loadWeather',
    target: 'api.jsx',
    deps: ['icons.jsx', 'data.jsx'],
    body: `
      const _res = (data, opts) => {
        const o = opts || {};
        const status = o.status != null ? o.status : 200;
        return Promise.resolve({
          ok: status >= 200 && status < 300,
          status,
          headers: { get: (k) => (String(k).toLowerCase() === 'content-type' ? 'application/json' : null) },
          json: () => Promise.resolve(data),
          text: () => Promise.resolve(typeof data === 'string' ? data : JSON.stringify(data)),
        });
      };
      window.fetch = (url) => {
        const u = String(url);
        if (u.indexOf('/api/weather/current') !== -1) {
          return _res({
            temperature_c: 20, is_stale: false, source: 'SMG',
            sources: { temperature_c: 'SMG', lightning: 'Blitzortung.org' },
            lightning: { count: 3, nearest: 5 },
          });
        }
        if (u.indexOf('/api/weather/forecast') !== -1) return _res({ buckets: [] });
        if (u.indexOf('/api/weather/typhoon') !== -1) return _res(null);
        return _res({}, { status: 404 });
      };
      A('WXA-004 SDPRS_API.loadWeather is published', typeof window.SDPRS_API.loadWeather === 'function', typeof window.SDPRS_API.loadWeather);
      const w = await window.SDPRS_API.loadWeather();
      A('WXA-004 lightning.count flows from backend (was hardcoded null)', w.lightning && w.lightning.count === 3, JSON.stringify(w.lightning));
      A('WXA-004 lightning.nearest flows from backend', w.lightning && w.lightning.nearest === 5, JSON.stringify(w.lightning));
      A('WXA-004 lightning source flows via sources.lightning', w.sources && w.sources.lightning === 'Blitzortung.org', JSON.stringify(w.sources));
    `,
  },
];
```

- [ ] **Step 2: Run the render suite to verify it fails**

Run: `node tools/spa/render_tests.js`
Expected: FAIL — the `loadWeather is published` assertion fails (not on the export yet) AND `lightning.count === 3` fails (L533 still hardcodes `{count:null,nearest:null}`).

- [ ] **Step 3: Expose `loadWeather` on the SPA public surface**

In `central_server/static/spa/api.jsx`, add `loadWeather` to the `window.SDPRS_API` object (~L1441). Change:

```js
  window.SDPRS_API = {
    loadInitial, refreshLive, markSeen,
```

to:

```js
  window.SDPRS_API = {
    loadInitial, refreshLive, loadWeather, markSeen,
```

- [ ] **Step 4: Replace the hardcoded lightning null in `mapWeather`**

In `central_server/static/spa/api.jsx` at L533, change:

```js
      lightning: { count: null, nearest: null },
```

to:

```js
      // WXA-004: flow the backend's aggregated lightning through (was
      // permanently hardcoded null). `sources.lightning` rides through the
      // `sources: backendSources` line below — no separate wiring needed.
      lightning: current.lightning || { count: null, nearest: null },
```

(Leave the L474 `!current` fallback null unchanged — "—" is correct when the endpoint is unreachable.)

- [ ] **Step 5: Show the source chip in the tile**

In `central_server/static/spa/pages/weather.jsx`, replace the L684-686 comment:

```js
            {/* No source label — lightning has no backend source yet
                (rendered as null in api.jsx mapWeather). Reserved for a
                future Blitzortung / HKO thunderstorm-warning integration. */}
```

with:

```js
            {/* WXA-004: Blitzortung source, shown like every sibling tile.
                SourceChip renders null on a falsy label, so this stays hidden
                until the backend supplies sources.lightning. */}
            <SourceChip label={sources.lightning}/>
```

- [ ] **Step 6: Run the render suite to verify it passes**

Run: `node tools/spa/render_tests.js`
Expected: PASS — all four WXA-004 assertions green.

- [ ] **Step 7: Full blocking SPA gate**

Run: `node tools/spa/run_all.js`
Expected: ends with "All blocking SPA checks passed." (If jsdom is missing in a fresh worktree, junction `tools/spa/node_modules` from the main checkout — see the SPA render-test notes.)

- [ ] **Step 8: Commit**

```bash
git add central_server/static/spa/api.jsx central_server/static/spa/pages/weather.jsx tools/spa/render_extra/wxa004-lightning.js
git commit -m "feat(wxa-004): map lightning through SPA + show Blitzortung source chip"
```

---

## Task 8: Full-suite verification + optional live smoke

**Files:** none (verification only)

- [ ] **Step 1: Run both new Python suites + the touched weather suites**

Run:
```bash
/c/Python314/python -m pytest central_server/tests/test_lightning_service.py central_server/tests/test_lightning_endpoint.py central_server/tests/test_lightning_lifespan.py central_server/tests/test_weather_throttle_newapi002.py central_server/tests/test_weather_config_persistence.py -v
```
Expected: all PASS.

- [ ] **Step 2: Run the full blocking SPA gate**

Run: `node tools/spa/run_all.js`
Expected: "All blocking SPA checks passed."

- [ ] **Step 3: Confirm no banned strings entered the diff**

Run: `git diff main --unified=0 | grep -nE "Msc@2333|MSC-Person|broker\\.emqx\\.io" && echo "BANNED STRING PRESENT — STOP" || echo "clean"`
Expected: `clean`.

- [ ] **Step 4 (optional, manual — NOT CI): live-feed smoke**

With `LIGHTNING_ENABLED=true` and network access, run the server and watch for `Lightning service started (Blitzortung.org)`, then confirm the tile populates over a few minutes during real activity. Capture one real frame as a regression fixture (`central_server/tests/fixtures/blitzortung_frame.txt`) if convenient — this is the deeper decoder check the CI tests deliberately don't require (spec §10 Q1). This step does NOT gate completion.

- [ ] **Step 5: Report for review**

Do NOT merge/push. Summarize what shipped on `feat/wxa-004-lightning-2026-08-05` and wait for the user's explicit "approved" before any integration to `origin/main`.

---

## Self-Review

**Spec coverage (each spec section → task):**
- §1 Problem (dead tile / hardcoded null) → Tasks 6+7 replace the nulls.
- §3 Data model (`lightning:{count,nearest}` + `sources.lightning`; two empty states; 50 km / 60 min count, 30 min nearest, 300 s stale) → Task 3 getter (windows/radius/empty-states) + Task 6 wire + Task 1 knobs.
- §4 New module (listener, deque, bbox, prune, pure getter, never-raises, singleton) → Tasks 2+3+4.
- §4c / §5(lifespan) wiring → Task 5.
- §5a endpoint overlay (fresh, service-absent bare-null) → Task 6.
- §5b/§5c SPA (L533, expose loadWeather, SourceChip) → Task 7.
- §5d dependency (`websockets>=12.0`) → Task 4 Step 4.
- §6 error handling (every failure → safe "—") → Task 3 never-raises test, Task 4 `_on_message` swallow test, Task 6 service-absent test.
- §7 config (5 knobs, both classes) → Task 1.
- §8 testing (decoder deterministic, radius/window edges, empty states, never-raises, endpoint merge, flag-off, SPA render) → distributed across Tasks 1-7; item 10 flag-off → Task 4 `test_start_is_noop_when_disabled` + Task 6 `test_overlay_bare_null_when_service_absent`.
- §9 rollout (flag default-on, self-degrading, backward compat) → Task 1 default + Task 5 disabled-boot smoke + Task 6 fallback.

**Placeholder scan:** every code/test step contains real content; no "TBD"/"add error handling"/"similar to Task N". The one deliberately-deferred item (real-frame fixture) is explicitly marked optional/non-CI in Task 8 Step 4 and spec §8.1.

**Type consistency:** `get_lightning` returns `{"count","nearest","source"}` in every task that touches it (Task 3 defines, Task 6 `_overlay_lightning` consumes `.get("count")/.get("nearest")/.get("source")`, Task 6 test stub returns the same three keys). `_overlay_lightning(payload)` mutates in place (defined Task 6, no other caller). `Strike(lat,lon,ts)` fields consistent across Tasks 2/3/4. `loadWeather` is the exact SPA export name added in Task 7 and referenced by the Task 7 render test. `SOURCE_LABEL == "Blitzortung.org"` matches the SPA test's expected string and the endpoint default.
