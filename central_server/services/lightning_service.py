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
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
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
            if not self._enabled:
                return {"count": None, "nearest": None, "source": None}
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
