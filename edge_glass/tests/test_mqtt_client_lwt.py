"""
MQTTClient Last-Will-and-Testament (LWT) 單元測試

驗證 _init_client 於建構期呼叫 will_set，讓 broker 在節點非正常斷線
（crash / power loss）時，立即發布 OFFLINE 標記到本節點心跳主題：

- will_set 恰好被呼叫一次
- topic == topic_heartbeat(node_id)
- payload 解析為 {"node_id": <id>, "status": "OFFLINE", "online": False}
- qos == 0、retain is False
- will_set 於 _init_client（建構期）設定，即 start()/connect_async 之前

註：與其他 mqtt 測試不同，本檔「不」跳過 _init_client —
    我們要讓 _init_client 真的執行以驗證 will_set。因此在 paho
    client 工廠層級（comms.mqtt_client.mqtt.Client）注入假 client，
    讓 _init_client 全程乾淨執行而不做任何真實 paho 建構 / 網路連線。
"""

import json
import types
from unittest import mock

from comms.mqtt_client import MQTTClient
from shared.mqtt_topics import topic_heartbeat


class _FakeClient:
    """假的 paho MQTT client：記錄 will_set，其餘 _init_client 用到的方法為 no-op。"""

    def __init__(self, *args, **kwargs):
        self.will_set_calls = []  # 記錄 (topic, payload, qos, retain)
        # _init_client 會設定的回調屬性
        self.on_connect = None
        self.on_message = None
        self.on_disconnect = None

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will_set_calls.append((topic, payload, qos, retain))

    def reconnect_delay_set(self, min_delay=1, max_delay=60):
        pass

    def username_pw_set(self, username, password):
        pass

    def tls_set(self):
        pass


def _make_client(node_id="glass_node_01"):
    """建立 MQTTClient，讓 _init_client 真的執行，並在工廠層注入假 client。"""
    config = {
        "node_id": node_id,
        "server": {"mqtt_broker": "localhost", "mqtt_port": 1883},
    }
    # 強制 PAHO_AVAILABLE=True 以通過建構子守衛；於工廠層注入假 client。
    # 注意：此環境未安裝 paho，模組層級 `mqtt` 為 None，故不能 patch
    # `mqtt.Client`（會在 None 上取屬性而報錯）。改為以帶 Client 屬性的
    # 假命名空間整個取代 `mqtt`，讓 _init_client 的 `mqtt.Client(...)` 命中假工廠。
    fake_mqtt = types.SimpleNamespace(Client=_FakeClient)
    with mock.patch("comms.mqtt_client.PAHO_AVAILABLE", True), \
            mock.patch("comms.mqtt_client.mqtt", fake_mqtt):
        client = MQTTClient(config)
    return client


class TestMQTTClientLWT:
    """MQTT Last-Will-and-Testament（LWT）。"""

    def test_will_set_called_with_offline_marker(self):
        """建構後 will_set 恰被呼叫一次，主題/內容/qos/retain 皆符合凍結契約。"""
        node_id = "glass_node_01"
        client = _make_client(node_id)

        calls = client._client.will_set_calls
        assert len(calls) == 1  # 恰好一次

        topic, payload, qos, retain = calls[0]

        # 主題為本節點自己的心跳主題
        assert topic == topic_heartbeat(node_id)

        # payload 解析為凍結契約
        parsed = json.loads(payload)
        assert parsed == {
            "node_id": node_id,
            "status": "OFFLINE",
            "online": False,
        }

        # QoS 0、retain=False（避免 broker 保留過期遺囑造成上線抖動）
        assert qos == 0
        assert retain is False

    def test_will_set_before_connect_semantically(self):
        """will_set 於 _init_client（建構期）已設定 — 即 start()/connect_async 之前。

        本測試不呼叫 start()，故若 will_set 已被記錄，即證明它發生在建構期
        （paho 要求 will_set 必須在 connect 之前，本測試以此驗證時序語意）。
        """
        client = _make_client()

        # 未呼叫 start()，仍已記錄 will_set → 於 _init_client 設定，先於任何 connect
        assert len(client._client.will_set_calls) == 1
