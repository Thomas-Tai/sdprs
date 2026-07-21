# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Webcam Router API Tests (Task 3)
Smart Disaster Prevention Response System

Tests for:
- POST /api/webcam/cameras (register cameras)
- PUT  /api/webcam/{node_id}/hls/{filename} (upload HLS segment/playlist)
- GET  /api/webcam/{node_id}/hls/{filename} (serve HLS file)
- POST /api/webcam/{node_id}/stream/start
- POST /api/webcam/{node_id}/stream/stop
- GET  /api/webcam/{node_id}/commands (long-poll)
"""
import pytest
from httpx import AsyncClient, ASGITransport
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from central_server.database import init_db, close_db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Initialize a fresh SQLite DB for each test.

    httpx's ASGITransport does not run the app's lifespan, so nothing else
    would create the webcam_clients / webcam_cameras tables — every query
    in the tests below would fail on a missing table without this.
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DB_PATH", db_path)
    init_db(db_path)
    yield
    close_db()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASS", "testpass123")
    monkeypatch.setenv("EDGE_API_KEY", "test-edge-key-12345678901234567890")
    monkeypatch.setenv("SECRET_KEY", "a" * 32)
    monkeypatch.setenv("HLS_STORAGE_PATH", str(tmp_path / "hls"))
    from central_server.config import get_settings
    get_settings.cache_clear()
    from central_server.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
async def authed_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/login", data={"username": "admin", "password": "testpass123"})
        yield c


@pytest.mark.anyio
async def test_full_webcam_flow(authed_client, tmp_path):
    # 1. Create webcam client
    resp = await authed_client.post("/api/nodes/webcam", json={"name": "Test PC"})
    assert resp.status_code == 201
    api_key = resp.json()["api_key"]

    # 2. Register cameras
    headers = {"X-API-Key": api_key}
    resp = await authed_client.post("/api/webcam/cameras",
        json={"cameras": [{"name": "Cam 1", "device_index": 0}]},
        headers=headers)
    assert resp.status_code == 201
    cam_node_id = resp.json()[0]["node_id"]

    # 3. Upload HLS segment
    resp = await authed_client.put(
        f"/api/webcam/{cam_node_id}/hls/seg_000001.ts",
        content=b"\x00" * 100,
        headers=headers)
    assert resp.status_code == 204

    # 4. Upload playlist
    playlist = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:2.0,\nseg_000001.ts\n"
    resp = await authed_client.put(
        f"/api/webcam/{cam_node_id}/hls/playlist.m3u8",
        content=playlist.encode(),
        headers=headers)
    assert resp.status_code == 204

    # 5. Serve HLS file (dashboard auth)
    resp = await authed_client.get(f"/api/webcam/{cam_node_id}/hls/playlist.m3u8")
    assert resp.status_code == 200
    assert b"seg_000001.ts" in resp.content

    # 6. Stream start
    resp = await authed_client.post(f"/api/webcam/{cam_node_id}/stream/start")
    assert resp.status_code == 200
    assert resp.json()["viewers"] == 1

    # 7. Poll commands (should get stream_start)
    resp = await authed_client.get(
        f"/api/webcam/{cam_node_id}/commands?timeout=1",
        headers=headers)
    assert resp.status_code == 200
    assert resp.json()["command"] == "stream_start"

    # 8. Stream stop
    resp = await authed_client.post(f"/api/webcam/{cam_node_id}/stream/stop")
    assert resp.status_code == 200
    assert resp.json()["viewers"] == 0


@pytest.mark.anyio
async def test_register_cameras_requires_api_key(authed_client):
    """No X-API-Key header must be rejected with 401."""
    resp = await authed_client.post("/api/webcam/cameras",
        json={"cameras": [{"name": "Cam 1", "device_index": 0}]})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_upload_hls_rejects_disallowed_extension(authed_client):
    resp = await authed_client.post("/api/nodes/webcam", json={"name": "Ext Test PC"})
    api_key = resp.json()["api_key"]
    headers = {"X-API-Key": api_key}
    resp = await authed_client.post("/api/webcam/cameras",
        json={"cameras": [{"name": "Cam 1", "device_index": 0}]},
        headers=headers)
    cam_node_id = resp.json()[0]["node_id"]

    resp = await authed_client.put(
        f"/api/webcam/{cam_node_id}/hls/evil.exe",
        content=b"not-a-segment",
        headers=headers)
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_upload_hls_rejects_unowned_camera(authed_client):
    """A client's API key must not be able to upload segments under a
    camera node_id registered to a DIFFERENT client."""
    # Client A registers a camera.
    resp = await authed_client.post("/api/nodes/webcam", json={"name": "Owner PC"})
    owner_key = resp.json()["api_key"]
    resp = await authed_client.post("/api/webcam/cameras",
        json={"cameras": [{"name": "Cam 1", "device_index": 0}]},
        headers={"X-API-Key": owner_key})
    cam_node_id = resp.json()[0]["node_id"]

    # Client B (different API key) tries to upload under Client A's camera.
    resp = await authed_client.post("/api/nodes/webcam", json={"name": "Intruder PC"})
    intruder_key = resp.json()["api_key"]

    resp = await authed_client.put(
        f"/api/webcam/{cam_node_id}/hls/seg_000001.ts",
        content=b"\x00" * 100,
        headers={"X-API-Key": intruder_key})
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_upload_hls_rejects_empty_body(authed_client):
    resp = await authed_client.post("/api/nodes/webcam", json={"name": "Empty Test PC"})
    api_key = resp.json()["api_key"]
    headers = {"X-API-Key": api_key}
    resp = await authed_client.post("/api/webcam/cameras",
        json={"cameras": [{"name": "Cam 1", "device_index": 0}]},
        headers=headers)
    cam_node_id = resp.json()[0]["node_id"]

    resp = await authed_client.put(
        f"/api/webcam/{cam_node_id}/hls/seg_000001.ts",
        content=b"",
        headers=headers)
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_serve_hls_requires_dashboard_auth(app):
    """Unauthenticated GET of an HLS file must be rejected."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/webcam/webcam_deadbeef/hls/playlist.m3u8")
        assert resp.status_code in (401, 302, 403)


@pytest.mark.anyio
async def test_serve_hls_file_not_found(authed_client):
    resp = await authed_client.get("/api/webcam/webcam_deadbeef/hls/playlist.m3u8")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_poll_commands_returns_none_when_empty(authed_client):
    resp = await authed_client.post("/api/nodes/webcam", json={"name": "Idle PC"})
    api_key = resp.json()["api_key"]
    headers = {"X-API-Key": api_key}
    resp = await authed_client.post("/api/webcam/cameras",
        json={"cameras": [{"name": "Cam 1", "device_index": 0}]},
        headers=headers)
    cam_node_id = resp.json()[0]["node_id"]

    resp = await authed_client.get(
        f"/api/webcam/{cam_node_id}/commands?timeout=1",
        headers=headers)
    assert resp.status_code == 200
    assert resp.json()["command"] is None


@pytest.mark.anyio
async def test_stream_stop_without_start_does_not_go_negative(authed_client):
    resp = await authed_client.post("/api/nodes/webcam", json={"name": "Stop First PC"})
    api_key = resp.json()["api_key"]
    resp = await authed_client.post("/api/webcam/cameras",
        json={"cameras": [{"name": "Cam 1", "device_index": 0}]},
        headers={"X-API-Key": api_key})
    cam_node_id = resp.json()[0]["node_id"]

    resp = await authed_client.post(f"/api/webcam/{cam_node_id}/stream/stop")
    assert resp.status_code == 200
    assert resp.json()["viewers"] == 0
