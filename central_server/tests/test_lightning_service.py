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
import json


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


def test_parse_strike_out_of_range_time_returns_none():
    # Untrusted feed: an overflowing epoch must return None, never raise
    # (never-raises invariant). float('inf') makes fromtimestamp raise
    # OverflowError, which the original narrow except tuple did NOT catch.
    import json
    assert L._parse_strike({"time": float("inf"), "lat": 1.0, "lon": 2.0}) is None
    assert L._decode_message(json.dumps({"time": float("inf"), "lat": 1.0, "lon": 2.0})) is None


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


def test_prune_tolerates_non_strike_head():
    # _prune must not raise on a corrupt (non-Strike) entry at the head —
    # the isinstance guard stops the pop loop instead of dereferencing .ts.
    svc = _svc()
    svc._strikes.append("not-a-strike")          # corrupt head
    _add(svc, 22.2, 113.5, 90)                   # a stale real strike behind it
    svc._prune()                                 # must NOT raise
    assert svc._strikes[0] == "not-a-strike"     # loop halted at the guard; nothing popped


def test_on_message_outer_guard_swallows_unexpected_error():
    # Force an error INSIDE the try, AFTER a successful decode, to exercise
    # _on_message's own except Exception (the garbage test is caught earlier
    # inside _decode_message, so it never reaches this guard).
    svc = _svc()
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    msg = json.dumps({"time": now_ns, "lat": 22.2, "lon": 113.5})  # valid, in-bbox
    def _boom(lat, lon):
        raise RuntimeError("boom")
    svc._in_bbox = _boom                          # make the post-decode path raise
    svc._on_message(msg)                          # must NOT raise
    assert svc._connected is True                 # liveness still marked (set before the try)
    assert svc._last_msg_at is not None
    assert len(svc._strikes) == 0                 # append never reached
