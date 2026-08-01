# -*- coding: utf-8 -*-
"""
SDPRS Central Server — two HIGH-severity auth findings (audit 2026-07-26).

AUTH-002 (HIGH): the WebSocket periodic session re-validation loop is a no-op.
    `_session_revalidation_loop` decided "stay open?" from
    `_get_session_user(websocket)`, which reads the connect-time FROZEN
    `scope["session"]`. That dict keeps naming the connect-time user forever,
    so the predicate was always truthy and the socket kept streaming live
    telemetry/pump-state after the session should have died. The fix makes the
    stay-open decision respect the SEC-001 absolute cap (`auth._session_expired`)
    — which IS observable on a frozen session because `login_at` is a fixed
    anchor and wall-clock time advances past it.

AUTH-004 (HIGH/UX-security): `GET /login` rendered the login form to an
    already-authenticated user instead of bouncing them to the dashboard.

TDD note: AUTH-002 is unit-tested via the extracted predicate
`_ws_session_still_valid` plus an async test that drives the real
`_session_revalidation_loop` against a fake WebSocket. AUTH-004 is driven
through TestClient (a real signed session cookie obtained by logging in).
"""
import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path

# Project root so `central_server` is importable (matches sibling suites).
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

from central_server import auth
from central_server.main import app
from central_server.services import websocket_service as wss
from central_server.timeutil import utcnow

GOOD_USER = os.environ["DASHBOARD_USER"]
GOOD_PASS = os.environ["DASHBOARD_PASS"]


# ============================================================================
# AUTH-002 — WebSocket session re-validation predicate
# ============================================================================

def _fresh():
    return utcnow().isoformat()


def _aged(**kw):
    return (utcnow() - timedelta(**kw)).isoformat()


def test_auth002_predicate_keeps_fresh_session_open():
    """A session with a user and a fresh login_at anchor stays open."""
    assert wss._ws_session_still_valid({"user": "op", "login_at": _fresh()}) is True


def test_auth002_predicate_keeps_session_within_cap_open():
    """Just inside the 24h absolute cap is still valid."""
    assert wss._ws_session_still_valid(
        {"user": "op", "login_at": _aged(hours=23, minutes=59)}
    ) is True


def test_auth002_predicate_closes_session_past_absolute_cap():
    """CORE BUG: a session past the SEC-001 24h cap must be judged invalid so
    the loop tears the socket down. The old user-only predicate returned True
    here (the frozen scope still names the user), keeping the socket alive."""
    assert wss._ws_session_still_valid(
        {"user": "op", "login_at": _aged(hours=25)}
    ) is False


def test_auth002_predicate_closes_session_without_login_at_anchor():
    """No login_at anchor => cannot prove age => expired (SEC-001), even though
    a user is present. The old predicate wrongly kept this open."""
    assert wss._ws_session_still_valid({"user": "op"}) is False


def test_auth002_predicate_closes_session_with_no_user():
    assert wss._ws_session_still_valid({}) is False
    assert wss._ws_session_still_valid(None) is False


class _FakeWS:
    """Minimal stand-in for a Starlette WebSocket for the re-validation loop.

    Only exposes what the loop touches: a `scope` carrying the frozen session,
    plus async `send_json` / `close` that record what they were called with.
    """

    def __init__(self, session):
        self.scope = {"session": session}
        self.sent = []
        self.closed = None

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


def test_auth002_loop_tears_down_expired_session(monkeypatch):
    """Drive the real loop: an expired (past-cap) session must be sent the
    `auth_expired` frame and closed with code 1008. Under the old no-op
    predicate the loop never returns, so this times out (RED)."""
    monkeypatch.setattr(wss, "SESSION_REVALIDATION_INTERVAL_SECONDS", 0.0)
    fake = _FakeWS({"user": "op", "login_at": _aged(hours=25)})

    asyncio.run(asyncio.wait_for(wss._session_revalidation_loop(fake), timeout=1.0))

    assert fake.closed is not None, "socket was never closed for an expired session"
    assert fake.closed[0] == 1008
    assert any(m.get("type") == "auth_expired" for m in fake.sent)


def test_auth002_loop_keeps_valid_session_open(monkeypatch):
    """A still-valid session must NOT be torn down: run the loop for several
    intervals, then cancel it, and assert it never closed the socket."""
    monkeypatch.setattr(wss, "SESSION_REVALIDATION_INTERVAL_SECONDS", 0.01)
    fake = _FakeWS({"user": "op", "login_at": _fresh()})

    async def run():
        task = asyncio.create_task(wss._session_revalidation_loop(fake))
        await asyncio.sleep(0.1)  # allow ~10 re-checks
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert fake.closed is None, "valid session was wrongly torn down"


# ============================================================================
# AUTH-004 — GET /login must redirect an already-authenticated user
# ============================================================================

@pytest.fixture
def client():
    return TestClient(app)


def _login(client):
    """Log in with the conftest credentials; returns the 303 response and
    leaves the signed session cookie on the client's cookie jar."""
    r = client.post(
        "/login",
        data={"username": GOOD_USER, "password": GOOD_PASS},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"login setup failed: {r.status_code} {r.text[:200]}"
    return r


def test_auth004_unauthenticated_get_login_returns_form(client):
    """An unauthenticated GET /login still renders the form (200) — the fix
    must not break the normal login path."""
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 200
    assert 'name="password"' in r.text
    assert 'name="username"' in r.text


def test_auth004_authenticated_get_login_redirects_to_dashboard(client):
    """CORE BUG: an authenticated (non-expired) session hitting GET /login is
    bounced to the dashboard instead of being shown the form again."""
    _login(client)
    r = client.get("/login", follow_redirects=False)
    assert r.status_code in (302, 303), f"expected redirect, got {r.status_code}"
    assert r.headers["location"] == "/"


def test_auth004_authenticated_get_login_honors_safe_next(client):
    """When authenticated, a safe ?next= is honored (round-trips through the
    existing _safe_next_path helper)."""
    _login(client)
    r = client.get("/login?next=/monitor%3Fsdprs_state%3Dabc", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/monitor?sdprs_state=abc"


def test_auth004_authenticated_get_login_rejects_open_redirect_next(client):
    """A hostile ?next= must NOT be honored for the authenticated redirect —
    it falls back to `/` via _safe_next_path (no open redirect)."""
    _login(client)
    r = client.get("/login?next=http://evil.example.com/steal", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/"


def test_auth004_authenticated_get_login_next_login_avoids_loop(client):
    """?next=/login must not produce a redirect loop back to the form."""
    _login(client)
    r = client.get("/login?next=/login", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
