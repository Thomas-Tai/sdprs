# Verifies the server's command publishers (send_stream_command /
# send_snooze_config) build their topics via the shared canonical helper
# shared.mqtt_topics.topic_cmd instead of hand-formatted strings that can
# drift from the scheme edge nodes actually subscribe to
# (sdprs/edge/{node_id}/cmd/{command}).
#
# mqtt_service.py uses package-relative imports (`from ..config import ...`),
# so it MUST be imported as `central_server.services.mqtt_service` with the
# sdprs repo root on sys.path (matches test_lwt_offline.py's pattern).
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

from central_server.services.mqtt_service import MQTTService
from shared.mqtt_topics import topic_cmd


def make_service():
    """Bare service without touching the paho client or broker (mirrors
    test_lwt_offline.py / test_offline_detection.py)."""
    svc = MQTTService.__new__(MQTTService)
    svc._lock = threading.Lock()
    svc.node_states = {}
    svc.db = None
    svc._loop = None
    return svc


def _capture_publish(svc):
    """Replace svc.publish with a recorder so no MQTT client is needed."""
    calls = []

    def fake_publish(topic, payload, qos=1):
        calls.append((topic, payload, qos))
        return True

    svc.publish = fake_publish
    return calls


def test_send_stream_command_uses_canonical_cmd_topic():
    svc = make_service()
    calls = _capture_publish(svc)

    assert svc.send_stream_command("glass_node_01", "stream_start") is True

    topic, payload, qos = calls[0]
    assert topic == topic_cmd("glass_node_01", "stream_start")
    assert topic == "sdprs/edge/glass_node_01/cmd/stream_start"
    assert qos == 1
    assert "timestamp" in payload


def test_send_stream_command_stop_topic():
    svc = make_service()
    calls = _capture_publish(svc)

    assert svc.send_stream_command("glass_node_02", "stream_stop") is True

    topic = calls[0][0]
    assert topic == topic_cmd("glass_node_02", "stream_stop")
    assert topic == "sdprs/edge/glass_node_02/cmd/stream_stop"


def test_send_snooze_config_uses_canonical_cmd_topic():
    svc = make_service()
    calls = _capture_publish(svc)

    assert svc.send_snooze_config(
        "glass_node_01", "2026-07-15T00:00:00", "typhoon passing") is True

    topic, payload, qos = calls[0]
    assert topic == topic_cmd("glass_node_01", "snooze")
    assert topic == "sdprs/edge/glass_node_01/cmd/snooze"
    assert qos == 1
    assert payload["snooze_until"] == "2026-07-15T00:00:00"
    assert payload["snooze_reason"] == "typhoon passing"
