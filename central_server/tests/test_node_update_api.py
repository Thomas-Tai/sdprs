# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Node Update API Unit Tests
Smart Disaster Prevention Response System

Phase 2 (edge auto-update): NodeStatus.version / NodeStatus.update_available
serialization and POST /api/nodes/{node_id}/update (dashboard "Update now").

Follows tests/test_nodes_api.py for app construction + get_current_user
override + the FakeMqttService / monkeypatch conventions.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import os

# Set required environment variables before importing the app
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from central_server.api import nodes as nodes_api
from central_server.auth import get_current_user
from central_server.services import release_check as release_check_module
from central_server.services.release_check import compute_update_available


# =============================================================================
# Pure serialization intent — documents the required behavior of the helper
# NodeStatus.update_available is derived from (Task 5).
# =============================================================================

def test_update_available_helper_contract():
    assert compute_update_available("a", "a") is False
    assert compute_update_available("a", "b") is True
    assert compute_update_available(None, "b") is None


# =============================================================================
# Endpoint tests — mirror test_nodes_api.py's FakeMqttService + client fixture,
# extended with get_node_state/send_update_command so the POST /update route
# can be exercised end to end.
# =============================================================================

class FakeMqttService:
    """Stand-in for MQTTService exposing what the nodes routes call,
    including the two methods POST /nodes/{id}/update needs."""

    def __init__(self, states):
        self._states = states
        self.update_calls = []
        self.send_update_result = True

    def get_node_states(self):
        return self._states

    def get_node_state(self, node_id):
        return self._states.get(node_id)

    def send_update_command(self, node_id):
        self.update_calls.append(node_id)
        return self.send_update_result


RELEASE_TIP = "723456f"


@pytest.fixture
def node_states():
    """Two nodes with LIVE MQTT state:
    - glass_node_01: ONLINE, reports version == the release tip -> up to date.
    - glass_node_02: OFFLINE, reports no version at all -> null/unknown.
    glass_node_03 deliberately has NO live state (exercises the DB-only
    fallback loop in list_nodes). pump_node_01 also has no live state."""
    return {
        "glass_node_01": {
            "type": "glass",
            "status": "ONLINE",
            "version": RELEASE_TIP,
        },
        "glass_node_02": {
            "type": "glass",
            "status": "OFFLINE",
        },
    }


@pytest.fixture
def db_rows():
    """DB rows as returned by get_all_nodes()/db_get_node(). metadata is
    already the parsed dict shape database.get_node/get_all_nodes hand back
    (json.loads already applied) — see database.py:1167-1169/1185-1188."""
    return {
        "glass_node_01": {
            "node_id": "glass_node_01",
            "node_type": "glass",
            "metadata": {"version": RELEASE_TIP},
        },
        "glass_node_02": {
            "node_id": "glass_node_02",
            "node_type": "glass",
            "metadata": {},
        },
        # DB-only: no live MQTT state, so list_nodes must take the
        # DB-fallback NodeStatus() branch and read version out of metadata.
        "glass_node_03": {
            "node_id": "glass_node_03",
            "node_type": "glass",
            "metadata": {"version": "0009999"},  # behind the tip
        },
        "pump_node_01": {
            "node_id": "pump_node_01",
            "node_type": "pump",
        },
    }


@pytest.fixture
def fake_mqtt(node_states):
    return FakeMqttService(node_states)


@pytest.fixture
def client(monkeypatch, fake_mqtt, db_rows):
    """Minimal app exposing just the nodes router, with the MQTT singleton,
    DB row loader, and release-check service monkeypatched so the test
    doesn't need a live broker, a real database, or network access. Mirrors
    tests/test_nodes_api.py's client fixture."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-testing")
    app.include_router(nodes_api.router, prefix="/api")
    app.state.latest_snapshots = {}

    # Bypass session auth — this test targets serialization/guards, not auth.
    app.dependency_overrides[get_current_user] = lambda: "test_user"

    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: fake_mqtt)
    monkeypatch.setattr(nodes_api, "get_all_nodes", lambda: list(db_rows.values()))
    # Single-row DB lookup used by GET-one/PATCH/snooze/pump/update.
    monkeypatch.setattr(
        nodes_api, "db_get_node",
        lambda node_id: db_rows.get(node_id),
        raising=False,
    )

    # list_nodes/get_node import get_release_check_service LOCALLY (from
    # ..services.release_check, at call time), same pattern as
    # test_nodes_api.py's set_node_snooze/get_mqtt_service patches — so the
    # home module's function is what must be patched, not an attribute on
    # nodes_api (which never binds this name at module scope).
    class _FakeReleaseCheck:
        tip_sha = RELEASE_TIP

    monkeypatch.setattr(release_check_module, "get_release_check_service", lambda: _FakeReleaseCheck())

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_list_nodes_serializes_version_and_update_available(client):
    """/api/nodes must surface version + update_available for:
    - a LIVE node up to date with the tip (version == tip -> False)
    - a LIVE node that never reported a version (-> both fields null)
    - a DB-ONLY node (no live MQTT state) behind the tip (-> True), proving
      the fallback loop reads db_row["metadata"]["version"], not state
    - a DB-ONLY node with no metadata.version at all (-> both fields null)
    """
    response = client.get("/api/nodes")
    assert response.status_code == 200
    by_id = {n["node_id"]: n for n in response.json()}

    live_current = by_id["glass_node_01"]
    assert live_current["version"] == RELEASE_TIP
    assert live_current["update_available"] is False

    live_unknown = by_id["glass_node_02"]
    assert live_unknown["version"] is None
    assert live_unknown["update_available"] is None

    db_only_behind = by_id["glass_node_03"]
    assert db_only_behind["version"] == "0009999"
    assert db_only_behind["update_available"] is True

    db_only_unknown = by_id["pump_node_01"]
    assert db_only_unknown["version"] is None
    assert db_only_unknown["update_available"] is None


def test_get_single_node_serializes_version_and_update_available(client):
    """Same two fields on the detail endpoint (GET /api/nodes/{id}), sourced
    from the live MQTT state (get_node always has one — it 404s otherwise)."""
    response = client.get("/api/nodes/glass_node_01")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == RELEASE_TIP
    assert data["update_available"] is False

    response2 = client.get("/api/nodes/glass_node_02")
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["version"] is None
    assert data2["update_available"] is None


# =============================================================================
# POST /api/nodes/{node_id}/update — dashboard "Update now"
# =============================================================================

def test_post_update_online_glass_node_202_and_sends_command(client, fake_mqtt):
    response = client.post("/api/nodes/glass_node_01/update")
    assert response.status_code == 202
    assert response.json() == {"status": "queued", "node_id": "glass_node_01"}
    assert fake_mqtt.update_calls == ["glass_node_01"]


def test_post_update_offline_glass_node_409(client, fake_mqtt):
    response = client.post("/api/nodes/glass_node_02/update")
    assert response.status_code == 409
    assert fake_mqtt.update_calls == []


def test_post_update_non_glass_node_400(client, fake_mqtt):
    response = client.post("/api/nodes/pump_node_01/update")
    assert response.status_code == 400
    assert "not a glass node" in response.json()["detail"]
    assert fake_mqtt.update_calls == []


def test_post_update_unknown_node_404(client, fake_mqtt):
    response = client.post("/api/nodes/does-not-exist/update")
    assert response.status_code == 404
    assert fake_mqtt.update_calls == []


def test_post_update_mqtt_publish_failure_502(client, fake_mqtt):
    fake_mqtt.send_update_result = False
    response = client.post("/api/nodes/glass_node_01/update")
    assert response.status_code == 502
    assert fake_mqtt.update_calls == ["glass_node_01"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
