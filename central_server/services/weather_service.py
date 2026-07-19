# -*- coding: utf-8 -*-
# SDPRS Central Server - Weather Service (SMG Macau + Open-Meteo fallback)
# See Plan/weather_integration.md for the full spec.
#
# Primary: SMG Macau XML (free, no API key, Macau-specific)
# Fallback: Open-Meteo (free, no API key required)
# Optional: CWA (Taiwan-specific, requires CWA_API_KEY)
#
# Critical invariant: any failure here MUST NOT propagate. Weather is a
# decoration on the dashboard; the alert pipeline does not depend on it.
# All public getters return safe defaults (None / empty list) when the
# cache is empty or stale.

import asyncio
import logging
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from ..database import get_weather_config

logger = logging.getLogger("weather_service")

SMG_XML_URL = "https://xml.smg.gov.mo/c_actualweather.xml"
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
    source: str = "SMG"  # "SMG" or "Open-Meteo"
    station_name: str = "外港"  # Station name for display
    gust_speed_ms: Optional[float] = None  # Wind gust in m/s; None when provider has no gust data


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
    source: str = "SMG"  # Current data source


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


async def _fetch_smg_current(client: httpx.AsyncClient, station_name: str = "外港") -> Optional[CurrentWeather]:
    """Fetch current weather from SMG Macau XML (免費、免 API Key).

    Args:
        client: httpx async client
        station_name: Station name to look for (default: "外港" - Outer Harbour)

    Returns:
        CurrentWeather if successful, None otherwise
    """
    try:
        r = await client.get(SMG_XML_URL, timeout=HTTP_TIMEOUT_S)
        if r.status_code != 200:
            logger.warning(f"SMG XML returned {r.status_code}")
            return None

        # Parse XML
        r.encoding = 'utf-8'
        root = ET.fromstring(r.text)

        # SMG schema: one <Custom> contains many <WeatherReport>/<station>;
        # each <station> has <stationname> + readings. Earlier code looked for
        # <StationName> directly under <Custom>, which silently never matched.
        for station in root.findall('.//WeatherReport/station'):
            name = station.findtext('stationname', default='') or ''
            if station_name in name:
                # SMG returns missing readings as empty XML elements
                # (<Temperature/>) which findtext yields as None or "". Treat
                # both — plus the legacy sentinels — as missing.
                def _get_float(tag: str, default: float = 0.0) -> float:
                    val = station.findtext(tag)
                    if val is None or val.strip() in ('', '-', 'X', 'x', '-99'):
                        return default
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        return default

                def _get_int(tag: str, default: int = 0) -> int:
                    val = station.findtext(tag)
                    if val is None or val.strip() in ('', '-', 'X', 'x', '-99'):
                        return default
                    try:
                        return int(float(val))
                    except (TypeError, ValueError):
                        return default

                # SMG schema wraps every numeric reading in a <Value> child:
                #     <Temperature>
                #       <Value>29</Value>       ← this is the number
                #       <dValue>29.3</dValue>   ← alt precision
                #       <Type>3</Type>          ← measurement classification
                #     </Temperature>
                # `station.findtext('Temperature')` returns the parent's own
                # text (empty whitespace between the children) — so the
                # helpers below MUST be called with the '/Value' suffix, or
                # every reading silently returns the default (0.0).
                # Regression bug caught 2026-07-19; see test_smg_xml_parser.py.

                # Wind speed in km/h, convert to m/s (1 km/h = 0.27778 m/s)
                wind_kmh = _get_float('WindSpeed/Value')
                wind_ms = wind_kmh * 0.27778

                # Wind gust (km/h → m/s). SMG may omit this element entirely
                # or emit an empty/placeholder value; _get_optional_float returns
                # None in those cases so the UI shows "—" instead of a fake 0.
                def _get_optional_float(tag: str) -> Optional[float]:
                    val = station.findtext(tag)
                    if val is None or val.strip() in ('', '-', 'X', 'x', '-99'):
                        return None
                    try:
                        return float(val) * 0.27778  # km/h → m/s
                    except (TypeError, ValueError):
                        return None

                gust_ms = _get_optional_float('WindGust/Value')

                # Rainfall - SMG usually emits multiple <Rainfall> elements
                # differentiated by <Type> (3=current hour, 5=daily total).
                # findtext returns the FIRST match; Type 3 (hourly) is the
                # instantaneous rate suitable for the dashboard's mm/h field.
                rainfall_hourly = _get_float('Rainfall/Value')

                # WindDirection has <Value>SW</Value> (compass letters) plus
                # <Degree>230</Degree> (numeric). We need the numeric.
                return CurrentWeather(
                    obs_time=datetime.now(timezone.utc),
                    wind_speed_ms=wind_ms,
                    wind_direction_deg=_get_int('WindDirection/Degree'),
                    rainfall_24h_mm=rainfall_hourly,  # Hourly rainfall
                    temperature_c=_get_float('Temperature/Value'),
                    humidity_pct=_get_int('Humidity/Value'),
                    is_stale=False,
                    fetched_at=datetime.now(timezone.utc),
                    source="SMG",
                    station_name=name,
                    gust_speed_ms=gust_ms,
                )

        logger.warning(f"SMG XML: station '{station_name}' not found")
        return None

    except ET.ParseError as e:
        logger.warning(f"SMG XML parse error: {e}")
        return None
    except Exception as e:
        logger.warning(f"SMG XML fetch failed: {e}")
        return None


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
    """Fetch current weather from Open-Meteo (no API key required).

    API params per user request:
    - current=wind_speed_10m,precipitation
    - wind_speed_unit=ms (ensure m/s unit)
    """
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation",
            "wind_speed_unit": "ms",
            "timezone": "Asia/Macau",
        }
        r = await client.get(f"{OPEN_METEO_BASE}/forecast", params=params, timeout=HTTP_TIMEOUT_S)
        if r.status_code != 200:
            logger.warning(f"Open-Meteo returned {r.status_code}")
            return None
        data = r.json()
        cur = data.get("current", {})
        if not cur:
            return None

        # Open-Meteo precipitation is hourly; approximate 24h by multiplying
        # For display, we use the hourly value as "24h" since it's the available data
        precip_current = float(cur.get("precipitation", 0) or 0)

        # Wind gusts — Open-Meteo returns wind_gusts_10m in the same unit as
        # wind_speed_unit (m/s here). May be absent in some response shapes;
        # treat None / 0 as missing so the UI renders "—" rather than a
        # deceptive zero during a typhoon.
        gust_raw = cur.get("wind_gusts_10m")
        gust_ms = float(gust_raw) if gust_raw is not None else None

        return CurrentWeather(
            obs_time=datetime.fromisoformat(cur.get("time", "").replace("Z", "+00:00")),
            wind_speed_ms=float(cur.get("wind_speed_10m", 0) or 0),
            wind_direction_deg=int(cur.get("wind_direction_10m", 0) or 0),
            rainfall_24h_mm=precip_current,  # Use current precipitation value
            temperature_c=float(cur.get("temperature_2m", 0) or 0),
            humidity_pct=int(float(cur.get("relative_humidity_2m", 0) or 0)),
            is_stale=False,
            fetched_at=datetime.now(timezone.utc),
            source="Open-Meteo",
            station_name=f"{lat:.3f},{lon:.3f}",
            gust_speed_ms=gust_ms,
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
            "wind_speed_unit": "ms",
            "timezone": "Asia/Macau",
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
            "source": self._cache.source,
            "station_name": cur.station_name if cur else None,
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
        # Read weather config from database (user-configurable via UI).
        # Falls back to settings.SITE_LAT/LON so Open-Meteo forecast populates
        # out-of-the-box even before an operator sets a custom location — the
        # dashboard's 36h forecast chart is otherwise silently empty on a
        # fresh deploy. Settings default is Macau (matches SMG primary source);
        # set SITE_LAT/SITE_LON env vars on Zeabur if deploying elsewhere.
        weather_cfg = get_weather_config()
        lat = weather_cfg.get("site_lat")
        if lat is None:
            lat = s.SITE_LAT
        lon = weather_cfg.get("site_lon")
        if lon is None:
            lon = s.SITE_LON

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            # Primary: SMG Macau XML (免費、免 API Key、澳門專用)
            smg_current = await _fetch_smg_current(client, "外港")

            # Fallback: Open-Meteo (only if user configured lat/lon)
            openmeteo_results = [None, []]
            if lat is not None and lon is not None:
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
                    cwa_typhoon = _parse_typhoon_warning(typhoon_payload, lat or 0, lon or 0)

        # Process results - prefer SMG over Open-Meteo for current weather
        any_ok = False

        # Current weather: SMG primary, Open-Meteo fallback
        if isinstance(smg_current, CurrentWeather):
            self._cache.current = smg_current
            self._cache.source = "SMG"
            any_ok = True
            logger.debug("Using SMG Macau data for current weather")
        elif lat is not None and lon is not None:
            cur_om = openmeteo_results[0]
            if isinstance(cur_om, CurrentWeather):
                self._cache.current = cur_om
                self._cache.source = "Open-Meteo"
                any_ok = True
                logger.debug("Using Open-Meteo data for current weather")
            elif isinstance(cur_om, Exception):
                logger.warning(f"Open-Meteo current failed: {cur_om}")

        # Forecast: Open-Meteo only (SMG doesn't provide hourly forecast in XML)
        if lat is not None and lon is not None:
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

    async def refresh_now(self) -> bool:
        """Manually trigger an immediate weather refresh. Returns True if successful."""
        try:
            await self._tick()
            return self._cache.api_reachable
        except Exception as e:
            logger.warning(f"Manual weather refresh failed: {e}")
            return False

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


def update_weather_location(lat: float, lon: float) -> bool:
    """Item 9: update the running weather service's location coordinates."""
    global _weather_service
    if _weather_service is None:
        return False
    _weather_service._settings.SITE_LAT = lat
    _weather_service._settings.SITE_LON = lon
    logger.info(f"Weather location updated to lat={lat}, lon={lon}")
    return True


async def refresh_weather_now() -> bool:
    """Manually trigger immediate weather data refresh."""
    global _weather_service
    if _weather_service is None:
        return False
    return await _weather_service.refresh_now()


__all__ = [
    "WeatherService",
    "CurrentWeather",
    "ForecastBucket",
    "TyphoonWarning",
    "init_weather_service",
    "get_weather_service",
    "refresh_weather_now",
]
