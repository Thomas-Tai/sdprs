# mqtt_service.py uses package-relative imports (`from ..config import ...`),
# so it MUST be imported as `central_server.services.mqtt_service` with the
# sdprs repo root on sys.path — a top-level `from services...` import raises
# "attempted relative import beyond top-level package". Monkeypatch targets
# must use the same fully-qualified module path. (No conftest.py in the repo;
# mirrors tests/test_lwt_offline.py's self-insert pattern.)
import os
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

import json

from central_server.services.mqtt_service import MQTTService


def make_service():
    """Construct a bare service without touching the paho client or broker.

    __new__ + manual field setup keeps the test isolated. `_loop` is a non-None
    sentinel so the recovery-broadcast guard (`self._loop is not None`) passes;
    the actual broadcast_from_sync is monkeypatched per-test. Set `_loop = None`
    in the test to exercise the no-broadcast guard.
    """
    svc = MQTTService.__new__(MQTTService)
    svc._lock = threading.Lock()
    svc.node_states = {}
    svc.db = None       # exercise the module-level upsert_node / update_node_status path
    svc._loop = object()  # non-None sentinel -> broadcast guard passes
    return svc


def _install_status_recorder(monkeypatch):
    """Stub the module-level OFFLINE writer (update_node_status) so no real DB is
    hit, recording (node_id, status)."""
    calls = []
    monkeypatch.setattr(
        "central_server.services.mqtt_service.update_node_status",
        lambda node_id, status: calls.append((node_id, status)),
    )
    return calls


def _install_upsert_recorder(monkeypatch):
    """Stub the module-level ONLINE writer (upsert_node) used by the heartbeat /
    pump_status paths."""
    calls = []
    monkeypatch.setattr(
        "central_server.services.mqtt_service.upsert_node",
        lambda node_id, node_type, status, *a, **k: calls.append((node_id, node_type, status)),
    )
    return calls


def _install_pump_reading_stub(monkeypatch):
    """Stub the module-level insert_pump_reading so the pump path does not touch
    a real DB."""
    monkeypatch.setattr(
        "central_server.services.mqtt_service.insert_pump_reading",
        lambda *a, **k: None,
    )


def _install_broadcast_recorder(monkeypatch):
    """Capture every broadcast_from_sync call. The handlers import the name
    INSIDE the function (`from .websocket_service import broadcast_from_sync`),
    so patch the module attribute (see tests/test_ws_loop_capture.py). Returns a
    list of (loop, msg) tuples."""
    msgs = []
    monkeypatch.setattr(
        "central_server.services.websocket_service.broadcast_from_sync",
        lambda loop, msg: msgs.append((loop, msg)),
    )
    return msgs


def _node_status_msgs(msgs):
    """Filter captured broadcasts down to node_status messages (pump path also
    emits a separate pump_status telemetry broadcast which we ignore here)."""
    return [m for (_loop, m) in msgs if m.get("type") == "node_status"]


# ----- (a) first heartbeat from unknown node -> exactly one ONLINE broadcast -----

def test_first_heartbeat_unknown_node_broadcasts_online(monkeypatch):
    _install_upsert_recorder(monkeypatch)
    msgs = _install_broadcast_recorder(monkeypatch)
    svc = make_service()

    svc._handle_heartbeat(
        "glass_node_01",
        json.dumps({"cpu_temp": 55.0, "memory_usage_percent": 40.0, "buffer_health": "ok"}),
    )

    node_status = _node_status_msgs(msgs)
    assert len(node_status) == 1
    assert node_status[0]["type"] == "node_status"
    assert node_status[0]["data"] == {
        "node_id": "glass_node_01",
        "status": "ONLINE",
        "cpu_temp": 55.0,
        "memory_usage_percent": 40.0,
    }


def test_first_heartbeat_omits_absent_telemetry(monkeypatch):
    """cpu_temp / memory_usage_percent are included ONLY when non-None."""
    _install_upsert_recorder(monkeypatch)
    msgs = _install_broadcast_recorder(monkeypatch)
    svc = make_service()

    svc._handle_heartbeat("glass_node_01", json.dumps({"buffer_health": "ok"}))

    node_status = _node_status_msgs(msgs)
    assert len(node_status) == 1
    assert node_status[0]["data"] == {"node_id": "glass_node_01", "status": "ONLINE"}
    assert "cpu_temp" not in node_status[0]["data"]
    assert "memory_usage_percent" not in node_status[0]["data"]


# ----- (b) second heartbeat while already ONLINE -> no broadcast -----

def test_second_heartbeat_no_rebroadcast(monkeypatch):
    _install_upsert_recorder(monkeypatch)
    msgs = _install_broadcast_recorder(monkeypatch)
    svc = make_service()

    svc._handle_heartbeat("glass_node_01", json.dumps({"cpu_temp": 50.0}))
    assert len(_node_status_msgs(msgs)) == 1  # transition to ONLINE

    # Second heartbeat while already ONLINE -> steady state, no new broadcast.
    svc._handle_heartbeat("glass_node_01", json.dumps({"cpu_temp": 51.0}))
    assert len(_node_status_msgs(msgs)) == 1


# ----- (c) OFFLINE (via LWT) then heartbeat -> ONLINE broadcast -----

def test_recovery_after_lwt_offline_broadcasts_online(monkeypatch):
    _install_status_recorder(monkeypatch)   # LWT path uses update_node_status
    _install_upsert_recorder(monkeypatch)   # heartbeat path uses upsert_node
    msgs = _install_broadcast_recorder(monkeypatch)
    svc = make_service()

    # Seed an ONLINE node, then force it OFFLINE via its Last-Will marker.
    svc.node_states["glass_node_01"] = {
        "type": "glass",
        "status": "ONLINE",
        "last_heartbeat": datetime.utcnow() - timedelta(seconds=5),
    }
    svc._handle_lwt_offline("glass_node_01")
    assert svc.node_states["glass_node_01"]["status"] == "OFFLINE"
    # LWT emitted an OFFLINE node_status; ignore it by clearing the recorder.
    msgs.clear()

    # Recovery heartbeat -> OFFLINE->ONLINE transition -> exactly one ONLINE broadcast.
    svc._handle_heartbeat("glass_node_01", json.dumps({"cpu_temp": 48.0}))
    node_status = _node_status_msgs(msgs)
    assert len(node_status) == 1
    assert node_status[0]["data"]["node_id"] == "glass_node_01"
    assert node_status[0]["data"]["status"] == "ONLINE"
    assert svc.node_states["glass_node_01"]["status"] == "ONLINE"


# ----- (d) pump_status transition -> ONLINE broadcast; repeat -> no second -----

def test_pump_status_transition_broadcasts_online_once(monkeypatch):
    _install_upsert_recorder(monkeypatch)
    _install_pump_reading_stub(monkeypatch)
    msgs = _install_broadcast_recorder(monkeypatch)
    svc = make_service()

    svc._handle_pump_status(
        "pump_node_01",
        json.dumps({"pump_state": "ON", "water_level": 80, "raining": True}),
    )
    node_status = _node_status_msgs(msgs)
    assert len(node_status) == 1
    # Pumps carry no cpu_temp/memory telemetry: node_id + status only.
    assert node_status[0]["data"] == {"node_id": "pump_node_01", "status": "ONLINE"}

    # Repeated pump_status while already ONLINE -> no second node_status broadcast
    # (the separate pump_status telemetry broadcast still fires, but is filtered).
    svc._handle_pump_status(
        "pump_node_01",
        json.dumps({"pump_state": "OFF", "water_level": 60, "raining": False}),
    )
    assert len(_node_status_msgs(msgs)) == 1


# ----- (e) _loop is None -> no broadcast attempted, no exception -----

def test_no_loop_no_broadcast_no_error(monkeypatch):
    _install_upsert_recorder(monkeypatch)
    msgs = _install_broadcast_recorder(monkeypatch)
    svc = make_service()
    svc._loop = None  # guard should short-circuit before importing/broadcasting

    # Must not raise even though a transition occurs.
    svc._handle_heartbeat("glass_node_01", json.dumps({"cpu_temp": 55.0}))

    assert msgs == []  # broadcast_from_sync never invoked
    assert svc.node_states["glass_node_01"]["status"] == "ONLINE"
