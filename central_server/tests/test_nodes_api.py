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
    """Stand-in for MQTTService exposing only what the nodes routes call."""

    def __init__(self, states):
        self._states = states

    def get_node_states(self):
        return self._states

    def get_node_state(self, node_id):
        # Mirrors MQTTService.get_node_state — single-node lookup used by
        # GET /api/nodes/{node_id}.
        return self._states.get(node_id)


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
    # Single-row DB lookup used by GET-one/PATCH/snooze. raising=False is
    # deliberate: on the pre-fix code the module attribute `db_get_node` does
    # not exist (the route function shadowed the plain `get_node` import), so
    # the shadowing regression tests below hit the original TypeError/500
    # instead of erroring out here in the fixture.
    monkeypatch.setattr(
        nodes_api, "db_get_node",
        lambda node_id: db_rows.get(node_id),
        raising=False,
    )

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


# =============================================================================
# get_node shadowing regression (CRITICAL fix)
#
# api/nodes.py's route `async def get_node(...)` rebinds the module global,
# shadowing the DB helper imported under the same name — so every
# `get_node(node_id)` DB lookup inside the routes recursed into the route
# coroutine with missing arguments (TypeError -> HTTP 500). This broke the
# success path of GET /api/nodes/{id} and ALL of PATCH /api/nodes/{id} and
# POST /api/nodes/{id}/snooze (both called by the SPA). The fix imports the
# helper as `db_get_node`; these tests exercise exactly the paths that used
# to 500 and MUST fail on the unfixed code.
# =============================================================================

def test_get_single_node_success_path(client):
    """GET /api/nodes/{id} success path: node has live MQTT state AND a DB
    row — the DB-enrichment call is what used to recurse into the route."""
    response = client.get("/api/nodes/pump_node_01")
    assert response.status_code == 200
    data = response.json()
    assert data["node_id"] == "pump_node_01"
    assert data["status"] == "ONLINE"
    assert data["pump_state"] == "ON"
    # location comes from the DB row via the (previously shadowed) helper.
    assert data["location"] == "Test Site"


def test_patch_node_location(client, monkeypatch, db_rows):
    """PATCH /api/nodes/{id}: both the exists-check and the read-back after
    set_node_location went through the shadowed name."""
    def fake_set_location(node_id, location):
        db_rows[node_id]["location"] = location
        return True

    monkeypatch.setattr(nodes_api, "set_node_location", fake_set_location)

    response = client.patch("/api/nodes/pump_node_01", json={"location": "New Site"})
    assert response.status_code == 200
    assert response.json() == {"node_id": "pump_node_01", "location": "New Site"}


def test_snooze_node(client, monkeypatch):
    """POST /api/nodes/{id}/snooze: the auto-create exists-check went through
    the shadowed name."""
    import central_server.database as database
    import central_server.services.mqtt_service as mqtt_service_module

    captured = {}

    def fake_set_snooze(node_id, until, reason):
        captured["args"] = (node_id, until, reason)
        return True

    # snooze_node imports these at call time from their home modules (not the
    # router-module bindings the fixture patches), so patch them there.
    monkeypatch.setattr(database, "set_node_snooze", fake_set_snooze)
    monkeypatch.setattr(mqtt_service_module, "get_mqtt_service", lambda: None)

    response = client.post(
        "/api/nodes/pump_node_01/snooze",
        json={"minutes": 30, "reason": "typhoon"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["node_id"] == "pump_node_01"
    assert data["snooze_reason"] == "typhoon"
    assert data["snoozed_until"]
    assert captured["args"][0] == "pump_node_01"
    assert captured["args"][2] == "typhoon"


# =============================================================================
# Unsnooze endpoint coverage (DELETE /api/nodes/{id}/snooze)
#
# Mirrors the monkeypatch style of test_snooze_node above: the route does
# `from ..database import set_node_snooze` etc. inside the function body, so
# we patch the home modules' attributes rather than nodes_api's globals.
# =============================================================================

def test_unsnooze_endpoint_clears_db(client, monkeypatch, db_rows):
    """DELETE /api/nodes/{id}/snooze must:
    - return 200 with {"node_id": ..., "snoozed_until": None}
    - call set_node_snooze(node_id, None, None) to clear the DB row
    The route intentionally does NOT read back the DB row, so the JSON response
    is the contract and the DB-clear call is the side effect we pin."""
    import central_server.database as database
    import central_server.services.mqtt_service as mqtt_service_module
    import central_server.services.audit_service as audit_service_module

    captured = {}

    def fake_set_snooze(node_id, until, reason):
        captured["args"] = (node_id, until, reason)
        # Mutate the fixture row so callers can verify the DB side effect too.
        if node_id in db_rows:
            db_rows[node_id]["snoozed_until"] = until
            db_rows[node_id]["snooze_reason"] = reason
        return True

    monkeypatch.setattr(database, "set_node_snooze", fake_set_snooze)
    monkeypatch.setattr(mqtt_service_module, "get_mqtt_service", lambda: None)
    monkeypatch.setattr(audit_service_module, "log_action", lambda *a, **kw: None)

    response = client.delete("/api/nodes/pump_node_01/snooze")
    assert response.status_code == 200
    assert response.json() == {"node_id": "pump_node_01", "snoozed_until": None}
    assert captured["args"] == ("pump_node_01", None, None)
    # DB row side effect: cleared.
    assert db_rows["pump_node_01"]["snoozed_until"] is None


def test_unsnooze_logs_action(client, monkeypatch):
    """DELETE /api/nodes/{id}/snooze must call audit_service.log_action with
    ACTION_UNSNOOZE, target_id=node_id, operator=the authenticated user."""
    import central_server.database as database
    import central_server.services.mqtt_service as mqtt_service_module
    import central_server.services.audit_service as audit_service_module

    calls = []

    def capture_log_action(operator, action_type, target_id=None, details=None):
        calls.append({
            "operator": operator,
            "action_type": action_type,
            "target_id": target_id,
            "details": details,
        })

    monkeypatch.setattr(database, "set_node_snooze", lambda *a, **kw: True)
    monkeypatch.setattr(mqtt_service_module, "get_mqtt_service", lambda: None)
    monkeypatch.setattr(audit_service_module, "log_action", capture_log_action)

    response = client.delete("/api/nodes/pump_node_01/snooze")
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["action_type"] == audit_service_module.ACTION_UNSNOOZE
    assert calls[0]["target_id"] == "pump_node_01"
    # The test fixture overrides get_current_user -> "test_user"
    assert calls[0]["operator"] == "test_user"
    assert calls[0]["details"] is None


def test_unsnooze_publishes_mqtt_cleared_config(client, monkeypatch):
    """DELETE /api/nodes/{id}/snooze must push (node_id, None, None) to the
    edge via mqtt_svc.send_snooze_config so the edge stops suppressing audio
    triggers locally. Mirrors the POST path which pushes (node_id, until,
    reason) — DELETE is the inverse."""
    import central_server.database as database
    import central_server.services.mqtt_service as mqtt_service_module
    import central_server.services.audit_service as audit_service_module

    class _FakeMqtt:
        def __init__(self):
            self.calls = []

        def send_snooze_config(self, node_id, until, reason):
            self.calls.append((node_id, until, reason))

    fake_mqtt = _FakeMqtt()
    monkeypatch.setattr(database, "set_node_snooze", lambda *a, **kw: True)
    monkeypatch.setattr(mqtt_service_module, "get_mqtt_service", lambda: fake_mqtt)
    monkeypatch.setattr(audit_service_module, "log_action", lambda *a, **kw: None)

    response = client.delete("/api/nodes/pump_node_01/snooze")
    assert response.status_code == 200
    assert fake_mqtt.calls == [("pump_node_01", None, None)]


def test_unsnooze_nonexistent_node_idempotent(client, monkeypatch):
    """DELETE /api/nodes/{id}/snooze on a node that doesn't exist.

    Current behavior (verified against api/nodes.py:508-526): the route does
    NOT check the return value of set_node_snooze (unlike POST which raises
    500 on failure), so it returns 200 with the cleared-shape response even
    when the DB UPDATE affected 0 rows. Pinned here as the contract — flag
    as a candidate for a 404 tightening in a follow-up if desired.
    """
    import central_server.database as database
    import central_server.services.mqtt_service as mqtt_service_module
    import central_server.services.audit_service as audit_service_module

    monkeypatch.setattr(database, "set_node_snooze", lambda *a, **kw: False)
    monkeypatch.setattr(mqtt_service_module, "get_mqtt_service", lambda: None)
    monkeypatch.setattr(audit_service_module, "log_action", lambda *a, **kw: None)

    response = client.delete("/api/nodes/does-not-exist/snooze")
    assert response.status_code == 200
    assert response.json() == {"node_id": "does-not-exist", "snoozed_until": None}


# =============================================================================
# DELETE /api/nodes/{node_id} — one-off node cleanup for smoke-test residue
# (docs/deployment/zeabur-cloud.md's `smoke_test_node` and similar).
# =============================================================================

def test_delete_node_success_removes_and_broadcasts(client, monkeypatch):
    """DELETE returns 200 with {node_id, deleted: True}, calls delete_node
    on the DB layer, writes an audit entry, and best-effort broadcasts a
    `node_deleted` WS event."""
    calls = {"delete": [], "log": [], "broadcast": []}

    monkeypatch.setattr(nodes_api, "db_delete_node",
                        lambda nid: (calls["delete"].append(nid), True)[1])

    import central_server.services.audit_service as audit_service_module
    monkeypatch.setattr(audit_service_module, "log_action",
                        lambda user, action, target_id=None, **kw: calls["log"].append(
                            {"user": user, "action": action, "target_id": target_id}))

    # WS broadcast: patch the module-level import site inside websocket_service.
    import central_server.services.websocket_service as ws_mod
    monkeypatch.setattr(ws_mod, "broadcast_from_sync",
                        lambda loop, evt: calls["broadcast"].append(evt))
    # Every get_mqtt_service() call must return the SAME fake instance so the
    # `_loop` we set below is visible when the route resolves the service.
    persistent_svc = FakeMqttService(node_states)
    persistent_svc._loop = object()
    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: persistent_svc)

    response = client.delete("/api/nodes/smoke_test_node")
    assert response.status_code == 200
    assert response.json() == {"node_id": "smoke_test_node", "deleted": True}
    assert calls["delete"] == ["smoke_test_node"]
    assert len(calls["log"]) == 1
    assert calls["log"][0]["action"] == "DELETE_NODE"
    assert calls["log"][0]["target_id"] == "smoke_test_node"
    assert len(calls["broadcast"]) == 1
    assert calls["broadcast"][0]["type"] == "node_deleted"
    assert calls["broadcast"][0]["data"] == {"node_id": "smoke_test_node"}


def test_delete_node_404_when_missing(client, monkeypatch):
    """DELETE returns 404 when db_delete_node reports the node didn't exist.
    Prevents silent no-op on typos + gives the SPA a distinct signal from
    'deleted successfully'. Also ensures no audit entry / no WS broadcast
    when the delete didn't happen."""
    calls = {"log": [], "broadcast": []}

    monkeypatch.setattr(nodes_api, "db_delete_node", lambda nid: False)

    import central_server.services.audit_service as audit_service_module
    monkeypatch.setattr(audit_service_module, "log_action",
                        lambda *a, **kw: calls["log"].append(a))
    import central_server.services.websocket_service as ws_mod
    monkeypatch.setattr(ws_mod, "broadcast_from_sync",
                        lambda *a, **kw: calls["broadcast"].append(a))

    response = client.delete("/api/nodes/does-not-exist")
    assert response.status_code == 404
    assert calls["log"] == []
    assert calls["broadcast"] == []


# =============================================================================
# POST /api/nodes/{node_id}/pump — manual pump ON/OFF command
# =============================================================================

def test_pump_command_on_publishes_and_audits(client, monkeypatch):
    """ON with a valid duration_s: publishes to MQTT via send_pump_command,
    audits with the action + duration, returns 200 with queued=True."""
    calls = {"mqtt": [], "log": []}

    class _FakeMqtt:
        def send_pump_command(self, node_id, action, duration_s):
            calls["mqtt"].append((node_id, action, duration_s))
            return True

    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: _FakeMqtt())

    import central_server.services.audit_service as audit_service_module
    monkeypatch.setattr(audit_service_module, "log_action",
                        lambda user, action, target_id=None, details=None:
                        calls["log"].append({"user": user, "action": action,
                                             "target_id": target_id,
                                             "details": details}))

    response = client.post("/api/nodes/pump_node_01/pump",
                           json={"action": "ON", "duration_s": 15})
    assert response.status_code == 200
    body = response.json()
    assert body == {"node_id": "pump_node_01", "action": "ON",
                    "duration_s": 15, "queued": True}
    assert calls["mqtt"] == [("pump_node_01", "ON", 15)]
    assert calls["log"] == [{
        "user": "test_user", "action": "PUMP_COMMAND",
        "target_id": "pump_node_01",
        "details": {"action": "ON", "duration_s": 15},
    }]


def test_pump_command_off_indefinite_ok(client, monkeypatch):
    """OFF without duration_s is legal — holds indefinitely (safe direction).
    duration_s must be forwarded verbatim (None) to the edge."""
    mqtt_calls = []

    class _FakeMqtt:
        def send_pump_command(self, node_id, action, duration_s):
            mqtt_calls.append((node_id, action, duration_s))
            return True

    monkeypatch.setattr(nodes_api, "get_mqtt_service", lambda: _FakeMqtt())
    import central_server.services.audit_service as audit_service_module
    monkeypatch.setattr(audit_service_module, "log_action", lambda *a, **kw: None)

    response = client.post("/api/nodes/pump_node_01/pump", json={"action": "OFF"})
    assert response.status_code == 200
    assert response.json()["action"] == "OFF"
    assert response.json()["duration_s"] is None
    assert mqtt_calls == [("pump_node_01", "OFF", None)]


def test_pump_command_on_without_duration_rejected(client, monkeypatch):
    """ON without duration_s must 400. The edge silently drops any ON
    command without a positive duration_s too — this is the front-line check
    that gives operators a clear error."""
    monkeypatch.setattr(nodes_api, "get_mqtt_service",
                        lambda: pytest.fail("mqtt should not be called"))
    response = client.post("/api/nodes/pump_node_01/pump", json={"action": "ON"})
    assert response.status_code == 400
    assert "duration_s" in response.json()["detail"]


def test_pump_command_bad_action_rejected(client, monkeypatch):
    """Only ON / OFF are legal. Anything else 422 at Pydantic layer."""
    monkeypatch.setattr(nodes_api, "get_mqtt_service",
                        lambda: pytest.fail("mqtt should not be called"))
    response = client.post("/api/nodes/pump_node_01/pump",
                           json={"action": "TOGGLE", "duration_s": 10})
    assert response.status_code == 422


def test_pump_command_wrong_node_type_rejected(client, monkeypatch):
    """Sending a pump command to a glass node is a 400 — glass nodes have no
    pump, forwarding the command would just be broker noise."""
    monkeypatch.setattr(nodes_api, "get_mqtt_service",
                        lambda: pytest.fail("mqtt should not be called"))
    # glass_node_01 exists via fixture as node_type='glass'
    monkeypatch.setattr(nodes_api, "db_get_node",
                        lambda nid: {"node_id": nid, "node_type": "glass"},
                        raising=False)
    response = client.post("/api/nodes/glass_node_01/pump",
                           json={"action": "OFF"})
    assert response.status_code == 400
    assert "not a pump" in response.json()["detail"]


def test_pump_command_missing_node_returns_404(client, monkeypatch):
    monkeypatch.setattr(nodes_api, "get_mqtt_service",
                        lambda: pytest.fail("mqtt should not be called"))
    monkeypatch.setattr(nodes_api, "db_get_node", lambda nid: None, raising=False)
    response = client.post("/api/nodes/does-not-exist/pump",
                           json={"action": "OFF"})
    assert response.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
