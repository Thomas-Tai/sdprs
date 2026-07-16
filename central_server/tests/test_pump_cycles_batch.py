# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Batch Pump Cycles API Unit Tests
Smart Disaster Prevention Response System

Tests for GET /api/pumps/cycles (PLURAL) — the batch endpoint that returns
ON->OFF cycle counts for EVERY pump node in a single HTTP round-trip,
eliminating the dashboard's per-pump N+1 request pattern.

Mirrors test_nodes_api.py's collection-safe conventions: required env vars set
before importing the app, a minimal FastAPI app exposing just the nodes router,
`get_current_user` bypassed via dependency_overrides, and the module-level DB
functions (`get_all_nodes` / `get_pump_readings_multi` for batch,
`get_pump_readings` for the single-node comparison endpoint) monkeypatched so
the test needs neither a live broker nor a real database.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import os
from datetime import datetime, timedelta

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


def _readings(states):
    """Build a time-ordered pump_readings list (recent timestamps, so the
    rows fall inside any queried window) from a sequence of pump_state values."""
    base = datetime.utcnow() - timedelta(minutes=len(states))
    return [
        {"timestamp": (base + timedelta(minutes=i)).isoformat(), "pump_state": st}
        for i, st in enumerate(states)
    ]


@pytest.fixture
def nodes_rows():
    """Simulated get_all_nodes() output: two pump nodes + one glass node.
    The glass node must be EXCLUDED from the batch response."""
    return [
        {"node_id": "pump_node_01", "node_type": "pump", "location": "Site A"},
        {"node_id": "pump_node_02", "node_type": "pump", "location": "Site B"},
        {"node_id": "glass_node_01", "node_type": "glass", "location": "Site C"},
    ]


@pytest.fixture
def readings_by_node():
    """pump_readings per node.
    - pump_node_01: ON,OFF,ON,OFF -> 2 ON->OFF transitions
    - pump_node_02: ON,OFF        -> 1 ON->OFF transition
    - glass_node_01: has none (and is filtered out by type anyway)
    """
    return {
        "pump_node_01": _readings(["ON", "OFF", "ON", "OFF"]),
        "pump_node_02": _readings(["ON", "OFF"]),
    }


@pytest.fixture
def client(monkeypatch, nodes_rows, readings_by_node):
    """Minimal app exposing just the nodes router with DB access monkeypatched
    at the nodes_api module level (both endpoints resolve get_all_nodes /
    get_pump_readings through that module — see nodes.py line 17 imports)."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-testing")
    app.include_router(nodes_api.router, prefix="/api")

    # Bypass session auth — these tests target the batch aggregation, not auth.
    app.dependency_overrides[get_current_user] = lambda: "test_user"

    monkeypatch.setattr(nodes_api, "get_all_nodes", lambda: list(nodes_rows))
    # Single-node path (used by test_batch_matches_single_endpoint).
    monkeypatch.setattr(
        nodes_api,
        "get_pump_readings",
        lambda node_id, start, end, limit: list(readings_by_node.get(node_id, [])),
    )
    # Batch path — nodes_api.pump_cycles_batch now calls get_pump_readings_multi
    # (one query WHERE node_id IN (...) / = ANY on PG) instead of iterating
    # per-node get_pump_readings — the audit follow-up eliminated the N+1.
    monkeypatch.setattr(
        nodes_api,
        "get_pump_readings_multi",
        lambda node_ids, start, end, limit: {
            nid: list(readings_by_node.get(nid, [])) for nid in node_ids
        },
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_batch_returns_all_pumps(client):
    """GET /api/pumps/cycles returns `window` + `nodes` keyed by node_id with
    the correct per-node count/alert, and the glass node is ABSENT."""
    response = client.get("/api/pumps/cycles?window=24h")
    assert response.status_code == 200
    data = response.json()

    assert data["window"] == "24h"
    nodes = data["nodes"]

    # Both pump nodes present; glass node excluded (type filter uses node_type).
    assert set(nodes.keys()) == {"pump_node_01", "pump_node_02"}
    assert "glass_node_01" not in nodes

    # ON,OFF,ON,OFF -> 2 transitions ; ON,OFF -> 1 transition
    assert nodes["pump_node_01"]["count"] == 2
    assert nodes["pump_node_01"]["alert"] is False
    assert nodes["pump_node_02"]["count"] == 1
    assert nodes["pump_node_02"]["alert"] is False


def test_batch_pump_with_no_readings_is_zero(client, monkeypatch, nodes_rows):
    """A pump node with no readings in-window is still PRESENT (identified by
    node_type == 'pump') with count 0 and alert False."""
    # pump_node_02 has no readings; pump_node_01 keeps its 4-reading set.
    _p01 = _readings(["ON", "OFF", "ON", "OFF"])
    monkeypatch.setattr(
        nodes_api,
        "get_pump_readings_multi",
        lambda node_ids, start, end, limit: {
            nid: (list(_p01) if nid == "pump_node_01" else []) for nid in node_ids
        },
    )

    response = client.get("/api/pumps/cycles?window=1h")
    assert response.status_code == 200
    nodes = response.json()["nodes"]

    assert "pump_node_02" in nodes
    assert nodes["pump_node_02"]["count"] == 0
    assert nodes["pump_node_02"]["alert"] is False


def test_batch_matches_single_endpoint(client):
    """The batch count for a node must equal the single /pump/{id}/cycles count
    — proves the shared _count_pump_cycles helper keeps them consistent."""
    nid = "pump_node_01"

    batch = client.get("/api/pumps/cycles?window=6h").json()
    single = client.get(f"/api/pump/{nid}/cycles?window=6h").json()

    assert single["count"] == batch["nodes"][nid]["count"]
    assert single["alert"] == batch["nodes"][nid]["alert"]


def test_batch_alert_threshold(client, monkeypatch, nodes_rows):
    """A pump with more than the >20 threshold of ON->OFF transitions -> alert True."""
    # 21 ON->OFF transitions = 42 readings alternating ON,OFF,...,ON,OFF
    heavy = _readings(["ON", "OFF"] * 21)  # 21 ON->OFF transitions

    monkeypatch.setattr(
        nodes_api,
        "get_pump_readings_multi",
        lambda node_ids, start, end, limit: {
            nid: (list(heavy) if nid == "pump_node_01" else []) for nid in node_ids
        },
    )

    response = client.get("/api/pumps/cycles?window=24h")
    assert response.status_code == 200
    node = response.json()["nodes"]["pump_node_01"]

    assert node["count"] == 21
    assert node["alert"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
