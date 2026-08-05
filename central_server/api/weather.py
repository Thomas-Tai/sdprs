# -*- coding: utf-8 -*-
# SDPRS Central Server - Weather API endpoints (Open-Meteo + CWA)
# See Plan/weather_integration.md sections 8 + 10.
#
# All endpoints session-auth, all return safe JSON (never 5xx from missing
# cache; that's 503 with a clear "service not initialised" body so the UI
# can show a placeholder rather than break).

import logging
import threading
import time
from dataclasses import asdict
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..config import get_settings
from ..database import get_weather_config, set_weather_config
from ..services.weather_service import (
    get_weather_service, update_weather_location, refresh_weather_now,
    _list_hko_temp_stations, SMG_XML_URL, HTTP_TIMEOUT_S,
)
from ..services.lightning_service import get_lightning_service
import httpx
import xml.etree.ElementTree as ET

logger = logging.getLogger("api.weather")

router = APIRouter()


# NEW-API-002: throttle the 3 routes that proxy live external fetches so an
# authenticated session cannot hammer the SMG / HKO providers (external-IP ban
# / DoS-by-proxy). Per-process, in-memory, monotonic-clock min-interval gates —
# mirrors the login-throttle idiom in main.py (per-process, resets on restart;
# acceptable for the single-worker uvicorn MVP).
_STATION_LIST_MIN_INTERVAL_S = 60.0   # station dropdowns are human-driven from the settings pane
_REFRESH_MIN_INTERVAL_S = 10.0        # matches the ~10s background weather tick

_MONO = time.monotonic  # module-level indirection so tests can control the clock


class _ThrottleGate:
    """Per-route min-interval gate with an optional last-good cached payload.

    Throttles BOTH the success and failure paths: once a fetch is attempted,
    no further external call is allowed for `min_interval` seconds. Callers
    within the window get the last GOOD cached payload (or None if nothing has
    succeeded yet). The attempt timestamp is reserved optimistically inside the
    lock to prevent a thundering herd on the exact expiry boundary.
    """

    def __init__(self, min_interval: float):
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last_attempt = None  # monotonic seconds; None = never attempted
        self._cached = None        # last GOOD payload

    def acquire(self):
        """Return (fetch_allowed: bool, cached_payload). If fetch_allowed is
        True the caller MUST perform the fetch and, on success, call store().
        If False, the caller should serve cached_payload (or its own empty
        default when cached_payload is None)."""
        now = _MONO()
        with self._lock:
            if self._last_attempt is not None and (now - self._last_attempt) < self._min_interval:
                return False, self._cached
            self._last_attempt = now
            return True, self._cached

    def store(self, payload):
        """Record a GOOD payload as the last-known-good cache."""
        with self._lock:
            self._cached = payload

    def reset(self):
        """Test seam: clear the gate to its never-attempted state."""
        with self._lock:
            self._last_attempt = None
            self._cached = None


_smg_gate = _ThrottleGate(_STATION_LIST_MIN_INTERVAL_S)
_hko_gate = _ThrottleGate(_STATION_LIST_MIN_INTERVAL_S)
_refresh_gate = _ThrottleGate(_REFRESH_MIN_INTERVAL_S)


class WeatherConfigPayload(BaseModel):
    site_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    site_lon: Optional[float] = Field(default=None, ge=-180, le=180)
    station_name: Optional[str] = Field(default=None, max_length=50)
    # Option C (2026-07-19): multi-source selectors.
    smg_station: Optional[str] = Field(default=None, max_length=100,
        description="SMG Macau station name (Chinese) selected from /api/weather/smg/stations")
    hko_station: Optional[str] = Field(default=None, max_length=100,
        description="HKO temperature station name (English) selected from /api/weather/hko/stations")
    fallback_provider: Optional[str] = Field(default=None, pattern="^(hko|openmeteo|both)$",
        description="When SMG primary station is unavailable / lacks a field, which fallback to prefer")


def _serialize(obj: Any) -> Any:
    # Recursively dataclass -> dict, and datetime -> ISO. The WeatherService
    # dataclasses are flat so this is sufficient.
    from datetime import datetime as _dt
    if obj is None:
        return None
    if isinstance(obj, _dt):
        return obj.isoformat()
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


@router.get("/weather/config")
async def get_weather_config_api(
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Item 9: get current weather location configuration."""
    return get_weather_config()


@router.put("/weather/config")
async def set_weather_config_api(
    request: Request,
    payload: WeatherConfigPayload,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Item 9 + Option C (2026-07-19): update weather location + multi-source
    selectors.

    API-F5/WHA-M3 fix (2026-07-20): this used to write `payload` straight
    through, so any field the caller didn't supply (Pydantic default: None)
    NULLed the stored value — e.g. the SPA settings form never round-tripped
    `station_name`, so every save silently wiped it. This is a partial
    update: a field is only overwritten when the payload actually supplies a
    non-null value; omitted fields keep whatever is already in the DB. Takes
    effect on the NEXT weather tick (~10s worst case) — no server restart
    needed since `_tick` reads the config row on every iteration."""
    existing = get_weather_config()

    def _merged(new_val: Any, key: str) -> Any:
        return new_val if new_val is not None else existing.get(key)

    site_lat = _merged(payload.site_lat, "site_lat")
    site_lon = _merged(payload.site_lon, "site_lon")
    station_name = _merged(payload.station_name, "station_name")
    smg_station = _merged(payload.smg_station, "smg_station")
    hko_station = _merged(payload.hko_station, "hko_station")
    fallback_provider = _merged(payload.fallback_provider, "fallback_provider")

    ok = set_weather_config(
        site_lat, site_lon, station_name,
        smg_station=smg_station,
        hko_station=hko_station,
        fallback_provider=fallback_provider,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update weather config")
    # Update the running weather service if available. Use the merged
    # values, not the raw payload — otherwise an omitted lat/lon in the
    # payload would blank out the live service's location even though the
    # DB row (and the merge above) correctly kept the existing coordinates.
    svc = get_weather_service()
    if svc:
        update_weather_location(site_lat, site_lon)
    return {
        "ok": True,
        "site_lat": site_lat, "site_lon": site_lon,
        "station_name": station_name,
        "smg_station": smg_station,
        "hko_station": hko_station,
        "fallback_provider": fallback_provider,
    }


async def _fetch_smg_stations() -> list:
    """Fetch + parse the SMG XML feed and return station names. Raises on any
    failure (HTTP, XML parse) — the caller is responsible for catching and
    converting to a safe empty-list response."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        r = await client.get(SMG_XML_URL, timeout=HTTP_TIMEOUT_S)
        r.raise_for_status()
        r.encoding = 'utf-8'
        root = ET.fromstring(r.text)
        stations = []
        for station in root.findall('.//WeatherReport/station'):
            name = (station.findtext('stationname') or '').strip()
            if name:
                stations.append(name)
    return stations


@router.get("/weather/smg/stations")
async def list_smg_stations(
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """List SMG Macau station names currently in the live XML feed.
    Used by the settings pane's SMG-station dropdown. Fetches SMG XML
    on-demand (cache-miss OK — call frequency is human-driven from the
    settings pane, not the 10s tick). Returns empty list on failure so
    the UI can fall back to a hardcoded default list.

    NEW-API-002: results are throttled/cached for
    _STATION_LIST_MIN_INTERVAL_S to protect the external SMG feed from an
    authenticated session hammering this endpoint."""
    allowed, cached = _smg_gate.acquire()
    if not allowed:
        return cached if cached is not None else {"stations": [], "source_reachable": False}
    try:
        stations = await _fetch_smg_stations()
        payload = {"stations": stations, "source_reachable": True}
        _smg_gate.store(payload)
        return payload
    except Exception as e:
        logger.warning(f"SMG station list fetch failed: {e}")
        return {"stations": [], "source_reachable": False}


async def _fetch_hko_stations() -> list:
    """Fetch the HKO rhrread feed and return temperature station names.
    Raises on any failure — the caller is responsible for catching and
    converting to a safe empty-list response."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        stations = await _list_hko_temp_stations(client)
    return stations


@router.get("/weather/hko/stations")
async def list_hko_stations(
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """List HKO Hong Kong temperature station names currently in the
    live rhrread feed (~26 stations). Same on-demand pattern as SMG.

    NEW-API-002: results are throttled/cached for
    _STATION_LIST_MIN_INTERVAL_S to protect the external HKO feed from an
    authenticated session hammering this endpoint."""
    allowed, cached = _hko_gate.acquire()
    if not allowed:
        return cached if cached is not None else {"stations": [], "source_reachable": False}
    try:
        stations = await _fetch_hko_stations()
        payload = {"stations": stations, "source_reachable": len(stations) > 0}
        if payload["source_reachable"]:
            _hko_gate.store(payload)
        return payload
    except Exception as e:
        logger.warning(f"HKO station list fetch failed: {e}")
        return {"stations": [], "source_reachable": False}


def _overlay_lightning(payload: Dict[str, Any]) -> None:
    """Overlay a FRESH lightning read onto a serialized weather payload.

    Never raises: service absent -> bare null-shape with NO sources.lightning
    (tile shows a bare "—"); service present -> {count, nearest} +
    sources.lightning label, with the config/getter branch guarded so a
    transient DB error also degrades to the bare null-shape instead of
    propagating out and 500-ing the whole weather endpoint."""
    lsvc = get_lightning_service()
    if lsvc is None:
        payload["lightning"] = {"count": None, "nearest": None}
        return
    try:
        cfg = get_weather_config() or {}
        settings = get_settings()
        lat = cfg.get("site_lat") if cfg.get("site_lat") is not None else settings.SITE_LAT
        lon = cfg.get("site_lon") if cfg.get("site_lon") is not None else settings.SITE_LON
        ll = lsvc.get_lightning(lat, lon)
        payload["lightning"] = {"count": ll.get("count"), "nearest": ll.get("nearest")}
        src = ll.get("source")
        if src:
            payload.setdefault("sources", {})["lightning"] = src
    except Exception as e:
        logger.warning(f"lightning overlay failed: {e}")
        payload["lightning"] = {"count": None, "nearest": None}


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


@router.get("/weather/forecast")
async def get_weather_forecast(request: Request, user: str = Depends(get_current_user)) -> Dict[str, Any]:
    svc = get_weather_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Weather service not enabled")
    return {"buckets": _serialize(svc.get_forecast_36h())}


@router.get("/weather/typhoon")
async def get_typhoon(request: Request, user: str = Depends(get_current_user)) -> Any:
    svc = get_weather_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Weather service not enabled")
    # Returning null when no warning is intentional — operator UI uses the
    # null check to hide the typhoon badge.
    return _serialize(svc.get_typhoon_warning())


@router.get("/weather/health")
async def get_weather_health(request: Request, user: str = Depends(get_current_user)) -> Dict[str, Any]:
    svc = get_weather_service()
    if svc is None:
        return {"enabled": False}
    return {"enabled": True, **svc.health()}


@router.post("/weather/refresh")
async def refresh_weather(request: Request, user: str = Depends(get_current_user)) -> Dict[str, Any]:
    """Manually trigger immediate weather data refresh.

    NEW-API-002: debounced for _REFRESH_MIN_INTERVAL_S (matches the ~10s
    background tick) so an authenticated session cannot hammer this route
    into running unconditional external fetches back-to-back."""
    svc = get_weather_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Weather service not enabled")
    allowed, _ = _refresh_gate.acquire()
    if not allowed:
        return {"ok": False, "message": "刷新過於頻繁，請稍候再試"}
    success = await refresh_weather_now()
    if success:
        return {"ok": True, "message": "Weather data refreshed successfully"}
    else:
        return {"ok": False, "message": "Weather refresh failed or data unavailable"}


__all__ = ["router"]