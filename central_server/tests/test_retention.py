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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])