# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Dashboard SPA Shell Injection Regression Test
Smart Disaster Prevention Response System

Theme 2 (trust boundary) fix: `dashboard_page()` in central_server/main.py
injects the logged-in dashboard username into an inline <script> tag in the
SPA shell (static/spa/index.html, the __SDPRS_USER__ placeholder).
`json.dumps(user)` alone is NOT safe inside a <script> block: a username
containing "</script>" (or "<!--", "<script") can break out of the tag and
inject arbitrary HTML/JS. `_js_safe_json()` escapes '<', '>', '&', and the
JS line-separator characters U+2028/U+2029 after json.dumps() so no tag
breakout is possible while the result stays valid JSON/JS.

This test drives the *real* `dashboard_page` route function imported from
central_server.main (not a re-implementation), mounted on a minimal app so
we don't have to stand up the full app's DB/MQTT/lifespan machinery that
this particular route never touches.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Set required environment variables before importing the app
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from central_server.main import dashboard_page

# Classic inline-script breakout payload: closes the SDPRS_USER <script>
# tag early and opens a fresh one that would execute if left unescaped.
MALICIOUS_USER = "</script><script>alert(1)</script>"


@pytest.fixture
def client():
    """Minimal app hosting the real `dashboard_page` route.

    `dashboard_page` only reads `request.session["user"]` and the SPA shell
    file off disk (via BASE_DIR, resolved inside central_server/main.py) --
    it never touches the DB/MQTT/lifespan, so the full central_server.main
    `app` isn't needed here. A `/test-login` helper writes the session
    directly (bypassing the real /login's credential check + audit log,
    which do need a DB) so we can inject an arbitrary crafted username.
    """
    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="test-secret-key-for-testing",
        session_cookie="sdprs_session",
    )
    app.add_api_route("/", dashboard_page, methods=["GET"])

    @app.post("/test-login")
    async def test_login(request: Request):
        form = await request.form()
        request.session["user"] = form.get("username", "")
        return {"ok": True}

    with TestClient(app) as test_client:
        yield test_client


def _login_as(client, username):
    resp = client.post("/test-login", data={"username": username})
    assert resp.status_code == 200


class TestDashboardSpaShellInjection:
    """Regression guard for the inline-script injection defect (Theme 2)."""

    def test_malicious_username_cannot_break_out_of_script_tag(self, client):
        _login_as(client, MALICIOUS_USER)

        resp = client.get("/")

        assert resp.status_code == 200
        body = resp.text
        # The raw payload must never appear verbatim in the response -- if
        # it does, the attacker's </script><script> broke out of the
        # SDPRS_USER <script> tag and would execute in the browser.
        assert MALICIOUS_USER not in body
        assert "</script><script>alert(1)</script>" not in body
        # Sanity: the injection point is actually present and was reached.
        assert "window.SDPRS_USER = " in body

    def test_normal_username_round_trips(self, client):
        _login_as(client, "operator")

        resp = client.get("/")

        assert resp.status_code == 200
        body = resp.text

        marker = "window.SDPRS_USER = "
        start = body.index(marker) + len(marker)
        end = body.index(";", start)
        injected = body[start:end]

        # Must still be valid JSON/JS and decode back to the plain username.
        assert json.loads(injected) == "operator"
