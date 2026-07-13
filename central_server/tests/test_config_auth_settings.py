# -*- coding: utf-8 -*-
"""
Tests for the T2 auth-hardening settings added to the central server config.

Verifies the FROZEN CONTRACT of four settings across defaults and env parsing:
- COOKIE_SECURE: bool = False
- ALLOWED_NODE_IDS: str = ""
- LOGIN_MAX_ATTEMPTS: int = 5
- LOGIN_LOCKOUT_SECONDS: int = 300
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

from central_server.config import get_settings


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
