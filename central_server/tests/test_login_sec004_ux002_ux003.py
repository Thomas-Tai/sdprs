# -*- coding: utf-8 -*-
"""Login-page Lows: UX-002 (lang), SEC-004 (error role + dynamic year),
UX-003 (lockout retry countdown hook).

- UX-002: the login page declared `lang="zh-Hant"` while the SPA uses `zh-TW`;
  align them so screen readers pick one Chinese variant consistently.
- SEC-004: the auth-error banner had no `role="alert"` (not announced to
  assistive tech), and the copyright year was hardcoded `© 2026`. The year is
  now server-injected from utcnow().
- UX-003: on a 429 lockout the server passes `retry_after`; the banner carries
  `data-retry-after` and /static/login.js ticks a live countdown while disabling
  the submit button.

GET-path assertions use TestClient; the error/lockout banner is asserted by
rendering the template directly with a context (it references no `request`), which
avoids the POST CSRF/lockout dance while still pinning the template contract.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

from central_server.main import app, templates
from central_server.timeutil import utcnow

REPO_ROOT = Path(__file__).parent.parent.parent
LOGIN_JS = REPO_ROOT / "central_server" / "static" / "login.js"


@pytest.fixture
def client():
    return TestClient(app)


def _render(**ctx):
    """Render login.html directly with a context (no request dependency)."""
    return templates.env.get_template("login.html").render(**ctx)


# --- UX-002 --------------------------------------------------------------
def test_login_lang_is_zh_tw(client):
    body = client.get("/login").text
    assert 'lang="zh-TW"' in body
    assert 'zh-Hant' not in body


# --- SEC-004: dynamic copyright year ------------------------------------
def test_copyright_year_is_current(client):
    body = client.get("/login").text
    assert f"© {utcnow().year}" in body


# --- SEC-004: error banner announced ------------------------------------
def test_error_banner_has_role_alert():
    html = _render(error="帳號或密碼錯誤", next="", year=2026)
    assert "帳號或密碼錯誤" in html
    # role="alert" must wrap the ERROR banner (capslock hint already had one, so
    # an error render has at least two).
    assert html.count('role="alert"') >= 2, html


def test_no_error_means_one_alert_region():
    html = _render(next="", year=2026)
    # Only the capslock hint carries role="alert" when there's no error banner.
    assert html.count('role="alert"') == 1, html


# --- UX-003: lockout countdown hook -------------------------------------
def test_lockout_banner_carries_retry_after():
    html = _render(error="嘗試次數過多，請於 42 秒後再試", next="", retry_after=42, year=2026)
    assert 'data-retry-after="42"' in html


def test_no_retry_after_attr_without_lockout():
    html = _render(error="帳號或密碼錯誤", next="", year=2026)
    assert "data-retry-after" not in html


def test_login_js_has_countdown_logic():
    text = LOGIN_JS.read_text(encoding="utf-8")
    assert "data-retry-after" in text
    assert "disabled" in text  # gates the submit button during lockout


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
