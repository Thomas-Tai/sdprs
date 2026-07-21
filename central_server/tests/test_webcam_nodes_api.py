# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Webcam Node Management API Tests (Task 2)
Smart Disaster Prevention Response System

Tests for:
- POST /api/nodes/webcam (create webcam client)
- POST /api/nodes/{node_id}/revoke-key (rotate API key)
- DELETE /api/nodes/webcam/{node_id} (decommission a webcam client)
- register_webcam_cameras() batch atomicity
- THE ID SEAM (end-to-end, mimicking the dashboard): GET /api/nodes lists
  CAMERA rows, but both admin endpoints above take the owning CLIENT's
  node_id. Both ids are minted as f"webcam_{secrets.token_hex(4)}" — identical
  in shape, independently generated, so they NEVER match. Until webcam rows
  carried client_id there was no way for the UI to address the client: the
  shipped 撤銷 Key button 404'd 100% of the time, and the delete button would
  have 404'd too (which the UI treats as "already gone" — i.e. it would have
  looked like it worked while doing nothing).
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
async def test_delete_releases_each_cameras_hls_lease(client, monkeypatch):
    """Deleting a client must release its CAMERAS' viewer leases.

    Another face of the id seam: HLS leases are keyed by the CAMERA node_id
    (stream/start arms one per camera), never by the client's. So a
    `release_lease(client_node_id)` is a silent no-op that merely *looks* like
    cleanup. The endpoint therefore captures the camera ids BEFORE deleting
    (afterwards the rows are gone and cannot be enumerated) and releases each.
    Under the old client-id call this test fails.
    """
    from central_server.database import register_webcam_cameras
    from central_server.services import hls_service

    _capture_broadcasts(monkeypatch)

    resp = await client.post("/api/nodes/webcam", json={"name": "Lease PC"})
    assert resp.status_code == 201
    node_id = resp.json()["node_id"]

    cams = register_webcam_cameras(node_id, [{"name": "Cam A"}, {"name": "Cam B"}])
    cam_ids = [c["node_id"] for c in cams]
    assert node_id not in cam_ids  # the seam: client id is never a camera id

    # Arm a viewer lease on each camera, exactly as POST /stream/start does.
    for cid in cam_ids:
        hls_service.touch_lease(cid)
        assert hls_service.has_active_lease(cid) is True

    resp2 = await client.delete(f"/api/nodes/webcam/{node_id}")
    assert resp2.status_code == 204

    for cid in cam_ids:
        assert hls_service.has_active_lease(cid) is False, (
            f"lease for camera {cid} survived the client delete — release_lease "
            "was likely called with the client id, which matches nothing"
        )


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
# THE ID SEAM — end-to-end, exactly as the dashboard drives it:
#   GET /api/nodes  ->  pick a webcam row  ->  admin it by that row's client_id
# A camera id must NEVER be sent to these endpoints, and a webcam row must
# never come back without a client_id (that is what forces the UI to guess).
# =============================================================================

class _FakeMqttForNodeList:
    """Stands in for the MQTT singleton on BOTH paths the end-to-end test walks:
    GET /api/nodes (needs get_node_states) and the delete route's WS broadcast
    (needs `_loop`). A webcam carries no MQTT state, so an empty map is enough
    for list_nodes to reach the webcam_cameras append."""

    _loop = object()

    def get_node_states(self):
        return {}

    def get_node_state(self, node_id):
        return None


@pytest.mark.anyio
async def test_node_list_client_id_drives_revoke_and_delete(client, monkeypatch):
    """THE regression: drive revoke + delete using ONLY what GET /api/nodes gave
    the dashboard, and prove both land.

    Before client_id was surfaced, the only id on a webcam row was the CAMERA's
    node_id — and both endpoints below 404 on a camera id (see the companion
    test). This walks the real dashboard path end to end: list, take a row, use
    row["client_id"], revoke (200), delete (204), and confirm BOTH cameras and
    the client are gone from the DB and from the node list itself.
    """
    import central_server.api.nodes as nodes_api
    import central_server.services.websocket_service as ws_mod
    from central_server.database import (
        get_db_cursor, get_webcam_cameras, register_webcam_cameras,
    )

    events = []
    monkeypatch.setattr(ws_mod, "broadcast_from_sync",
                        lambda loop, evt: events.append(evt))
    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: _FakeMqttForNodeList())

    created = await client.post("/api/nodes/webcam", json={"name": "Bench PC"})
    assert created.status_code == 201
    client_node_id = created.json()["node_id"]

    cams = register_webcam_cameras(
        client_node_id, [{"name": "Cam 1"}, {"name": "Cam 2"}])
    cam_ids = sorted(c["node_id"] for c in cams)
    assert len(cam_ids) == 2

    # --- what the dashboard actually receives -------------------------------
    listed = await client.get("/api/nodes")
    assert listed.status_code == 200
    webcam_rows = [n for n in listed.json() if n["node_type"] == "webcam"]
    assert sorted(n["node_id"] for n in webcam_rows) == cam_ids, \
        "GET /api/nodes lists CAMERA rows, not client rows"

    # Every webcam row must be addressable. A null here means the UI has
    # nothing to send but the camera id, which is the shipped 404.
    assert all(n.get("client_id") for n in webcam_rows), webcam_rows
    assert {n["client_id"] for n in webcam_rows} == {client_node_id}
    # ...and the two ids are genuinely different values of the same shape.
    assert all(n["client_id"] != n["node_id"] for n in webcam_rows)

    # The client's HUMAN name rides along (webcam_clients.name, LEFT JOINed in)
    # so the delete confirm dialog can say 「Bench PC」 instead of the opaque
    # webcam_xxxxxxxx the operator has never seen. Display only — the id above
    # is still what gets sent.
    assert {n.get("client_name") for n in webcam_rows} == {"Bench PC"}
    # ...and it must not be confused with the CAMERA's own name, which is what
    # `location` carries.
    assert sorted(n["location"] for n in webcam_rows) == ["Cam 1", "Cam 2"]

    # --- admin the CLIENT using only the row's own client_id ----------------
    row = webcam_rows[0]
    revoked = await client.post(f"/api/nodes/{row['client_id']}/revoke-key")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["api_key"].startswith("sk-webcam-")

    deleted = await client.delete(f"/api/nodes/webcam/{row['client_id']}")
    assert deleted.status_code == 204, deleted.text

    # BOTH cameras go with the client — not just the row that was clicked.
    assert get_webcam_cameras(client_node_id) == []
    with get_db_cursor() as cursor:
        cursor.execute("SELECT node_id FROM webcam_cameras WHERE node_id IN (?, ?)",
                       (cam_ids[0], cam_ids[1]))
        assert cursor.fetchall() == []
        cursor.execute("SELECT node_id FROM webcam_clients WHERE node_id = ?",
                       (client_node_id,))
        assert cursor.fetchone() is None

    # And the dashboard's own view agrees on the next refresh.
    relisted = await client.get("/api/nodes")
    assert relisted.status_code == 200
    assert [n for n in relisted.json() if n["node_type"] == "webcam"] == []

    assert [e["type"] for e in events] == ["node_deleted"]


@pytest.mark.anyio
async def test_node_list_carries_client_name_for_every_webcam_row(client, monkeypatch):
    """Every webcam row names its owning client PC, and siblings agree.

    The delete dialog decommissions the whole client, so it must be able to
    show the name the operator typed at creation. Two clients here so a broken
    join (e.g. a cross product, or reading the CAMERA's name into the field)
    cannot pass by accident.
    """
    import central_server.api.nodes as nodes_api
    from central_server.database import register_webcam_cameras

    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: _FakeMqttForNodeList())

    a_id = (await client.post("/api/nodes/webcam", json={"name": "櫃台電腦"})).json()["node_id"]
    b_id = (await client.post("/api/nodes/webcam", json={"name": "車道主機"})).json()["node_id"]
    register_webcam_cameras(a_id, [{"name": "前門"}, {"name": "後門"}])
    register_webcam_cameras(b_id, [{"name": "車道"}])

    listed = await client.get("/api/nodes")
    assert listed.status_code == 200
    rows = [n for n in listed.json() if n["node_type"] == "webcam"]
    assert len(rows) == 3

    by_client = {}
    for n in rows:
        by_client.setdefault(n["client_id"], set()).add(n["client_name"])
    assert by_client == {a_id: {"櫃台電腦"}, b_id: {"車道主機"}}


@pytest.mark.anyio
async def test_orphan_camera_still_lists_with_null_client_name(client, monkeypatch):
    """LEFT JOIN, not INNER: a camera whose webcam_clients row is missing must
    STILL appear in the node list, with client_name None.

    An INNER JOIN would make such a row silently vanish from the dashboard
    while it kept uploading — the operator would have no way to see, let alone
    clean up, a camera that is still writing snapshots to disk.
    """
    import central_server.api.nodes as nodes_api
    from central_server.database import get_db_cursor, register_webcam_cameras

    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: _FakeMqttForNodeList())

    orphan_client = (await client.post(
        "/api/nodes/webcam", json={"name": "Vanishing PC"})).json()["node_id"]
    keep_client = (await client.post(
        "/api/nodes/webcam", json={"name": "Healthy PC"})).json()["node_id"]
    orphan_cam = register_webcam_cameras(orphan_client, [{"name": "Orphan Cam"}])[0]["node_id"]
    keep_cam = register_webcam_cameras(keep_client, [{"name": "Keep Cam"}])[0]["node_id"]

    # Rip out ONLY the client row, leaving the camera behind (SQLite's FK
    # cascade is inert here — no PRAGMA foreign_keys=ON — which is precisely
    # how this state can occur in the field).
    with get_db_cursor() as cursor:
        cursor.execute("DELETE FROM webcam_clients WHERE node_id = ?", (orphan_client,))

    listed = await client.get("/api/nodes")
    assert listed.status_code == 200
    rows = {n["node_id"]: n for n in listed.json() if n["node_type"] == "webcam"}

    assert orphan_cam in rows, f"orphan camera vanished from the node list: {list(rows)}"
    assert rows[orphan_cam]["client_name"] is None
    # The id is still there — the row stays addressable/diagnosable.
    assert rows[orphan_cam]["client_id"] == orphan_client
    assert rows[orphan_cam]["location"] == "Orphan Cam"

    # The healthy sibling client is unaffected by the join.
    assert rows[keep_cam]["client_name"] == "Healthy PC"


@pytest.mark.anyio
async def test_camera_node_id_is_404_on_both_client_endpoints(client, monkeypatch):
    """The bug's signature, pinned: a CAMERA node_id 404s on revoke AND delete.

    This must STAY a 404 — the endpoints key on webcam_clients by design. It is
    the UI's job never to send a camera id (see the render tests: revoke/delete
    are wired to node.clientId, and a row without one disables the button).
    """
    from central_server.database import get_webcam_cameras, register_webcam_cameras

    events = _capture_broadcasts(monkeypatch)

    created = await client.post("/api/nodes/webcam", json={"name": "Bench PC 2"})
    assert created.status_code == 201
    client_node_id = created.json()["node_id"]
    cam_id = register_webcam_cameras(client_node_id, [{"name": "Cam 1"}])[0]["node_id"]

    # Same prefix, same length, different value — indistinguishable by eye,
    # which is exactly why the wrong one got sent.
    assert cam_id != client_node_id
    assert cam_id.startswith("webcam_") and client_node_id.startswith("webcam_")
    assert len(cam_id) == len(client_node_id)

    revoked = await client.post(f"/api/nodes/{cam_id}/revoke-key")
    assert revoked.status_code == 404

    deleted = await client.delete(f"/api/nodes/webcam/{cam_id}")
    assert deleted.status_code == 404

    # A wrong-id call must be inert: nothing deleted, nothing broadcast.
    assert [c["node_id"] for c in get_webcam_cameras(client_node_id)] == [cam_id]
    assert events == []


# =============================================================================
# Clientless webcam client — created via 新增 Webcam Client but never provisioned
# by the wizard, so it has NO camera rows. GET /api/nodes lists CAMERAS, so
# without a dedicated surface such a client is invisible in the node list, which
# means its live API key can never be revoked nor the client retired.
# =============================================================================

@pytest.mark.anyio
async def test_node_list_surfaces_clientless_webcam_client(client, monkeypatch):
    """A camera-less webcam client must still appear so it stays revocable.

    It appears as its own webcam node whose client_id IS its own node_id (a
    clientless client is the addressable client), so the revoke-key / delete
    affordances — which key on the CLIENT node_id — target it directly. Before
    this surface existed the row never appeared and the key was undead: valid
    forever with no UI path to rotate or retire it.
    """
    import central_server.api.nodes as nodes_api
    import central_server.services.websocket_service as ws_mod
    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: _FakeMqttForNodeList())
    # This test also DELETEs, which broadcasts node_deleted; stub the WS seam so
    # the real broadcaster isn't fired against the fake's dummy loop (which would
    # leave an un-awaited coroutine warning).
    monkeypatch.setattr(ws_mod, "broadcast_from_sync", lambda loop, evt: None)

    created = await client.post("/api/nodes/webcam", json={"name": "Unprovisioned PC"})
    assert created.status_code == 201
    client_node_id = created.json()["node_id"]  # no register_webcam_cameras call

    listed = await client.get("/api/nodes")
    assert listed.status_code == 200
    rows = [n for n in listed.json() if n["node_type"] == "webcam"]
    assert len(rows) == 1, f"clientless webcam client did not surface: {rows}"
    row = rows[0]
    # It IS its own client — node_id and client_id coincide here (unlike a camera
    # row, where they are deliberately different).
    assert row["node_id"] == client_node_id
    assert row["client_id"] == client_node_id
    assert row["client_name"] == "Unprovisioned PC"

    # And it is genuinely retireable via the exact id the row hands the UI.
    deleted = await client.delete(f"/api/nodes/webcam/{row['client_id']}")
    assert deleted.status_code == 204
    relisted = await client.get("/api/nodes")
    assert [n for n in relisted.json() if n["node_type"] == "webcam"] == []


@pytest.mark.anyio
async def test_provisioned_client_not_double_listed(client, monkeypatch):
    """A client WITH cameras appears once per camera, never also as a bare row.

    The clientless surface excludes provisioned clients via
    NOT IN (SELECT client_id FROM webcam_cameras). Without that guard a client
    with two cameras would show three rows — two cameras plus a phantom client.
    """
    from central_server.database import register_webcam_cameras
    import central_server.api.nodes as nodes_api
    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: _FakeMqttForNodeList())

    provisioned = (await client.post(
        "/api/nodes/webcam", json={"name": "Has Cameras"})).json()["node_id"]
    clientless = (await client.post(
        "/api/nodes/webcam", json={"name": "No Cameras"})).json()["node_id"]
    cams = register_webcam_cameras(provisioned, [{"name": "A"}, {"name": "B"}])
    cam_ids = sorted(c["node_id"] for c in cams)

    listed = await client.get("/api/nodes")
    rows = [n for n in listed.json() if n["node_type"] == "webcam"]
    # Exactly the two camera rows + the one clientless row — no phantom for the
    # provisioned client.
    node_ids = sorted(n["node_id"] for n in rows)
    assert node_ids == sorted(cam_ids + [clientless]), node_ids
    assert provisioned not in node_ids  # provisioned client is NOT itself a row


# =============================================================================
# DELETE + live stream — deleting a client whose cameras are being watched must
# tell the field PC to stop encoding. release_lease pops the lease, which also
# removes it from cleanup_stale_streams' expiry scan, so an explicit stream_stop
# is the ONLY thing that ever reaches the client. stream_stop is the EXISTING
# control command — no new downlink surface is introduced.
# =============================================================================

@pytest.mark.anyio
async def test_delete_enqueues_stream_stop_only_for_live_cameras(client, monkeypatch):
    """Live cameras get a stream_stop on delete; idle cameras get nothing.

    Proves both halves: the enqueue (so the field PC stops encoding a camera
    that no longer exists) and the `if was_live` guard (no spurious command to a
    camera nobody was watching).
    """
    from central_server.database import register_webcam_cameras
    from central_server.services import hls_service

    _capture_broadcasts(monkeypatch)

    node_id = (await client.post(
        "/api/nodes/webcam", json={"name": "Streaming PC"})).json()["node_id"]
    cams = register_webcam_cameras(
        node_id, [{"name": "Live A"}, {"name": "Live B"}, {"name": "Idle C"}])
    cam_ids = [c["node_id"] for c in cams]
    live_ids, idle_id = cam_ids[:2], cam_ids[2]

    # Two cameras being watched, one not.
    for cid in live_ids:
        hls_service.touch_lease(cid)
    assert hls_service.has_active_lease(idle_id) is False

    resp = await client.delete(f"/api/nodes/webcam/{node_id}")
    assert resp.status_code == 204

    # Each watched camera is commanded to stop. Under the pre-fix code (only
    # release_lease, no enqueue) these queues are empty and the dequeue is None.
    for cid in live_ids:
        cmd = await hls_service.dequeue_command(cid, timeout=0.2)
        assert cmd is not None, f"no stream_stop enqueued for live camera {cid}"
        assert cmd["command"] == "stream_stop"

    # The idle camera nobody watched gets no command — the was_live guard holds.
    assert await hls_service.dequeue_command(idle_id, timeout=0.2) is None


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
