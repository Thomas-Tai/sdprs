# sdprs/webcam_client/tests/test_push_engine.py
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.push_engine import PushEngine


def test_push_engine_init():
    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480],
              "jpeg_quality": 40, "target_fps": 8, "motion_threshold": 25}
    engine = PushEngine(config, "https://example.com", "sk-test")
    assert engine._node_id == "webcam_01"
    assert engine._streaming is False


def test_set_streaming_flag():
    config = {"node_id": "webcam_01", "device_index": 0}
    engine = PushEngine(config, "https://example.com", "sk-test")
    with patch.object(engine, "_start_encoder"):
        engine.set_streaming(True)
        assert engine._streaming is True
    with patch.object(engine, "_stop_encoder"):
        engine.set_streaming(False)
        assert engine._streaming is False


def test_push_snapshot_uses_webcam_endpoint_and_raises(monkeypatch):
    # C1 client-side guard: normal-mode frames go to /api/webcam/.../snapshot
    # (never /api/edge), and a 4xx must surface via raise_for_status(), not be
    # swallowed. This is the regression that made the whole feature fail silently.
    import numpy as np
    config = {"node_id": "webcam_01", "device_index": 0, "resolution": [640, 480]}
    engine = PushEngine(config, "https://example.com", "sk-test")
    mock_resp = MagicMock()
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    engine._client = mock_client
    engine._push_snapshot(np.zeros((480, 640, 3), dtype=np.uint8))
    posted_url = mock_client.post.call_args[0][0]
    assert "/api/webcam/webcam_01/snapshot" in posted_url
    assert "/api/edge/" not in posted_url
    mock_resp.raise_for_status.assert_called_once()
