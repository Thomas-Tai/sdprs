# mqtt_service.py uses package-relative imports (`from ..config import ...`),
# so it MUST be imported as `central_server.services.mqtt_service` with the
# sdprs repo root on sys.path — a top-level `from services...` import raises
# "attempted relative import beyond top-level package". Monkeypatch targets
# must use the same fully-qualified module path. (No conftest.py in the repo;
# matches tests/test_alerts_api.py's self-insert pattern.)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json
from central_server.services.mqtt_service import MQTTService


def make_service():
    svc = MQTTService.__new__(MQTTService)
    import threading
    svc._lock = threading.Lock()
    svc.node_states = {}
    svc.db = None
    svc._loop = None
    return svc


def test_pump_status_stores_new_flags(monkeypatch):
    svc = make_service()
    monkeypatch.setattr("central_server.services.mqtt_service.upsert_node", lambda *a, **k: None)
    monkeypatch.setattr("central_server.services.mqtt_service.insert_pump_reading", lambda *a, **k: None)
    payload = json.dumps({"node_id": "pump_node_01", "pump_state": "ON",
                          "water_level": 82.4, "raining": True,
                          "sensor_conflict": True, "dry_run_protect": False})
    svc._handle_pump_status("pump_node_01", payload)
    st = svc.node_states["pump_node_01"]
    assert st["raining"] is True and st["sensor_conflict"] is True


def test_malformed_payload_still_bumps_last_seen():
    svc = make_service()
    svc._handle_pump_status("pump_node_01", "{not json")
    assert "pump_node_01" in svc.node_states
    assert svc.node_states["pump_node_01"]["last_heartbeat"] is not None


def test_non_dict_payload_still_bumps_last_seen():
    """Valid JSON that isn't an object (e.g. a bare array) must still refresh
    last_heartbeat, symmetric with the unparseable-JSON branch above — a
    glitchy-but-alive node must not trip the 30s false-offline (spec §8#1)."""
    svc = make_service()
    svc._handle_pump_status("pump_node_01", json.dumps([1, 2, 3]))
    assert "pump_node_01" in svc.node_states
    assert svc.node_states["pump_node_01"]["last_heartbeat"] is not None
