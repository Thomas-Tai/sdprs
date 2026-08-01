# -*- coding: utf-8 -*-
"""DATA-026 (Low): stream_health bitrate must be null (unknown), not a fake 0,
when there is no prior sample.

/api/stream/health computes bitrate as a first-derivative on a mediamtx bytes
counter, caching the previous reading in the per-process function attribute
`stream_health._cache`. A derivative needs TWO samples. With only one (the very
first scrape, or — under a multi-worker deploy — a request landing on a fresh
worker whose per-process cache is empty) the old code fell back to delta 0 and
reported `bitrate_kbps: 0`, which reads as a measured "no traffic". Report null
instead so the SPA renders it as "—" (StreamHealthCell, OPS-002; api.jsx maps
bitrate_kbps==null -> null). A second sample yields a real integer bitrate.

(The per-process cache itself is a single-worker assumption, consistent with the
login throttle; a true multi-worker fix needs a shared store. This makes the
degradation honest rather than fabricated.)
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

import central_server.api.stream as stream_mod

os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")


class _FakeResp:
    def __init__(self, text):
        self.status_code = 200
        self.text = text


class _FakeClient:
    """Stand-in for httpx.AsyncClient(...) as an async context manager."""

    def __init__(self, text):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return _FakeResp(self._text)


def _metrics(bytes_val):
    return (
        'rtsp_session_bytes_received{path="glass_node_01"} ' + str(bytes_val) + "\n"
        'num_viewers{path="glass_node_01"} 2\n'
    )


def _reset(monkeypatch, text):
    import httpx
    from central_server.config import get_settings

    monkeypatch.setenv("MEDIAMTX_METRICS_URL", "http://mediamtx:9998/metrics")
    get_settings.cache_clear()
    # No stale per-process derivative cache from another test.
    if hasattr(stream_mod.stream_health, "_cache"):
        del stream_mod.stream_health._cache
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(text))


def test_first_scrape_reports_null_bitrate_not_fabricated_zero(monkeypatch):
    _reset(monkeypatch, _metrics(1000))
    result = asyncio.run(stream_mod.stream_health(user="op"))
    assert result["enabled"] and result["reachable"], result
    node = result["nodes"]["glass_node_01"]
    # No prior sample -> unknown, NOT a fabricated measured 0.
    assert node["bitrate_kbps"] is None, node
    from central_server.config import get_settings
    get_settings.cache_clear()


def test_second_scrape_computes_integer_bitrate(monkeypatch):
    _reset(monkeypatch, _metrics(1000))
    # First scrape seeds the per-process cache (bitrate null).
    asyncio.run(stream_mod.stream_health(user="op"))
    # Second scrape: bytes increased -> a real derivative (non-negative int).
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(_metrics(100000)))
    result = asyncio.run(stream_mod.stream_health(user="op"))
    node = result["nodes"]["glass_node_01"]
    assert isinstance(node["bitrate_kbps"], int) and node["bitrate_kbps"] >= 0, node
    from central_server.config import get_settings
    get_settings.cache_clear()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
