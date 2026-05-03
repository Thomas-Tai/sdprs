# -*- coding: utf-8 -*-
# SDPRS Central Server - Weather API endpoints (CWA Open Data)
# See Plan/weather_integration.md sections 8 + 10.
#
# All endpoints session-auth, all return safe JSON (never 5xx from missing
# cache; that's 503 with a clear "service not initialised" body so the UI
# can show a placeholder rather than break).

import logging
from dataclasses import asdict
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from ..services.weather_service import get_weather_service

logger = logging.getLogger("api.weather")

router = APIRouter()


def _require_session(request: Request) -> None:
    user = request.session.get("user") if hasattr(request, "session") else None
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")


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


@router.get("/weather/current")
async def get_current_weather(request: Request) -> Dict[str, Any]:
    _require_session(request)
    svc = get_weather_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Weather service not enabled")
    cur = svc.get_current()
    if cur is None:
        raise HTTPException(status_code=503, detail="Weather data not available yet")
    return _serialize(cur)


@router.get("/weather/forecast")
async def get_weather_forecast(request: Request) -> Dict[str, Any]:
    _require_session(request)
    svc = get_weather_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Weather service not enabled")
    return {"buckets": _serialize(svc.get_forecast_36h())}


@router.get("/weather/typhoon")
async def get_typhoon(request: Request) -> Any:
    _require_session(request)
    svc = get_weather_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Weather service not enabled")
    # Returning null when no warning is intentional — operator UI uses the
    # null check to hide the typhoon badge.
    return _serialize(svc.get_typhoon_warning())


@router.get("/weather/health")
async def get_weather_health(request: Request) -> Dict[str, Any]:
    _require_session(request)
    svc = get_weather_service()
    if svc is None:
        return {"enabled": False}
    return {"enabled": True, **svc.health()}


__all__ = ["router"]
