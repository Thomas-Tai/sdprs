# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Retention Service PostgreSQL Dispatch Tests (Track 3 T3-1)

Verifies run_retention_cleanup branches on central_server.database.get_backend()
for the EVENTS portion of the cleanup:

  * PostgreSQL backend -> the events SELECT/DELETE go through the shared
    SQLAlchemy engine (`database._get_engine()` + `sqlalchemy.text()`), using a
    native `created_at < :cutoff` comparison bound to a naive-UTC datetime
    object (PG's `created_at` column is a native TIMESTAMP, so no SQLite-only
    `datetime()` wrapper is needed or allowed). sqlite3.connect must NEVER be
    called on this path.
  * SQLite backend (default) -> unchanged behaviour. The existing suites
    (test_retention.py, test_retention_subdirs.py) already cover this deeply;
    this file adds one minimal smoke test proving the branch is still the
    default and still works.
"""

import os
import sys
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

import central_server.database as database
import central_server.services.retention_service as retention_service
from central_server.timeutil import utcnow


def _boom_sqlite_connect(*_args, **_kwargs):
    raise AssertionError(
        "SQLite path (sqlite3.connect) was taken under PostgreSQL backend"
    )


# =============================================================================
# Fake SQLAlchemy engine/connection for the PG dispatch test (no real PG)
# =============================================================================

class _FakeResult:
    def __init__(self, rows=None, rowcount=None):
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, select_rows, delete_rowcount, calls):
        self._select_rows = select_rows
        self._delete_rowcount = delete_rowcount
        self._calls = calls
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, clause, params=None):
        sql = str(clause)
        self._calls.append((sql, params))
        upper = sql.upper()
        if "SELECT" in upper and "CREATED_AT" in upper:
            return _FakeResult(rows=self._select_rows)
        if "DELETE" in upper:
            return _FakeResult(rowcount=self._delete_rowcount)
        raise AssertionError(f"Unexpected SQL executed against fake engine: {sql}")

    def commit(self):
        self.committed = True


class _FakeEngine:
    def __init__(self, select_rows, delete_rowcount, calls):
        self._select_rows = select_rows
        self._delete_rowcount = delete_rowcount
        self._calls = calls

    def connect(self):
        return _FakeConnection(self._select_rows, self._delete_rowcount, self._calls)


@pytest.fixture
def temp_mp4_files():
    """Two real temp files standing in for MP4s referenced by canned rows,
    so the code's os.path.exists()/os.remove() actually execute."""
    fd1, path1 = tempfile.mkstemp(suffix=".mp4")
    os.close(fd1)
    fd2, path2 = tempfile.mkstemp(suffix=".mp4")
    os.close(fd2)
    yield path1, path2
    for p in (path1, path2):
        try:
            os.remove(p)
        except OSError:
            pass


# =============================================================================
# PostgreSQL dispatch (events path only — T3-1 scope)
# =============================================================================

def test_pg_dispatch_events_select_and_delete(monkeypatch, temp_mp4_files, tmp_path):
    path1, path2 = temp_mp4_files
    calls = []
    fake_engine = _FakeEngine(
        select_rows=[
            {"id": 1, "mp4_path": path1},
            {"id": 2, "mp4_path": path2},
        ],
        delete_rowcount=2,
        calls=calls,
    )

    monkeypatch.setattr(database, "_backend", "postgresql")
    monkeypatch.setattr(database, "_get_engine", lambda: fake_engine)
    # Loud failure if the SQLite branch is wrongly taken.
    monkeypatch.setattr(retention_service.sqlite3, "connect", _boom_sqlite_connect)

    result = retention_service.run_retention_cleanup(
        db_path=":memory:",
        storage_dir=str(tmp_path),
        retention_days=30,
    )

    # 1) Both a SELECT and a DELETE against events were issued.
    events_calls = [(sql, params) for sql, params in calls if "EVENTS" in sql.upper()]
    select_calls = [c for c in events_calls if "SELECT" in c[0].upper()]
    delete_calls = [c for c in events_calls if "DELETE" in c[0].upper()]
    assert select_calls, f"expected a SELECT against events, got calls={calls}"
    assert delete_calls, f"expected a DELETE against events, got calls={calls}"

    # 2) SQL uses a native `created_at <` comparison, never SQLite's datetime().
    for sql, params in events_calls:
        assert "created_at <" in sql.lower(), f"missing native created_at compare: {sql}"
        assert "datetime(" not in sql.lower(), f"SQLite-only datetime() leaked into PG SQL: {sql}"
        # Bound to the naive-UTC cutoff datetime object, not its isoformat string.
        assert isinstance(params, dict) and "cutoff" in params, f"expected :cutoff param, got {params}"
        assert isinstance(params["cutoff"], datetime), (
            f"cutoff param must be a datetime object, not {type(params['cutoff'])}"
        )
        assert params["cutoff"].tzinfo is None, "cutoff must be naive-UTC"

    # 3) deleted_events reflects the fake DELETE rowcount.
    assert result["deleted_events"] == 2

    # 4) The MP4 files pointed to by the canned SELECT rows were deleted.
    assert os.path.exists(path1) is False
    assert os.path.exists(path2) is False
    assert result["deleted_files"] == 2

    # 5) sqlite3.connect was never called (guaranteed by the boom raising
    # nothing — if it had been called the AssertionError above would have
    # propagated out of run_retention_cleanup's try/except as an error entry
    # or raised outright). Belt-and-suspenders: no "Database connection
    # failed" error was recorded, and no unexpected errors were collected.
    assert result["errors"] == []

    # T3-1 scope: pump_readings pruning + surviving-refs are NOT implemented
    # yet (that is T3-2). The intermediate state must be fail-safe.
    assert result["deleted_pump_readings"] == 0


# =============================================================================
# SQLite path unchanged (still the default backend)
# =============================================================================

def test_sqlite_still_default_and_unaffected(monkeypatch, tmp_path):
    """Minimal smoke test: with the default sqlite backend, retention still
    goes through sqlite3.connect and deletes expired events normally. The
    existing suites (test_retention.py, test_retention_subdirs.py) cover this
    path exhaustively; this just proves the new branch didn't break default
    dispatch."""
    monkeypatch.setattr(database, "_backend", "sqlite")

    db_path = str(tmp_path / "test.db")
    db = sqlite3.connect(db_path)
    db.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT NOT NULL,
            timestamp DATETIME NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING_VIDEO',
            mp4_path TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    old = (utcnow() - timedelta(days=40)).isoformat()
    recent = (utcnow() - timedelta(days=1)).isoformat()
    db.execute(
        "INSERT INTO events (node_id, timestamp, mp4_path, created_at) VALUES (?, ?, ?, ?)",
        ("node_01", old, None, old),
    )
    db.execute(
        "INSERT INTO events (node_id, timestamp, mp4_path, created_at) VALUES (?, ?, ?, ?)",
        ("node_01", recent, None, recent),
    )
    db.commit()
    db.close()

    result = retention_service.run_retention_cleanup(
        db_path=db_path,
        storage_dir=str(tmp_path / "storage"),
        retention_days=30,
        subdirs=["events"],
    )

    assert result["deleted_events"] == 1

    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert remaining == 1
