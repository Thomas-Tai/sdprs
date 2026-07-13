# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Retention Service Unit Tests
Smart Disaster Prevention Response System

Tests for data retention cleanup functionality.
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta

import pytest

from central_server.services.retention_service import run_retention_cleanup


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def test_db(temp_dir):
    """Create a test SQLite database."""
    db_path = os.path.join(temp_dir, "test.db")
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    
    db.execute("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT NOT NULL,
            timestamp DATETIME NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING_VIDEO',
            mp4_path TEXT,
            visual_confidence REAL,
            audio_db_peak REAL,
            audio_freq_peak_hz REAL,
            resolved_by TEXT,
            resolved_at DATETIME,
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()
    
    yield db, db_path
    
    db.close()


def insert_event(db, node_id, days_ago, mp4_path=None):
    """Insert a test event with created_at set to days_ago days ago."""
    created = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    db.execute(
        "INSERT INTO events (node_id, timestamp, status, mp4_path, created_at) VALUES (?, ?, 'RESOLVED', ?, ?)",
        (node_id, created, mp4_path, created)
    )
    db.commit()


def create_mp4(storage_dir, node_id, filename):
    """Create a dummy MP4 file."""
    node_dir = os.path.join(storage_dir, "events", node_id)
    os.makedirs(node_dir, exist_ok=True)
    
    filepath = os.path.join(node_dir, filename)
    with open(filepath, "wb") as f:
        f.write(b"fake mp4 content")
    
    return filepath


class TestRetentionCleanup:
    """Tests for run_retention_cleanup function."""
    
    def test_cleanup_deletes_expired_events(self, test_db, temp_dir):
        """Test that expired events are deleted."""
        db, db_path = test_db
        storage_dir = os.path.join(temp_dir, "storage")
        
        # Create MP4 files
        mp4_31 = create_mp4(storage_dir, "node_01", "old1.mp4")
        mp4_35 = create_mp4(storage_dir, "node_01", "old2.mp4")
        mp4_29 = create_mp4(storage_dir, "node_01", "recent.mp4")
        
        # Insert events
        insert_event(db, "node_01", 31, mp4_31)
        insert_event(db, "node_01", 35, mp4_35)
        insert_event(db, "node_01", 29, mp4_29)
        
        # Run cleanup
        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)
        
        # Verify
        assert result["deleted_events"] == 2
        assert result["deleted_files"] == 2
        assert not os.path.exists(mp4_31)
        assert not os.path.exists(mp4_35)
        assert os.path.exists(mp4_29)
        
        # Check database
        cursor = db.cursor()
        cursor.execute("SELECT COUNT(*) FROM events")
        assert cursor.fetchone()[0] == 1
    
    def test_cleanup_handles_missing_mp4(self, test_db, temp_dir):
        """Test cleanup when MP4 file doesn't exist."""
        db, db_path = test_db
        storage_dir = os.path.join(temp_dir, "storage")
        
        # Insert event with non-existent MP4 path
        insert_event(db, "node_01", 31, "/nonexistent/path.mp4")
        
        # Run cleanup
        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)
        
        # Should not raise error, event should still be deleted
        assert result["deleted_events"] == 1
        assert result["deleted_files"] == 0
        
        # Check database
        cursor = db.cursor()
        cursor.execute("SELECT COUNT(*) FROM events")
        assert cursor.fetchone()[0] == 0
    
    def test_cleanup_removes_empty_dirs(self, test_db, temp_dir):
        """Test that empty directories are removed after cleanup."""
        db, db_path = test_db
        storage_dir = os.path.join(temp_dir, "storage")
        
        # Create MP4 in a node directory
        mp4_path = create_mp4(storage_dir, "node_01", "old.mp4")
        
        # Insert event
        insert_event(db, "node_01", 31, mp4_path)
        
        # Verify directory exists
        node_dir = os.path.join(storage_dir, "events", "node_01")
        assert os.path.isdir(node_dir)
        
        # Run cleanup
        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)
        
        # Verify empty directory was removed
        assert result["deleted_dirs"] >= 1
        assert not os.path.isdir(node_dir)
    
    def test_cleanup_preserves_nonempty_dirs(self, test_db, temp_dir):
        """Test that directories with remaining files are not deleted."""
        db, db_path = test_db
        storage_dir = os.path.join(temp_dir, "storage")
        
        # Create two MP4 files in same directory
        mp4_old = create_mp4(storage_dir, "node_01", "old.mp4")
        mp4_new = create_mp4(storage_dir, "node_01", "recent.mp4")
        
        # Insert events
        insert_event(db, "node_01", 31, mp4_old)
        insert_event(db, "node_01", 5, mp4_new)  # Recent event
        
        # Run cleanup
        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)
        
        # Verify directory still exists
        node_dir = os.path.join(storage_dir, "events", "node_01")
        assert os.path.isdir(node_dir)
        
        # Recent MP4 should still exist
        assert os.path.exists(mp4_new)
    
    def test_cleanup_with_no_expired_events(self, test_db, temp_dir):
        """Test cleanup when no events are expired."""
        db, db_path = test_db
        storage_dir = os.path.join(temp_dir, "storage")
        
        # Insert only recent events
        insert_event(db, "node_01", 5, None)
        insert_event(db, "node_01", 10, None)
        
        # Run cleanup
        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)
        
        # Verify no deletions
        assert result["deleted_events"] == 0
        assert result["deleted_files"] == 0
        
        # Check database
        cursor = db.cursor()
        cursor.execute("SELECT COUNT(*) FROM events")
        assert cursor.fetchone()[0] == 2
    
    def test_cleanup_with_empty_database(self, test_db, temp_dir):
        """Test cleanup with empty database."""
        db, db_path = test_db
        storage_dir = os.path.join(temp_dir, "storage")
        
        # Run cleanup on empty database
        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)
        
        # Should not raise error
        assert result["deleted_events"] == 0
        assert result["deleted_files"] == 0
        assert result["deleted_dirs"] == 0
    
    def test_cleanup_with_custom_retention_days(self, test_db, temp_dir):
        """Test cleanup with custom retention period."""
        db, db_path = test_db
        storage_dir = os.path.join(temp_dir, "storage")
        
        # Create MP4
        mp4_path = create_mp4(storage_dir, "node_01", "test.mp4")
        
        # Insert event 10 days ago
        insert_event(db, "node_01", 10, mp4_path)
        
        # Run cleanup with 7 day retention - should delete
        result = run_retention_cleanup(db_path, storage_dir, retention_days=7)
        assert result["deleted_events"] == 1
        assert not os.path.exists(mp4_path)
        
        # Insert another event 10 days ago
        mp4_path2 = create_mp4(storage_dir, "node_01", "test2.mp4")
        insert_event(db, "node_01", 10, mp4_path2)
        
        # Run cleanup with 15 day retention - should not delete
        result = run_retention_cleanup(db_path, storage_dir, retention_days=15)
        assert result["deleted_events"] == 0
        assert os.path.exists(mp4_path2)

    def test_delimiter_boundary_not_off_by_a_day(self, test_db, temp_dir):
        """Regression: a same-date row that is actually NEWER than the cutoff
        must survive despite SQLite storing created_at with a SPACE delimiter.

        The delimiter bug: stored "YYYY-MM-DD HH:MM:SS" (space 0x20) vs the
        T-delimited cutoff.isoformat() (T 0x54). A naive string compare treats
        ANY same-date row as older than cutoff (space < 'T'), mis-deleting it.
        This test FAILS if the datetime() normalization is reverted.
        """
        db, db_path = test_db
        storage_dir = os.path.join(temp_dir, "storage")

        cutoff = datetime.utcnow() - timedelta(days=30)

        # Boundary row: SAME calendar date as cutoff but NEWER in real time,
        # stored in SQLite's space-delimited form. Clamp within the day so we
        # never roll onto the next date (which would defeat the same-date test).
        boundary_dt = cutoff + timedelta(hours=1)
        if boundary_dt.date() != cutoff.date():
            boundary_dt = cutoff.replace(hour=23, minute=59, second=59, microsecond=0)
        boundary_str = boundary_dt.strftime("%Y-%m-%d %H:%M:%S")

        # Clearly-old row: 5 days past the cutoff, same stored space format.
        old_str = (cutoff - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")

        db.execute(
            "INSERT INTO events (node_id, timestamp, status, created_at) VALUES (?, ?, 'RESOLVED', ?)",
            ("node_bnd", boundary_str, boundary_str),
        )
        db.execute(
            "INSERT INTO events (node_id, timestamp, status, created_at) VALUES (?, ?, 'RESOLVED', ?)",
            ("node_old", old_str, old_str),
        )
        db.commit()

        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)

        # Only the clearly-old row should go; the boundary row is genuinely
        # newer than cutoff and must remain.
        cursor = db.cursor()
        cursor.execute("SELECT node_id FROM events ORDER BY node_id")
        remaining = [r[0] for r in cursor.fetchall()]
        assert remaining == ["node_bnd"], f"boundary row mis-deleted: remaining={remaining}"
        assert result["deleted_events"] == 1

    def test_pump_readings_pruned(self, test_db, temp_dir):
        """pump_readings older than the cutoff are pruned; recent ones survive.

        Old row uses a SPACE-delimited timestamp, recent row uses T-delimited,
        proving the datetime() compare is robust across both formats.
        """
        db, db_path = test_db
        storage_dir = os.path.join(temp_dir, "storage")

        db.execute("""
            CREATE TABLE pump_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                water_level REAL,
                pump_state TEXT
            )
        """)
        old_ts = (datetime.utcnow() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
        recent_ts = (datetime.utcnow() - timedelta(days=2)).isoformat()
        db.execute(
            "INSERT INTO pump_readings (node_id, timestamp, water_level, pump_state) VALUES (?, ?, ?, ?)",
            ("pump_01", old_ts, 80.0, "ON"),
        )
        db.execute(
            "INSERT INTO pump_readings (node_id, timestamp, water_level, pump_state) VALUES (?, ?, ?, ?)",
            ("pump_01", recent_ts, 20.0, "OFF"),
        )
        db.commit()

        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)

        assert result["deleted_pump_readings"] == 1
        cursor = db.cursor()
        cursor.execute("SELECT timestamp FROM pump_readings")
        rows = [r[0] for r in cursor.fetchall()]
        assert rows == [recent_ts]

    def test_pump_readings_prune_missing_table(self, test_db, temp_dir):
        """Cleanup must not crash when pump_readings does not exist (old DBs)."""
        db, db_path = test_db
        storage_dir = os.path.join(temp_dir, "storage")

        # No pump_readings table created by the test_db fixture.
        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)

        assert result["deleted_pump_readings"] == 0
        assert result["errors"] == []

    def test_orphan_sweep(self, test_db, temp_dir):
        """Old MP4s referenced by NO surviving event are swept; referenced and
        recent files are kept."""
        db, db_path = test_db
        storage_dir = os.path.join(temp_dir, "storage")

        referenced = create_mp4(storage_dir, "node_01", "referenced.mp4")
        old_orphan = create_mp4(storage_dir, "node_01", "old_orphan.mp4")
        recent_orphan = create_mp4(storage_dir, "node_01", "recent_orphan.mp4")

        # A recent (surviving) event references referenced.mp4 -> must be kept
        # even though the file was just written (recent mtime).
        insert_event(db, "node_01", 2, referenced)

        # Make the old orphan's mtime clearly older than the 30-day cutoff.
        old_time = (datetime.utcnow() - timedelta(days=45)).timestamp()
        os.utime(old_orphan, (old_time, old_time))

        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)

        assert result["deleted_orphans"] == 1
        assert os.path.exists(referenced)      # referenced by a surviving event
        assert not os.path.exists(old_orphan)  # old + unreferenced -> swept
        assert os.path.exists(recent_orphan)   # recent -> kept


if __name__ == "__main__":
    pytest.main([__file__, "-v"])