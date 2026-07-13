# mqtt_service.py uses package-relative imports (`from ..config import ...`),
# so it MUST be imported as `central_server.services.mqtt_service` with the
# sdprs repo root on sys.path — a top-level `from services...` import raises
# "attempted relative import beyond top-level package". Monkeypatch targets
# must use the same fully-qualified module path. (No conftest.py in the repo;
# matches tests/test_handle_pump_status.py's self-insert pattern.)
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

from central_server.services.mqtt_service import MQTTService


def make_service():
    """Construct a bare service without touching the paho client or broker.

    `MQTTService.__init__` calls get_settings() and creates no network client
    (the client is only created in start()), but __new__ + manual field setup
    keeps the test fully isolated and mirrors test_handle_pump_status.py.
    """
    svc = MQTTService.__new__(MQTTService)
    svc._lock = threading.Lock()
    svc.node_states = {}
    svc.db = None       # exercise the module-level update_node_status path
    svc._loop = None    # _mark_node_offline's WS broadcast is guarded by this -> no-op
    return svc


def _install_recorder(monkeypatch):
    """Stub the module-level DB writer so no real database is hit, and record
    every (node_id, status) call so tests can assert what was written."""
    calls = []
    monkeypatch.setattr(
        "central_server.services.mqtt_service.update_node_status",
        lambda node_id, status: calls.append((node_id, status)),
    )
    return calls


def test_genuinely_stale_node_marked_offline(monkeypatch):
    """A node whose last heartbeat is far past the glass timeout is marked
    OFFLINE by the full _check_offline_nodes -> _mark_node_offline path."""
    calls = _install_recorder(monkeypatch)
    svc = make_service()
    svc.node_states["glass_node_01"] = {
        "type": "glass",
        "status": "ONLINE",
        "last_heartbeat": datetime.utcnow() - timedelta(seconds=999),
    }

    svc._check_offline_nodes()

    assert svc.node_states["glass_node_01"]["status"] == "OFFLINE"
    assert ("glass_node_01", "OFFLINE") in calls


def test_fresh_heartbeat_in_gap_not_marked_offline(monkeypatch):
    """THE FIX: _check_offline_nodes selected the node while it was stale, but a
    fresh heartbeat lands (last_heartbeat = now) before _mark_node_offline
    re-acquires the lock. The re-check must abort the transition: node stays
    ONLINE and no OFFLINE DB write happens."""
    calls = _install_recorder(monkeypatch)
    svc = make_service()
    # Node was stale enough for the scan to select it...
    svc.node_states["glass_node_01"] = {
        "type": "glass",
        "status": "ONLINE",
        "last_heartbeat": datetime.utcnow() - timedelta(seconds=999),
    }
    elapsed_from_scan = 999.0

    # ...but a heartbeat arrived in the gap, refreshing last_heartbeat to NOW.
    svc.node_states["glass_node_01"]["last_heartbeat"] = datetime.utcnow()

    # Simulate the scan calling the mark with the now-stale elapsed value.
    svc._mark_node_offline("glass_node_01", "glass", elapsed_from_scan)

    assert svc.node_states["glass_node_01"]["status"] == "ONLINE"
    assert ("glass_node_01", "OFFLINE") not in calls


def test_fresh_heartbeat_in_gap_pump_not_marked_offline(monkeypatch):
    """Same race for a pump node (30s timeout, CRITICAL log path). A fresh beat
    in the gap must not trigger a spurious CRITICAL / OFFLINE write."""
    calls = _install_recorder(monkeypatch)
    svc = make_service()
    svc.node_states["pump_node_01"] = {
        "type": "pump",
        "status": "ONLINE",
        "last_heartbeat": datetime.utcnow(),  # fresh beat already in the gap
    }

    svc._mark_node_offline("pump_node_01", "pump", 999.0)

    assert svc.node_states["pump_node_01"]["status"] == "ONLINE"
    assert ("pump_node_01", "OFFLINE") not in calls


def test_already_offline_node_skipped(monkeypatch):
    """A node already OFFLINE is not re-processed: no duplicate DB write and no
    state change."""
    calls = _install_recorder(monkeypatch)
    svc = make_service()
    svc.node_states["glass_node_01"] = {
        "type": "glass",
        "status": "OFFLINE",
        "last_heartbeat": datetime.utcnow() - timedelta(seconds=999),
    }

    # _check_offline_nodes should skip OFFLINE nodes entirely...
    svc._check_offline_nodes()
    assert calls == []

    # ...and calling _mark_node_offline directly must also no-op (defense in depth).
    svc._mark_node_offline("glass_node_01", "glass", 999.0)
    assert ("glass_node_01", "OFFLINE") not in calls
    assert svc.node_states["glass_node_01"]["status"] == "OFFLINE"


def test_missing_node_in_gap_skipped(monkeypatch):
    """If the node was removed from node_states in the gap, the mark must no-op
    without KeyError and without a DB write."""
    calls = _install_recorder(monkeypatch)
    svc = make_service()

    svc._mark_node_offline("ghost_node", "glass", 999.0)

    assert calls == []
    assert "ghost_node" not in svc.node_states
