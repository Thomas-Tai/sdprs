# -*- coding: utf-8 -*-
"""WXA-004 endpoint overlay tests. Mirrors the direct-coroutine-call idiom of
test_weather_throttle_newapi002.py (no TestClient)."""
import sys
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server.api import weather


class _StubLightning:
    def __init__(self, payload):
        self._payload = payload
    def get_lightning(self, lat, lon):
        return dict(self._payload)  # fresh copy each call


def test_overlay_sets_lightning_and_source(monkeypatch):
    monkeypatch.setattr(weather, "get_lightning_service",
                        lambda: _StubLightning({"count": 3, "nearest": 5.0, "source": "Blitzortung.org"}))
    monkeypatch.setattr(weather, "get_weather_config", lambda: {"site_lat": 22.19, "site_lon": 113.55})
    payload = {"sources": {"temperature_c": "SMG"}}
    weather._overlay_lightning(payload)
    assert payload["lightning"] == {"count": 3, "nearest": 5.0}
    assert payload["sources"]["lightning"] == "Blitzortung.org"


def test_overlay_bare_null_when_service_absent(monkeypatch):
    monkeypatch.setattr(weather, "get_lightning_service", lambda: None)
    payload = {"sources": {"temperature_c": "SMG"}}
    weather._overlay_lightning(payload)
    assert payload["lightning"] == {"count": None, "nearest": None}
    assert "lightning" not in payload["sources"]  # bare "—", no source label


def test_overlay_is_fresh_each_call(monkeypatch):
    stub = _StubLightning({"count": 1, "nearest": 9.0, "source": "Blitzortung.org"})
    monkeypatch.setattr(weather, "get_lightning_service", lambda: stub)
    monkeypatch.setattr(weather, "get_weather_config", lambda: {"site_lat": 22.19, "site_lon": 113.55})
    p1 = {}
    weather._overlay_lightning(p1)
    stub._payload = {"count": 2, "nearest": 4.0, "source": "Blitzortung.org"}  # deque changed
    p2 = {}
    weather._overlay_lightning(p2)
    assert p1["lightning"]["count"] == 1 and p2["lightning"]["count"] == 2  # not frozen


def test_overlay_falls_back_to_settings_site(monkeypatch):
    seen = {}
    class _Rec:
        def get_lightning(self, lat, lon):
            seen["lat"], seen["lon"] = lat, lon
            return {"count": 0, "nearest": None, "source": "Blitzortung.org"}
    monkeypatch.setattr(weather, "get_lightning_service", lambda: _Rec())
    monkeypatch.setattr(weather, "get_weather_config", lambda: {"site_lat": None, "site_lon": None})
    weather._overlay_lightning({})
    s = weather.get_settings()
    assert seen["lat"] == s.SITE_LAT and seen["lon"] == s.SITE_LON
