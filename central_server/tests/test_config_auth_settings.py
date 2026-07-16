# -*- coding: utf-8 -*-
"""
Tests for T2 auth-hardening settings + 2026-07-16 SECURITY validation of
credential quality (KNOWN_INSECURE_VALUES / length / entropy rejection).

Verifies the FROZEN CONTRACT of:
- COOKIE_SECURE: bool = False
- ALLOWED_NODE_IDS: str = ""
- LOGIN_MAX_ATTEMPTS: int = 5
- LOGIN_LOCKOUT_SECONDS: int = 300

And the FROZEN CONTRACT of validate_settings failing closed on:
- Known-insecure placeholder values (setup_server.sh legacy defaults)
- Any value containing "changeme"
- SECRET_KEY / EDGE_API_KEY shorter than 32 chars
- SECRET_KEY / EDGE_API_KEY with fewer than 8 unique chars
- DASHBOARD_PASS shorter than 8 chars
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "PytestSuite2026!")
os.environ.setdefault(
    "EDGE_API_KEY",
    "a1b2c3d4e5f67890abcdef0123456789abcdef0123456789abcdef012345678f",
)
os.environ.setdefault(
    "SECRET_KEY",
    "9f8e7d6c5b4a3928a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4",
)

from central_server.config import get_settings, validate_settings


_GOOD_PASS = "PytestSuite2026!"
_GOOD_EDGE = "a1b2c3d4e5f67890abcdef0123456789abcdef0123456789abcdef012345678f"
_GOOD_SECRET = "9f8e7d6c5b4a3928a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4"


# ===== Existing T2 auth-hardening default tests =====

def test_auth_defaults():
    get_settings.cache_clear()
    s = get_settings()
    assert s.COOKIE_SECURE is False
    assert s.ALLOWED_NODE_IDS == ""
    assert s.LOGIN_MAX_ATTEMPTS == 5
    assert s.LOGIN_LOCKOUT_SECONDS == 300


def test_auth_settings_from_env(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("ALLOWED_NODE_IDS", "a,b")
    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "9")
    monkeypatch.setenv("LOGIN_LOCKOUT_SECONDS", "60")

    get_settings.cache_clear()
    s = get_settings()
    assert s.COOKIE_SECURE is True
    assert s.ALLOWED_NODE_IDS == "a,b"
    assert s.LOGIN_MAX_ATTEMPTS == 9
    assert s.LOGIN_LOCKOUT_SECONDS == 60

    # Reset the cached singleton so other test modules see the default-env
    # instance (monkeypatch auto-reverts the env vars after this test).
    get_settings.cache_clear()


# ===== 2026-07-16 SECURITY: validate_settings fail-closed tests =====

def _make_settings(monkeypatch, **overrides):
    """
    Build a Settings via env-var monkeypatch. Pydantic BaseSettings reads
    from process env at construction, so we override selectively and
    fall back to the strong defaults from conftest.py for the rest.
    """
    monkeypatch.setenv("DASHBOARD_USER", overrides.get("DASHBOARD_USER", "admin"))
    monkeypatch.setenv("DASHBOARD_PASS", overrides.get("DASHBOARD_PASS", _GOOD_PASS))
    monkeypatch.setenv("EDGE_API_KEY", overrides.get("EDGE_API_KEY", _GOOD_EDGE))
    monkeypatch.setenv("SECRET_KEY", overrides.get("SECRET_KEY", _GOOD_SECRET))
    get_settings.cache_clear()
    return get_settings()


def test_validate_passes_with_random_credentials(monkeypatch):
    s = _make_settings(monkeypatch)
    assert validate_settings(s) is True
    get_settings.cache_clear()


def test_validate_rejects_setup_default_dashboard_pass(monkeypatch):
    s = _make_settings(monkeypatch, DASHBOARD_PASS="changeme-strong-password")
    with pytest.raises(ValueError, match="known-insecure|changeme"):
        validate_settings(s)
    get_settings.cache_clear()


def test_validate_rejects_setup_default_secret_key(monkeypatch):
    s = _make_settings(monkeypatch, SECRET_KEY="changeme-session-secret")
    with pytest.raises(ValueError, match="known-insecure|changeme"):
        validate_settings(s)
    get_settings.cache_clear()


def test_validate_rejects_setup_default_edge_key(monkeypatch):
    s = _make_settings(monkeypatch, EDGE_API_KEY="changeme-random-secret-key")
    with pytest.raises(ValueError, match="known-insecure|changeme"):
        validate_settings(s)
    get_settings.cache_clear()


def test_validate_rejects_changeme_substring(monkeypatch):
    # Any value containing "changeme" — even a longer custom string — fails.
    s = _make_settings(
        monkeypatch,
        SECRET_KEY="my-changeme-flavored-secret-1234567890abcdef",
    )
    with pytest.raises(ValueError, match="changeme"):
        validate_settings(s)
    get_settings.cache_clear()


def test_validate_rejects_short_secret_key(monkeypatch):
    # 6 chars — fails SECRET_MIN_LENGTH (32).
    s = _make_settings(monkeypatch, SECRET_KEY="abc123")
    with pytest.raises(ValueError, match="SECRET_KEY too short"):
        validate_settings(s)
    get_settings.cache_clear()


def test_validate_rejects_short_edge_api_key(monkeypatch):
    s = _make_settings(monkeypatch, EDGE_API_KEY="short_edge_9chr")
    with pytest.raises(ValueError, match="EDGE_API_KEY too short"):
        validate_settings(s)
    get_settings.cache_clear()


def test_validate_rejects_low_entropy_secret_key(monkeypatch):
    # 32 chars but only 1 unique — fails SECRET_MIN_UNIQUE_CHARS (8).
    s = _make_settings(monkeypatch, SECRET_KEY="a" * 32)
    with pytest.raises(ValueError, match="insufficient entropy"):
        validate_settings(s)
    get_settings.cache_clear()


def test_validate_rejects_short_dashboard_pass(monkeypatch):
    # 5 chars — fails PASSWORD_MIN_LENGTH (8).
    s = _make_settings(monkeypatch, DASHBOARD_PASS="short")
    with pytest.raises(ValueError, match="DASHBOARD_PASS too short"):
        validate_settings(s)
    get_settings.cache_clear()


def test_validate_rejects_empty_field(monkeypatch):
    # Pydantic still requires the field (required=True at Settings), so
    # setting to empty string is the closest "not configured" simulation.
    # Build settings first with strong values, then blank one to test.
    monkeypatch.setenv("DASHBOARD_USER", "")
    monkeypatch.setenv("DASHBOARD_PASS", _GOOD_PASS)
    monkeypatch.setenv("EDGE_API_KEY", _GOOD_EDGE)
    monkeypatch.setenv("SECRET_KEY", _GOOD_SECRET)
    get_settings.cache_clear()
    s = get_settings()
    with pytest.raises(ValueError, match="DASHBOARD_USER is not configured"):
        validate_settings(s)
    get_settings.cache_clear()
