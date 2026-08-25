import os, sys, json, threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

from central_server.services.mqtt_service import MQTTService
from shared.mqtt_topics import topic_cmd


def make_service():
    svc = MQTTService.__new__(MQTTService)
    svc._lock = threading.Lock()
    svc.node_states = {}
    svc.db = None
    svc._loop = None
    return svc


def test_heartbeat_persists_hold_fields(monkeypatch):
    svc = make_service()
    captured = {}
    monkeypatch.setattr("central_server.services.mqtt_service.upsert_node",
                        lambda nid, ntype, status, meta: captured.update(meta=meta))
    payload = json.dumps({"node_id": "glass_node_01", "status": "online",
                          "update_held": True, "hold_reason": "active_alert"})
    svc._handle_heartbeat("glass_node_01", payload)
    assert svc.node_states["glass_node_01"]["update_held"] is True
    assert svc.node_states["glass_node_01"]["hold_reason"] == "active_alert"
    assert captured["meta"]["update_held"] is True
    assert captured["meta"]["hold_reason"] == "active_alert"


def test_heartbeat_hold_defaults(monkeypatch):
    svc = make_service()
    monkeypatch.setattr("central_server.services.mqtt_service.upsert_node",
                        lambda *a, **k: None)
    svc._handle_heartbeat("glass_node_01", json.dumps({"node_id": "glass_node_01"}))
    assert svc.node_states["glass_node_01"]["update_held"] is False
    assert svc.node_states["glass_node_01"]["hold_reason"] is None


def test_send_hold_command_topic_and_payload():
    svc = make_service()
    calls = []
    svc.publish = lambda topic, payload, qos=1: calls.append((topic, payload, qos)) or True
    assert svc.send_hold_command("glass_node_01", True, "active_alert") is True
    assert calls[0][0] == topic_cmd("glass_node_01", "hold")
    assert calls[0][1]["hold"] is True
    assert calls[0][1]["reason"] == "active_alert"
