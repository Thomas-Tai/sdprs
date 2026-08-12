# -*- coding: utf-8 -*-
"""WXA-004 lifespan-wiring test. Boots the app via TestClient (triggers the
lifespan) with init_lightning_service stubbed so no live socket opens in CI."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def test_lifespan_wires_lightning(monkeypatch):
    from central_server import main as main_mod
    from central_server.main import app
    from fastapi.testclient import TestClient

    started = {"n": 0}

    class _StubSvc:
        def __init__(self):
            self._task = None
        async def start(self):
            started["n"] += 1
        async def stop(self):
            pass

    # main.py did `from .services.lightning_service import init_lightning_service`,
    # so patch the name bound INTO main — the lifespan builds our stub, no socket.
    monkeypatch.setattr(main_mod, "init_lightning_service", lambda settings: _StubSvc())

    with TestClient(app):
        assert app.state.lightning_service is not None
    assert started["n"] == 1  # start() awaited exactly once during startup
