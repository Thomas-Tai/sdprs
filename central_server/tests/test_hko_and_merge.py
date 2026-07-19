# -*- coding: utf-8 -*-
"""HKO fetcher + multi-source merge tests (Phase 1 of the weather
multi-source design, docs/weather-multi-source-decision.md).

Covers:
- HKO rhrread JSON parsing: temperature-station selection, humidity
  from HK Observatory, per-district rainfall roll-up, absent-station
  short-circuit, HTTP-error safety.
- merge_currents: priority selection, per-field fallback fill, sources
  dict propagation, all-None input, single-candidate identity, and the
  "SMG missing temperature -> HKO fills" case that motivates per-field
  merging over provider-selection.
"""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import os
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

import pytest
from datetime import datetime, timezone

from central_server.services.weather_service import (
    CurrentWeather,
    _fetch_hko_current,
    merge_currents,
)


# Trimmed fixture matching the shape of the live HKO endpoint sampled
# 2026-07-19 — one instrumented temperature station used in the tests
# plus a rainfall row and the always-HKO-Observatory humidity row.
HKO_JSON_FIXTURE = json.dumps({
    "temperature": {
        "data": [
            {"place": "Hong Kong Observatory", "value": 29, "unit": "C"},
            {"place": "Central Weather Station", "value": 30, "unit": "C"},
            {"place": "Sai Kung", "value": 28, "unit": "C"},
        ],
        "recordTime": "2026-07-19T20:00:00+08:00",
    },
    "humidity": {
        "recordTime": "2026-07-19T20:00:00+08:00",
        "data": [
            {"unit": "percent", "value": 82, "place": "Hong Kong Observatory"},
        ],
    },
    "rainfall": {
        "data": [
            {"unit": "mm", "place": "Central & Western District", "max": 0, "main": "FALSE"},
            {"unit": "mm", "place": "Sha Tin", "max": 3.5, "main": "FALSE"},
            {"unit": "mm", "place": "Tuen Mun", "max": 1.2, "main": "FALSE"},
        ],
        "startTime": "2026-07-19T18:45:00+08:00",
        "endTime": "2026-07-19T19:45:00+08:00",
    },
    "updateTime": "2026-07-19T20:02:00+08:00",
})


class _FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def get(self, url, params=None, timeout=None):
        return self._response


def _cur(source, station, sources_fields, **overrides):
    """Small factory for tidy CurrentWeather test data."""
    defaults = dict(
        obs_time=datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc),
        wind_speed_ms=0.0, wind_direction_deg=0, rainfall_24h_mm=0.0,
        temperature_c=0.0, humidity_pct=0,
        is_stale=False,
        fetched_at=datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc),
        source=source, station_name=station,
        gust_speed_ms=None,
        sources={f: f"{source} {station}" for f in sources_fields},
    )
    defaults.update(overrides)
    return CurrentWeather(**defaults)


# ============================================================================
# HKO fetcher
# ============================================================================

def test_hko_parses_selected_temperature_station():
    """Temperature comes from the exact 'place' the caller asked for,
    not the first station in the list. Humidity always from HK
    Observatory (only humidity station). Rainfall = max across all
    districts (Sha Tin's 3.5 wins here)."""
    client = _FakeClient(_FakeResponse(200, HKO_JSON_FIXTURE))
    cur = asyncio.run(_fetch_hko_current(client, temp_station="Central Weather Station"))
    assert cur is not None
    assert cur.temperature_c == 30.0
    assert cur.humidity_pct == 82
    assert cur.rainfall_24h_mm == 3.5
    assert cur.source == "HKO"
    assert cur.station_name == "Central Weather Station"
    # HKO rhrread has no wind — must NOT claim wind fields in sources
    assert 'wind_speed_ms' not in cur.sources
    assert 'wind_direction_deg' not in cur.sources
    assert 'gust_speed_ms' not in cur.sources
    # Fields HKO does supply — must be labeled
    assert 'temperature_c' in cur.sources
    assert cur.sources['temperature_c'] == "HKO Central Weather Station"
    assert cur.sources['humidity_pct'] == "HKO Hong Kong Observatory"
    assert 'rainfall_24h_mm' in cur.sources


def test_hko_returns_none_when_station_not_found():
    """Typo / removed station — bail rather than silently use a
    different station's temperature."""
    client = _FakeClient(_FakeResponse(200, HKO_JSON_FIXTURE))
    cur = asyncio.run(_fetch_hko_current(client, temp_station="Nonexistent Peak"))
    assert cur is None


def test_hko_returns_none_on_http_error():
    client = _FakeClient(_FakeResponse(503, ""))
    cur = asyncio.run(_fetch_hko_current(client, temp_station="Hong Kong Observatory"))
    assert cur is None


def test_hko_omits_rainfall_source_when_no_district_data():
    """If HKO returns an empty rainfall.data[] (edge case, but possible
    during API maintenance), the sources dict must NOT claim rainfall
    — merge_currents should fall through to another source."""
    slim = json.loads(HKO_JSON_FIXTURE)
    slim["rainfall"]["data"] = []
    client = _FakeClient(_FakeResponse(200, json.dumps(slim)))
    cur = asyncio.run(_fetch_hko_current(client, temp_station="Hong Kong Observatory"))
    assert cur is not None
    assert cur.rainfall_24h_mm == 0.0
    assert 'rainfall_24h_mm' not in cur.sources


# ============================================================================
# merge_currents
# ============================================================================

def test_merge_none_when_all_candidates_none():
    assert merge_currents([]) is None
    assert merge_currents([(None, "SMG")]) is None
    assert merge_currents([(None, "SMG"), (None, "HKO")]) is None


def test_merge_single_candidate_populates_sources_from_that_source():
    smg = _cur("SMG", "外港", ['temperature_c', 'humidity_pct', 'wind_speed_ms'],
               temperature_c=29.0, humidity_pct=83, wind_speed_ms=2.78)
    merged = merge_currents([(smg, "SMG")])
    assert merged is not None
    assert merged.temperature_c == 29.0
    assert merged.humidity_pct == 83
    assert merged.wind_speed_ms == 2.78
    # All three fields must be labeled with SMG's per-field source
    assert merged.sources['temperature_c'] == "SMG 外港"
    assert merged.sources['humidity_pct'] == "SMG 外港"
    assert merged.sources['wind_speed_ms'] == "SMG 外港"
    # Legacy single-label preserved for back-compat consumers
    assert merged.source == "SMG"


def test_merge_prefers_earlier_candidate_when_both_have_field():
    """SMG > HKO priority: if both supply temperature, SMG wins."""
    smg = _cur("SMG", "外港", ['temperature_c'], temperature_c=29.0)
    hko = _cur("HKO", "Central Weather Station", ['temperature_c'], temperature_c=30.5)
    merged = merge_currents([(smg, "SMG"), (hko, "HKO")])
    assert merged.temperature_c == 29.0  # SMG wins
    assert merged.sources['temperature_c'] == "SMG 外港"


def test_merge_fills_missing_field_from_later_candidate():
    """This is the per-field merging behavior that motivated Phase 1.
    SMG bridge station reports wind but no temperature — HKO fills the
    temperature and the sources dict labels each field with its origin."""
    smg = _cur("SMG", "澳門大橋北", ['wind_speed_ms', 'gust_speed_ms'],
               wind_speed_ms=4.72, gust_speed_ms=6.94)
    hko = _cur("HKO", "Hong Kong Observatory", ['temperature_c', 'humidity_pct'],
               temperature_c=29.0, humidity_pct=82)
    merged = merge_currents([(smg, "SMG"), (hko, "HKO")])
    assert merged is not None
    # Wind stays SMG (bridge station's raison d'être)
    assert merged.wind_speed_ms == 4.72
    assert merged.gust_speed_ms == 6.94
    assert merged.sources['wind_speed_ms'] == "SMG 澳門大橋北"
    # Temperature filled from HKO
    assert merged.temperature_c == 29.0
    assert merged.humidity_pct == 82
    assert merged.sources['temperature_c'] == "HKO Hong Kong Observatory"
    assert merged.sources['humidity_pct'] == "HKO Hong Kong Observatory"


def test_merge_skips_none_candidates_transparently():
    """If SMG fetch failed (None), HKO takes over. Priority stays
    intact — no artificial promotion of the failed candidate."""
    hko = _cur("HKO", "Central Weather Station", ['temperature_c'], temperature_c=30.0)
    om = _cur("Open-Meteo", "22.19,113.55", ['wind_speed_ms'], wind_speed_ms=3.5)
    merged = merge_currents([(None, "SMG"), (hko, "HKO"), (om, "Open-Meteo")])
    assert merged.temperature_c == 30.0
    assert merged.wind_speed_ms == 3.5
    assert merged.sources['temperature_c'] == "HKO Central Weather Station"
    assert merged.sources['wind_speed_ms'] == "Open-Meteo 22.19,113.55"
    # Highest-priority successful candidate's label becomes legacy .source
    assert merged.source == "HKO"


def test_merge_leaves_field_unlabeled_when_no_source_supplied_it():
    """If NO candidate claims 'pressure' (or other field), it stays
    absent from sources dict — SPA renders '—' on that tile instead
    of a suspicious zero labeled with a wrong source."""
    smg = _cur("SMG", "外港", ['temperature_c'], temperature_c=29.0)
    hko = _cur("HKO", "Hong Kong Observatory", ['humidity_pct'], humidity_pct=82)
    merged = merge_currents([(smg, "SMG"), (hko, "HKO")])
    # Neither supplied gust — sources must NOT claim it
    assert 'gust_speed_ms' not in merged.sources


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
