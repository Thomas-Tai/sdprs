# -*- coding: utf-8 -*-
# SDPRS Central Server - Weather Service (CWA Open Data + Open-Meteo fallback)
# See Plan/weather_integration.md for the full spec.
#
# Primary: Open-Meteo (free, no API key required)
# Optional: CWA (Taiwan-specific, requires CWA_API_KEY)
#
# Critical invariant: any failure here MUST NOT propagate. Weather is a
# decoration on the dashboard; the alert pipeline does not depend on it.
# All public getters return safe defaults (None / empty list) when the
# cache is empty or stale.

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("weather_service")

CWA_BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
OPEN_METEO_BASE = "https://api.open-meteo.com/v1"
HTTP_TIMEOUT_S = 8.0


@dataclass
class CurrentWeather:
    obs_time: datetime
    wind_speed_ms: float
    wind_direction_deg: int
    rainfall_24h_mm: float
    temperature_c: float
    humidity_pct: int
    is_stale: bool
    fetched_at: datetime


@dataclass
class ForecastBucket:
    start_time: datetime
    end_time: datetime
    wind_speed_ms: float
    rainfall_mm: float
    weather_phenomenon: str
    pop_pct: int


@dataclass
class TyphoonWarning:
    name: str
    category: str
    distance_to_site_km: float
    bearing_to_site_deg: int
    max_wind_ms: float
    moving_speed_kmh: float
    eta_landfall: Optional[datetime]


@dataclass
class _Cache:
    current: Optional[CurrentWeather] = None
    forecast_36h: List[ForecastBucket] = field(default_factory=list)
    typhoon: Optional[TyphoonWarning] = None
    last_success_at: Optional[datetime] = None
    last_error: Optional[str] = None
    api_reachable: bool = False
    consecutive_failures: int = 0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # Standard great-circle distance.
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return int((math.degrees(math.atan2(y, x)) + 360) % 360)


def _parse_station_observation(payload: Dict[str, Any], station_id: str) -> Optional[CurrentWeather]:
    # CWA O-A0003-001 returns records.location[]; field shapes have shifted
    # over the years, so we keep the parser defensive.
    try:
        records = payload.get("records", {})
        # API has used both "location" and "Station" keys depending on version;
        # try both before giving up.
        stations = records.get("location") or records.get("Station") or []
        for st in stations:
            sid = (
                st.get("stationId")
                or st.get("StationId")
                or st.get("StationID")
                or ""
            )
            if sid != station_id:
                continue
            elements = st.get("weatherElement") or st.get("WeatherElement") or []
            kv: Dict[str, Any] = {}
            if isinstance(elements, list):
                for el in elements:
                    name = el.get("elementName") or el.get("ElementName")
                    val = el.get("elementValue")
                    if val is None:
                        val = el.get("ElementValue")
                    if name is not None:
                        kv[name] = val
            elif isinstance(elements, dict):
                # Newer schema: WeatherElement is itself a dict.
                kv = elements

            def _f(name: str, default: float = 0.0) -> float:
                v = kv.get(name)
                if v in (None, "-", "X", "x", "-99", -99, -99.0):
                    return default
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return default

            obs_str = (
                st.get("obsTime")
                or st.get("ObsTime")
                or {}
            )
            if isinstance(obs_str, dict):
                obs_str = obs_str.get("DateTime") or obs_str.get("obsDate") or ""
            try:
                obs_time = datetime.fromisoformat(str(obs_str).replace("Z", "+00:00"))
            except ValueError:
                obs_time = datetime.now(timezone.utc)

            return CurrentWeather(
                obs_time=obs_time,
                wind_speed_ms=_f("WDSD"),
                wind_direction_deg=int(_f("WDIR")),
                rainfall_24h_mm=_f("H_24R"),
                temperature_c=_f("TEMP"),
                humidity_pct=int(_f("HUMD") * 100) if _f("HUMD") <= 1 else int(_f("HUMD")),
                is_stale=False,
                fetched_at=datetime.now(timezone.utc),
            )
        return None
    except Exception as e:
        logger.warning(f"Failed to parse station observation: {e}")
        return None


def _parse_township_forecast(payload: Dict[str, Any]) -> List[ForecastBucket]:
    # F-C0032-001 / F-D0047-091 share a similar nested element layout.
    # We stitch parallel arrays (Wx, PoP, T, MaxT, MinT, ...) by their times.
    try:
        records = payload.get("records", {})
        locs = records.get("location") or records.get("locations") or []
        if not locs:
            return []
        # F-D0047-091 wraps "locations" twice
        if isinstance(locs, list) and locs and isinstance(locs[0], dict) and "location" in locs[0]:
            locs = locs[0].get("location", [])

        loc = locs[0] if locs else {}
        elements = loc.get("weatherElement", []) or loc.get("WeatherElement", [])
        # Build {element_name: [{startTime, endTime, value}]}
        by_name: Dict[str, List[Dict[str, Any]]] = {}
        for el in elements:
            name = el.get("elementName") or el.get("ElementName") or ""
            times = el.get("time") or el.get("Time") or []
            by_name[name] = times

        # Use Wx (weather phenomenon) to enumerate bucket boundaries.
        wx_times = by_name.get("Wx") or by_name.get("WeatherDescription") or []
        buckets: List[ForecastBucket] = []
        for w in wx_times[:12]:  # 36hr / 3hr = 12 buckets
            try:
                start = datetime.fromisoformat(w.get("startTime", "").replace("Z", "+00:00"))
                end = datetime.fromisoformat(w.get("endTime", "").replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            phenomenon = (w.get("parameter", {}) or {}).get("parameterName", "")

            def _lookup(name: str) -> str:
                for entry in by_name.get(name, []):
                    if entry.get("startTime") == w.get("startTime"):
                        return (entry.get("parameter", {}) or {}).get("parameterName", "")
                return ""

            try:
                pop = int(_lookup("PoP") or 0)
            except ValueError:
                pop = 0
            buckets.append(ForecastBucket(
                start_time=start,
                end_time=end,
                wind_speed_ms=0.0,    # F-C0032 doesn't expose direct wind speed; left 0 for MVP
                rainfall_mm=0.0,
                weather_phenomenon=phenomenon,
                pop_pct=pop,
            ))
        return buckets
    except Exception as e:
        logger.warning(f"Failed to parse township forecast: {e}")
        return []


def _parse_typhoon_warning(payload: Dict[str, Any], site_lat: float, site_lon: float) -> Optional[TyphoonWarning]:
    try:
        records = payload.get("records", {})
        cyclones = records.get("tropicalCyclones", {}).get("tropicalCyclone", []) \
            or records.get("typhoon", []) \
            or []
        if not cyclones:
            return None
        ty = cyclones[0]
        name = ty.get("typhoonName") or ty.get("cwbTyphoonName") or "未命名"
        # The fix-shaped payload has analysisData.fix[] containing positions.
        analysis = ty.get("analysisData", {}).get("fix", [{}])
        latest = analysis[-1] if analysis else {}
        try:
            lat = float(latest.get("coordinate", "0,0").split(",")[1])
            lon = float(latest.get("coordinate", "0,0").split(",")[0])
        except (ValueError, IndexError):
            lat, lon = 0.0, 0.0
        max_wind = float(latest.get("maxWindSpeed", 0) or 0)
        moving_speed = float(latest.get("movingSpeed", 0) or 0)
        category = latest.get("typhoonIntensity") or "颱風"
        return TyphoonWarning(
            name=name,
            category=category,
            distance_to_site_km=_haversine_km(lat, lon, site_lat, site_lon),
            bearing_to_site_deg=_bearing_deg(lat, lon, site_lat, site_lon),
            max_wind_ms=max_wind,
            moving_speed_kmh=moving_speed,
            eta_landfall=None,
        )
    except Exception as e:
        logger.warning(f"Failed to parse typhoon warning: {e}")
        return None


# ===== Open-Meteo (free, no API key) =====
# Open-Meteo provides global weather data without registration.
# Endpoint: https://api.open-meteo.com/v1/forecast
# Parameters: latitude, longitude, hourly/daily variables, timezone

OPEN_METEO_WEATHER_CODES = {
    0: "晴", 1: "晴", 2: "多雲", 3: "陰",
    45: "霧", 48: "霧",
    51: "小雨", 53: "小雨", 55: "中雨",
    56: "凍雨", 57: "凍雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "凍雨", 67: "凍雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    77: "雪粒",
    80: "小雨", 81: "中雨", 82: "大雨",
    85: "小雪", 86: "大雪",
    95: "雷暴", 96: "雷暴+小冰雹", 99: "雷暴+大冰雹",
}


async def _fetch_openmeteo_current(
    client: httpx.AsyncClient, lat: float, lon: float
) -> Optional[CurrentWeather]:
    """Fetch current weather from Open-Meteo (no API key required)."""
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation,rain",
            "timezone": "Asia/Taipei",
        }
        r = await client.get(f"{OPEN_METEO_BASE}/forecast", params=params, timeout=HTTP_TIMEOUT_S)
        if r.status_code != 200:
            logger.warning(f"Open-Meteo returned {r.status_code}")
            return None
        data = r.json()
        cur = data.get("current", {})
        if not cur:
            return None

        # Open-Meteo returns precipitation as hourly sum; we approximate 24h by
        # multiplying hourly rate by 24 (rough estimate; CWA provides actual 24h sum).
        precip_1h = float(cur.get("precipitation", 0) or 0)
        rain_1h = float(cur.get("rain", 0) or 0)
        rainfall_24h = max(precip_1h, rain_1h) * 24  # rough approximation

        return CurrentWeather(
            obs_time=datetime.fromisoformat(cur.get("time", "").replace("Z", "+00:00")),
            wind_speed_ms=float(cur.get("wind_speed_10m", 0) or 0),
            wind_direction_deg=int(cur.get("wind_direction_10m", 0) or 0),
            rainfall_24h_mm=rainfall_24h,
            temperature_c=float(cur.get("temperature_2m", 0) or 0),
            humidity_pct=int(float(cur.get("relative_humidity_2m", 0) or 0)),
            is_stale=False,
            fetched_at=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.warning(f"Open-Meteo fetch failed: {e}")
        return None


async def _fetch_openmeteo_forecast(
    client: httpx.AsyncClient, lat: float, lon: float, hours: int = 36
) -> List[ForecastBucket]:
    """Fetch hourly forecast from Open-Meteo."""
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation_probability,precipitation,weathercode",
            "timezone": "Asia/Taipei",
            "forecast_hours": hours,
        }
        r = await client.get(f"{OPEN_METEO_BASE}/forecast", params=params, timeout=HTTP_TIMEOUT_S)
        if r.status_code != 200:
            return []
        data = r.json()
        hourly = data.get("hourly", {})
        if not hourly:
            return []

        times = hourly.get("time", [])
        wind = hourly.get("wind_speed_10m", [])
        precip = hourly.get("precipitation", [])
        pop = hourly.get("precipitation_probability", [])
        codes = hourly.get("weathercode", [])

        buckets = []
        for i, t_str in enumerate(times[:hours]):
            try:
                start = datetime.fromisoformat(t_str.replace("Z", "+00:00"))
                end = start + timedelta(hours=1)
                ws = float(wind[i] if i < len(wind) else 0)
                rf = float(precip[i] if i < len(precip) else 0)
                prob = int(pop[i] if i < len(pop) else 0)
                code = int(codes[i] if i < len(codes) else 0)
                phenomenon = OPEN_METEO_WEATHER_CODES.get(code, "未知")
                buckets.append(ForecastBucket(
                    start_time=start, end_time=end,
                    wind_speed_ms=ws, rainfall_mm=rf,
                    weather_phenomenon=phenomenon, pop_pct=prob,
                ))
            except Exception:
                continue
        return buckets
    except Exception as e:
        logger.warning(f"Open-Meteo forecast fetch failed: {e}")
        return []


class WeatherService:
    # Singleton-style; module-level instance is created by init_weather_service().

    def __init__(self, settings) -> None:
        self._settings = settings
        self._cache = _Cache()
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._key_warned = False
        # Backoff state for 429s. We never sleep less than the configured
        # refresh interval — the 429 path only ever extends the gap.
        self._backoff_extra_s = 0.0

    # ---- public surface -----------------------------------------------------
    def get_current(self) -> Optional[CurrentWeather]:
        cur = self._cache.current
        if cur is None:
            return None
        # Recompute is_stale at read time so callers don't see a fixed flag.
        age_s = (datetime.now(timezone.utc) - cur.fetched_at).total_seconds()
        cur.is_stale = age_s > self._settings.WEATHER_CACHE_STALE_SECONDS
        return cur

    def get_forecast_36h(self) -> List[ForecastBucket]:
        return list(self._cache.forecast_36h)

    def get_typhoon_warning(self) -> Optional[TyphoonWarning]:
        return self._cache.typhoon

    def is_lightning_window(self) -> bool:
        # True if a forecast bucket covering the current hour mentions 雷.
        now = datetime.now(timezone.utc)
        for b in self._cache.forecast_36h:
            if b.start_time <= now <= b.end_time and "雷" in b.weather_phenomenon:
                return True
        return False

    def health(self) -> Dict[str, Any]:
        cur = self._cache.current
        cache_age = None
        if cur is not None:
            cache_age = (datetime.now(timezone.utc) - cur.fetched_at).total_seconds()
        return {
            "cache_age_s": cache_age,
            "last_fetch_at": self._cache.last_success_at.isoformat() if self._cache.last_success_at else None,
            "is_stale": cur.is_stale if cur else True,
            "api_reachable": self._cache.api_reachable,
            "last_error": self._cache.last_error,
        }

    # ---- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None:
            return
        # Weather service now uses Open-Meteo (free, no API key) as primary source.
        # CWA is optional for Taiwan-specific typhoon warnings when CWA_API_KEY is set.
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run())
        source = "Open-Meteo (free)"
        if self._settings.CWA_API_KEY:
            source += " + CWA (typhoon warnings)"
        logger.info(f"Weather service started: {source} (refresh every {self._settings.WEATHER_REFRESH_SECONDS}s)")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None

    # ---- internal ----------------------------------------------------------
    async def _run(self) -> None:
        # Tick once immediately so the cache has data within seconds of startup.
        try:
            await self._tick()
        except Exception as e:
            logger.warning(f"Initial weather tick failed: {e}")
        while True:
            assert self._stop_event is not None
            interval = self._settings.WEATHER_REFRESH_SECONDS + self._backoff_extra_s
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                return  # stop was set
            except asyncio.TimeoutError:
                pass
            try:
                await self._tick()
            except Exception as e:
                # Belt-and-braces; _tick should never raise.
                logger.warning(f"Weather tick raised: {e}")

    async def _tick(self) -> None:
        s = self._settings
        lat, lon = s.SITE_LAT, s.SITE_LON
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            # Primary: Open-Meteo (free, no API key)
            openmeteo_results = await asyncio.gather(
                _fetch_openmeteo_current(client, lat, lon),
                _fetch_openmeteo_forecast(client, lat, lon, 36),
                return_exceptions=True,
            )

            # Optional: CWA typhoon warnings (Taiwan-specific, requires key)
            cwa_typhoon = None
            if s.CWA_API_KEY:
                params_typhoon = {"Authorization": s.CWA_API_KEY}
                typhoon_payload = await self._fetch(client, "W-C0033-001", params_typhoon)
                if isinstance(typhoon_payload, dict):
                    cwa_typhoon = _parse_typhoon_warning(typhoon_payload, lat, lon)

        # Process Open-Meteo results
        any_ok = False
        cur_om = openmeteo_results[0]
        if isinstance(cur_om, CurrentWeather):
            self._cache.current = cur_om
            any_ok = True
        elif isinstance(cur_om, Exception):
            logger.warning(f"Open-Meteo current failed: {cur_om}")

        fc_om = openmeteo_results[1]
        if isinstance(fc_om, list) and fc_om:
            self._cache.forecast_36h = fc_om
            any_ok = True
        elif isinstance(fc_om, Exception):
            logger.warning(f"Open-Meteo forecast failed: {fc_om}")

        # Typhoon from CWA (if available)
        if cwa_typhoon:
            self._cache.typhoon = cwa_typhoon
            any_ok = True

        if any_ok:
            self._cache.last_success_at = datetime.now(timezone.utc)
            self._cache.api_reachable = True
            self._cache.last_error = None
            self._cache.consecutive_failures = 0
            self._backoff_extra_s = 0.0
        else:
            self._cache.consecutive_failures += 1
            self._cache.api_reachable = False

    async def _fetch(self, client: httpx.AsyncClient, dataset_id: str, params: Dict[str, Any]) -> Any:
        url = f"{CWA_BASE}/{dataset_id}"
        try:
            r = await client.get(url, params=params)
            if r.status_code == 401 or r.status_code == 403:
                if not self._key_warned:
                    logger.error(f"CWA returned {r.status_code} (bad API key?) on {dataset_id}; further auth failures will be DEBUG")
                    self._key_warned = True
                else:
                    logger.debug(f"CWA {r.status_code} on {dataset_id}")
                self._cache.last_error = f"AUTH:{r.status_code}"
                return None
            if r.status_code == 429:
                # Step backoff: 10 / 60 / 300 / 600 (capped at refresh).
                steps = [10, 60, 300, 600]
                idx = min(self._cache.consecutive_failures, len(steps) - 1)
                self._backoff_extra_s = float(steps[idx])
                logger.warning(f"CWA rate-limited (429) on {dataset_id}; extra backoff {self._backoff_extra_s}s")
                self._cache.last_error = "RATE_LIMIT"
                return None
            if r.status_code >= 500:
                logger.warning(f"CWA 5xx on {dataset_id}: {r.status_code}")
                self._cache.last_error = f"SERVER:{r.status_code}"
                return None
            return r.json()
        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.warning(f"CWA request failed for {dataset_id}: {e}")
            self._cache.last_error = f"NETWORK:{type(e).__name__}"
            return None
        except Exception as e:
            logger.warning(f"Unexpected error fetching {dataset_id}: {e}")
            self._cache.last_error = f"OTHER:{type(e).__name__}"
            return None


# Module-level singleton, populated by init_weather_service().
_weather_service: Optional[WeatherService] = None


def init_weather_service(settings) -> WeatherService:
    global _weather_service
    _weather_service = WeatherService(settings)
    return _weather_service


def get_weather_service() -> Optional[WeatherService]:
    return _weather_service


__all__ = [
    "WeatherService",
    "CurrentWeather",
    "ForecastBucket",
    "TyphoonWarning",
    "init_weather_service",
    "get_weather_service",
]
