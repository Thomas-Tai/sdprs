# mqtt_service.py uses package-relative imports (`from ..config import ...`),
# so it MUST be imported as `central_server.services.mqtt_service` with the
# sdprs repo root on sys.path — a top-level `from services...` import raises
# "attempted relative import beyond top-level package". Monkeypatch targets
# must use the same fully-qualified module path. (No conftest.py in the repo;
# matches tests/test_offline_detection.py's self-insert pattern.)
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

    __new__ + manual field setup keeps the test fully isolated and mirrors
    test_offline_detection.py / test_handle_pump_status.py.
    """
    svc = MQTTService.__new__(MQTTService)
    svc._lock = threading.Lock()
    svc.node_states = {}
    svc.db = None       # exercise the module-level update_node_status / upsert_node path
    svc._loop = None    # _handle_lwt_offline's WS broadcast is guarded by this -> no-op
    return svc


def _install_status_recorder(monkeypatch):
    """Stub the module-level OFFLINE writer so no real database is hit, and
    record every (node_id, status) call so tests can assert what was written."""
    calls = []
    monkeypatch.setattr(
        "central_server.services.mqtt_service.update_node_status",
        lambda node_id, status: calls.append((node_id, status)),
    )
    return calls


def _install_upsert_recorder(monkeypatch):
    """Stub the module-level ONLINE writer (upsert_node) used by the normal
    heartbeat path, recording (node_id, node_type, status) tuples."""
    calls = []
    monkeypatch.setattr(
        "central_server.services.mqtt_service.upsert_node",
        lambda node_id, node_type, status, *a, **k: calls.append((node_id, node_type, status)),
    )
    return calls


def test_lwt_marks_node_offline(monkeypatch):
    """CRUCIAL: an LWT/offline marker arriving on the heartbeat topic must mark
    the node OFFLINE — NOT ONLINE (the bug this feature fixes)."""
    calls = _install_status_recorder(monkeypatch)
    svc = make_service()
    svc.node_states["glass_node_01"] = {
        "type": "glass",
        "status": "ONLINE",
        "last_heartbeat": datetime.utcnow() - timedelta(seconds=5),
    }

    svc._handle_heartbeat(
        "glass_node_01",
        json.dumps({"node_id": "glass_node_01", "status": "OFFLINE", "online": False}),
    )

    assert svc.node_states["glass_node_01"]["status"] == "OFFLINE"
    assert ("glass_node_01", "OFFLINE") in calls


def test_lwt_forces_offline_even_with_recent_heartbeat(monkeypatch):
    """An LWT is definitive: even with a brand-new last_heartbeat it must force
    OFFLINE, proving it does NOT re-validate staleness like the timeout path."""
    calls = _install_status_recorder(monkeypatch)
    svc = make_service()
    svc.node_states["glass_node_01"] = {
        "type": "glass",
        "status": "ONLINE",
        "last_heartbeat": datetime.utcnow(),  # fresh, would abort the timeout path
    }

    svc._handle_heartbeat(
        "glass_node_01",
        json.dumps({"node_id": "glass_node_01", "status": "OFFLINE", "online": False}),
    )

    assert svc.node_states["glass_node_01"]["status"] == "OFFLINE"
    assert ("glass_node_01", "OFFLINE") in calls


def test_normal_heartbeat_still_marks_online(monkeypatch):
    """A normal heartbeat (no offline marker) must still mark the node ONLINE,
    refresh last_heartbeat, and never write OFFLINE."""
    status_calls = _install_status_recorder(monkeypatch)
    upsert_calls = _install_upsert_recorder(monkeypatch)
    svc = make_service()

    before = datetime.utcnow()
    svc._handle_heartbeat(
        "glass_node_01",
        json.dumps({"cpu_temp": 50, "buffer_health": "ok"}),
    )

    st = svc.node_states["glass_node_01"]
    assert st["status"] == "ONLINE"
    assert st["last_heartbeat"] >= before
    # ONLINE path uses upsert_node, never update_node_status(OFFLINE).
    assert ("glass_node_01", "glass", "ONLINE") in upsert_calls
    assert ("glass_node_01", "OFFLINE") not in status_calls


def test_lwt_unknown_node_no_crash(monkeypatch):
    """An LWT for a never-seen node must not raise and must produce an OFFLINE
    state plus an OFFLINE DB write."""
    calls = _install_status_recorder(monkeypatch)
    svc = make_service()

    svc._handle_heartbeat(
        "never_seen",
        json.dumps({"node_id": "never_seen", "status": "OFFLINE", "online": False}),
    )

    assert svc.node_states["never_seen"]["status"] == "OFFLINE"
    assert ("never_seen", "OFFLINE") in calls
