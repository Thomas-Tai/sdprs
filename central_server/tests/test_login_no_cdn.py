# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Login CDN Removal / CSP Tests
Smart Disaster Prevention Response System

Regression tests for finding Auth-H1 (2026-07-16): the login page must NOT
load Tailwind (or any other asset) from an external CDN, because a script
tag executes with full page context and could be used to hook the password
field if the CDN is compromised or DNS-hijacked on a hostile LAN.

Covered:
  1. login.html contains no `cdn.tailwindcss.com` and no `https://` / `http://`
     scheme references at all.
  2. login.html references the same-origin vendored copy at
     `/static/vendor/tailwind.min.js`.
  3. login.html carries a Content-Security-Policy <meta> tag that pins
     `default-src` and `form-action` to `'self'`.
  4. The vendored file exists on disk at `central_server/static/vendor/
     tailwind.min.js` and is non-trivial in size (>= 10 KB).
  5. `GET /login` returns 200 and the response body still references the
     local vendor path (not the CDN URL).
  6. `GET /static/vendor/tailwind.min.js` returns 200 through the /static
     mount, so the browser can actually load it.

The TestClient setup mirrors central_server/tests/test_login_throttle.py:49-57
— no lifespan context is needed because /login GET and the static mount are
self-contained and never touch the DB.
"""

import re
import sys
from pathlib import Path

# Add the sdprs root to the import path so `central_server` is importable.
# conftest.py at central_server/tests/conftest.py already provides strong
# credentials via os.environ.setdefault().
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

from central_server.main import app


REPO_ROOT = Path(__file__).parent.parent.parent
LOGIN_HTML = REPO_ROOT / "central_server" / "templates" / "login.html"
VENDOR_TAILWIND = (
    REPO_ROOT / "central_server" / "static" / "vendor" / "tailwind.min.js"
)


@pytest.fixture
def client():
    # No lifespan context: /login GET renders a static template and the
    # /static mount serves files off disk. Both are self-contained.
    return TestClient(app)


# --- File-content assertions -------------------------------------------------

def test_login_html_has_no_cdn_reference():
    """The Tailwind CDN URL must be gone from login.html (case-insensitive),
    and no other external http(s) scheme references should have been added."""
    body = LOGIN_HTML.read_text(encoding="utf-8")

    # Case-insensitive search so a rename like `CDN.TailwindCSS.com` still fails.
    assert re.search(r"cdn\.tailwindcss\.com", body, re.IGNORECASE) is None, (
        "login.html still references cdn.tailwindcss.com — external CDN "
        "was supposed to be removed (finding Auth-H1)."
    )

    # No other external scheme references either (belt-and-suspenders against a
    # different CDN sneaking in later).
    assert "https://" not in body, (
        "login.html contains an https:// URL — the page must only load "
        "same-origin assets."
    )
    assert "http://" not in body, (
        "login.html contains an http:// URL — the page must only load "
        "same-origin assets."
    )


def test_login_html_references_local_tailwind():
    """The vendored same-origin path must be present in the <script> tag."""
    body = LOGIN_HTML.read_text(encoding="utf-8")
    assert '<script src="/static/vendor/tailwind.min.js"></script>' in body, (
        "login.html must load Tailwind from the same-origin vendored copy."
    )


def test_login_html_has_csp_meta_tag():
    """A CSP <meta> tag must pin default-src and form-action to 'self'."""
    body = LOGIN_HTML.read_text(encoding="utf-8")
    assert 'http-equiv="Content-Security-Policy"' in body, (
        "login.html must carry a Content-Security-Policy meta tag."
    )
    # These are the two directives that most directly mitigate the
    # credential-exfil scenario the finding described.
    assert "default-src 'self'" in body, (
        "CSP must include default-src 'self' to block off-origin loads."
    )
    assert "form-action 'self'" in body, (
        "CSP must include form-action 'self' so a malicious script cannot "
        "redirect the login POST off-domain."
    )


# --- On-disk vendored asset --------------------------------------------------

def test_vendored_tailwind_exists_and_is_nontrivial():
    """The Tailwind bytes must actually be present at the vendored location."""
    assert VENDOR_TAILWIND.exists(), (
        f"Vendored Tailwind is missing at {VENDOR_TAILWIND}. Copy it from "
        f"central_server/static/spa/vendor/tailwind.min.js."
    )
    size = VENDOR_TAILWIND.stat().st_size
    assert size >= 10 * 1024, (
        f"Vendored Tailwind is only {size} bytes; expected >=10240. Something "
        f"went wrong with the copy."
    )


# --- Live HTTP surface -------------------------------------------------------

def test_get_login_serves_local_tailwind(client):
    """The rendered /login response must reference the local vendor path and
    must NOT leak the old CDN URL."""
    resp = client.get("/login")
    assert resp.status_code == 200
    body = resp.text
    assert "/static/vendor/tailwind.min.js" in body
    assert re.search(r"cdn\.tailwindcss\.com", body, re.IGNORECASE) is None


def test_get_vendored_tailwind_served_by_static_mount(client):
    """The /static mount must actually serve the vendored file so the browser
    can load it at page render time."""
    resp = client.get("/static/vendor/tailwind.min.js")
    assert resp.status_code == 200
    # Non-empty body — TestClient decodes it as bytes here.
    assert len(resp.content) >= 10 * 1024
