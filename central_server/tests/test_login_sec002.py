# -*- coding: utf-8 -*-
"""SEC-002 (Medium): login form UX/accessibility hardening.

The login form lacked `autocomplete` attributes (breaking password managers /
browser autofill), a Caps Lock hint (the single most common cause of a "correct"
password being rejected), and a password reveal affordance. These are added to
`templates/login.html`; the interactive parts (reveal toggle, Caps Lock hint)
live in a same-origin `/static/login.js` because the page CSP is
`script-src 'self'` (no inline scripts). These tests pin the rendered contract
and that the script is actually served — the JS behaviour itself needs a
browser and is kept simple/correct-by-inspection.

Harness mirrors tests/test_login_no_cdn.py (no lifespan; /login GET + /static
mount are self-contained and never touch the DB).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

from central_server.main import app

REPO_ROOT = Path(__file__).parent.parent.parent
LOGIN_JS = REPO_ROOT / "central_server" / "static" / "login.js"


@pytest.fixture
def client():
    return TestClient(app)


def test_login_form_has_autocomplete_attributes(client):
    body = client.get("/login").text
    assert 'autocomplete="username"' in body
    assert 'autocomplete="current-password"' in body


def test_login_form_has_password_reveal_toggle(client):
    body = client.get("/login").text
    assert 'id="toggle-password"' in body
    # Must be type="button" so clicking it never submits the form.
    assert 'type="button"' in body
    assert 'aria-controls="password"' in body


def test_login_form_has_capslock_hint(client):
    body = client.get("/login").text
    assert 'id="capslock-hint"' in body
    # Hidden until JS toggles it on; announced to assistive tech.
    assert 'role="alert"' in body


def test_login_references_external_script(client):
    body = client.get("/login").text
    assert '<script src="/static/login.js"' in body


def test_login_js_is_served_and_covers_both_features(client):
    resp = client.get("/static/login.js")
    assert resp.status_code == 200
    text = resp.content.decode("utf-8")
    # Both affordances present in the served asset.
    assert "CapsLock" in text
    assert "toggle-password" in text or "toggle" in text


def test_login_js_exists_on_disk():
    assert LOGIN_JS.exists(), f"missing {LOGIN_JS}"
    assert LOGIN_JS.stat().st_size > 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
