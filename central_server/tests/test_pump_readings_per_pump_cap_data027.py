# -*- coding: utf-8 -*-
"""DATA-027 (Low): get_pump_readings_multi must cap rows PER PUMP, not globally.

The batch fetch used one ``ORDER BY node_id, timestamp ASC LIMIT :lim`` (default
50 000) across ALL pumps. Ordered by node_id, a fast-cycling pump early in the
ordering consumes the whole cap and later pumps get truncated to zero readings —
so their ON->OFF cycle count is silently undercounted (often to 0). Cap each
pump independently via ROW_NUMBER() OVER (PARTITION BY node_id ...).

Unlike test_pump_cycles_batch.py (which mocks get_pump_readings_multi), this
exercises the REAL SQL against a temp SQLite DB so the capping is actually
verified.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

import central_server.database as db


@pytest.fixture
def test_db():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = db.init_db(db_path)
    try:
        yield conn
    finally:
        db.close_db()
        try:
            os.unlink(db_path)
        except OSError:
            pass


_BASE = datetime(2026, 3, 3, 12, 0, 0)


def _seed(node_id, states, water=50.0):
    for i, st in enumerate(states):
        db.insert_pump_reading(node_id, (_BASE + timedelta(seconds=i)).isoformat(), water, st)


def _window():
    return (_BASE - timedelta(minutes=1)).isoformat(), (_BASE + timedelta(minutes=1)).isoformat()


def test_cap_is_per_pump_not_global(test_db):
    # pump_A cycles fast (5 readings); pump_B has 2. Same window.
    _seed("pump_A", ["ON", "OFF", "ON", "OFF", "ON"])
    _seed("pump_B", ["ON", "OFF"], water=40.0)
    start, end = _window()

    # A tiny per-pump cap of 3. A GLOBAL cap of 3 would be exhausted by pump_A
    # (alphabetically first), starving pump_B down to zero readings.
    grouped = db.get_pump_readings_multi(["pump_A", "pump_B"], start, end, limit=3)

    # pump_A capped at 3 of its 5 readings...
    assert len(grouped.get("pump_A", [])) == 3, grouped
    # ...and pump_B keeps ALL of its readings (the global cap used to starve it).
    assert len(grouped.get("pump_B", [])) == 2, grouped


def test_under_cap_returns_all_and_preserves_shape(test_db):
    """When every pump is under the per-pump cap, all rows come back, and the
    per-row shape is unchanged (node_id stripped; time-series fields present,
    no window-function `rn` column leaks through)."""
    _seed("pump_A", ["ON", "OFF", "ON"])
    _seed("pump_B", ["ON", "OFF"], water=40.0)
    start, end = _window()

    grouped = db.get_pump_readings_multi(["pump_A", "pump_B"], start, end, limit=1000)
    assert len(grouped["pump_A"]) == 3
    assert len(grouped["pump_B"]) == 2

    row = grouped["pump_A"][0]
    assert "pump_state" in row and "timestamp" in row
    assert "node_id" not in row
    assert "rn" not in row


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
