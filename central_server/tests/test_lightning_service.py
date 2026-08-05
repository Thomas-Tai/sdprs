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
