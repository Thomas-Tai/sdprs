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
