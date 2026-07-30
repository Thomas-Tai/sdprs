# -*- coding: utf-8 -*-
"""SEC-001: absolute 24h session lifetime enforced from login_at.

Before this fix, ``login_at`` was written at login and re-stamped by
``/api/session/extend`` but NEVER compared, so an active session was immortal
and every extend reset the clock indefinitely (the code comment claiming a hard
24h expiry was factually wrong).

These pin the hard absolute cap:
  * a session older than 24h from login_at is rejected AND cleared;
  * a session with no login_at anchor at all is treated as expired;
  * an HTML request past the cap redirects to /login, an API request gets 401;
  * extend refuses to resurrect an expired session, and WITHIN the cap it does
    NOT move the absolute anchor (so it can slide the cookie but never reset the
    24h clock).

These call the async auth/endpoint functions directly with a minimal fake
Request (a ``session`` dict + a ``headers`` mapping) via ``asyncio.run`` — no
event-loop plugin or signed session cookie required.
"""
import asyncio
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi import HTTPException

from central_server import auth
from central_server.timeutil import utcnow


def _request(session, accept=""):
    return SimpleNamespace(session=session, headers={"accept": accept})


def test_fresh_session_is_accepted():
    session = {"user": "op", "login_at": utcnow().isoformat()}
    assert asyncio.run(auth.get_current_user(_request(session))) == "op"


def test_session_just_within_cap_still_valid():
    fresh = (utcnow() - timedelta(hours=23, minutes=59)).isoformat()
    session = {"user": "op", "login_at": fresh}
    assert asyncio.run(auth.get_current_user(_request(session))) == "op"


def test_session_past_absolute_cap_is_rejected_and_cleared():
    stale = (utcnow() - timedelta(hours=24, minutes=1)).isoformat()
    session = {"user": "op", "login_at": stale}
    with pytest.raises(HTTPException) as ei:
        asyncio.run(auth.get_current_user(_request(session)))
    assert ei.value.status_code == 401
    assert "user" not in session  # cleared so a stale cookie can't be reused


def test_session_without_login_at_is_treated_as_expired():
    session = {"user": "op"}  # no absolute anchor -> cannot prove age -> expired
    with pytest.raises(HTTPException):
        asyncio.run(auth.get_current_user(_request(session)))


def test_html_request_past_cap_redirects_to_login():
    stale = (utcnow() - timedelta(days=2)).isoformat()
    session = {"user": "op", "login_at": stale}
    with pytest.raises(HTTPException) as ei:
        asyncio.run(auth.get_current_user(_request(session, accept="text/html")))
    assert ei.value.status_code == 302
    assert ei.value.headers.get("Location") == "/login"


def test_extend_refuses_expired_session_and_clears():
    from central_server import main as main_mod

    stale = (utcnow() - timedelta(hours=25)).isoformat()
    session = {"user": "op", "login_at": stale}
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main_mod.extend_session(_request(session)))
    assert ei.value.status_code == 401
    assert "user" not in session


def test_extend_within_cap_does_not_move_the_absolute_anchor():
    from central_server import main as main_mod

    anchor = (utcnow() - timedelta(hours=1)).isoformat()
    session = {"user": "op", "login_at": anchor}
    result = asyncio.run(main_mod.extend_session(_request(session)))
    assert result["ok"] is True
    # The absolute 24h clock must NOT be reset by an extend (that immortality
    # bug is exactly what SEC-001 fixes).
    assert session["login_at"] == anchor
