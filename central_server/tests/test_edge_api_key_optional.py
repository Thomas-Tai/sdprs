import os
os.environ.setdefault("EDGE_API_KEY", "test-api-key-1234567890abcdefghij")  # for import-time Settings, if any
os.environ.setdefault("SECRET_KEY", "test-secret-key-1234567890abcdefghij")
import sys
import tempfile
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server.config import (
    validate_settings, SECRET_MIN_LENGTH, SECRET_MIN_UNIQUE_CHARS,
)

# Two distinct strong values (>=32 chars, >=8 unique, not placeholders).
_STRONG_SECRET = "0123456789abcdef0123456789abcdef"      # 32 chars, 16 unique
_STRONG_EDGE   = "fedcba9876543210fedcba9876543210"      # 32 chars, 16 unique


def _valid_settings(**overrides):
    base = dict(
        DASHBOARD_USER="operator", DASHBOARD_PASS="s3cure-pass-9",
        SECRET_KEY=_STRONG_SECRET, EDGE_API_KEY=_STRONG_EDGE,
        MQTT_PORT=1883, RETENTION_DAYS=30, SERVER_PORT=8000,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _fresh_db(monkeypatch):
    """Point the DB layer at a fresh temp SQLite file and init the schema.

    Mirrors test_edge_node_keys.py's helper of the same name so this file
    stays self-contained (no cross-file import dependency).
    """
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    from central_server import database
    database.init_db(db_path)
    return database, db_path


def test_edge_api_key_unset_passes_validation():
    # Empty shared key => per-node-only mode => validation must PASS.
    assert validate_settings(_valid_settings(EDGE_API_KEY="")) is True


def test_edge_api_key_set_but_weak_still_fails():
    # A SET shared key is still strength-checked (fails closed).
    with pytest.raises(ValueError):
        validate_settings(_valid_settings(EDGE_API_KEY="short"))


def test_edge_api_key_set_and_strong_passes():
    assert validate_settings(_valid_settings(EDGE_API_KEY=_STRONG_EDGE)) is True


def test_secret_key_still_required_even_when_edge_unset():
    # Removing EDGE_API_KEY from required_fields must NOT relax SECRET_KEY.
    with pytest.raises(ValueError):
        validate_settings(_valid_settings(EDGE_API_KEY="", SECRET_KEY=""))


def test_verify_api_key_pernode_only_when_shared_unset(monkeypatch):
    # With EDGE_API_KEY empty, ONLY a provisioned per-node key authenticates.
    database, _ = _fresh_db(monkeypatch)          # copy of test_edge_node_keys.py's helper
    from central_server import auth as auth_mod
    from central_server.config import get_settings
    monkeypatch.setattr(get_settings(), "EDGE_API_KEY", "", raising=False)
    key = database.provision_edge_node_key("glass_optional_1")["api_key"]
    import asyncio
    req = types.SimpleNamespace(state=types.SimpleNamespace())

    async def go():
        assert await auth_mod.verify_api_key(request=req, api_key=key) == key
        assert req.state.edge_auth_node == "glass_optional_1"
        with pytest.raises(Exception):     # HTTPException 401 — shared path disabled
            await auth_mod.verify_api_key(request=types.SimpleNamespace(state=types.SimpleNamespace()), api_key="not-a-real-key")
    asyncio.run(go())


def test_verify_api_key_or_session_pernode_only_when_shared_unset(monkeypatch):
    # Direct coverage for verify_api_key_or_session — the function this task
    # actually changed (the L285 guard). With EDGE_API_KEY empty, ONLY a
    # provisioned per-node key authenticates; a wrong key with no session
    # must 401 rather than fall through to the (now-disabled) shared path.
    database, _ = _fresh_db(monkeypatch)
    from central_server import auth as auth_mod
    from central_server.config import get_settings
    monkeypatch.setattr(get_settings(), "EDGE_API_KEY", "", raising=False)
    key = database.provision_edge_node_key("glass_optional_2")["api_key"]
    import asyncio
    req = types.SimpleNamespace(state=types.SimpleNamespace(), session={})

    async def go():
        assert await auth_mod.verify_api_key_or_session(request=req, api_key=key) == key
        assert req.state.edge_auth_node == "glass_optional_2"
        req2 = types.SimpleNamespace(state=types.SimpleNamespace(), session={})
        with pytest.raises(HTTPException) as ei:
            await auth_mod.verify_api_key_or_session(request=req2, api_key="not-a-real-key")
        assert ei.value.status_code == 401
    asyncio.run(go())


def test_verify_api_key_or_session_shared_key_still_works_when_set(monkeypatch):
    # Regression for the TRUE branch of the guard this task changed: a SET
    # shared key must still authenticate on verify_api_key_or_session, and
    # a shared-key match stays unbound (edge_auth_node is None).
    database, _ = _fresh_db(monkeypatch)   # harmless; not needed for this path
    from central_server import auth as auth_mod
    from central_server.config import get_settings
    monkeypatch.setattr(get_settings(), "EDGE_API_KEY", _STRONG_EDGE, raising=False)
    import asyncio
    req = types.SimpleNamespace(state=types.SimpleNamespace(), session={})

    async def go():
        assert await auth_mod.verify_api_key_or_session(request=req, api_key=_STRONG_EDGE) == _STRONG_EDGE
        assert req.state.edge_auth_node is None
    asyncio.run(go())
