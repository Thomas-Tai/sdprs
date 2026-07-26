# sdprs/webcam_client/tests/test_control_channel.py
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.control_channel import ControlChannel


def test_control_channel_init():
    cb = MagicMock()
    ch = ControlChannel("https://example.com", "sk-test", ["webcam_01"], cb)
    assert ch._node_ids == ["webcam_01"]
    assert not ch._stop_event.is_set()


def test_stop():
    cb = MagicMock()
    ch = ControlChannel("https://example.com", "sk-test", ["webcam_01"], cb)
    ch.stop()
    assert ch._stop_event.is_set()


def test_poll_node_5xx_triggers_backoff():
    # httpx does NOT raise on 5xx, so neither the 200-dispatch, the 401-stop, nor
    # the ConnectError backoff fires. A persistent non-200/non-401 must back off
    # (positive delay), not immediately re-poll (Task 9 [Important] busy-loop).
    cb = MagicMock()
    ch = ControlChannel("https://example.com", "sk-test", ["webcam_01"], cb)
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_client = MagicMock()
    mock_client.get.return_value = mock_resp
    ch._client = mock_client
    with patch.object(ch._stop_event, "wait") as mock_wait:
        ch._poll_node("webcam_01")
    cb.assert_not_called()                 # 5xx must NOT dispatch a command
    assert mock_wait.call_count == 1
    delay = mock_wait.call_args[0][0]
    assert delay > 0                       # backed off, not an immediate re-poll
    assert ch._backoff > 1.0               # backoff grew for the next cycle


def test_control_channel_reports_bad_key_on_401():
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append)

    class Resp:
        status_code = 401

    ch._client = type("C", (), {"get": lambda self, *a, **k: Resp()})()
    ch._poll_node("n1")
    assert seen == [Fault.BAD_KEY]


def test_control_channel_reports_bad_key_on_403():
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append)

    class Resp:
        status_code = 403

    ch._client = type("C", (), {"get": lambda self, *a, **k: Resp()})()
    ch._poll_node("n1")
    assert seen == [Fault.BAD_KEY]


def test_control_channel_reports_none_on_clean_poll():
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append)

    class Resp:
        status_code = 200

        def json(self):
            return {"command": None}

    ch._client = type("C", (), {"get": lambda self, *a, **k: Resp()})()
    ch._poll_node("n1")
    assert seen == [Fault.NONE]


def test_control_channel_reports_no_server_on_connect_timeout():
    """Review finding 1: ConnectTimeout is NOT a subclass of ConnectError
    (verified against the installed httpx 0.28.1 -- ConnectTimeout's MRO is
    TimeoutException -> TransportError -> RequestError -> HTTPError, with no
    ConnectError in it). It IS the client's own configured connect=3.0 timeout
    (control_channel.py run()), a real and likely failure mode. Before the fix
    it fell into the bare `except Exception` arm, which only logs -- the
    operator's status went silently stale. It must reach the hub as NO_SERVER,
    same as ConnectError."""
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault
    import httpx

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append)

    def fake_poll_node(node_id):
        ch._stop_event.set()  # let run()'s while-loop exit after this cycle
        raise httpx.ConnectTimeout("timed out")

    ch._poll_node = fake_poll_node
    with patch.object(ch._stop_event, "wait"):
        ch.run()
    assert seen == [Fault.NO_SERVER]


def test_control_channel_dedups_repeated_faults():
    """A failing poll runs continuously; the hub must not be called every time."""
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append)

    class Resp:
        status_code = 401

    ch._client = type("C", (), {"get": lambda self, *a, **k: Resp()})()
    for _ in range(5):
        ch._poll_node("n1")
    assert seen == [Fault.BAD_KEY], "repeat identical faults must report once"
