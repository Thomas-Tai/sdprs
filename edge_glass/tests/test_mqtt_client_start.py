"""
MQTTClient.start() 開機健壯性（boot robustness）單元測試

回歸驗證 start() 的非阻塞連線與自動重連行為：
- start() 使用非阻塞 connect_async（而非阻塞 connect），並帶 keepalive=60
- connect_async 拋出例外時 start() 不得中斷，loop_start() 仍必須被呼叫
  （核心回歸：broker 在開機時暫時無法連線，網路迴圈仍會啟動並自動重連）
- start() 具幂等性：第二次呼叫因 _running 守衛而不重複啟動

註：不連線任何 broker，也不相依 paho 版本 —
    建構時跳過 _init_client（真實 paho 初始化），並注入假 client 攔截連線呼叫。
    另以 mock.patch.object 把 _start_heartbeat_loop 換成 no-op，避免啟動真實計時器線程。
"""

from unittest import mock

from comms.mqtt_client import MQTTClient


class _FakeClient:
    """假的 paho MQTT client：記錄連線/迴圈呼叫，不做任何網路連線。"""

    def __init__(self):
        self.connect_async_calls = []   # 記錄 (host, port, keepalive)
        self.connect_calls = []         # 阻塞 connect（不應被呼叫）
        self.loop_start_count = 0
        self.raise_on_connect_async = False

    def connect_async(self, host, port, keepalive=60):
        self.connect_async_calls.append((host, port, keepalive))
        if self.raise_on_connect_async:
            raise OSError("simulated: broker unreachable at boot")

    def connect(self, host, port, keepalive=60):
        self.connect_calls.append((host, port, keepalive))

    def loop_start(self):
        self.loop_start_count += 1


def _make_client():
    """建立不連線的 MQTTClient：跳過真實 paho 初始化並注入假 client，並停用心跳線程。"""
    config = {
        "node_id": "glass_node_01",
        "server": {"mqtt_broker": "test-broker", "mqtt_port": 1883},
    }
    # 強制 PAHO_AVAILABLE=True 以通過建構子守衛，並把 _init_client 換成 no-op
    # （避免任何真實 paho.Client 建構 / 網路連線 / 版本相依）。
    with mock.patch("comms.mqtt_client.PAHO_AVAILABLE", True), \
            mock.patch.object(MQTTClient, "_init_client", lambda self: None):
        client = MQTTClient(config)
    client._client = _FakeClient()  # 注入假 client 以攔截連線呼叫
    return client


class TestMQTTClientStart:
    """start() 開機健壯性。"""

    def test_start_uses_connect_async_not_blocking_connect(self):
        """start() 使用非阻塞 connect_async（帶 keepalive=60），不呼叫阻塞 connect。"""
        client = _make_client()

        # 停用真實心跳計時器線程
        with mock.patch.object(MQTTClient, "_start_heartbeat_loop", lambda self: None):
            client.start()

        fake = client._client
        assert fake.connect_async_calls == [("test-broker", 1883, 60)]
        assert fake.connect_calls == []  # 阻塞 connect 絕不應被呼叫
        assert fake.loop_start_count == 1

    def test_start_starts_loop_even_when_connect_async_raises(self):
        """核心回歸：connect_async 拋例外時 start() 不中斷，loop_start 仍被呼叫。"""
        client = _make_client()
        client._client.raise_on_connect_async = True

        with mock.patch.object(MQTTClient, "_start_heartbeat_loop", lambda self: None):
            # 不得拋出例外（開機時 broker 暫時無法連線仍要能啟動）
            client.start()

        fake = client._client
        # connect_async 被嘗試過（並拋例外）
        assert fake.connect_async_calls == [("test-broker", 1883, 60)]
        # 關鍵：即使連線設定失敗，網路迴圈仍必須啟動以觸發自動重連
        assert fake.loop_start_count == 1
        assert client._running is True

    def test_start_is_idempotent(self):
        """重複呼叫 start() 因 _running 守衛而不重複啟動（loop_start 只被呼叫一次）。"""
        client = _make_client()

        with mock.patch.object(MQTTClient, "_start_heartbeat_loop", lambda self: None):
            client.start()
            client.start()  # 第二次應提早返回

        fake = client._client
        assert fake.loop_start_count == 1
        assert len(fake.connect_async_calls) == 1
