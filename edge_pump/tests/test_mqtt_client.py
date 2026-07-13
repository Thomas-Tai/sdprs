"""Desktop tests for PumpMQTTClient's broker-connect ordering.

mqtt_client.py guards `network`/`umqtt` imports so the module imports under
desktop CPython, but PumpMQTTClient itself still calls network.WLAN(...) in
__init__ and constructs umqtt's MQTTClient in ensure_connection(). These
fakes stand in for both so the connect-ordering fix (settimeout applied
before connect(), per spec §9) is exercised without real hardware.
"""
import mqtt_client


class FakeSocket:
    """Records settimeout() calls into a call list shared with the client."""

    def __init__(self, calls):
        self._calls = calls

    def settimeout(self, value):
        self._calls.append(("settimeout", value))


class FakeMQTTClient:
    """Stand-in for umqtt.simple.MQTTClient that records call order.

    Unlike the real umqtt.simple (where .sock is None until connect() opens
    it), this fake exposes .sock from construction so the pre-connect
    settimeout attempt is observable and call order can be asserted.
    """

    def __init__(self, client_id, server, port, user=None, password=None):
        self.calls = []
        self.sock = FakeSocket(self.calls)
        self.client_id = client_id
        self.server = server
        self.port = port

    def set_last_will(self, topic, msg, retain=False, qos=0):
        self.calls.append(("set_last_will",))

    def connect(self):
        self.calls.append(("connect",))

    def publish(self, topic, msg):
        self.calls.append(("publish",))


class FakeWLAN:
    """Always-connected WLAN stub so ensure_connection() skips straight to
    the broker-connect block under test (the _wait_wifi path is untouched
    by this fix and is intentionally not exercised here)."""

    def __init__(self, mode=None):
        pass

    def active(self, v=None):
        return True

    def isconnected(self):
        return True

    def disconnect(self):
        pass

    def connect(self, ssid, password):
        pass

    def ifconfig(self):
        return ("127.0.0.1",)


class FakeNetwork:
    STA_IF = 0
    WLAN = FakeWLAN


def _make_client(monkeypatch):
    monkeypatch.setattr(mqtt_client, "network", FakeNetwork)
    monkeypatch.setattr(mqtt_client, "MQTTClient", FakeMQTTClient)
    return mqtt_client.PumpMQTTClient(
        ssid="s", password="p", broker="b", port=1883,
        node_id="pump_node_01", topic="sdprs/edge/pump_node_01/pump_status")


def test_ensure_connection_applies_socket_timeout_before_connect(monkeypatch):
    client = _make_client(monkeypatch)
    assert client.ensure_connection() is True

    kinds = [c[0] for c in client._client.calls]
    # settimeout must be applied before connect() (spec §9: bound the broker
    # connect so a dead broker raises OSError instead of hanging the loop).
    assert kinds.index("settimeout") < kinds.index("connect")
    # set_last_will() must still precede connect() (LWT ordering invariant).
    assert kinds.index("set_last_will") < kinds.index("connect")


def test_ensure_connection_still_bounds_publish_after_connect(monkeypatch):
    # The post-connect settimeout call is preserved so publish()/check_msg()
    # remain bounded even on real umqtt.simple builds where .sock is None
    # until connect() creates it (the pre-connect call is best-effort there).
    client = _make_client(monkeypatch)
    client.ensure_connection()

    kinds = [c[0] for c in client._client.calls]
    assert kinds.count("settimeout") == 2
    connect_idx = kinds.index("connect")
    assert any(i > connect_idx for i, k in enumerate(kinds) if k == "settimeout")
