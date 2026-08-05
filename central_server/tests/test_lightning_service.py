# -*- coding: utf-8 -*-
"""WXA-004 lightning service unit tests. No live WebSocket — the socket and
decoder are exercised through pure functions / injected state."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server.config import get_settings


def test_lightning_settings_defaults():
    s = get_settings()
    assert s.LIGHTNING_ENABLED is True
    assert s.LIGHTNING_COUNT_RADIUS_KM == 50.0
    assert s.LIGHTNING_NEAREST_WINDOW_MIN == 30
    assert s.LIGHTNING_COUNT_WINDOW_MIN == 60
    assert s.LIGHTNING_STALE_AFTER_S == 300
