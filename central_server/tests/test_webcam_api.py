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
import time

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
async def test_poll_commands_rejects_unowned_camera(authed_client):
    """A client's API key must not be able to long-poll the command queue of
    a camera node_id registered to a DIFFERENT client. Without this check,
    asyncio.Queue.get() is single-consumer FIFO, so a rogue poller racing the
    legitimate client would silently steal the command meant for it."""
    # Client A registers a camera.
    resp = await authed_client.post("/api/nodes/webcam", json={"name": "Owner PC"})
    owner_key = resp.json()["api_key"]
    resp = await authed_client.post("/api/webcam/cameras",
        json={"cameras": [{"name": "Cam 1", "device_index": 0}]},
        headers={"X-API-Key": owner_key})
    cam_node_id = resp.json()[0]["node_id"]

    # Client B (different API key) tries to poll Client A's camera queue.
    resp = await authed_client.post("/api/nodes/webcam", json={"name": "Intruder PC"})
    intruder_key = resp.json()["api_key"]

    resp = await authed_client.get(
        f"/api/webcam/{cam_node_id}/commands?timeout=1",
        headers={"X-API-Key": intruder_key})
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_upload_hls_rejects_path_traversal_filename(authed_client):
    """A filename containing backslash-separated ".." segments must not be
    able to escape the node's HLS directory. Starlette's single-segment
    route match only excludes "/", and on Windows pathlib treats "\\" as a
    separator, so filename="..\\..\\evil.ts" would otherwise write outside
    HLS_STORAGE_PATH/<node_id>/."""
    resp = await authed_client.post("/api/nodes/webcam", json={"name": "Traversal PC"})
    api_key = resp.json()["api_key"]
    headers = {"X-API-Key": api_key}
    resp = await authed_client.post("/api/webcam/cameras",
        json={"cameras": [{"name": "Cam 1", "device_index": 0}]},
        headers=headers)
    cam_node_id = resp.json()[0]["node_id"]

    traversal_filename = "..\\..\\evil.ts"
    resp = await authed_client.put(
        f"/api/webcam/{cam_node_id}/hls/{traversal_filename}",
        content=b"\x00" * 10,
        headers=headers)
    assert resp.status_code == 400


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


# ===== Task 3b: viewer lease model (replaces the raw viewer counter) =========


async def _register_camera(client) -> str:
    """Create a webcam client + one camera. Returns cam_node_id."""
    resp = await client.post("/api/nodes/webcam", json={"name": "Lease PC"})
    api_key = resp.json()["api_key"]
    resp = await client.post(
        "/api/webcam/cameras",
        json={"cameras": [{"name": "Cam 1", "device_index": 0}]},
        headers={"X-API-Key": api_key},
    )
    return resp.json()[0]["node_id"]


@pytest.mark.anyio
async def test_stream_start_is_idempotent_single_lease(authed_client):
    """A second start while a lease is already live stays at viewers==1 and does
    NOT enqueue a duplicate stream_start (0->1 transition only)."""
    from central_server.services import hls_service

    cam = await _register_camera(authed_client)

    r1 = await authed_client.post(f"/api/webcam/{cam}/stream/start")
    assert r1.status_code == 200
    assert r1.json()["viewers"] == 1

    r2 = await authed_client.post(f"/api/webcam/{cam}/stream/start")
    assert r2.status_code == 200
    assert r2.json()["viewers"] == 1  # single lease per node

    # Exactly ONE stream_start command was enqueued.
    first = await hls_service.dequeue_command(cam, timeout=0.5)
    assert first is not None and first["command"] == "stream_start"
    second = await hls_service.dequeue_command(cam, timeout=0.1)
    assert second is None


@pytest.mark.anyio
async def test_stream_renew_extends_lease(authed_client):
    """renew pushes the lease expiry forward so the cleanup scan still sees it
    live."""
    from central_server.services import hls_service

    cam = await _register_camera(authed_client)
    await authed_client.post(f"/api/webcam/{cam}/stream/start")

    # Simulate the lease being close to expiry, then renew.
    hls_service._stream_leases[cam] = time.time() + 2
    resp = await authed_client.post(f"/api/webcam/{cam}/stream/renew")
    assert resp.status_code == 200
    assert resp.json()["viewers"] == 1
    # Lease is now pushed well into the future (~LEASE_TTL_SECONDS).
    assert hls_service._stream_leases[cam] > time.time() + 60


@pytest.mark.anyio
async def test_lease_expiry_forces_stream_stop(authed_client):
    """LOAD-BEARING (H1/H2 regression guard): a lease past expiry ->
    cleanup_stale_streams() MUST enqueue stream_stop to the client AND the client
    can dequeue it. Without this, a forgotten browser tab pins a field PC's
    uplink forever."""
    from central_server.services import hls_service

    cam = await _register_camera(authed_client)

    # Arm the lease via the API (0->1 enqueues stream_start); drain that command.
    resp = await authed_client.post(f"/api/webcam/{cam}/stream/start")
    assert resp.json()["viewers"] == 1
    first = await hls_service.dequeue_command(cam, timeout=0.5)
    assert first is not None and first["command"] == "stream_start"

    # Force the lease into the past, then run the (async) cleanup scan.
    hls_service._stream_leases[cam] = time.time() - 1
    await hls_service.cleanup_stale_streams()

    # The client must be able to dequeue a REAL stream_stop.
    cmd = await hls_service.dequeue_command(cam, timeout=0.1)
    assert cmd is not None
    assert cmd["command"] == "stream_stop"

    # Lease is gone -> viewers back to 0.
    assert hls_service.get_viewer_count(cam) == 0
    assert hls_service.has_active_lease(cam) is False


@pytest.mark.anyio
async def test_stream_stop_releases_lease_immediately(authed_client):
    """Explicit stop drops the lease at once (viewers 0 + stop enqueued)."""
    from central_server.services import hls_service

    cam = await _register_camera(authed_client)
    await authed_client.post(f"/api/webcam/{cam}/stream/start")
    # Drain the stream_start so the queue only holds what stop enqueues.
    await hls_service.dequeue_command(cam, timeout=0.5)

    resp = await authed_client.post(f"/api/webcam/{cam}/stream/stop")
    assert resp.status_code == 200
    assert resp.json()["viewers"] == 0
    assert hls_service.has_active_lease(cam) is False

    cmd = await hls_service.dequeue_command(cam, timeout=0.5)
    assert cmd is not None and cmd["command"] == "stream_stop"
