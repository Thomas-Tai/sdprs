# -*- coding: utf-8 -*-
"""DATA-016 (Low): GET /api/audit must echo the CLAMPED paging values.

`list_audit` clamps `limit` to [1,500] and `offset` to >=0 for the query, but the
response used to echo the RAW request params: `?limit=1000000` came back as
`{"limit": 1000000}` while only 500 rows were ever returned. The SPA pager reads
that echoed value to decide whether more pages exist, so an un-clamped echo tells
it to keep paging past rows the endpoint will never serve. Echo the effective
(clamped) values instead.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")


@pytest.fixture
def client(monkeypatch):
    import central_server.api.audit as audit
    from central_server.config import get_settings

    captured = {}

    def _fake_list_actions(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(audit, "list_actions", _fake_list_actions)
    admin_user = get_settings().DASHBOARD_USER
    monkeypatch.setattr(audit, "_require_session", lambda request: admin_user)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-testing")
    app.include_router(audit.router, prefix="/api")
    c = TestClient(app)
    c._captured = captured
    with c:
        yield c


def test_over_max_limit_echoes_clamped_500(client):
    r = client.get("/api/audit?limit=1000000&offset=-5")
    assert r.status_code == 200
    body = r.json()
    # Echoed value must be the clamp, not the raw param.
    assert body["limit"] == 500, body
    assert body["offset"] == 0, body
    # And the query itself used the clamped values.
    assert client._captured["limit"] == 500
    assert client._captured["offset"] == 0


def test_in_range_values_pass_through(client):
    r = client.get("/api/audit?limit=100&offset=10")
    body = r.json()
    assert body["limit"] == 100
    assert body["offset"] == 10


def test_below_min_limit_clamps_to_one(client):
    r = client.get("/api/audit?limit=0")
    assert r.json()["limit"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
