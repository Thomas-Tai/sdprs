"""版本回報（deployed SHA）單元測試：讀取 marker + 心跳帶入 version。
不連線 broker、不相依 paho —— 建構時跳過 _init_client 並注入假 client。"""
import json
from unittest import mock

from comms.mqtt_client import MQTTClient, _read_deployed_version


class _FakeClient:
    def __init__(self):
        self.last_payload = None

    def publish(self, topic, payload, qos=0):
        self.last_payload = payload


def _make_client():
    config = {"node_id": "glass_node_01",
              "server": {"mqtt_broker": "localhost", "mqtt_port": 1883}}
    with mock.patch("comms.mqtt_client.PAHO_AVAILABLE", True), \
            mock.patch.object(MQTTClient, "_init_client", lambda self: None):
        client = MQTTClient(config)
    client._client = _FakeClient()
    return client


def test_read_deployed_version_present(tmp_path):
    f = tmp_path / "sha"
    f.write_text("723456f76fec578f9af85d6ecc460896cba38254\n")
    assert _read_deployed_version(str(f)) == "723456f76fec578f9af85d6ecc460896cba38254"


def test_read_deployed_version_missing(tmp_path):
    assert _read_deployed_version(str(tmp_path / "nope")) is None


def test_read_deployed_version_blank(tmp_path):
    f = tmp_path / "sha"
    f.write_text("   \n")
    assert _read_deployed_version(str(f)) is None


def test_heartbeat_includes_version_key():
    client = _make_client()
    client._publish_heartbeat()
    payload = json.loads(client._client.last_payload)
    assert "version" in payload  # key always present (value may be None)


def test_heartbeat_reports_set_version():
    client = _make_client()
    client._version = "deadbeefcafe"
    client._publish_heartbeat()
    payload = json.loads(client._client.last_payload)
    assert payload["version"] == "deadbeefcafe"
