# -*- coding: utf-8 -*-
"""Open-Meteo current-weather fetcher tests — rainfall rate vs daily total.

Verifies _fetch_openmeteo_current maps `current.precipitation` to the live
rate (rainfall_rate_mmh) and `daily.precipitation_sum[0]` to the genuine
24h total (rainfall_24h_mm), with per-field source labels.
"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import os
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

import pytest

from central_server.services.weather_service import _fetch_openmeteo_current, CurrentWeather


def _om_payload(with_daily=True):
    p = {
        "current": {
            "time": "2026-08-06T12:00",
            "temperature_2m": 30.0,
            "relative_humidity_2m": 70,
            "wind_speed_10m": 3.0,
            "wind_direction_10m": 180,
            "wind_gusts_10m": 5.0,
            "precipitation": 1.5,
            "pressure_msl": 1005.0,
        },
        "hourly": {"visibility": [12000.0]},
    }
    if with_daily:
        p["daily"] = {"precipitation_sum": [12.0]}
    return p


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response):
        self._response = response
    async def get(self, url, params=None, timeout=None):
        return self._response


def test_openmeteo_current_precip_is_rate_and_daily_sum_is_24h():
    client = _FakeClient(_FakeResponse(200, _om_payload(with_daily=True)))
    cur = asyncio.run(_fetch_openmeteo_current(client, 22.19, 113.55))
    assert cur is not None
    assert isinstance(cur, CurrentWeather)
    assert cur.rainfall_rate_mmh == 1.5          # current.precipitation
    assert cur.rainfall_24h_mm == 12.0           # daily.precipitation_sum[0]
    assert cur.sources.get('rainfall_rate_mmh', '').startswith('Open-Meteo')
    assert cur.sources.get('rainfall_24h_mm', '').startswith('Open-Meteo')


def test_openmeteo_missing_daily_leaves_24h_unlabeled_but_keeps_rate():
    client = _FakeClient(_FakeResponse(200, _om_payload(with_daily=False)))
    cur = asyncio.run(_fetch_openmeteo_current(client, 22.19, 113.55))
    assert cur is not None
    assert cur.rainfall_rate_mmh == 1.5
    assert 'rainfall_rate_mmh' in cur.sources
    # No daily block -> no genuine 24h total -> must NOT be labeled
    assert cur.rainfall_24h_mm == 0.0
    assert 'rainfall_24h_mm' not in cur.sources


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
