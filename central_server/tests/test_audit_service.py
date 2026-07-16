# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Audit Service Unit Tests
Smart Disaster Prevention Response System

Covers get_snooze_provenance() behavior across snooze + unsnooze sequences.

The provenance helper queries ONLY ACTION_SNOOZE rows (not ACTION_UNSNOOZE)
for each target_id, returning the most recent SNOOZE operator/timestamp.
An UNSNOOZE row therefore MUST NOT affect provenance: the "who snoozed it"
audit trail is preserved even after the snooze is cleared. This is the
contract the SPA relies on to surface provenance only for currently-snoozed
nodes while still knowing who flipped the switch originally.
"""
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Set required environment variables before importing the app
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

import pytest

import central_server.database as database
from central_server.services.audit_service import (
    ACTION_SNOOZE,
    ACTION_UNSNOOZE,
    get_snooze_provenance,
    log_action,
)


@pytest.fixture
def sqlite_conn():
    """In-memory SQLite with sqlite3.Row so cursor results behave like dicts
    (which is what the audit service expects from get_db_cursor rows)."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE operator_actions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            operator     TEXT NOT NULL,
            action_type  TEXT NOT NULL,
            target_id    TEXT,
            details_json TEXT
        )
    """)
    conn.commit()
    return conn


@pytest.fixture
def audit_env(monkeypatch, sqlite_conn):
    """Patch database.get_db_cursor and database.get_backend so audit_service
    calls route through our in-memory connection."""
    @contextmanager
    def _cursor():
        cur = sqlite_conn.cursor()
        try:
            yield cur
            sqlite_conn.commit()
        except Exception:
            sqlite_conn.rollback()
            raise

    monkeypatch.setattr(database, "get_db_cursor", _cursor)
    monkeypatch.setattr(database, "get_backend", lambda: "sqlite")
    # The audit module imports get_db_cursor / get_backend at module-load from
    # ..database, so patching the source module's attributes is sufficient
    # (audit_service.py reads them via `from ..database import ...` at top).
    import central_server.services.audit_service as audit_service_module
    monkeypatch.setattr(audit_service_module, "get_db_cursor", _cursor)
    monkeypatch.setattr(audit_service_module, "get_backend", lambda: "sqlite")


def test_snooze_provenance_preserved_after_unsnooze(audit_env):
    """get_snooze_provenance MUST return the last SNOOZE operator even after
    an ACTION_UNSNOOZE row has been appended for the same target_id.

    Setup:
      T1: alice SNOOZE abc
      T2: bob   UNSNOOZE abc   (newer row, but action_type != SNOOZE)

    Expected: {"abc": {"by": "alice", "at": "T1"}} — unsnooze does NOT
    overwrite the provenance. Pinning this here so a future SQL change that
    accidentally joins across action_types (or treats the latest row
    regardless of type as authoritative) is caught.
    """
    log_action("alice", ACTION_SNOOZE, target_id="abc", details={"minutes": 30})
    log_action("bob", ACTION_UNSNOOZE, target_id="abc")

    result = get_snooze_provenance(["abc"])
    assert "abc" in result, (
        "Provenance disappeared after unsnooze — unsnooze row must NOT "
        "clobber the prior SNOOZE row in provenance lookup."
    )
    assert result["abc"]["by"] == "alice"
    assert result["abc"]["at"]  # non-empty timestamp string


def test_snooze_provenance_tracks_latest_snooze_per_node(audit_env):
    """Two successive snoozes by different operators: provenance points at
    the most recent one. (Regression guard: the helper orders by id DESC and
    takes the first hit per target_id.)"""
    log_action("alice", ACTION_SNOOZE, target_id="abc")
    log_action("carol", ACTION_SNOOZE, target_id="abc")

    result = get_snooze_provenance(["abc"])
    assert result["abc"]["by"] == "carol"


def test_snooze_provenance_missing_node_returns_empty_key(audit_env):
    """A node with no SNOOZE row at all must be absent from the result — the
    SPA treats absence as 'never snoozed'."""
    log_action("bob", ACTION_UNSNOOZE, target_id="never-snoozed")
    result = get_snooze_provenance(["never-snoozed"])
    assert result == {}


def test_snooze_provenance_empty_input(audit_env):
    """Empty input list -> empty dict, no DB round-trip error."""
    assert get_snooze_provenance([]) == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
