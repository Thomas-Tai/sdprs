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
import central_server.services.audit_service as audit_service
import central_server.services.event_service as event_service
from central_server.timeutil import utcnow


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


def test_set_handover_note_pg_dispatch(force_pg):
    captured = {}

    def fake_set(note, author, updated_at_iso):
        captured["args"] = (note, author, updated_at_iso)

    force_pg.setattr(database, "_pg_set_handover_note_sync", fake_set)

    database.set_handover_note("note text", "admin", "2026-07-14T10:00:00")

    assert captured["args"] == ("note text", "admin", "2026-07-14T10:00:00")


def test_get_effective_handover_note_pg_dispatch(force_pg):
    """Fresh note under PG: dispatches via get_handover_note_row's PG branch
    and applies the shared TTL (not expired for a just-written stamp)."""
    fresh = utcnow().isoformat()

    def fake_fetch_one(query, params):
        return {"note": "hi", "author": "admin", "updated_at": fresh}

    force_pg.setattr(database, "_pg_fetch_one_sync", fake_fetch_one)

    row = database.get_effective_handover_note()

    assert row == {"note": "hi", "author": "admin", "updated_at": fresh, "expired": False}


def test_get_effective_handover_note_pg_expired(force_pg):
    """A stamp older than the TTL flips expired=True (space-delimited stamp
    exercises the ' '->'T' normalization on the PG-shaped value)."""
    from datetime import timedelta
    stale = (utcnow() - timedelta(hours=48)).isoformat(sep=" ", timespec="seconds")

    def fake_fetch_one(query, params):
        return {"note": "old", "author": "admin", "updated_at": stale}

    force_pg.setattr(database, "_pg_fetch_one_sync", fake_fetch_one)

    row = database.get_effective_handover_note()

    assert row["expired"] is True
    # Caller-chosen TTL is respected.
    assert database.get_effective_handover_note(ttl_hours=72)["expired"] is False


def test_log_action_pg_dispatch(force_pg):
    """Under PG, log_action must route to the PG mirror instead of the
    SQLite-only get_db_cursor() (whose failure the never-raises contract
    used to swallow — silently losing the audit trail)."""
    captured = {}

    def fake_insert(operator, action_type, target_id, details_json):
        captured["args"] = (operator, action_type, target_id, details_json)

    force_pg.setattr(audit_service, "_pg_log_action_sync", fake_insert)

    audit_service.log_action(
        "admin", audit_service.ACTION_RESOLVE, target_id=7, details={"bulk": True}
    )

    assert captured["args"] == ("admin", "RESOLVE", "7", '{"bulk": true}')


def test_list_actions_pg_dispatch(force_pg):
    captured = {}
    sentinel = [{
        "id": 1, "timestamp": "2026-07-14 10:00:00", "operator": "admin",
        "action_type": "LOGIN", "target_id": None, "details": None,
    }]

    def fake_list(limit, offset, operator, action_type, since):
        captured["args"] = (limit, offset, operator, action_type, since)
        return sentinel

    force_pg.setattr(audit_service, "_pg_list_actions_sync", fake_list)

    rows = audit_service.list_actions(limit=50, operator="admin")

    assert rows is sentinel
    assert captured["args"] == (50, 0, "admin", None, None)


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


def test_sqlite_get_event_created_ats_same_day_window(sqlite_db):
    """Regression (alert-rate sparkline blank): the SQLite branch used a raw
    string compare of the stored space-delimited created_at
    ('YYYY-MM-DD HH:MM:SS', SQLite CURRENT_TIMESTAMP) against the
    T-delimited cutoff — ' ' < 'T', so any cutoff on the SAME day sorted
    above every same-day row and returned zero rows. datetime() on both
    sides (retention_service.py idiom) normalizes the delimiters."""
    eid = _seed_event(status="PENDING")
    created_at = str(database.get_event(eid)["created_at"])  # 'YYYY-MM-DD HH:MM:SS'
    same_day_start = created_at[:10] + "T00:00:00"

    rows = database.get_event_created_ats(same_day_start)

    assert rows == [created_at]


def test_sqlite_set_and_get_effective_handover_note(sqlite_db):
    stamp = utcnow().isoformat()

    database.set_handover_note("hand over", "admin", stamp)
    row = database.get_effective_handover_note()

    assert row == {"note": "hand over", "author": "admin",
                   "updated_at": stamp, "expired": False}


def test_sqlite_handover_note_expires_after_ttl(sqlite_db):
    from datetime import timedelta
    stale = (utcnow() - timedelta(hours=25)).isoformat()

    database.set_handover_note("old note", "admin", stale)

    assert database.get_effective_handover_note()["expired"] is True
    # Caller-chosen TTL is respected.
    assert database.get_effective_handover_note(ttl_hours=48)["expired"] is False


def test_sqlite_log_and_list_actions(sqlite_db):
    """SQLite audit path stays byte-identical after the PG dispatch was added:
    a logged action round-trips through list_actions with parsed details."""
    audit_service.log_action(
        "admin", audit_service.ACTION_LOGIN, details={"ip": "1.2.3.4"}
    )

    rows = audit_service.list_actions(limit=10)

    assert len(rows) == 1
    assert rows[0]["operator"] == "admin"
    assert rows[0]["action_type"] == "LOGIN"
    assert rows[0]["details"] == {"ip": "1.2.3.4"}


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
