# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Authentication Hardening Unit Tests
Smart Disaster Prevention Response System

Covers the auth-hardening work in central_server/auth.py:
- Task 1: constant-time credential comparison in authenticate_user
- Task 2: node_id allowlist enforcement via verify_node_id
"""

import os
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Set required environment variables BEFORE importing the app modules
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

import pytest
from fastapi import HTTPException

from central_server.auth import authenticate_user, verify_node_id
from central_server.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Read fresh settings for every test and avoid leaking the cached
    settings singleton (especially a mutated ALLOWED_NODE_IDS) into other
    test modules that run later in the same process."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ===== Task 1: constant-time credential comparison =====

def test_authenticate_user_accepts_correct_creds(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASS", "testpass123")
    get_settings.cache_clear()
    assert authenticate_user("admin", "testpass123") is True


def test_authenticate_user_rejects_wrong_password(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASS", "testpass123")
    get_settings.cache_clear()
    assert authenticate_user("admin", "nope") is False


def test_authenticate_user_rejects_wrong_username(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASS", "testpass123")
    get_settings.cache_clear()
    assert authenticate_user("mallory", "testpass123") is False


# ===== Task 2: node_id allowlist =====

def test_verify_node_id_allows_all_when_empty(monkeypatch):
    # Empty/unset allowlist -> allow all (backward compatible single-node mode)
    monkeypatch.delenv("ALLOWED_NODE_IDS", raising=False)
    get_settings.cache_clear()
    assert verify_node_id("anything") is None


def test_verify_node_id_allows_listed(monkeypatch):
    monkeypatch.setenv("ALLOWED_NODE_IDS", "glass_node_01, pump_node_01")
    get_settings.cache_clear()
    # Whitespace around entries is stripped; a listed id passes silently.
    assert verify_node_id("glass_node_01") is None
    assert verify_node_id("pump_node_01") is None


def test_verify_node_id_rejects_unlisted(monkeypatch):
    monkeypatch.setenv("ALLOWED_NODE_IDS", "glass_node_01, pump_node_01")
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        verify_node_id("rogue")
    assert exc.value.status_code == 403


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
