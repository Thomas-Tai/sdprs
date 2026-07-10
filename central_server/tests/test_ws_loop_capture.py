# mqtt_service.py uses package-relative imports (`from ..config import ...`),
# so import it as a central_server submodule with the sdprs repo root on
# sys.path. A top-level `from services...` import raises "attempted relative
# import beyond top-level package". Monkeypatch targets use the same FQ path.
# (No conftest.py in the repo — matches tests/test_alerts_api.py.)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server.services.mqtt_service import MQTTService


def test_broadcast_uses_stored_loop(monkeypatch):
    svc = MQTTService.__new__(MQTTService)
    sentinel = object()
    svc._loop = sentinel
    captured = {}
    monkeypatch.setattr("central_server.services.websocket_service.broadcast_from_sync",
                        lambda loop, msg: captured.update(loop=loop, msg=msg))
    svc._broadcast_pump_status("pump_node_01", {"pump_state": "ON", "water_level": 80,
                                                "raining": True, "sensor_conflict": False})
    assert captured["loop"] is sentinel
    assert captured["msg"]["type"] == "pump_status"
    assert captured["msg"]["data"]["raining"] is True


def test_broadcast_noop_without_loop(monkeypatch):
    svc = MQTTService.__new__(MQTTService)
    svc._loop = None
    called = {"n": 0}
    monkeypatch.setattr("central_server.services.websocket_service.broadcast_from_sync",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    svc._broadcast_pump_status("n", {})
    assert called["n"] == 0
