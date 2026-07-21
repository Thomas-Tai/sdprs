# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Webcam Node Management API Tests (Task 2)
Smart Disaster Prevention Response System

Tests for:
- POST /api/nodes/webcam (create webcam client)
- POST /api/nodes/{node_id}/revoke-key (rotate API key)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from httpx import AsyncClient, ASGITransport

from central_server.database import init_db, close_db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Initialize a fresh SQLite DB for each test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DB_PATH", db_path)
    init_db(db_path)
    yield
    close_db()


@pytest.fixture
def app(monkeypatch):
    from central_server.config import get_settings
    get_settings.cache_clear()
    from central_server.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Login to get session cookie.
        # conftest.py sets DASHBOARD_USER=admin, DASHBOARD_PASS=PytestSuite2026!
        import os
        user = os.environ.get("DASHBOARD_USER", "admin")
        password = os.environ.get("DASHBOARD_PASS", "PytestSuite2026!")
        await c.post("/login", data={"username": user, "password": password})
        yield c


@pytest.mark.anyio
async def test_create_webcam_client(client):
    resp = await client.post("/api/nodes/webcam", json={"name": "櫃台電腦"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["node_id"].startswith("webcam_")
    assert data["api_key"].startswith("sk-webcam-")
    assert data["name"] == "櫃台電腦"


@pytest.mark.anyio
async def test_revoke_webcam_key(client):
    resp = await client.post("/api/nodes/webcam", json={"name": "Revoke Test"})
    assert resp.status_code == 201
    node_id = resp.json()["node_id"]
    old_key = resp.json()["api_key"]

    resp2 = await client.post(f"/api/nodes/{node_id}/revoke-key")
    assert resp2.status_code == 200
    new_key = resp2.json()["api_key"]
    assert new_key != old_key
    assert new_key.startswith("sk-webcam-")


@pytest.mark.anyio
async def test_revoke_key_nonexistent_node(client):
    resp = await client.post("/api/nodes/webcam_nonexist/revoke-key")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_create_webcam_requires_auth(app):
    """Unauthenticated request must be rejected (401 or redirect)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/nodes/webcam", json={"name": "No Auth"})
        assert resp.status_code in (401, 302, 403)


@pytest.mark.anyio
async def test_create_webcam_empty_name_rejected(client):
    """Pydantic validation: empty name must return 422."""
    resp = await client.post("/api/nodes/webcam", json={"name": ""})
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_create_webcam_name_too_long_rejected(client):
    """Pydantic validation: name > 60 chars must return 422."""
    resp = await client.post("/api/nodes/webcam", json={"name": "x" * 61})
    assert resp.status_code == 422
