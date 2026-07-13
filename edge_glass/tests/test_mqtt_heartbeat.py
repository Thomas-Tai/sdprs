"""
MQTTClient 心跳偵測器健康狀態（telemetry-only）單元測試

驗證 set_detector_health 與 _publish_heartbeat：
- 建構後 _visual_health / _audio_health 預設為 "unknown"
- set_detector_health 後，心跳 payload 內含 visual_health / audio_health
- 既有心跳欄位（node_id / status ...）不受影響

註：不連線任何 broker，也不相依 paho 版本 —
    建構時跳過 _init_client（真實 paho 初始化），並注入假 client 攔截 publish。
"""

import json
from unittest import mock

from comms.mqtt_client import MQTTClient


class _FakeClient:
    """假的 paho MQTT client：只記錄最後一次 publish，不做任何網路連線。"""

    def __init__(self):
        self.last_topic = None
        self.last_payload = None
        self.last_qos = None

    def publish(self, topic, payload, qos=0):
        self.last_topic = topic
        self.last_payload = payload
        self.last_qos = qos


def _make_client():
    """建立不連線的 MQTTClient：跳過真實 paho 初始化並注入假 client。"""
    config = {
        "node_id": "glass_node_01",
        "server": {"mqtt_broker": "localhost", "mqtt_port": 1883},
    }
    # 強制 PAHO_AVAILABLE=True 以通過建構子守衛，並把 _init_client 換成 no-op
    # （避免任何真實 paho.Client 建構 / 網路連線 / 版本相依）。
    with mock.patch("comms.mqtt_client.PAHO_AVAILABLE", True), \
            mock.patch.object(MQTTClient, "_init_client", lambda self: None):
        client = MQTTClient(config)
    client._client = _FakeClient()  # 注入假 client 以攔截心跳 publish
    return client


class TestDetectorHealthTelemetry:
    """偵測器健康狀態遙測（telemetry-only）。"""

    def test_defaults_unknown(self):
        """建構後 _visual_health / _audio_health 預設為 "unknown"。"""
        client = _make_client()
        assert client._visual_health == "unknown"
        assert client._audio_health == "unknown"

    def test_heartbeat_includes_detector_health(self):
        """set_detector_health 後，心跳 payload 內含對應健康欄位，既有欄位仍在。"""
        client = _make_client()

        client.set_detector_health(visual="blinded", audio="disabled")
        client._publish_heartbeat()

        # 解析假 client 攔截到的心跳 JSON
        payload = json.loads(client._client.last_payload)

        # 新增的偵測器健康欄位
        assert payload["visual_health"] == "blinded"
        assert payload["audio_health"] == "disabled"

        # 既有欄位不受影響
        assert payload["node_id"] == "glass_node_01"
        assert payload["status"] == "online"

    def test_partial_update_keeps_other_side(self):
        """僅更新 visual 時，audio 維持原值（None 參數不覆寫）。"""
        client = _make_client()

        client.set_detector_health(visual="paused")
        assert client._visual_health == "paused"
        assert client._audio_health == "unknown"

        client.set_detector_health(audio="stale")
        assert client._visual_health == "paused"  # 未被覆寫
        assert client._audio_health == "stale"
