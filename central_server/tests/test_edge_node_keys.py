import sys, os, tempfile, types
from pathlib import Path
import hashlib
import pytest
from fastapi import HTTPException
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _fresh_db(monkeypatch):
    """Point the DB layer at a fresh temp SQLite file and init the schema."""
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    from central_server import database
    database.init_db(db_path)
    return database, db_path


def test_nodes_table_has_api_key_hash_and_index(monkeypatch):
    database, db_path = _fresh_db(monkeypatch)
    import sqlite3
    con = sqlite3.connect(db_path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(nodes)").fetchall()}
    assert "api_key_hash" in cols
    idx = {r[1] for r in con.execute("PRAGMA index_list(nodes)").fetchall()}
    assert "idx_nodes_api_key_hash" in idx
    con.close()


def test_get_edge_node_by_key(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    # Seed a node row with a known key hash via a direct upsert.
    database.upsert_node("glass_node_01", "glass", "OFFLINE", None)
    raw = "sk-edge-KNOWNTESTKEY"
    h = hashlib.sha256(raw.encode()).hexdigest()
    from central_server.database import get_db_cursor
    with get_db_cursor() as cur:
        cur.execute("UPDATE nodes SET api_key_hash = ? WHERE node_id = ?", (h, "glass_node_01"))
    assert database.get_edge_node_by_key(raw)["node_id"] == "glass_node_01"
    assert database.get_edge_node_by_key("sk-edge-WRONG") is None
    assert database.get_edge_node_by_key("") is None


def test_provision_edge_node_key(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    out = database.provision_edge_node_key("glass_node_07")
    assert out["api_key"].startswith("sk-edge-")
    # The freshly provisioned key resolves back to the node.
    assert database.get_edge_node_by_key(out["api_key"])["node_id"] == "glass_node_07"
    # Re-provisioning rotates: the old key stops resolving.
    out2 = database.provision_edge_node_key("glass_node_07")
    assert out2["api_key"] != out["api_key"]
    assert database.get_edge_node_by_key(out["api_key"]) is None
    assert database.get_edge_node_by_key(out2["api_key"])["node_id"] == "glass_node_07"


def test_clear_edge_node_key(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    out = database.provision_edge_node_key("glass_node_09")
    assert database.get_edge_node_by_key(out["api_key"])["node_id"] == "glass_node_09"
    assert database.clear_edge_node_key("glass_node_09") is True
    # Key no longer resolves, but the node row still exists.
    assert database.get_edge_node_by_key(out["api_key"]) is None
    assert database.get_node("glass_node_09") is not None
    # Clearing an unknown node reports False.
    assert database.clear_edge_node_key("nope_404") is False


def _req():
    return types.SimpleNamespace(state=types.SimpleNamespace())


@pytest.mark.asyncio
async def test_verify_api_key_per_node_and_fallback(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    from central_server import auth
    from central_server.config import get_settings
    monkeypatch.setattr(get_settings(), "EDGE_API_KEY", "shared-" + "x" * 32, raising=False)

    out = database.provision_edge_node_key("glass_node_11")
    # Per-node key: authenticates AND stamps the bound node identity.
    r1 = _req()
    assert await auth.verify_api_key(r1, out["api_key"]) == out["api_key"]
    assert r1.state.edge_auth_node == "glass_node_11"
    # Shared key: authenticates, identity is None (unbound / grace period).
    r2 = _req()
    assert await auth.verify_api_key(r2, "shared-" + "x" * 32) == "shared-" + "x" * 32
    assert r2.state.edge_auth_node is None
    # Invalid key: 401.
    with pytest.raises(HTTPException) as ei:
        await auth.verify_api_key(_req(), "sk-edge-bogus")
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_api_key_or_session_accepts_per_node(monkeypatch):
    database, _ = _fresh_db(monkeypatch)
    from central_server import auth
    out = database.provision_edge_node_key("glass_node_13")
    r = types.SimpleNamespace(state=types.SimpleNamespace(),
                              session={}, headers={})
    assert await auth.verify_api_key_or_session(r, out["api_key"]) == out["api_key"]
    assert r.state.edge_auth_node == "glass_node_13"
