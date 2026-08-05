import sys, os, tempfile
from pathlib import Path
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
