# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Webcam Node List Tests (Task 5, Step 0a)
Smart Disaster Prevention Response System

Guards the end-to-end surfacing of webcam cameras through GET /api/nodes:
- a registered webcam camera appears as a node with node_type == "webcam"
- location == the camera name; last_heartbeat / snapshot_timestamp == last_upload
- a camera whose last_upload is older than STALE_THRESHOLD_SECONDS is is_stale

Webcam ingest stamps webcam_cameras (never the `nodes` table, audit C3 closed by
Task 3b), so these rows are disjoint by node_id from the pump/glass list and the
Step 0a append is a plain, non-deduplicated add.

Fixture style mirrors the already-passing test_webcam_nodes_api.py (init_db +
AsyncClient + ASGITransport; anyio's plugin supplies anyio_backend). GET
/api/nodes hard-requires a live mqtt_service — ASGITransport does not run the
lifespan, so we stub the singleton with an empty-state fake; a webcam node
carries no MQTT state, so an empty get_node_states() is all list_nodes needs
before appending the webcam_cameras rows.
"""
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest
from httpx import AsyncClient, ASGITransport

from central_server.database import (
    init_db,
    close_db,
    register_webcam_cameras,
    get_db_cursor,
)
from central_server.timeutil import utcnow

# Keep in lockstep with central_server.api.nodes.STALE_THRESHOLD_SECONDS.
from central_server.api.nodes import STALE_THRESHOLD_SECONDS


class _FakeMqtt:
    """Minimal stand-in for the MQTT singleton. A webcam node has no live MQTT
    state, so an empty node-state map is enough for list_nodes to reach the
    webcam_cameras append."""

    def get_node_states(self):
        return {}

    def get_node_state(self, node_id):
        return None


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Fresh SQLite DB per test + a stubbed mqtt_service so GET /api/nodes
    returns 200 rather than the 503 it raises when no service is wired."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DB_PATH", db_path)
    init_db(db_path)

    from central_server.api import nodes as nodes_api
    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: _FakeMqtt())
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
        import os
        user = os.environ.get("DASHBOARD_USER", "admin")
        password = os.environ.get("DASHBOARD_PASS", "PytestSuite2026!")
        await c.post("/login", data={"username": user, "password": password})
        yield c


def _set_last_upload(cam_node_id: str, iso: str) -> None:
    """Stamp webcam_cameras.last_upload directly (register_webcam_cameras leaves
    it NULL). Used to plant a known-fresh or known-stale timestamp."""
    with get_db_cursor() as cursor:
        cursor.execute(
            "UPDATE webcam_cameras SET last_upload = ? WHERE node_id = ?",
            (iso, cam_node_id),
        )


async def _register_camera(client, client_name: str, camera_name: str) -> str:
    """Create a webcam client PC then register one camera under it. Returns the
    camera's node_id."""
    created = await client.post("/api/nodes/webcam", json={"name": client_name})
    assert created.status_code == 201
    client_node_id = created.json()["node_id"]
    cams = register_webcam_cameras(
        client_node_id, [{"name": camera_name, "device_index": 0}]
    )
    return cams[0]["node_id"]


@pytest.mark.anyio
async def test_webcam_camera_appears_as_webcam_node(client):
    """A registered camera with a fresh upload surfaces as a node_type=='webcam'
    row whose location is the camera name and whose last_heartbeat /
    snapshot_timestamp both carry last_upload; a fresh upload is not stale."""
    cam_id = await _register_camera(client, "櫃台電腦", "Front Door")
    fresh = utcnow().isoformat()
    _set_last_upload(cam_id, fresh)

    resp = await client.get("/api/nodes")
    assert resp.status_code == 200
    by_id = {n["node_id"]: n for n in resp.json()}

    assert cam_id in by_id, f"{cam_id} missing from {list(by_id)}"
    row = by_id[cam_id]
    assert row["node_type"] == "webcam"
    assert row["location"] == "Front Door"
    assert row["status"] == "OFFLINE"          # webcam_cameras default
    assert row["last_heartbeat"] == fresh
    assert row["snapshot_timestamp"] == fresh
    assert row["is_stale"] is False


@pytest.mark.anyio
async def test_webcam_camera_stale_when_upload_old(client):
    """last_upload older than STALE_THRESHOLD_SECONDS comes back is_stale: true."""
    cam_id = await _register_camera(client, "後門主機", "Back Door")
    old = (utcnow() - timedelta(seconds=STALE_THRESHOLD_SECONDS + 50)).isoformat()
    _set_last_upload(cam_id, old)

    resp = await client.get("/api/nodes")
    assert resp.status_code == 200
    by_id = {n["node_id"]: n for n in resp.json()}

    assert cam_id in by_id
    row = by_id[cam_id]
    assert row["node_type"] == "webcam"
    assert row["is_stale"] is True
    assert row["last_heartbeat"] == old


@pytest.mark.anyio
async def test_webcam_camera_never_uploaded_not_stale(client):
    """A camera that has never uploaded (last_upload NULL) is not falsely marked
    stale, and its heartbeat/snapshot timestamps come back null rather than
    crashing the list."""
    cam_id = await _register_camera(client, "大堂主機", "Lobby")
    # No _set_last_upload -> last_upload stays NULL.

    resp = await client.get("/api/nodes")
    assert resp.status_code == 200
    by_id = {n["node_id"]: n for n in resp.json()}

    assert cam_id in by_id
    row = by_id[cam_id]
    assert row["node_type"] == "webcam"
    assert row["is_stale"] is False
    assert row["last_heartbeat"] is None
    assert row["snapshot_timestamp"] is None


@pytest.mark.anyio
async def test_webcam_node_does_not_leak_into_nodes_table(client):
    """Registering a webcam camera must NOT create a matching `nodes` row (audit
    C3): the two lists are disjoint by node_id, so the webcam id appears exactly
    once in GET /api/nodes."""
    cam_id = await _register_camera(client, "側門主機", "Side Door")

    resp = await client.get("/api/nodes")
    assert resp.status_code == 200
    ids = [n["node_id"] for n in resp.json()]
    assert ids.count(cam_id) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
