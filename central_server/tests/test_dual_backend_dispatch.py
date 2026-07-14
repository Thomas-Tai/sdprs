# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Dual-Backend Dispatch Tests (theme T1)

Verifies that the sync data-access call sites made PostgreSQL-safe pick the
correct branch based on the active backend:

  * PostgreSQL backend  -> the PG branch runs and the SQLite path (get_db) is
    NEVER touched. We DON'T need a real Postgres server: the throwaway-engine
    PG helpers are monkeypatched with fakes, so we only assert *dispatch*.
  * SQLite backend      -> behaviour is unchanged, whether the caller passes an
    explicit `db` or lets it default to None (fetched via get_db()).
"""

import os
import sys
import tempfile
import unittest  # noqa: F401  (kept for parity with sibling suites)
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

# Required env vars before importing app modules (mirrors test_alerts_api.py)
os.environ["DASHBOARD_USER"] = "admin"
os.environ["DASHBOARD_PASS"] = "testpass123"
os.environ["EDGE_API_KEY"] = "test-api-key-12345"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing"

import central_server.database as database
import central_server.services.event_service as event_service


def _boom(*_args, **_kwargs):
    """Stand-in for get_db() that must never be called on the PG path."""
    raise AssertionError("SQLite path (get_db) was taken under PostgreSQL backend")


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sqlite_db():
    """Initialise a temporary SQLite DB via the production init_db() path.

    Uses init_db (not a hand-rolled connect) so `_db_lock` is set up and
    get_db_cursor() works — same rationale as test_alerts_api.py's test_db.
    """
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = database.init_db(db_path)
    try:
        yield conn
    finally:
        database.close_db()
        try:
            os.unlink(db_path)
        except OSError:
            pass


@pytest.fixture
def force_pg(monkeypatch):
    """Force the postgresql backend for the duration of a test.

    Patched via the module attribute so get_backend() (which reads _backend
    live) reports "postgresql" in both database.py and event_service.py.
    Also makes get_db explode so any accidental SQLite fall-through is loud.
    """
    monkeypatch.setattr(database, "_backend", "postgresql")
    monkeypatch.setattr(database, "get_db", _boom)
    monkeypatch.setattr(event_service, "get_db", _boom)
    return monkeypatch


# =============================================================================
# get_backend accessor
# =============================================================================

def test_get_backend_reads_live_state(monkeypatch):
    monkeypatch.setattr(database, "_backend", "sqlite")
    assert database.get_backend() == "sqlite"
    monkeypatch.setattr(database, "_backend", "postgresql")
    assert database.get_backend() == "postgresql"
    # event_service sees the same live value through the shared accessor
    assert event_service.get_backend() == "postgresql"


# =============================================================================
# PostgreSQL dispatch (no real PG server; helpers are faked)
# =============================================================================

def test_get_pending_alert_ids_pg_dispatch(force_pg):
    calls = {}

    def fake_fetch_many(query, params):
        calls["query"] = query
        return [{"id": 7}, {"id": 9}]

    force_pg.setattr(database, "_pg_fetch_many_sync", fake_fetch_many)

    result = database.get_pending_alert_ids()

    assert result == [7, 9]
    assert "events" in calls["query"] and "PENDING" in calls["query"]


def test_get_event_created_ats_pg_dispatch(force_pg):
    calls = {}

    def fake_fetch_many(query, params):
        calls["params"] = params
        return [{"created_at": "2026-07-13 10:00:00"}, {"created_at": "2026-07-13 10:15:00"}]

    force_pg.setattr(database, "_pg_fetch_many_sync", fake_fetch_many)

    result = database.get_event_created_ats("2026-07-13T00:00:00")

    assert result == ["2026-07-13 10:00:00", "2026-07-13 10:15:00"]
    assert calls["params"] == {"since": "2026-07-13T00:00:00"}


def test_get_handover_note_row_pg_dispatch(force_pg):
    def fake_fetch_one(query, params):
        return {"note": "hi", "author": "admin", "updated_at": "2026-07-13 10:00:00"}

    force_pg.setattr(database, "_pg_fetch_one_sync", fake_fetch_one)

    result = database.get_handover_note_row()

    assert result == {"note": "hi", "author": "admin", "updated_at": "2026-07-13 10:00:00"}


def test_get_event_counts_pg_dispatch(force_pg):
    def fake_rows():
        return [
            {"status": "PENDING", "count": 3},
            {"status": "RESOLVED", "count": 2},
            {"status": "ACKNOWLEDGED", "count": 1},
        ]

    force_pg.setattr(event_service, "_event_counts_rows_pg", fake_rows)

    counts = event_service.get_event_counts()  # db=None on purpose

    assert counts == {
        "pending_video": 0,
        "pending": 3,
        "acknowledged": 1,
        "resolved": 2,
        "total": 6,
    }


def test_list_events_pg_dispatch(force_pg):
    sentinel = {"items": [], "total": 0, "page": 1, "page_size": 20, "total_pages": 0}
    captured = {}

    def fake_list(status_filter, node_filter, page, page_size):
        captured["args"] = (status_filter, node_filter, page, page_size)
        return sentinel

    force_pg.setattr(event_service, "_list_events_pg", fake_list)

    result = event_service.list_events(status_filter="PENDING", node_filter="n1", page=2)

    assert result is sentinel
    assert captured["args"] == ("PENDING", "n1", 2, 20)


def test_acknowledge_event_pg_dispatch(force_pg):
    sentinel = {"alert_id": 5, "acknowledged_by": "admin", "acknowledged_at": "t"}
    captured = {}

    def fake_ack(alert_id, acknowledged_by):
        captured["args"] = (alert_id, acknowledged_by)
        return sentinel

    force_pg.setattr(event_service, "_acknowledge_event_pg", fake_ack)

    result = event_service.acknowledge_event(alert_id=5, acknowledged_by="admin")

    assert result is sentinel
    assert captured["args"] == (5, "admin")


def test_resolve_event_pg_dispatch(force_pg):
    captured = {}

    def fake_resolve(alert_id, resolved_by, notes):
        captured["args"] = (alert_id, resolved_by, notes)
        return True

    force_pg.setattr(event_service, "_resolve_event_pg", fake_resolve)

    ok = event_service.resolve_event(alert_id=8, resolved_by="admin", notes="done")

    assert ok is True
    assert captured["args"] == (8, "admin", "done")


# =============================================================================
# SQLite path unchanged (db=None and explicit db yield identical behaviour)
# =============================================================================

def _seed_event(node_id="glass_01", status="PENDING"):
    """Insert one event and return its id (SQLite backend must be active)."""
    return database.insert_event(
        node_id=node_id,
        timestamp="2026-07-13T10:00:00",
        visual_confidence=0.9,
        audio_db_peak=70.0,
        audio_freq_peak_hz=1200.0,
        status=status,
    )


def test_sqlite_get_pending_alert_ids(sqlite_db):
    a = _seed_event(status="PENDING")
    b = _seed_event(status="PENDING")
    _seed_event(status="RESOLVED")

    ids = database.get_pending_alert_ids()

    assert set(ids) == {a, b}


def test_sqlite_get_event_created_ats(sqlite_db):
    _seed_event(status="PENDING")
    _seed_event(status="RESOLVED")

    rows = database.get_event_created_ats("2000-01-01T00:00:00")

    assert len(rows) == 2
    assert all(isinstance(r, str) for r in rows)


def test_sqlite_get_event_counts_db_none_and_explicit(sqlite_db):
    _seed_event(status="PENDING")
    _seed_event(status="PENDING")
    _seed_event(status="RESOLVED")

    counts_none = event_service.get_event_counts()          # defaults to get_db()
    counts_explicit = event_service.get_event_counts(sqlite_db)

    assert counts_none == counts_explicit
    assert counts_none["pending"] == 2
    assert counts_none["resolved"] == 1
    assert counts_none["total"] == 3


def test_sqlite_list_events_db_none_and_explicit(sqlite_db):
    _seed_event(status="PENDING")
    _seed_event(status="PENDING")

    res_none = event_service.list_events(status_filter="PENDING")
    res_explicit = event_service.list_events(sqlite_db, status_filter="PENDING")

    assert res_none["total"] == 2
    assert res_explicit["total"] == 2
    assert len(res_none["items"]) == 2


def test_sqlite_acknowledge_event_db_none(sqlite_db):
    eid = _seed_event(status="PENDING")

    result = event_service.acknowledge_event(alert_id=eid, acknowledged_by="admin")

    assert result is not None
    assert result["alert_id"] == eid
    assert result["acknowledged_by"] == "admin"
    # Status actually transitioned
    ev = database.get_event(eid)
    assert ev["status"] == "ACKNOWLEDGED"


def test_sqlite_resolve_event_explicit_db(sqlite_db):
    eid = _seed_event(status="PENDING")

    ok = event_service.resolve_event(sqlite_db, alert_id=eid, resolved_by="admin", notes="ok")

    assert ok is True
    ev = database.get_event(eid)
    assert ev["status"] == "RESOLVED"
    assert ev["resolved_by"] == "admin"
