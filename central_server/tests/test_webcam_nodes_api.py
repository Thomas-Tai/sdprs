# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Webcam Node Management API Tests (Task 2)
Smart Disaster Prevention Response System

Tests for:
- POST /api/nodes/webcam (create webcam client)
- POST /api/nodes/{node_id}/revoke-key (rotate API key)
- DELETE /api/nodes/webcam/{node_id} (decommission a webcam client)
- register_webcam_cameras() batch atomicity
"""
import sqlite3
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


# =============================================================================
# DELETE /api/nodes/webcam/{node_id} — decommission a webcam client PC.
# Revoking the key only rotates the secret; this is what actually retires it.
# =============================================================================

class _FakeMqttWithLoop:
    """Minimal stand-in for the MQTT service: the delete route only reads
    `_loop` to decide whether a WS broadcast can be scheduled."""
    _loop = object()


def _capture_broadcasts(monkeypatch):
    """Patch the WS + MQTT seams the delete route uses and return the list
    that captured events land in (mirrors test_nodes_api's delete_node test)."""
    import central_server.api.nodes as nodes_api
    import central_server.services.websocket_service as ws_mod

    events = []
    monkeypatch.setattr(ws_mod, "broadcast_from_sync",
                        lambda loop, evt: events.append(evt))
    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: _FakeMqttWithLoop())
    return events


@pytest.mark.anyio
async def test_delete_webcam_client_removes_client_and_cameras(client, monkeypatch):
    """204, both webcam_clients and webcam_cameras rows gone, node_deleted sent."""
    from central_server.database import (
        get_db_cursor, get_webcam_cameras, register_webcam_cameras,
    )

    events = _capture_broadcasts(monkeypatch)

    resp = await client.post("/api/nodes/webcam", json={"name": "Decommission Me"})
    assert resp.status_code == 201
    node_id = resp.json()["node_id"]

    cams = register_webcam_cameras(node_id, [{"name": "Cam A"}, {"name": "Cam B"}])
    assert len(cams) == 2
    assert len(get_webcam_cameras(node_id)) == 2

    resp2 = await client.delete(f"/api/nodes/webcam/{node_id}")
    assert resp2.status_code == 204

    # Cameras must go with the client — SQLite's FK cascade is inert here
    # (no PRAGMA foreign_keys=ON), so this is what proves the explicit delete.
    assert get_webcam_cameras(node_id) == []
    with get_db_cursor() as cursor:
        cursor.execute("SELECT node_id FROM webcam_clients WHERE node_id = ?", (node_id,))
        assert cursor.fetchone() is None

    assert len(events) == 1
    assert events[0]["type"] == "node_deleted"
    assert events[0]["data"] == {"node_id": node_id}


@pytest.mark.anyio
async def test_delete_webcam_client_nonexistent_returns_404(client, monkeypatch):
    """Unknown node_id is a 404, and nothing is broadcast."""
    events = _capture_broadcasts(monkeypatch)

    resp = await client.delete("/api/nodes/webcam/webcam_nonexist")
    assert resp.status_code == 404
    assert events == []


@pytest.mark.anyio
async def test_delete_webcam_client_requires_auth(app):
    """Unauthenticated delete must be rejected."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.delete("/api/nodes/webcam/webcam_whatever")
        assert resp.status_code in (401, 302, 403)


@pytest.mark.anyio
async def test_delete_webcam_client_leaves_other_clients_alone(client, monkeypatch):
    """Deleting one client must not touch another client's cameras."""
    from central_server.database import get_webcam_cameras, register_webcam_cameras

    _capture_broadcasts(monkeypatch)

    keep_id = (await client.post("/api/nodes/webcam", json={"name": "Keep"})).json()["node_id"]
    drop_id = (await client.post("/api/nodes/webcam", json={"name": "Drop"})).json()["node_id"]
    register_webcam_cameras(keep_id, [{"name": "Keep Cam"}])
    register_webcam_cameras(drop_id, [{"name": "Drop Cam"}])

    resp = await client.delete(f"/api/nodes/webcam/{drop_id}")
    assert resp.status_code == 204

    assert get_webcam_cameras(drop_id) == []
    assert [c["name"] for c in get_webcam_cameras(keep_id)] == ["Keep Cam"]


# =============================================================================
# register_webcam_cameras() atomicity — a client registers its WHOLE camera
# list in one call, so a batch that fails halfway must leave no rows at all.
# =============================================================================

@pytest.mark.anyio
async def test_register_webcam_cameras_rolls_back_partial_batch(client, monkeypatch):
    """Force a failure on the 2nd INSERT and assert the 1st did not survive.

    Collide the generated camera node_ids so INSERT #2 violates the
    webcam_cameras PRIMARY KEY. Before the batch was wrapped in one
    transaction, camera #1 had already been committed by its own
    get_db_cursor() block and would still be there after the raise.
    """
    import central_server.database as db

    node_id = (await client.post(
        "/api/nodes/webcam", json={"name": "Atomicity"})).json()["node_id"]

    # Patch AFTER creating the client so its own node_id stays unique.
    monkeypatch.setattr(db.secrets, "token_hex", lambda n: "cafed00d")

    with pytest.raises(sqlite3.IntegrityError):
        db.register_webcam_cameras(node_id, [{"name": "Cam 1"}, {"name": "Cam 2"}])

    # get_webcam_cameras() doesn't generate ids, so the patch is harmless here
    # (and monkeypatch tears it down at end of test).
    assert db.get_webcam_cameras(node_id) == []


@pytest.mark.anyio
async def test_register_webcam_cameras_commits_whole_batch(client):
    """Sanity guard for the rollback test: a clean batch still persists fully."""
    import central_server.database as db

    node_id = (await client.post(
        "/api/nodes/webcam", json={"name": "Batch"})).json()["node_id"]

    db.register_webcam_cameras(
        node_id, [{"name": "C1"}, {"name": "C2"}, {"name": "C3"}])
    assert sorted(c["name"] for c in db.get_webcam_cameras(node_id)) == ["C1", "C2", "C3"]
