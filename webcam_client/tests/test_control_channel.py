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
