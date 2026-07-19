# -*- coding: utf-8 -*-
"""SMG Macau XML parser tests.

Regression guard for the "all readings return 0" bug (2026-07-19): the SMG
schema wraps every reading in a <Value> child element, but the parser was
calling `station.findtext('Temperature')` — which returns the parent's own
text (empty whitespace between children), not the numeric value. Result:
Temperature=0.0, WindSpeed=0.0, Humidity=0, WindDirection=0, Rainfall=0
regardless of what the SMG API actually reported. On the dashboard the
weather section rendered "0°C · 0 km/h · 0%" which is indistinguishable
from a broken service.

Test isolates `_fetch_smg_current` behind a mocked httpx AsyncClient that
returns a fixture matching the real SMG XML shape (verified 2026-07-19 by
sampling https://xml.smg.gov.mo/c_actualweather.xml).
"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import os
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

import pytest

from central_server.services.weather_service import _fetch_smg_current, CurrentWeather


# Fixture mirrors the real 2026-07-19 SMG XML — one instrumented station
# ("外港") with all the readings we care about, plus one bare station that
# should NOT match. Every reading is wrapped in <Value> per the actual
# schema — the parser must descend into that child, not read the parent.
SMG_XML_FIXTURE = """<?xml version='1.0' encoding='utf-8'?>
<ActualWeather>
  <Custom>
    <WeatherReport>
      <station code="FM">
        <stationname>大炮台</stationname>
        <Temperature><Value>31</Value><dValue>31.2</dValue></Temperature>
        <Humidity><Value>75</Value></Humidity>
        <WindSpeed><Value>8</Value></WindSpeed>
        <WindGust><Value>15</Value></WindGust>
        <WindDirection><Value>SW</Value><Degree>230</Degree></WindDirection>
        <Rainfall><Type>3</Type><Value>0.0</Value></Rainfall>
      </station>
      <station code="PE">
        <stationname>外港</stationname>
        <Temperature><Value>29</Value><dValue>29.3</dValue></Temperature>
        <Humidity><Value>83</Value></Humidity>
        <WindSpeed><Value>10</Value></WindSpeed>
        <WindGust><Value>17</Value></WindGust>
        <WindDirection><Value>S</Value><Degree>168</Degree></WindDirection>
        <Rainfall><Type>3</Type><Value>2.5</Value></Rainfall>
      </station>
      <station code="EMPTY_TEST">
        <stationname>殘缺數據</stationname>
        <Temperature/>
        <WindSpeed><Value/></WindSpeed>
        <Humidity><Value>-99</Value></Humidity>
      </station>
    </WeatherReport>
  </Custom>
</ActualWeather>
"""


class _FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text
        self.encoding = 'utf-8'


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def get(self, url, timeout=None):
        return self._response


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) \
        if not asyncio.get_event_loop().is_closed() \
        else asyncio.run(coro)


def test_smg_parses_station_readings_from_value_child_elements():
    """Regression: the parser MUST descend into <Value> for each reading.
    Before the fix, every field returned its default (0.0 / 0) because
    findtext('Temperature') got the parent's own (empty) text instead of
    the <Value> child. This test would fail on the pre-fix code."""
    client = _FakeClient(_FakeResponse(200, SMG_XML_FIXTURE))
    cur = asyncio.run(_fetch_smg_current(client, "外港"))
    assert cur is not None, "should have found station '外港'"
    assert isinstance(cur, CurrentWeather)
    # Wind speed: SMG reports km/h, service converts to m/s (10 * 0.27778 ≈ 2.78)
    assert cur.wind_speed_ms == pytest.approx(2.7778, abs=0.01), \
        f"expected ~2.78 m/s (10 km/h), got {cur.wind_speed_ms} — parser likely returned the 0 default"
    # Wind gust: 17 km/h → ~4.72 m/s
    assert cur.gust_speed_ms is not None and cur.gust_speed_ms == pytest.approx(4.722, abs=0.01), \
        f"expected ~4.72 m/s gust (17 km/h), got {cur.gust_speed_ms}"
    # Wind direction: <Degree>168</Degree>, not the <Value>S</Value>
    assert cur.wind_direction_deg == 168, \
        f"expected degree=168, got {cur.wind_direction_deg} — WindDirection needs /Degree not /Value"
    # Temperature 29
    assert cur.temperature_c == 29.0, f"expected 29.0°C, got {cur.temperature_c}"
    # Humidity 83
    assert cur.humidity_pct == 83, f"expected 83%, got {cur.humidity_pct}"
    # Rainfall (Type 3 == current hour): 2.5mm
    assert cur.rainfall_24h_mm == 2.5, f"expected 2.5mm, got {cur.rainfall_24h_mm}"
    # Attribution
    assert cur.source == "SMG"
    assert cur.station_name == "外港"
    assert cur.is_stale is False


def test_smg_returns_none_when_station_not_found():
    """A misconfigured station_name (or SMG dropping the station) must
    return None so the fallback path (Open-Meteo) can take over."""
    client = _FakeClient(_FakeResponse(200, SMG_XML_FIXTURE))
    cur = asyncio.run(_fetch_smg_current(client, "does-not-exist"))
    assert cur is None


def test_smg_handles_missing_reading_gracefully():
    """Empty <Value/>, absent element, or sentinel (-99) must NOT crash —
    the field falls back to the default (0.0 / 0). Real SMG stations on
    the highway bridges omit Temperature/Humidity/Rainfall entirely."""
    client = _FakeClient(_FakeResponse(200, SMG_XML_FIXTURE))
    cur = asyncio.run(_fetch_smg_current(client, "殘缺數據"))
    assert cur is not None
    assert cur.temperature_c == 0.0    # <Temperature/> → empty → default
    assert cur.wind_speed_ms == 0.0    # <WindSpeed><Value/></WindSpeed> → default
    assert cur.humidity_pct == 0       # <Humidity><Value>-99</Value></Humidity> → sentinel → default


def test_smg_returns_none_on_http_error():
    """5xx from SMG must return None (fallback path takes over), not crash."""
    client = _FakeClient(_FakeResponse(503, ""))
    cur = asyncio.run(_fetch_smg_current(client, "外港"))
    assert cur is None


def test_smg_returns_none_on_malformed_xml():
    """Parse error must return None (fallback path takes over)."""
    client = _FakeClient(_FakeResponse(200, "not xml <>"))
    cur = asyncio.run(_fetch_smg_current(client, "外港"))
    assert cur is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
