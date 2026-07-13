# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Login Throttle / Session Hardening Tests
Smart Disaster Prevention Response System

Covers the hardened /login flow in central_server/main.py:
- correct credentials succeed (303 + session cookie)
- wrong credentials return the error template (not a redirect)
- per-IP throttle locks out after LOGIN_MAX_ATTEMPTS failures (429)
- a successful login clears the IP's failure counter
"""

import os
import sys
from pathlib import Path

# Add the sdprs root to the import path so `central_server` is importable.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

# Set required environment variables BEFORE importing the app. setdefault so we
# don't clobber values another test module may already have set in this session.
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

from central_server import main as main_mod
from central_server.main import app
from central_server.config import get_settings

GOOD_USER = os.environ["DASHBOARD_USER"]
GOOD_PASS = os.environ["DASHBOARD_PASS"]


@pytest.fixture(autouse=True)
def reset_throttle():
    """The throttle dict is a module-global that persists across requests and
    tests, so reset it around every test for isolation."""
    main_mod._login_attempts.clear()
    yield
    main_mod._login_attempts.clear()


@pytest.fixture
def client():
    # No lifespan context is needed: the /login route is self-contained and
    # log_action() never raises even without a DB. Default TestClient host is
    # "testclient", so all requests share one throttle key.
    return TestClient(app)


def _post_login(client, username, password):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_correct_login_succeeds(client):
    resp = _post_login(client, GOOD_USER, GOOD_PASS)
    assert resp.status_code == 303
    # The session cookie must be issued on a successful login.
    assert "sdprs_session" in resp.headers.get("set-cookie", "")


def test_wrong_password_returns_error(client):
    resp = _post_login(client, GOOD_USER, "definitely-wrong")
    # Current handler renders the login template with an error (200), not a
    # redirect. Accept 401 too in case the status is tightened later.
    assert resp.status_code != 303
    assert resp.status_code in (200, 401)


def test_throttle_locks_after_max_attempts(client):
    main_mod._login_attempts.clear()
    get_settings.cache_clear()
    n = get_settings().LOGIN_MAX_ATTEMPTS

    # n failed attempts are each processed (error template, not locked yet).
    for _ in range(n):
        r = _post_login(client, GOOD_USER, "wrong")
        assert r.status_code in (200, 401)

    # The next attempt exceeds the limit and is rejected without checking creds.
    r = _post_login(client, GOOD_USER, "wrong")
    assert r.status_code == 429


def test_success_clears_throttle(client):
    main_mod._login_attempts.clear()
    get_settings.cache_clear()
    n = get_settings().LOGIN_MAX_ATTEMPTS

    # A few failures, strictly fewer than the max so we are not yet locked.
    pre = min(n - 1, 3)
    for _ in range(pre):
        _post_login(client, GOOD_USER, "wrong")

    # Correct login succeeds and clears this IP's failure counter.
    ok = _post_login(client, GOOD_USER, GOOD_PASS)
    assert ok.status_code == 303

    # Because the counter was reset, the next wrong attempt is failure #1, not
    # a lockout.
    again = _post_login(client, GOOD_USER, "wrong")
    assert again.status_code in (200, 401)

    total_recorded = sum(len(v) for v in main_mod._login_attempts.values())
    assert total_recorded == 1
