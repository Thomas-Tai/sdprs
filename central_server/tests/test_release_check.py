import sys
import asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server.services.release_check import (
    compute_update_available, ReleaseCheckService,
)


def test_compute_unknown_when_version_missing():
    assert compute_update_available(None, "abc") is None

def test_compute_unknown_when_tip_missing():
    assert compute_update_available("abc", None) is None

def test_compute_up_to_date():
    assert compute_update_available("abc123", "abc123") is False

def test_compute_update_available():
    assert compute_update_available("old111", "new222") is True


class _FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
    def json(self):
        return self._body
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc = resp, exc
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, url, **kw):
        if self._exc:
            raise self._exc
        return self._resp


def test_refresh_caches_tip(monkeypatch):
    svc = ReleaseCheckService(owner="o", repo="r", branch="edge-release")
    resp = _FakeResp(200, {"object": {"sha": "cafef00d"}})
    monkeypatch.setattr("central_server.services.release_check.httpx.AsyncClient",
                        lambda *a, **k: _FakeClient(resp=resp))
    asyncio.run(svc.refresh())
    assert svc.tip_sha == "cafef00d"


def test_refresh_keeps_last_on_failure(monkeypatch):
    svc = ReleaseCheckService(owner="o", repo="r", branch="edge-release")
    svc.tip_sha = "known"
    monkeypatch.setattr("central_server.services.release_check.httpx.AsyncClient",
                        lambda *a, **k: _FakeClient(exc=RuntimeError("network")))
    asyncio.run(svc.refresh())  # must not raise
    assert svc.tip_sha == "known"
