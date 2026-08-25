import os, sys, threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

from central_server.services.mqtt_service import MQTTService


def make_service(states):
    svc = MQTTService.__new__(MQTTService)
    svc._lock = threading.Lock()
    svc.node_states = states
    svc.db = None
    svc._loop = None
    svc._hold_asserted = {}
    svc._sent = []
    svc.send_hold_command = lambda nid, hold, reason=None: svc._sent.append((nid, hold, reason)) or True
    return svc


def _online_glass(nid):
    return {"type": "glass", "status": "ONLINE"}


def test_reasserts_hold_for_active_alert():
    svc = make_service({"g1": _online_glass("g1")})
    svc.reconcile_alert_holds(active_lookup=lambda nid: True)
    svc.reconcile_alert_holds(active_lookup=lambda nid: True)  # re-assert each tick
    assert svc._sent == [("g1", True, "active_alert"), ("g1", True, "active_alert")]


def test_unhold_once_on_transition():
    svc = make_service({"g1": _online_glass("g1")})
    svc.reconcile_alert_holds(active_lookup=lambda nid: True)     # -> hold
    svc._sent.clear()
    svc.reconcile_alert_holds(active_lookup=lambda nid: False)    # -> unhold once
    svc.reconcile_alert_holds(active_lookup=lambda nid: False)    # -> silent
    assert svc._sent == [("g1", False, None)]


def test_never_held_node_is_silent():
    svc = make_service({"g1": _online_glass("g1")})
    svc.reconcile_alert_holds(active_lookup=lambda nid: False)
    assert svc._sent == []


def test_offline_and_pump_nodes_skipped():
    svc = make_service({
        "g_off": {"type": "glass", "status": "OFFLINE"},
        "p1": {"type": "pump", "status": "ONLINE"},
    })
    svc.reconcile_alert_holds(active_lookup=lambda nid: True)
    assert svc._sent == []
