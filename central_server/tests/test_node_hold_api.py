# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Node Hold API Unit Tests
Smart Disaster Prevention Response System

Phase 3 (edge auto-update hold): NodeStatus.update_held / NodeStatus.hold_reason
serialization on GET /api/nodes (live + offline/DB-only) and GET /api/nodes/{id}.

Mirrors central_server/tests/test_node_update_api.py's app construction,
FakeMqttService, and monkeypatch conventions verbatim.
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


RELEASE_TIP = "723456f"


class FakeMqttService:
    """Stand-in for MQTTService exposing what the nodes routes call."""

    def __init__(self, states):
        self._states = states

    def get_node_states(self):
        return self._states

    def get_node_state(self, node_id):
        return self._states.get(node_id)


@pytest.fixture
def node_states():
    """glass_node_01: ONLINE, live state carries an active update hold.
    glass_node_02: OFFLINE, no hold fields in live state at all."""
    return {
        "glass_node_01": {
            "type": "glass",
            "status": "ONLINE",
            "update_held": True,
            "hold_reason": "event_capture",
        },
        "glass_node_02": {
            "type": "glass",
            "status": "OFFLINE",
        },
    }


@pytest.fixture
def db_rows():
    """DB rows as returned by get_all_nodes()/db_get_node(); metadata is
    already the parsed dict shape (json.loads already applied)."""
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
        # DB-only: no live MQTT state, no hold metadata at all -> must not
        # raise, fields come back falsy/None.
        "glass_node_03": {
            "node_id": "glass_node_03",
            "node_type": "glass",
            "metadata": {"version": "0009999"},
        },
    }


@pytest.fixture
def fake_mqtt(node_states):
    return FakeMqttService(node_states)


@pytest.fixture
def client_and_state(monkeypatch, fake_mqtt, db_rows, node_states):
    """Minimal app exposing just the nodes router, mirroring
    test_node_update_api.py's `client` fixture."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-testing")
    app.include_router(nodes_api.router, prefix="/api")
    app.state.latest_snapshots = {}

    app.dependency_overrides[get_current_user] = lambda: "test_user"

    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: fake_mqtt)
    monkeypatch.setattr(nodes_api, "get_all_nodes", lambda: list(db_rows.values()))
    monkeypatch.setattr(
        nodes_api, "db_get_node",
        lambda node_id: db_rows.get(node_id),
        raising=False,
    )

    class _FakeReleaseCheck:
        tip_sha = RELEASE_TIP

    monkeypatch.setattr(release_check_module, "get_release_check_service", lambda: _FakeReleaseCheck())

    with TestClient(app) as test_client:
        yield test_client, fake_mqtt

    app.dependency_overrides.clear()


def test_list_nodes_exposes_hold_fields_live(client_and_state):
    client, mqtt_svc = client_and_state
    r = client.get("/api/nodes")
    assert r.status_code == 200
    node = next(n for n in r.json() if n["node_id"] == "glass_node_01")
    assert node["update_held"] is True
    assert node["hold_reason"] == "event_capture"


def test_offline_node_hold_defaults_none(client_and_state):
    client, _ = client_and_state
    # glass_node_03 is DB-only (no live MQTT state) with no hold metadata at
    # all -> must not raise; fields come back falsy/None.
    r = client.get("/api/nodes")
    assert r.status_code == 200
    node = next(n for n in r.json() if n["node_id"] == "glass_node_03")
    assert not node["update_held"]
    assert node["hold_reason"] is None


def test_live_node_without_hold_state_defaults_none(client_and_state):
    client, _ = client_and_state
    # glass_node_02 has live state but never reports hold fields.
    r = client.get("/api/nodes")
    assert r.status_code == 200
    node = next(n for n in r.json() if n["node_id"] == "glass_node_02")
    assert not node["update_held"]
    assert node["hold_reason"] is None


def test_get_single_node_exposes_hold_fields(client_and_state):
    client, _ = client_and_state
    response = client.get("/api/nodes/glass_node_01")
    assert response.status_code == 200
    data = response.json()
    assert data["update_held"] is True
    assert data["hold_reason"] == "event_capture"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
