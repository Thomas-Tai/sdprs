# -*- coding: utf-8 -*-
"""
Tests for WebSocketManager.broadcast() head-of-line-blocking fix.

broadcast() must dispatch to all clients CONCURRENTLY with a per-client send
timeout, so a single slow / stalled / failing client cannot block or stall
delivery to the other clients (and is instead isolated + removed).

No FastAPI app import is needed: we construct WebSocketManager directly and
feed it FAKE websocket objects inserted straight into the private
`_connections` set (bypassing add(), which would call websocket.accept()).

Async tests run via asyncio.run(...) inside plain sync test functions. This
matches the plain-sync style of test_ws_loop_capture.py and avoids depending on
pytest-asyncio's config mode (no conftest.py / pytest.ini exists, and we must
not add one).
"""
import os
import sys
import asyncio
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

from central_server.services import websocket_service
from central_server.services.websocket_service import WebSocketManager


# --- Fake websocket clients -------------------------------------------------

class FastClient:
    """Healthy client: records every frame it receives."""

    def __init__(self):
        self.received = []

    async def send_json(self, data):
        self.received.append(data)


class SlowClient:
    """Stalled client: send never completes within the (shrunk) timeout."""

    def __init__(self, sleep_seconds=1.0):
        self.sleep_seconds = sleep_seconds
        self.received = []

    async def send_json(self, data):
        await asyncio.sleep(self.sleep_seconds)
        # If we ever get here (should not, because timeout fires first) record it.
        self.received.append(data)


class FailClient:
    """Broken client: send raises immediately."""

    def __init__(self):
        self.received = []

    async def send_json(self, data):
        raise RuntimeError("boom")


def _make_manager(clients):
    """Build a WebSocketManager and inject fake clients directly.

    We bypass add() (which calls websocket.accept()) by populating the private
    connection set. The constructor already creates self._lock, but we assert
    it exists to be robust.
    """
    mgr = WebSocketManager()
    if getattr(mgr, "_lock", None) is None:
        mgr._lock = asyncio.Lock()
    for c in clients:
        mgr._connections.add(c)
    return mgr


# --- Tests ------------------------------------------------------------------

def test_slow_client_does_not_block_fast_client(monkeypatch):
    """A slow/stalled client must not delay delivery to healthy clients."""
    monkeypatch.setattr(websocket_service, "SEND_TIMEOUT_SECONDS", 0.05)

    slow = SlowClient(sleep_seconds=1.0)
    fast = FastClient()
    mgr = _make_manager([slow, fast])

    async def run():
        start = time.monotonic()
        await mgr.broadcast({"x": 1})
        return time.monotonic() - start

    elapsed = asyncio.run(run())

    # Concurrency + isolation: the whole broadcast finishes well before the
    # slow client's 1.0s sleep would have elapsed (bounded by the 0.05s timeout).
    assert elapsed < 0.3, f"broadcast took {elapsed:.3f}s (should be << 1.0s)"
    assert fast.received == [{"x": 1}]


def test_failing_client_removed(monkeypatch):
    """A client whose send raises is removed; healthy clients still get the frame."""
    monkeypatch.setattr(websocket_service, "SEND_TIMEOUT_SECONDS", 0.05)

    fail = FailClient()
    fast = FastClient()
    mgr = _make_manager([fail, fast])

    asyncio.run(mgr.broadcast({"x": 2}))

    assert fast.received == [{"x": 2}]
    assert fail not in mgr._connections
    assert fast in mgr._connections
    assert len(mgr._connections) == 1


def test_timed_out_client_removed(monkeypatch):
    """A client that can't accept a frame within the timeout is removed."""
    monkeypatch.setattr(websocket_service, "SEND_TIMEOUT_SECONDS", 0.05)

    slow = SlowClient(sleep_seconds=1.0)  # >> timeout
    fast = FastClient()
    mgr = _make_manager([slow, fast])

    asyncio.run(mgr.broadcast({"x": 3}))

    assert fast.received == [{"x": 3}]
    assert slow not in mgr._connections
    assert fast in mgr._connections
    assert len(mgr._connections) == 1


def test_broadcast_empty_no_error():
    """Broadcasting with no connected clients returns without error."""
    mgr = _make_manager([])
    # Should hit the early `if not self._connections: return` and not raise.
    asyncio.run(mgr.broadcast({"x": 4}))
    assert len(mgr._connections) == 0
