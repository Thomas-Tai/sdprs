# -*- coding: utf-8 -*-
# SDPRS Central Server - Weather API endpoints (Open-Meteo + CWA)
# See Plan/weather_integration.md sections 8 + 10.
#
# All endpoints session-auth, all return safe JSON (never 5xx from missing
# cache; that's 503 with a clear "service not initialised" body so the UI
# can show a placeholder rather than break).

import logging
from dataclasses import asdict
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..database import get_weather_config, set_weather_config
from ..services.weather_service import get_weather_service, update_weather_location, refresh_weather_now

logger = logging.getLogger("api.weather")

router = APIRouter()


class WeatherConfigPayload(BaseModel):
    site_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    site_lon: Optional[float] = Field(default=None, ge=-180, le=180)
    station_name: Optional[str] = Field(default=None, max_length=50)


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
    """Item 9: update weather location. Requires restart for new coordinates to take effect."""
    ok = set_weather_config(payload.site_lat, payload.site_lon, payload.station_name)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update weather config")
    # Update the running weather service if available
    svc = get_weather_service()
    if svc:
        update_weather_location(payload.site_lat, payload.site_lon)
    return {"ok": True, "site_lat": payload.site_lat, "site_lon": payload.site_lon, "station_name": payload.station_name}


@router.get("/weather/current")
async def get_current_weather(request: Request, user: str = Depends(get_current_user)) -> Dict[str, Any]:
    svc = get_weather_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Weather service not enabled")
    cur = svc.get_current()
    if cur is None:
        raise HTTPException(status_code=503, detail="Weather data not available yet")
    return _serialize(cur)


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
    """Manually trigger immediate weather data refresh."""
    svc = get_weather_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Weather service not enabled")
    success = await refresh_weather_now()
    if success:
        return {"ok": True, "message": "Weather data refreshed successfully"}
    else:
        return {"ok": False, "message": "Weather refresh failed or data unavailable"}


__all__ = ["router"]