# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Webcam JPEG Ingest Tests (Task 3b)
Smart Disaster Prevention Response System

Tests for the 1Hz JPEG ingest endpoint (fixes audit C1/C2/C3):
- POST /api/webcam/{node_id}/snapshot (X-API-Key) -> 204

Coverage:
1. Owned camera -> 204; the frame is then readable via the shared edge buffer
   at GET /api/edge/{node_id}/snapshot/latest; webcam_cameras.last_upload is set.
2. Unowned camera -> 403.
3. Missing / invalid key -> 401.
4. Ingest does NOT create a `nodes` row for the camera (C3 stays fixed).
5. Empty body -> 400; >5 MB -> 413.
"""
import pytest
from httpx import AsyncClient, ASGITransport
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from central_server.database import init_db, close_db, get_db_cursor


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Initialize a fresh SQLite DB for each test.

    httpx's ASGITransport does not run the app's lifespan, so nothing else
    would create the webcam_clients / webcam_cameras tables — every query in
    the tests below would fail on a missing table without this.
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
    # ASGITransport does not run the app lifespan, which is what normally
    # initializes the shared in-memory snapshot buffer. The ingest endpoint
    # writes into it (same buffer the edge path uses), so seed it here.
    fastapi_app.state.latest_snapshots = {}
    return fastapi_app


@pytest.fixture
async def authed_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/login", data={"username": "admin", "password": "testpass123"})
        yield c


async def _register_camera(client) -> tuple[str, str]:
    """Create a webcam client + one camera. Returns (api_key, cam_node_id)."""
    resp = await client.post("/api/nodes/webcam", json={"name": "Ingest PC"})
    assert resp.status_code == 201
    api_key = resp.json()["api_key"]
    resp = await client.post(
        "/api/webcam/cameras",
        json={"cameras": [{"name": "Cam 1", "device_index": 0}]},
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 201
    return api_key, resp.json()[0]["node_id"]


@pytest.mark.anyio
async def test_ingest_owned_camera_shares_edge_buffer_and_stamps_last_upload(authed_client):
    api_key, cam = await _register_camera(authed_client)
    headers = {"X-API-Key": api_key}
    jpeg = b"\xff\xd8\xff\xe0THIS-IS-A-FAKE-WEBCAM-JPEG\xff\xd9"

    # Ingest succeeds with 204.
    resp = await authed_client.post(
        f"/api/webcam/{cam}/snapshot", content=jpeg, headers=headers
    )
    assert resp.status_code == 204

    # The frame is now readable through the SHARED edge snapshot buffer, proving
    # POST /snapshot writes request.app.state.latest_snapshots (same as the edge
    # path) so the existing read endpoint serves webcams unchanged.
    resp = await authed_client.get(f"/api/edge/{cam}/snapshot/latest")
    assert resp.status_code == 200
    assert resp.content == jpeg
    assert resp.headers.get("X-Snapshot-Status") != "placeholder"

    # webcam_cameras.last_upload is now non-null (proves the C2 writer).
    with get_db_cursor() as cursor:
        cursor.execute("SELECT last_upload FROM webcam_cameras WHERE node_id = ?", (cam,))
        row = dict(cursor.fetchone())
    assert row["last_upload"] is not None


@pytest.mark.anyio
async def test_ingest_does_not_create_nodes_row(authed_client):
    """C3 guard: ingest must NEVER create a `nodes` row for the webcam camera
    (that dual identity is exactly what reusing touch_node_upload caused)."""
    api_key, cam = await _register_camera(authed_client)
    resp = await authed_client.post(
        f"/api/webcam/{cam}/snapshot",
        content=b"\xff\xd8\xff\xe0abc\xff\xd9",
        headers={"X-API-Key": api_key},
    )
    assert resp.status_code == 204

    with get_db_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS n FROM nodes WHERE node_id = ?", (cam,))
        count = dict(cursor.fetchone())["n"]
    assert count == 0


@pytest.mark.anyio
async def test_ingest_unowned_camera_forbidden(authed_client):
    # Client A owns the camera.
    _owner_key, cam = await _register_camera(authed_client)

    # Client B (different key) tries to push a frame under A's camera.
    resp = await authed_client.post("/api/nodes/webcam", json={"name": "Intruder PC"})
    intruder_key = resp.json()["api_key"]

    resp = await authed_client.post(
        f"/api/webcam/{cam}/snapshot",
        content=b"\xff\xd8\xff\xe0x\xff\xd9",
        headers={"X-API-Key": intruder_key},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_ingest_requires_api_key(authed_client):
    _api_key, cam = await _register_camera(authed_client)
    # No X-API-Key at all.
    resp = await authed_client.post(
        f"/api/webcam/{cam}/snapshot", content=b"\xff\xd8x\xff\xd9"
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_ingest_invalid_api_key(authed_client):
    _api_key, cam = await _register_camera(authed_client)
    resp = await authed_client.post(
        f"/api/webcam/{cam}/snapshot",
        content=b"\xff\xd8x\xff\xd9",
        headers={"X-API-Key": "sk-webcam-not-a-real-key"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_ingest_empty_body_rejected(authed_client):
    api_key, cam = await _register_camera(authed_client)
    resp = await authed_client.post(
        f"/api/webcam/{cam}/snapshot", content=b"", headers={"X-API-Key": api_key}
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_ingest_oversize_body_rejected(authed_client):
    api_key, cam = await _register_camera(authed_client)
    too_big = b"\x00" * (5 * 1024 * 1024 + 1)
    resp = await authed_client.post(
        f"/api/webcam/{cam}/snapshot", content=too_big, headers={"X-API-Key": api_key}
    )
    assert resp.status_code == 413
