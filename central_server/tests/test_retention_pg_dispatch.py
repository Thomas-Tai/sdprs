# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Retention Service PostgreSQL Dispatch Tests (Track 3 T3-1/T3-2)

Verifies run_retention_cleanup branches on central_server.database.get_backend()
for the full PostgreSQL cleanup:

  * PostgreSQL backend -> the events SELECT/DELETE, the pump_readings DELETE,
    and the surviving-refs SELECT all go through the shared SQLAlchemy engine
    (`database._get_engine()` + `sqlalchemy.text()`), using native
    `created_at < :cutoff` / `timestamp < :cutoff` comparisons bound to a
    naive-UTC datetime object (PG's `created_at`/`timestamp` columns are
    native TIMESTAMP, so no SQLite-only `datetime()` wrapper is needed or
    allowed). sqlite3.connect must NEVER be called on this path. A missing
    pump_readings table (sqlalchemy.exc.ProgrammingError under real PG) must
    not crash the whole cleanup, mirroring the SQLite branch's
    sqlite3.OperationalError tolerance.
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
import sqlalchemy

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
    """Routes execute() calls to canned results/errors by SQL content, so a
    single fake engine can serve all four statements the PG branch issues:
    the events expired SELECT, the events DELETE, the pump_readings DELETE,
    and the surviving-refs SELECT. Each `with engine.connect() as conn:`
    block gets its own _FakeConnection instance (mirroring the production
    code's separate-transaction structure), but they all share the same
    `calls` list and the same owning `_FakeEngine`'s canned data."""

    def __init__(self, engine, calls):
        self._engine = engine
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
            # events expired SELECT: "SELECT id, mp4_path FROM events WHERE created_at < :cutoff"
            return _FakeResult(rows=self._engine.select_rows)
        if "DELETE" in upper and "EVENTS" in upper:
            # events DELETE: "DELETE FROM events WHERE created_at < :cutoff"
            return _FakeResult(rowcount=self._engine.delete_rowcount)
        if "DELETE" in upper and "PUMP_READINGS" in upper:
            # pump_readings DELETE: "DELETE FROM pump_readings WHERE timestamp < :cutoff"
            if self._engine.pump_error is not None:
                raise self._engine.pump_error
            return _FakeResult(rowcount=self._engine.pump_rowcount)
        if "SELECT" in upper and "MP4_PATH" in upper and "IS NOT NULL" in upper:
            # surviving-refs SELECT: "SELECT mp4_path FROM events WHERE mp4_path IS NOT NULL"
            return _FakeResult(rows=self._engine.surviving_rows)
        raise AssertionError(f"Unexpected SQL executed against fake engine: {sql}")

    def commit(self):
        self.committed = True


class _FakeEngine:
    def __init__(
        self,
        select_rows,
        delete_rowcount,
        calls,
        pump_rowcount=0,
        pump_error=None,
        surviving_rows=None,
    ):
        self.select_rows = select_rows
        self.delete_rowcount = delete_rowcount
        self.pump_rowcount = pump_rowcount
        self.pump_error = pump_error
        self.surviving_rows = surviving_rows if surviving_rows is not None else []
        self._calls = calls

    def connect(self):
        return _FakeConnection(self, self._calls)


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
        pump_rowcount=7,
        surviving_rows=[],
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

    # 1) Both a SELECT and a DELETE against events were issued. Filter on
    # CREATED_AT too, since the surviving-refs SELECT (T3-2) also queries
    # `FROM events` but has no created_at compare.
    events_calls = [
        (sql, params) for sql, params in calls
        if "EVENTS" in sql.upper() and "CREATED_AT" in sql.upper()
    ]
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

    # T3-2: pump_readings is now pruned via a separate DELETE against the
    # fake engine, reflecting the canned pump rowcount.
    assert result["deleted_pump_readings"] == 7


# =============================================================================
# PostgreSQL dispatch — pump_readings prune + surviving-refs orphan sweep
# (T3-2 scope)
# =============================================================================

def test_pg_dispatch_pump_prune_and_orphan_sweep(monkeypatch, temp_mp4_files, tmp_path):
    """T3-2: the pump_readings DELETE and the surviving-refs SELECT now run
    under PostgreSQL (each its own connection/transaction), which lets the
    backend-agnostic on-disk orphan sweep operate identically to the SQLite
    branch: a file referenced by a surviving event row is kept, an
    unreferenced old file is swept."""
    path1, path2 = temp_mp4_files  # stand in for the events-expired SELECT rows

    # Real on-disk storage dir with two old MP4s under events/<node>/.
    events_dir = tmp_path / "events" / "node_01"
    events_dir.mkdir(parents=True)
    keep_path = events_dir / "keep.mp4"
    orphan_path = events_dir / "orphan.mp4"
    keep_path.write_bytes(b"keep-me")
    orphan_path.write_bytes(b"sweep-me")

    # Both older than the retention cutoff (30 days) so the orphan sweep
    # actually considers them.
    old_ts = (utcnow() - timedelta(days=40)).timestamp()
    os.utime(str(keep_path), (old_ts, old_ts))
    os.utime(str(orphan_path), (old_ts, old_ts))

    keep_norm = os.path.normpath(os.path.abspath(str(keep_path)))

    calls = []
    fake_engine = _FakeEngine(
        select_rows=[
            {"id": 1, "mp4_path": path1},
            {"id": 2, "mp4_path": path2},
        ],
        delete_rowcount=2,
        pump_rowcount=5,
        # Only `keep_path`'s normalized path is a surviving reference ->
        # `orphan_path` must be swept, `keep_path` must survive.
        surviving_rows=[{"mp4_path": keep_norm}],
        calls=calls,
    )

    monkeypatch.setattr(database, "_backend", "postgresql")
    monkeypatch.setattr(database, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(retention_service.sqlite3, "connect", _boom_sqlite_connect)

    result = retention_service.run_retention_cleanup(
        db_path=":memory:",
        storage_dir=str(tmp_path),
        retention_days=30,
        subdirs=["events"],
    )

    # The pump DELETE used a native `timestamp <` compare bound to a
    # naive-UTC :cutoff datetime, never SQLite's datetime().
    pump_calls = [(sql, params) for sql, params in calls if "PUMP_READINGS" in sql.upper()]
    assert pump_calls, f"expected a DELETE against pump_readings, got calls={calls}"
    for sql, params in pump_calls:
        assert "timestamp <" in sql.lower(), f"missing native timestamp compare: {sql}"
        assert "datetime(" not in sql.lower(), f"SQLite-only datetime() leaked into PG SQL: {sql}"
        assert isinstance(params, dict) and "cutoff" in params, f"expected :cutoff param, got {params}"
        assert isinstance(params["cutoff"], datetime), (
            f"cutoff param must be a datetime object, not {type(params['cutoff'])}"
        )
        assert params["cutoff"].tzinfo is None, "cutoff must be naive-UTC"

    assert result["deleted_pump_readings"] == 5

    # Surviving-refs SELECT issued too.
    surviving_calls = [
        (sql, params) for sql, params in calls
        if "SELECT" in sql.upper() and "MP4_PATH" in sql.upper() and "IS NOT NULL" in sql.upper()
    ]
    assert surviving_calls, f"expected the surviving-refs SELECT, got calls={calls}"

    # Orphan sweep ran: unreferenced file removed, referenced file kept.
    assert not orphan_path.exists(), "orphaned MP4 should have been swept"
    assert keep_path.exists(), "referenced MP4 must survive the sweep"
    assert result["deleted_orphans"] == 1


def test_pg_dispatch_pump_missing_table_tolerated(monkeypatch, temp_mp4_files, tmp_path):
    """A missing pump_readings table under real PostgreSQL raises
    sqlalchemy.exc.ProgrammingError. run_retention_cleanup must tolerate
    this (deleted_pump_readings=0, no crash) exactly like the SQLite branch
    tolerates sqlite3.OperationalError for the same missing-table case, and
    the already-committed events work must be unaffected."""
    path1, path2 = temp_mp4_files
    calls = []
    pump_error = sqlalchemy.exc.ProgrammingError(
        "DELETE FROM pump_readings WHERE timestamp < :cutoff",
        {},
        Exception('relation "pump_readings" does not exist'),
    )
    fake_engine = _FakeEngine(
        select_rows=[
            {"id": 1, "mp4_path": path1},
            {"id": 2, "mp4_path": path2},
        ],
        delete_rowcount=2,
        pump_error=pump_error,
        surviving_rows=[],
        calls=calls,
    )

    monkeypatch.setattr(database, "_backend", "postgresql")
    monkeypatch.setattr(database, "_get_engine", lambda: fake_engine)
    monkeypatch.setattr(retention_service.sqlite3, "connect", _boom_sqlite_connect)

    result = retention_service.run_retention_cleanup(
        db_path=":memory:",
        storage_dir=str(tmp_path),
        retention_days=30,
    )

    assert result["deleted_pump_readings"] == 0
    # events work (already committed in its own transaction) is unaffected
    # by the pump table being missing.
    assert result["deleted_events"] == 2
    assert result["errors"] == []


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
