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


def test_pump_status_stores_manual_override(monkeypatch):
    """MSP-F6: the device publishes its manual-override slot in the status
    flags. Persisting it is what lets /api/nodes show that a pump is still
    being HELD by hand — an indefinite manual OFF used to be invisible."""
    svc = make_service()
    monkeypatch.setattr("central_server.services.mqtt_service.upsert_node", lambda *a, **k: None)
    monkeypatch.setattr("central_server.services.mqtt_service.insert_pump_reading", lambda *a, **k: None)
    payload = json.dumps({"node_id": "pump_node_01", "pump_state": "OFF",
                          "water_level": 40.0, "manual_override": "OFF"})
    svc._handle_pump_status("pump_node_01", payload)
    assert svc.node_states["pump_node_01"]["manual_override"] == "OFF"


def test_pump_status_without_manual_override_stores_none(monkeypatch):
    """No hold (or firmware predating the flag) -> None, never KeyError. This
    is also what a released hold looks like on the next publish, so the field
    must clear itself rather than latch."""
    svc = make_service()
    monkeypatch.setattr("central_server.services.mqtt_service.upsert_node", lambda *a, **k: None)
    monkeypatch.setattr("central_server.services.mqtt_service.insert_pump_reading", lambda *a, **k: None)

    held = json.dumps({"node_id": "pump_node_01", "pump_state": "OFF",
                       "manual_override": "OFF"})
    svc._handle_pump_status("pump_node_01", held)
    assert svc.node_states["pump_node_01"]["manual_override"] == "OFF"

    # Hold released on the device — the next publish omits the flag entirely.
    released = json.dumps({"node_id": "pump_node_01", "pump_state": "ON"})
    svc._handle_pump_status("pump_node_01", released)
    assert svc.node_states["pump_node_01"]["manual_override"] is None


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


def test_heartbeat_stores_visual_and_audio_health(monkeypatch):
    """A glass heartbeat carrying the new visual_health/audio_health telemetry
    fields must land them in node_states[node_id], alongside buffer_health, so
    /api/nodes can surface an online-but-unable-to-alert node. Telemetry-only:
    no command surface, just stored like buffer_health."""
    svc = make_service()
    monkeypatch.setattr("central_server.services.mqtt_service.upsert_node", lambda *a, **k: None)
    payload = json.dumps({
        "node_id": "glass_node_01",
        "cpu_temp": 48.5,
        "buffer_health": "ok",
        "visual_health": "blinded",
        "audio_health": "disabled",
    })
    svc._handle_heartbeat("glass_node_01", payload)
    st = svc.node_states["glass_node_01"]
    assert st["visual_health"] == "blinded"
    assert st["audio_health"] == "disabled"


def test_heartbeat_without_health_fields_stores_none(monkeypatch):
    """A heartbeat that omits the new fields must store None for them (absent
    == None, same contract as buffer_health) — never KeyError, never crash."""
    svc = make_service()
    monkeypatch.setattr("central_server.services.mqtt_service.upsert_node", lambda *a, **k: None)
    svc._handle_heartbeat("glass_node_02", json.dumps({"node_id": "glass_node_02", "cpu_temp": 45.0}))
    st = svc.node_states["glass_node_02"]
    assert st["visual_health"] is None
    assert st["audio_health"] is None


def test_heartbeat_preserves_established_pump_type(monkeypatch):
    """DATA-007: a heartbeat is a generic liveness signal and must NOT downgrade
    an already-known pump to "glass". The in-memory type drives the offline
    timeout loop (30s pump vs 90s glass) and critical-vs-warning logging, so a
    heartbeat that flipped a pump to glass would delay dead-pump detection 3x
    and mute its CRITICAL log. The DB upsert must likewise carry the preserved
    type, not the hardcoded "glass"."""
    svc = make_service()
    upserts = []
    monkeypatch.setattr(
        "central_server.services.mqtt_service.upsert_node",
        lambda node_id, node_type, status, metadata=None:
            upserts.append((node_id, node_type, status)),
    )

    # Node already established as a pump in memory (prior pump_status).
    svc.node_states["pump_node_01"] = {"type": "pump", "status": "ONLINE"}

    svc._handle_heartbeat(
        "pump_node_01", json.dumps({"node_id": "pump_node_01", "cpu_temp": 40.0})
    )

    assert svc.node_states["pump_node_01"]["type"] == "pump"
    assert upserts == [("pump_node_01", "pump", "ONLINE")], upserts


def test_heartbeat_new_node_defaults_to_glass(monkeypatch):
    """A genuinely new node's first contact is a heartbeat; glass stays the
    correct default (cameras are the common case). Preserving an existing type
    must not break the new-node path."""
    svc = make_service()
    upserts = []
    monkeypatch.setattr(
        "central_server.services.mqtt_service.upsert_node",
        lambda node_id, node_type, status, metadata=None:
            upserts.append((node_id, node_type, status)),
    )
    svc._handle_heartbeat(
        "glass_node_09", json.dumps({"node_id": "glass_node_09", "cpu_temp": 44.0})
    )
    assert svc.node_states["glass_node_09"]["type"] == "glass"
    assert upserts == [("glass_node_09", "glass", "ONLINE")], upserts
