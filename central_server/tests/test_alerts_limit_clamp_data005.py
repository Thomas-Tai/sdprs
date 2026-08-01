# -*- coding: utf-8 -*-
"""DATA-005 (Medium): GET /api/alerts must clamp `limit`/`offset`.

`list_alerts` took `limit: int = 50` and passed it straight to the DB helpers
with no upper bound, so `?limit=1000000000` would ask the database for a billion
rows (a trivial memory/DoS lever on an operator-facing endpoint). The sibling
audit endpoint already clamps `min(max(int(limit), 1), 500)`; the alerts list
never got the same treatment. These pin the clamp: huge -> 500, non-positive
-> 1, negative offset -> 0, and a normal value passes through untouched.

The DB helpers are imported inside the handler (`from ..database import ...`),
so the spies patch them on `central_server.database` (what the late import
resolves) and record the effective limit/offset the handler forwards.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

# Strong values so config.validate_settings (fail-closed at startup) accepts the
# session — matches the sibling alerts suites exactly.
os.environ["DASHBOARD_USER"] = "admin"
os.environ["DASHBOARD_PASS"] = "testpass123"
os.environ["EDGE_API_KEY"] = "test-api-key-12345"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing"


@pytest.fixture
def test_db():
    import central_server.database as db_module

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = db_module.init_db(db_path)
    try:
        yield conn
    finally:
        db_module.close_db()
        try:
            os.unlink(db_path)
        except OSError:
            pass


@pytest.fixture
def client(test_db):
    from central_server.api.alerts import router as alerts_router

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-testing")
    app.include_router(alerts_router, prefix="/api")
    app.state.latest_snapshots = {}
    with TestClient(app) as c:
        yield c


API_HEADERS = {"X-API-Key": "test-api-key-12345"}


def _spy_get_all_events(monkeypatch):
    captured = {}

    def fake(limit, offset):
        captured["limit"] = limit
        captured["offset"] = offset
        return []

    monkeypatch.setattr("central_server.database.get_all_events", fake)
    return captured


def test_huge_limit_is_clamped_to_500(client, monkeypatch):
    captured = _spy_get_all_events(monkeypatch)
    r = client.get("/api/alerts?limit=1000000000", headers=API_HEADERS)
    assert r.status_code == 200
    assert captured["limit"] == 500, captured


def test_nonpositive_limit_is_clamped_to_1(client, monkeypatch):
    captured = _spy_get_all_events(monkeypatch)
    r = client.get("/api/alerts?limit=0", headers=API_HEADERS)
    assert r.status_code == 200
    assert captured["limit"] == 1, captured


def test_negative_offset_is_clamped_to_0(client, monkeypatch):
    captured = _spy_get_all_events(monkeypatch)
    r = client.get("/api/alerts?limit=50&offset=-5", headers=API_HEADERS)
    assert r.status_code == 200
    assert captured["offset"] == 0, captured


def test_normal_limit_passes_through(client, monkeypatch):
    captured = _spy_get_all_events(monkeypatch)
    r = client.get("/api/alerts?limit=50&offset=10", headers=API_HEADERS)
    assert r.status_code == 200
    assert captured["limit"] == 50 and captured["offset"] == 10, captured


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
