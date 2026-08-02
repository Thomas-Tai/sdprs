# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Weather external-fetch throttle regression tests
(NEW-API-002).

`central_server/api/weather.py` has 3 routes that proxy live external
fetches, gated only by session auth — no throttle/cache. An authenticated
session could hammer them, risking an external-provider IP ban / DoS-by-proxy:
  - GET /weather/smg/stations
  - GET /weather/hko/stations
  - POST /weather/refresh

These tests prove each route is now min-interval gated: within the interval a
repeat call is served from cache / debounced (no new external fetch); once
the interval elapses a fresh fetch is allowed again. Time is controlled via
monkeypatching `weather._MONO` (module-level indirection over
`time.monotonic`), so tests never sleep.
"""

# This project's tests import via the `central_server.` package prefix with
# the sdprs repo root on sys.path (matches
# tests/test_weather_config_persistence.py — there is no conftest.py). A bare
# `import weather` does NOT resolve under pytest here.
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import asyncio

from central_server.api import weather


def _dummy_request():
    return types.SimpleNamespace()


def _run(coro):
    return asyncio.run(coro)


def test_smg_stations_throttled_within_interval(monkeypatch):
    weather._smg_gate.reset()

    calls = {"n": 0}

    async def _fake_fetch():
        calls["n"] += 1
        return ["A", "B"]

    monkeypatch.setattr(weather, "_fetch_smg_stations", _fake_fetch)
    monkeypatch.setattr(weather, "_MONO", lambda: 1000.0)

    r1 = _run(weather.list_smg_stations(_dummy_request(), user="tester"))
    r2 = _run(weather.list_smg_stations(_dummy_request(), user="tester"))

    assert calls["n"] == 1
    assert r1 == {"stations": ["A", "B"], "source_reachable": True}
    assert r2 == {"stations": ["A", "B"], "source_reachable": True}


def test_smg_stations_refetch_after_interval(monkeypatch):
    weather._smg_gate.reset()

    calls = {"n": 0}

    async def _fake_fetch():
        calls["n"] += 1
        return ["A", "B"]

    monkeypatch.setattr(weather, "_fetch_smg_stations", _fake_fetch)

    clock = {"t": 1000.0}
    monkeypatch.setattr(weather, "_MONO", lambda: clock["t"])

    _run(weather.list_smg_stations(_dummy_request(), user="tester"))
    clock["t"] += weather._STATION_LIST_MIN_INTERVAL_S + 1.0
    _run(weather.list_smg_stations(_dummy_request(), user="tester"))

    assert calls["n"] == 2


def test_hko_stations_throttled_within_interval(monkeypatch):
    weather._hko_gate.reset()

    calls = {"n": 0}

    async def _fake_fetch():
        calls["n"] += 1
        return ["S1"]

    monkeypatch.setattr(weather, "_fetch_hko_stations", _fake_fetch)
    monkeypatch.setattr(weather, "_MONO", lambda: 2000.0)

    _run(weather.list_hko_stations(_dummy_request(), user="tester"))
    _run(weather.list_hko_stations(_dummy_request(), user="tester"))

    assert calls["n"] == 1


def test_refresh_debounced_within_interval(monkeypatch):
    weather._refresh_gate.reset()

    calls = {"n": 0}

    async def _fake_refresh_now():
        calls["n"] += 1
        return True

    monkeypatch.setattr(weather, "get_weather_service", lambda: object())
    monkeypatch.setattr(weather, "refresh_weather_now", _fake_refresh_now)
    monkeypatch.setattr(weather, "_MONO", lambda: 3000.0)

    r1 = _run(weather.refresh_weather(_dummy_request(), user="tester"))
    r2 = _run(weather.refresh_weather(_dummy_request(), user="tester"))

    assert calls["n"] == 1
    assert r2 == {"ok": False, "message": "刷新過於頻繁，請稍候再試"}


def test_refresh_allowed_after_interval(monkeypatch):
    weather._refresh_gate.reset()

    calls = {"n": 0}

    async def _fake_refresh_now():
        calls["n"] += 1
        return True

    monkeypatch.setattr(weather, "get_weather_service", lambda: object())
    monkeypatch.setattr(weather, "refresh_weather_now", _fake_refresh_now)

    clock = {"t": 3000.0}
    monkeypatch.setattr(weather, "_MONO", lambda: clock["t"])

    _run(weather.refresh_weather(_dummy_request(), user="tester"))
    clock["t"] += weather._REFRESH_MIN_INTERVAL_S + 1.0
    _run(weather.refresh_weather(_dummy_request(), user="tester"))

    assert calls["n"] == 2
