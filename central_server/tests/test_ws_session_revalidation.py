# -*- coding: utf-8 -*-
"""
Tests for the API-F6 fix: periodic WebSocket session re-validation.

Connect-time auth only proves the session was valid at handshake. The new
_session_revalidation_loop re-checks it every SESSION_REVALIDATION_INTERVAL_
SECONDS and, on an invalid session, sends the EXISTING `auth_expired` frame
and closes with 1008 — without inventing a new WS message type.

Had zero coverage. These drive the loop directly with the interval patched
to 0 so it runs instantly, using asyncio.run inside sync tests (the idiom in
test_ws_broadcast.py).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import asyncio

from central_server.services import websocket_service as ws_svc


class _StopLoop(Exception):
    """Breaks the otherwise-infinite valid-session loop in the test below
    without going through the close path."""


class FakeWS:
    """Minimal WebSocket stand-in: a scope-based session plus recorders."""

    def __init__(self, session):
        self.scope = {"session": session}
        self.sent = []
        self.closed = None

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000, reason=None):
        self.closed = {"code": code, "reason": reason}


def test_get_session_user_reads_scope_session():
    assert ws_svc._get_session_user(FakeWS({"user": "alice"})) == "alice"
    assert ws_svc._get_session_user(FakeWS({})) is None
    assert ws_svc._get_session_user(FakeWS(None)) is None


def test_loop_closes_and_sends_auth_expired_on_invalid_session(monkeypatch):
    monkeypatch.setattr(ws_svc, "SESSION_REVALIDATION_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(ws_svc, "_get_session_user", lambda ws: None)
    ws = FakeWS({})

    asyncio.run(ws_svc._session_revalidation_loop(ws))

    # Reuses the existing message type — NOT a new one.
    assert ws.sent and ws.sent[0]["type"] == "auth_expired"
    assert ws.closed is not None and ws.closed["code"] == 1008


def test_loop_continues_while_valid_then_closes_when_it_lapses(monkeypatch):
    monkeypatch.setattr(ws_svc, "SESSION_REVALIDATION_INTERVAL_SECONDS", 0)
    # Valid for two checks, then the session lapses.
    seq = iter(["alice", "alice", None])
    monkeypatch.setattr(ws_svc, "_get_session_user", lambda ws: next(seq))
    ws = FakeWS({})

    asyncio.run(ws_svc._session_revalidation_loop(ws))

    assert ws.closed is not None and ws.closed["code"] == 1008
    # auth_expired sent exactly once — only on the lapse, not the valid checks.
    assert sum(1 for m in ws.sent if m.get("type") == "auth_expired") == 1


def test_valid_session_does_not_close_within_the_checks(monkeypatch):
    """A session that stays valid must never send auth_expired or close.
    Bounded by a finite sequence so the otherwise-infinite loop terminates
    via StopIteration rather than hanging the test."""
    monkeypatch.setattr(ws_svc, "SESSION_REVALIDATION_INTERVAL_SECONDS", 0)
    seq = iter(["alice"] * 5)

    def check(ws):
        try:
            return next(seq)
        except StopIteration:
            raise _StopLoop  # break the loop without triggering the close path

    monkeypatch.setattr(ws_svc, "_get_session_user", check)
    ws = FakeWS({})

    try:
        asyncio.run(ws_svc._session_revalidation_loop(ws))
    except _StopLoop:
        pass

    assert ws.closed is None
    assert not any(m.get("type") == "auth_expired" for m in ws.sent)
