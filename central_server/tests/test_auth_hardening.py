# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Authentication Hardening Unit Tests
Smart Disaster Prevention Response System

Covers the auth-hardening work in central_server/auth.py:
- Task 1 (legacy): constant-time credential comparison in authenticate_user
- Task 2 (legacy): node_id allowlist enforcement via verify_node_id
- Storage-B (2026-07-16): defense-in-depth char-class gate on node_id
  runs unconditionally so downstream storage_path/events/<node_id>/ cannot
  be walked out of the events tree via `..` or an absolute path.
- Auth-G1 (2026-07-16): verify_api_key logs a SHA-256 digest rather than
  the first 8 chars of the rejected credential.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# conftest.py handles env setup with values that pass validate_settings.

import pytest
from fastapi import HTTPException

from central_server.auth import (
    authenticate_user,
    verify_api_key,
    verify_node_id,
)
from central_server.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Read fresh settings for every test and avoid leaking the cached
    settings singleton (especially a mutated ALLOWED_NODE_IDS) into other
    test modules that run later in the same process."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ===== Legacy Task 1: constant-time credential comparison =====

def test_authenticate_user_accepts_correct_creds():
    settings = get_settings()
    assert authenticate_user(settings.DASHBOARD_USER, settings.DASHBOARD_PASS) is True


def test_authenticate_user_rejects_wrong_password():
    settings = get_settings()
    assert authenticate_user(settings.DASHBOARD_USER, "nope") is False


def test_authenticate_user_rejects_wrong_username():
    settings = get_settings()
    assert authenticate_user("mallory", settings.DASHBOARD_PASS) is False


# ===== Storage-B: verify_node_id char-class + length gate =====
# The gate runs UNCONDITIONALLY, regardless of ALLOWED_NODE_IDS state.

def test_verify_node_id_accepts_valid_id_no_allowlist(monkeypatch):
    """(1) Valid char class passes when allowlist is empty (allow-all)."""
    monkeypatch.delenv("ALLOWED_NODE_IDS", raising=False)
    get_settings.cache_clear()
    assert verify_node_id("glass_node_01") is None


def test_verify_node_id_rejects_dot_dot_traversal(monkeypatch):
    """(2) `..` path-traversal segment is rejected even with empty allowlist."""
    monkeypatch.delenv("ALLOWED_NODE_IDS", raising=False)
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        verify_node_id("../etc/passwd")
    assert exc.value.status_code == 400
    assert "Invalid node_id" in exc.value.detail


def test_verify_node_id_rejects_absolute_path(monkeypatch):
    """(3) Absolute path is rejected even with empty allowlist. If it slipped
    through, `storage_events_root / "/absolute/path"` collapses to
    `Path("/absolute/path")` and escapes the events tree entirely."""
    monkeypatch.delenv("ALLOWED_NODE_IDS", raising=False)
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        verify_node_id("/absolute/path")
    assert exc.value.status_code == 400


def test_verify_node_id_rejects_whitespace(monkeypatch):
    """(4) Whitespace inside the id is not in the [A-Za-z0-9._-] class."""
    monkeypatch.delenv("ALLOWED_NODE_IDS", raising=False)
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        verify_node_id("has space")
    assert exc.value.status_code == 400


def test_verify_node_id_rejects_overlong(monkeypatch):
    """(5) 65-char id busts the 64-char length cap."""
    monkeypatch.delenv("ALLOWED_NODE_IDS", raising=False)
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        verify_node_id("a" * 65)
    assert exc.value.status_code == 400


def test_verify_node_id_rejects_empty(monkeypatch):
    """(6) Empty string is rejected by the {1,64} length quantifier."""
    monkeypatch.delenv("ALLOWED_NODE_IDS", raising=False)
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        verify_node_id("")
    assert exc.value.status_code == 400


# ===== Legacy Task 2: node_id allowlist still functions =====

def test_verify_node_id_allowlist_rejects_valid_but_unlisted(monkeypatch):
    """(7) Char-class-valid id is still rejected 403 when not on the allowlist."""
    monkeypatch.setenv("ALLOWED_NODE_IDS", "glass_node_02")
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as exc:
        verify_node_id("glass_node_01")
    assert exc.value.status_code == 403


def test_verify_node_id_allowlist_accepts_listed(monkeypatch):
    """(8) Listed id passes when the allowlist is populated."""
    monkeypatch.setenv("ALLOWED_NODE_IDS", "glass_node_01,glass_node_02")
    get_settings.cache_clear()
    assert verify_node_id("glass_node_01") is None


# ===== Auth-G1: api_key logging no longer leaks the first 8 chars =====

def test_verify_api_key_log_omits_raw_prefix_uses_digest():
    """(9) A wrong api_key must not have its first 8 chars written to the log.
    The digest form must be present instead so operators can still correlate
    repeated attempts from the same offender."""
    wrong_key = "totally-wrong-key-please-do-not-log-my-prefix-here-plz"
    raw_prefix = wrong_key[:8]

    with patch("central_server.auth.logger") as mock_logger:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(verify_api_key(api_key=wrong_key))
        assert exc.value.status_code == 401

        # There should be at least one warning call for the failed attempt.
        assert mock_logger.warning.called, "expected logger.warning to be called"

        # Concatenate every positional arg from every warning call.
        emitted = " ".join(
            str(arg)
            for call_args in mock_logger.warning.call_args_list
            for arg in call_args.args
        )

        # The raw first-8-char prefix must NOT appear.
        assert raw_prefix not in emitted, (
            f"leaked raw key prefix {raw_prefix!r} in log message: {emitted!r}"
        )

        # The SHA-256 digest form must appear (16 hex chars).
        import hashlib
        expected_digest = hashlib.sha256(wrong_key.encode("utf-8")).hexdigest()[:16]
        assert expected_digest in emitted, (
            f"expected sha256 digest {expected_digest!r} in log message: {emitted!r}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
