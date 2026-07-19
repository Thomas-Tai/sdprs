# -*- coding: utf-8 -*-
"""
SDPRS Central Server — CSRF Origin Gate + Auth Audit Persistence Tests
Smart Disaster Prevention Response System

Covers three 2026-07-16 hardening additions in central_server/main.py:

- Auth-E1: CSRFOriginMiddleware rejects cross-site mutating requests to
  /api/* and /logout by comparing Origin/Referer against the request's
  Host header (with optional CSRF_TRUSTED_ORIGINS extension). GET/HEAD/
  OPTIONS bypass. /login bypasses (unauthenticated form POST is the point).
  Requests with NO Origin AND NO Referer bypass (non-browser clients).

- Auth-I1: a failed /login attempt appends ACTION_LOGIN_FAILED to the
  operator_actions table, with the source IP as target_id.

- Auth-I2: a /login attempt that hits the throttle lockout appends
  ACTION_LOGIN_LOCKED to the same table (noisy on purpose — the row-pile
  IS the signal an operator uses to find the source IP).

The tests use FastAPI's TestClient (whose default base URL is
http://testserver and whose default Host header is thus "testserver") to
drive the middleware; audit-persistence tests spin up an in-memory
SQLite table and monkey-patch get_db_cursor / get_backend the same way
test_audit_service.py does.
"""

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

# Add project root so `central_server` is importable.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# conftest.py already sets strong-enough DASHBOARD_USER / DASHBOARD_PASS /
# EDGE_API_KEY / SECRET_KEY values that pass validate_settings.

import pytest
from fastapi.testclient import TestClient

import central_server.database as database
from central_server import main as main_mod
from central_server.main import app
from central_server.config import get_settings
from central_server.services import audit_service as audit_service_module
from central_server.services.audit_service import (
    ACTION_LOGIN_FAILED,
    ACTION_LOGIN_LOCKED,
    list_actions,
    log_action,
)

GOOD_USER = os.environ["DASHBOARD_USER"]
GOOD_PASS = os.environ["DASHBOARD_PASS"]


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def _reset_throttle_and_env(monkeypatch):
    """Reset the login-throttle dict and clear CSRF_TRUSTED_ORIGINS between
    tests so state does not leak across cases. get_settings() is cached, so
    clear it too — the middleware also reads os.environ directly at request
    time (deliberate: no restart needed to add trusted origins), so the env
    clear alone is sufficient for CSRF cases."""
    main_mod._login_attempts.clear()
    monkeypatch.delenv("CSRF_TRUSTED_ORIGINS", raising=False)
    get_settings.cache_clear()
    yield
    main_mod._login_attempts.clear()
    get_settings.cache_clear()


@pytest.fixture
def client():
    """TestClient — default base URL is http://testserver, so the same-origin
    Origin string is "http://testserver". The client host (request.client.host,
    used as target_id in audit rows) is "testclient" per Starlette default."""
    return TestClient(app)


@pytest.fixture
def audit_env(monkeypatch):
    """In-memory SQLite operator_actions table, wired into audit_service via
    the same monkeypatch pattern as test_audit_service.py.

    audit_service.py does `from ..database import get_db_cursor, get_backend`
    at module load, binding those names in its own module. Patching the
    source module is not enough — we patch both for safety."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE operator_actions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            operator     TEXT NOT NULL,
            action_type  TEXT NOT NULL,
            target_id    TEXT,
            details_json TEXT
        )
    """)
    conn.commit()

    @contextmanager
    def _cursor():
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    monkeypatch.setattr(database, "get_db_cursor", _cursor)
    monkeypatch.setattr(database, "get_backend", lambda: "sqlite")
    monkeypatch.setattr(audit_service_module, "get_db_cursor", _cursor)
    monkeypatch.setattr(audit_service_module, "get_backend", lambda: "sqlite")
    yield conn
    conn.close()


def _post_login(client, username, password, headers=None):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
        headers=headers or {},
    )


# ============================================================================
# Auth-E1: CSRFOriginMiddleware
# ============================================================================

def test_csrf_bypasses_get_requests(client):
    """(1) GET is never guarded — Origin from anywhere must pass through.
    /api/health is a public GET route that returns 200 regardless of auth,
    so we get a clean signal that the middleware did not interfere."""
    r = client.get("/api/health", headers={"Origin": "http://evil.example.com"})
    assert r.status_code == 200
    assert r.json().get("status") == "healthy"


def test_csrf_bypasses_login_route(client):
    """(2) POST /login is on the bypass list even under a cross-site Origin.
    An empty form yields the standard "wrong credentials" template (200);
    the important assertion is that we did NOT get the middleware's
    403 "CSRF: origin not allowed" body."""
    r = _post_login(client, "no-such-user", "no-such-pass",
                    headers={"Origin": "http://evil.example.com"})
    assert r.status_code in (200, 401)  # login handler's response, not CSRF 403
    assert "CSRF" not in r.text


def test_csrf_same_origin_post_passes_gate(client):
    """(3) A same-origin POST to a guarded /api/* route passes the CSRF
    check. The route itself will 401 (no session), but the important thing
    is that we reached the route rather than being 403'd by the gate."""
    r = client.post(
        "/api/session/extend",
        headers={"Origin": "http://testserver"},
    )
    assert r.status_code == 401
    assert "CSRF" not in r.text


def test_csrf_cross_site_post_is_blocked(client):
    """(4) A cross-site Origin on a guarded /api/* POST returns 403 with
    the middleware's specific error body — proving the gate fired before
    the route saw the request."""
    r = client.post(
        "/api/session/extend",
        headers={"Origin": "http://evil.example.com"},
    )
    assert r.status_code == 403
    assert "CSRF: origin not allowed" in r.text


def test_csrf_missing_origin_and_referer_passes(client):
    """(5) A request with NEITHER Origin NOR Referer must bypass the gate
    (typical of curl/httpx clients and same-origin server-rendered forms).
    Only the route's own auth check should apply — 401 from the route,
    NOT 403 from the middleware."""
    r = client.post("/api/session/extend")  # no Origin, no Referer
    assert r.status_code == 401
    assert "CSRF" not in r.text


def test_csrf_malformed_origin_is_rejected(client):
    """(6) A malformed Origin (no scheme/netloc parseable) yields the
    middleware's 'malformed origin' 403 — distinct body from the
    'origin not allowed' case, so operators can distinguish the two in
    logs and forensic review."""
    r = client.post(
        "/api/session/extend",
        headers={"Origin": "not-a-real-url"},
    )
    assert r.status_code == 403
    assert "CSRF: malformed origin" in r.text


def test_csrf_referer_fallback_passes_when_origin_absent(client):
    """(7) With no Origin header, the middleware falls back to Referer for
    the same-origin check. A same-origin Referer must be accepted so that
    browsers which strip Origin (e.g. some navigation POSTs) still work."""
    r = client.post(
        "/api/session/extend",
        headers={"Referer": "http://testserver/some/page"},
    )
    assert r.status_code == 401  # route auth, not CSRF
    assert "CSRF" not in r.text


def test_csrf_https_origin_passes_when_request_scheme_is_http(client):
    """Regression: behind a TLS-terminating proxy (Zeabur, nginx, cloudfront)
    the app sees `http://` internally while the browser's Origin header is
    `https://<same-host>`. The middleware must NOT 403 that — same-host
    same-origin is legitimate regardless of internal scheme. Before the fix,
    the allowlist was computed as `{request.url.scheme}://{host}` = `http://testserver`,
    so an `https://testserver` Origin fell out of allowed and 403'd all
    state-changing SPA requests behind Zeabur."""
    r = client.post(
        "/api/session/extend",
        headers={"Origin": "https://testserver"},
    )
    assert r.status_code == 401  # gate passed, route rejected on auth
    assert "CSRF" not in r.text


def test_csrf_forwarded_proto_header_honored(client):
    """When X-Forwarded-Proto is present (real deployment behind a proxy),
    combining it with the request's Host header must produce a matching
    allowed origin. Belt-and-suspenders for the both-schemes guard above."""
    r = client.post(
        "/api/session/extend",
        headers={
            "Origin": "https://ops.example.com",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "ops.example.com",
        },
    )
    assert r.status_code == 401
    assert "CSRF" not in r.text


def test_csrf_trusted_origins_env_extends_allowlist(client, monkeypatch):
    """(8) CSRF_TRUSTED_ORIGINS is honored — an origin listed there must
    be accepted even though it is not the request's own Host. Comma-
    separated; whitespace tolerated. Read at request time (no restart
    needed for rollout), so setting env AFTER app construction still
    takes effect."""
    monkeypatch.setenv("CSRF_TRUSTED_ORIGINS", "http://ops.example.com, http://alt.example.com")
    r = client.post(
        "/api/session/extend",
        headers={"Origin": "http://ops.example.com"},
    )
    assert r.status_code == 401  # gate passed, route rejected on auth
    assert "CSRF" not in r.text


# ============================================================================
# Auth-I1: failed-login persistence
# ============================================================================

def test_failed_login_writes_login_failed_audit_row(client, audit_env):
    """(9) A POST /login with wrong credentials appends exactly one
    ACTION_LOGIN_FAILED row to operator_actions. Operator = the attempted
    username; target_id = the source IP (TestClient's default is
    'testclient')."""
    r = _post_login(client, "attacker", "definitely-wrong")
    # The handler renders the error template — NOT a redirect.
    assert r.status_code in (200, 401)

    rows = list_actions(action_type=ACTION_LOGIN_FAILED)
    assert len(rows) == 1, f"expected 1 LOGIN_FAILED row, got {len(rows)}: {rows!r}"
    row = rows[0]
    assert row["action_type"] == ACTION_LOGIN_FAILED
    assert row["operator"] == "attacker"
    # target_id is the client IP; TestClient's default host is 'testclient'.
    assert row["target_id"] == "testclient"


# ============================================================================
# Auth-I2: lockout-attempt persistence
# ============================================================================

def test_lockout_writes_login_locked_audit_row(client, audit_env):
    """(10) Once the throttle has fired, each subsequent attempt-while-
    locked adds an ACTION_LOGIN_LOCKED row. We first burn through
    LOGIN_MAX_ATTEMPTS wrong attempts (each producing a LOGIN_FAILED row),
    then a further attempt — the trip — which must produce a LOGIN_LOCKED
    row and a 429 response."""
    n = get_settings().LOGIN_MAX_ATTEMPTS

    # Prime the throttle: n wrong attempts, each is a LOGIN_FAILED row.
    for _ in range(n):
        r = _post_login(client, "mallory", "wrong")
        assert r.status_code in (200, 401)  # not yet locked

    # The (n+1)-th attempt hits the lockout branch — 429 + LOGIN_LOCKED row.
    trip = _post_login(client, "mallory", "wrong")
    assert trip.status_code == 429

    failed_rows = list_actions(action_type=ACTION_LOGIN_FAILED)
    locked_rows = list_actions(action_type=ACTION_LOGIN_LOCKED)
    assert len(failed_rows) == n, (
        f"expected {n} LOGIN_FAILED rows for the pre-lock attempts, got {len(failed_rows)}"
    )
    assert len(locked_rows) == 1, (
        f"expected exactly 1 LOGIN_LOCKED row for the trip attempt, got {len(locked_rows)}"
    )
    row = locked_rows[0]
    assert row["operator"] == "mallory"
    assert row["target_id"] == "testclient"


# ============================================================================
# Auth-H1: `?next=` open-redirect defense on /login
# ============================================================================
#
# The SPA's session-expiry modal (app.jsx) redirects to
#   /login?next=<encoded target>
# so that after re-auth, the operator lands back on the page they lost
# (with the ?sdprs_state=... carrier still on the URL). The backend
# _safe_next_path() helper is the trust boundary — it MUST reject any
# absolute URL, protocol-relative URL, or path with a scheme/netloc, and
# fall back to `/`. Anything else is an open-redirect that phishing kits
# can chain off /login. See main.py:_safe_next_path.

def test_login_get_echoes_next_into_hidden_input(client):
    """(11) GET /login?next=/foo renders a hidden <input name=next value=/foo>
    inside the form so the value round-trips through the POST. Value is
    echoed verbatim (Jinja auto-escapes); validation lives in POST."""
    r = client.get("/login?next=/dashboard%3Fsdprs_state%3Dabc")
    assert r.status_code == 200
    assert 'name="next"' in r.text
    # Jinja url-decodes the query param and then escapes the value: '?' → &#39; no, '?' is literal in attr;
    # after urldecode we get '/dashboard?sdprs_state=abc', which Jinja emits verbatim in attribute context.
    assert 'value="/dashboard?sdprs_state=abc"' in r.text


def test_login_get_without_next_omits_hidden_input(client):
    """(12) GET /login (no ?next=) must NOT emit an empty hidden input —
    the `{% if next %}` guard suppresses it. Keeps the form DOM clean
    when the user reached /login directly."""
    r = client.get("/login")
    assert r.status_code == 200
    assert 'name="next"' not in r.text


def test_login_success_honors_safe_next_path(client):
    """(13) A successful POST with next=/some/path redirects to that path
    (303 See Other, Location: /some/path). Baseline for the guard tests
    below — proves the mechanism works end-to-end when the value is safe."""
    r = client.post(
        "/login",
        data={"username": GOOD_USER, "password": GOOD_PASS,
              "next": "/monitor?sdprs_state=abc"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/monitor?sdprs_state=abc"


@pytest.mark.parametrize("evil_next", [
    "http://evil.com/steal",       # absolute URL with scheme+netloc
    "https://evil.com",            # https variant
    "//evil.com/anything",         # protocol-relative — browser treats as absolute
    "/\\evil.com",                 # backslash trick — some parsers normalize
    "javascript:alert(1)",          # javascript: scheme (XSS-via-redirect)
    "ftp://evil.com/",              # any non-empty scheme
    "not-a-path-at-all",            # no leading /
    "",                             # empty string
])
def test_login_success_rejects_evil_next(client, evil_next):
    """(14) Every hostile `next` value falls back to `/`. Uses parametrize
    so a regression on any single attack shape shows up as a distinct
    test failure. Covers the four defensive branches of _safe_next_path:
    non-string/empty, missing leading slash, protocol-relative or
    backslash prefix, and non-empty scheme/netloc from urlparse."""
    r = client.post(
        "/login",
        data={"username": GOOD_USER, "password": GOOD_PASS, "next": evil_next},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/", (
        f"evil next={evil_next!r} was honored — open redirect regression"
    )


def test_login_failure_preserves_next_for_retry(client):
    """(15) A failed login must re-render the form with the same hidden
    `next` value so the retry preserves the intended target. Without
    this, the second attempt would silently drop the caller back to `/`
    after re-auth."""
    r = _post_login(client, "attacker", "definitely-wrong",
                    headers={})
    # Wrong creds → template response, not redirect. The response must
    # carry the (empty) `next` context, which yields no hidden input.
    assert r.status_code in (200, 401)
    assert 'name="next"' not in r.text  # no next supplied → no input

    # Now with a next value:
    r2 = client.post(
        "/login",
        data={"username": "attacker", "password": "wrong",
              "next": "/audit?filter=today"},
        follow_redirects=False,
    )
    assert r2.status_code in (200, 401)
    assert 'name="next"' in r2.text
    assert 'value="/audit?filter=today"' in r2.text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
