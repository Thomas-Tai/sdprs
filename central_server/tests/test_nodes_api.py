# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Nodes API Unit Tests
Smart Disaster Prevention Response System

Tests for GET /api/nodes.

Regression guard for I-1 (reviewer finding): `NodeStatus.snoozed_until` is
typed `Optional[str]` but was populated straight from the DB row without the
`_ts_to_iso()` coercion used for other timestamp fields. In PostgreSQL mode,
a TIMESTAMP column deserializes to a Python `datetime` via SQLAlchemy, which
Pydantic v2 rejects for a `str` field -> ValidationError -> HTTP 500 for the
whole /api/nodes list whenever any node has an active snooze. SQLite masked
this because it round-trips the column as a plain string.

To reproduce the failure mode without standing up PostgreSQL, this test
monkeypatches the DB-row source (`get_all_nodes`) to return a row with a
real `datetime` object for `snoozed_until` — exactly what the PG backend
would hand back — and asserts `list_nodes()` still returns 200 with the
field serialized as an ISO string.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import os
from datetime import datetime

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


class FakeMqttService:
    """Stand-in for MQTTService exposing only what list_nodes() calls."""

    def __init__(self, states):
        self._states = states

    def get_node_states(self):
        return self._states


@pytest.fixture
def node_states():
    """One live pump node with all pump-health flags set, plus two glass
    nodes exercising the new visual_health/audio_health telemetry fields —
    one with them set ("blinded"/"disabled") and one without them at all
    (must serialize to null, not crash), as MQTTService would report them in
    memory (see _handle_pump_status / _handle_heartbeat)."""
    return {
        "pump_node_01": {
            "type": "pump",
            "status": "ONLINE",
            "last_heartbeat": datetime(2026, 7, 13, 9, 59, 0),
            "pump_state": "ON",
            "water_level": 55.2,
            "raining": True,
            "sensor_conflict": False,
            "dry_run_protect": True,
        },
        "glass_node_01": {
            "type": "glass",
            "status": "ONLINE",
            "last_heartbeat": datetime(2026, 7, 13, 9, 59, 30),
            "cpu_temp": 48.5,
            "buffer_health": "ok",
            "visual_health": "blinded",
            "audio_health": "disabled",
        },
        "glass_node_02": {
            # No visual_health/audio_health keys at all — must come back null.
            "type": "glass",
            "status": "ONLINE",
            "last_heartbeat": datetime(2026, 7, 13, 9, 59, 45),
            "cpu_temp": 45.0,
            "buffer_health": "ok",
        },
    }


@pytest.fixture
def db_rows():
    """Simulated DB rows as returned by get_all_nodes(). snoozed_until is a
    real datetime object here — matching what PostgreSQL/SQLAlchemy hands
    back for a TIMESTAMP column (the bug this test guards against). One row
    has a live MQTT state (pump_node_01), the other is DB-only (exercises
    the second NodeStatus construction site in nodes.py's DB-only loop)."""
    return {
        "pump_node_01": {
            "node_id": "pump_node_01",
            "node_type": "pump",
            "location": "Test Site",
            "battery_voltage": 12.1,
            "power_source": "battery",
            "snoozed_until": datetime(2026, 7, 13, 10, 0, 0),
        },
        "pump_node_02": {
            "node_id": "pump_node_02",
            "node_type": "pump",
            "location": "Backup Site",
            "battery_voltage": 11.8,
            "power_source": "mains",
            "snoozed_until": datetime(2026, 7, 13, 11, 30, 0),
        },
    }


@pytest.fixture
def client(monkeypatch, node_states, db_rows):
    """Minimal app exposing just the nodes router, with the MQTT singleton
    and DB row loader monkeypatched so the test doesn't need a live broker
    or a real database connection."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-testing")
    app.include_router(nodes_api.router, prefix="/api")
    app.state.latest_snapshots = {}

    # Bypass session auth — this test targets serialization, not auth.
    app.dependency_overrides[get_current_user] = lambda: "test_user"

    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: FakeMqttService(node_states))
    monkeypatch.setattr(nodes_api, "get_all_nodes", lambda: list(db_rows.values()))

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_list_nodes_serializes_pump_fields_and_snoozed_until(client):
    """/api/nodes must return 200 (not 500) and:
    - surface raining/sensor_conflict/dry_run_protect for a node with live state
    - serialize snoozed_until as an ISO string even when the DB layer hands
      back a datetime object (both the live-state loop and the DB-only loop)
    """
    response = client.get("/api/nodes")
    assert response.status_code == 200
    data = response.json()
    # pump_node_01 + glass_node_01 + glass_node_02 (live) + pump_node_02 (DB-only)
    assert len(data) == 4

    by_id = {n["node_id"]: n for n in data}

    live = by_id["pump_node_01"]
    assert live["raining"] is True
    assert live["sensor_conflict"] is False
    assert live["dry_run_protect"] is True
    assert isinstance(live["snoozed_until"], str)
    assert live["snoozed_until"].startswith("2026-07-13T10:00:00")

    db_only = by_id["pump_node_02"]
    assert isinstance(db_only["snoozed_until"], str)
    assert db_only["snoozed_until"].startswith("2026-07-13T11:30:00")


def test_list_nodes_surfaces_visual_and_audio_health(client):
    """The two new glass-health telemetry fields (visual_health/audio_health)
    must travel through /api/nodes exactly like buffer_health: present when the
    node reports them, and null (not a crash / not omitted) when it doesn't.
    Mirrors the frozen contract the SPA agent depends on."""
    response = client.get("/api/nodes")
    assert response.status_code == 200
    by_id = {n["node_id"]: n for n in response.json()}

    # Node that reports the fields carries them through verbatim.
    blinded = by_id["glass_node_01"]
    assert blinded["visual_health"] == "blinded"
    assert blinded["audio_health"] == "disabled"

    # Node that never reported them yields null (field present, value None).
    quiet = by_id["glass_node_02"]
    assert "visual_health" in quiet and quiet["visual_health"] is None
    assert "audio_health" in quiet and quiet["audio_health"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
