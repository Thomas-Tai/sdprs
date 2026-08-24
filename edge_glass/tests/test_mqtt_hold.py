"""Update-hold aggregation + writer. No broker, no paho: skip _init_client and
inject a fake client (mirrors test_mqtt_version.py)."""
import json
from unittest import mock

from comms.mqtt_client import MQTTClient, SERVER_HOLD_TTL, _write_hold_file


class _FakeClient:
    def __init__(self):
        self.last_payload = None

    def publish(self, topic, payload, qos=0):
        self.last_payload = payload


def _make_client(tmp_path):
    config = {"node_id": "glass_node_01",
              "server": {"mqtt_broker": "localhost", "mqtt_port": 1883}}
    with mock.patch("comms.mqtt_client.PAHO_AVAILABLE", True), \
            mock.patch.object(MQTTClient, "_init_client", lambda self: None):
        client = MQTTClient(config)
    client._client = _FakeClient()
    client._hold_file = str(tmp_path / "update_hold")
    client._version_file = str(tmp_path / "sha")  # keep version read harmless
    return client


def _hold_content(client):
    with open(client._hold_file) as f:
        return f.read().strip()


def test_write_hold_file_emits_1_and_0(tmp_path):
    p = str(tmp_path / "h")
    _write_hold_file(p, True)
    assert open(p).read().strip() == "1"
    _write_hold_file(p, False)
    assert open(p).read().strip() == "0"


def test_write_hold_file_missing_dir_does_not_raise(tmp_path):
    _write_hold_file(str(tmp_path / "nodir" / "h"), True)  # must not raise


def test_not_held_by_default(tmp_path):
    client = _make_client(tmp_path)
    held, reason = client._compute_hold()
    assert held is False and reason is None


def test_local_capture_hold(tmp_path):
    client = _make_client(tmp_path)
    client.set_local_capture_hold(True, "event_capture")
    held, reason = client._compute_hold()
    assert held is True and reason == "event_capture"


def test_server_hold_within_ttl(tmp_path):
    client = _make_client(tmp_path)
    t = [1000.0]
    client._clock = lambda: t[0]
    client.set_server_hold(True, "active_alert")
    t[0] = 1000.0 + SERVER_HOLD_TTL - 1
    held, reason = client._compute_hold()
    assert held is True and reason == "active_alert"


def test_server_hold_expires_after_ttl(tmp_path):
    client = _make_client(tmp_path)
    t = [1000.0]
    client._clock = lambda: t[0]
    client.set_server_hold(True, "active_alert")
    t[0] = 1000.0 + SERVER_HOLD_TTL + 1
    held, reason = client._compute_hold()
    assert held is False and reason is None


def test_local_wins_reason_over_server(tmp_path):
    client = _make_client(tmp_path)
    client.set_server_hold(True, "active_alert")
    client.set_local_capture_hold(True, "event_capture")
    _, reason = client._compute_hold()
    assert reason == "event_capture"


def test_heartbeat_writes_hold_file_and_fields(tmp_path):
    client = _make_client(tmp_path)
    client.set_local_capture_hold(True, "event_capture")
    client._publish_heartbeat()
    payload = json.loads(client._client.last_payload)
    assert payload["update_held"] is True
    assert payload["hold_reason"] == "event_capture"
    assert _hold_content(client) == "1"


def test_heartbeat_writes_zero_when_not_held(tmp_path):
    client = _make_client(tmp_path)
    client._publish_heartbeat()
    payload = json.loads(client._client.last_payload)
    assert payload["update_held"] is False
    assert payload["hold_reason"] is None
    assert _hold_content(client) == "0"


def test_hold_write_failure_does_not_break_heartbeat(tmp_path):
    client = _make_client(tmp_path)
    client._hold_file = str(tmp_path / "nodir" / "update_hold")  # unwritable dir
    client._publish_heartbeat()  # must not raise
    payload = json.loads(client._client.last_payload)
    assert "update_held" in payload
