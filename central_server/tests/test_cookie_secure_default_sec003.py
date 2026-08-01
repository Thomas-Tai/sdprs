# -*- coding: utf-8 -*-
"""SEC-003 (Medium): the session cookie must be Secure by default in cloud mode.

main.py sets SessionMiddleware `https_only=get_settings().COOKIE_SECURE`.
COOKIE_SECURE defaulted to a flat False, and the Zeabur production config never
sets it — so the live HTTPS deployment shipped a session cookie with NO Secure
attribute (it could ride along any accidental plain-HTTP leg). Fail closed: when
DATABASE_URL is set (the codebase's cloud / PostgreSQL = HTTPS signal) and the
operator did not explicitly set COOKIE_SECURE, default it True. An explicit env
value always wins (including a deliberate `false` for PG-over-plain-HTTP dev),
and the SQLite / HTTP-LAN default stays False.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

import central_server.config as config


REQUIRED = {
    "DASHBOARD_USER": "admin",
    "DASHBOARD_PASS": "testpass123",
    "EDGE_API_KEY": "test-api-key-12345",
    "SECRET_KEY": "test-secret-key-for-testing",
}


@pytest.fixture
def fresh_settings(monkeypatch):
    """Provide the required env and bust the get_settings() lru_cache around the
    test so it can't inherit or leak a cached Settings across cases."""
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_cloud_mode_defaults_cookie_secure_true(fresh_settings, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/db")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    config.get_settings.cache_clear()
    assert config.get_settings().COOKIE_SECURE is True


def test_explicit_false_wins_even_in_cloud_mode(fresh_settings, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/db")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    config.get_settings.cache_clear()
    assert config.get_settings().COOKIE_SECURE is False


def test_lan_sqlite_mode_stays_insecure_default(fresh_settings, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    config.get_settings.cache_clear()
    assert config.get_settings().COOKIE_SECURE is False


def test_explicit_true_honored_in_lan_mode(fresh_settings, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("COOKIE_SECURE", "true")
    config.get_settings.cache_clear()
    assert config.get_settings().COOKIE_SECURE is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
