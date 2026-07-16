# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Retention Subdir Sweep Tests
Smart Disaster Prevention Response System

Covers the Storage-F fix: the on-disk orphan sweep + empty-dir cleanup are
no longer hardcoded to storage/events/ but iterate the subdirs configured by
STORAGE_RETENTION_SUBDIRS (comma-separated, default "events").

Tests verify:
1. Default (no override) still sweeps "events" only (backward-compat).
2. Single custom subdir sweeps that subdir instead of "events".
3. Multiple subdirs are all swept.
4. Whitespace and empty entries in the config value are tolerated.
5. Missing subdirs on disk are silently skipped, not errors.
6. Explicit subdirs=[...] parameter overrides env config.
"""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from central_server.config import get_settings
from central_server.services.retention_service import (
    _parse_retention_subdirs,
    run_retention_cleanup,
)
from central_server.timeutil import utcnow
from datetime import timedelta, timezone


# ---------- fixtures ----------

@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Invalidate the get_settings() lru_cache around every test.

    Tests in this file flip STORAGE_RETENTION_SUBDIRS via monkeypatch.setenv.
    Without clearing the cache, the singleton from a prior test (or from
    another test module that ran earlier) would return stale env values.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def storage(tmp_path):
    """Return (db_path, storage_dir) with a fresh events schema initialized."""
    db_path = tmp_path / "test.db"
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()

    db = sqlite3.connect(str(db_path))
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
    db.close()

    return str(db_path), str(storage_dir)


# ---------- helpers ----------

def _mkfile(root, subdir, node_id, filename, days_old=None):
    """Create an empty MP4 under root/subdir/node_id/filename.

    If days_old is given, backdate the file's mtime by that many days so it
    trips the retention cutoff. The mtime is computed from a timezone-aware
    UTC datetime and turned into a POSIX timestamp, so results are correct
    regardless of the host's local TZ. Return the absolute filesystem path.
    """
    node_dir = os.path.join(root, subdir, node_id)
    os.makedirs(node_dir, exist_ok=True)
    path = os.path.join(node_dir, filename)
    with open(path, "wb") as f:
        f.write(b"fake mp4")
    if days_old is not None:
        aware = (utcnow() - timedelta(days=days_old)).replace(tzinfo=timezone.utc)
        ts = aware.timestamp()
        os.utime(path, (ts, ts))
    return path


def _insert_event(db_path, node_id, mp4_path, days_old):
    """Insert a single event row aged days_old (both timestamp and created_at)."""
    stamp = (utcnow() - timedelta(days=days_old)).isoformat()
    db = sqlite3.connect(db_path)
    db.execute(
        "INSERT INTO events (node_id, timestamp, status, mp4_path, created_at) "
        "VALUES (?, ?, 'RESOLVED', ?, ?)",
        (node_id, stamp, mp4_path, stamp),
    )
    db.commit()
    db.close()


# ---------- parser unit tests ----------

class TestParseSubdirs:
    def test_empty_string_defaults_to_events(self):
        assert _parse_retention_subdirs("") == ["events"]

    def test_whitespace_only_defaults_to_events(self):
        assert _parse_retention_subdirs("   ") == ["events"]

    def test_single_entry(self):
        assert _parse_retention_subdirs("uploads") == ["uploads"]

    def test_multi_entries_stripped(self):
        assert _parse_retention_subdirs(" events , , uploads ") == [
            "events",
            "uploads",
        ]

    def test_dedup_preserves_first_occurrence_order(self):
        assert _parse_retention_subdirs("uploads,events,uploads,exports") == [
            "uploads",
            "events",
            "exports",
        ]

    def test_only_commas_defaults_to_events(self):
        assert _parse_retention_subdirs(",,,") == ["events"]


# ---------- integration tests ----------

class TestRetentionSubdirsSweep:

    def test_default_sweeps_events_only(self, storage, monkeypatch):
        """Backward-compat: no env override -> sweep uses only 'events'.

        Old orphan under storage/events/ is swept; identical old orphan
        under storage/uploads/ is left untouched because uploads is not in
        the default subdir list.
        """
        monkeypatch.delenv("STORAGE_RETENTION_SUBDIRS", raising=False)
        get_settings.cache_clear()

        db_path, storage_dir = storage

        events_orphan = _mkfile(storage_dir, "events", "node_01", "old.mp4", days_old=45)
        uploads_orphan = _mkfile(storage_dir, "uploads", "node_01", "old.mp4", days_old=45)

        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)

        assert result["errors"] == []
        assert not os.path.exists(events_orphan), "events/ orphan should be swept by default"
        assert os.path.exists(uploads_orphan), "uploads/ should NOT be swept by default"
        assert result["deleted_orphans"] == 1

    def test_single_custom_subdir(self, storage, monkeypatch):
        """STORAGE_RETENTION_SUBDIRS='uploads' sweeps uploads, not events."""
        monkeypatch.setenv("STORAGE_RETENTION_SUBDIRS", "uploads")
        get_settings.cache_clear()

        db_path, storage_dir = storage

        events_orphan = _mkfile(storage_dir, "events", "node_01", "old.mp4", days_old=45)
        uploads_orphan = _mkfile(storage_dir, "uploads", "node_01", "old.mp4", days_old=45)

        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)

        assert result["errors"] == []
        assert os.path.exists(events_orphan), "events/ should NOT be swept when config excludes it"
        assert not os.path.exists(uploads_orphan), "uploads/ orphan should be swept"
        assert result["deleted_orphans"] == 1

    def test_multi_subdirs(self, storage, monkeypatch):
        """'events,uploads' sweeps both. Recent files in either survive."""
        monkeypatch.setenv("STORAGE_RETENTION_SUBDIRS", "events,uploads")
        get_settings.cache_clear()

        db_path, storage_dir = storage

        events_orphan_old = _mkfile(storage_dir, "events", "node_01", "old.mp4", days_old=45)
        uploads_orphan_old = _mkfile(storage_dir, "uploads", "node_01", "old.mp4", days_old=45)
        events_recent = _mkfile(storage_dir, "events", "node_01", "recent.mp4")
        uploads_recent = _mkfile(storage_dir, "uploads", "node_01", "recent.mp4")

        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)

        assert result["errors"] == []
        assert not os.path.exists(events_orphan_old), "old events/ orphan should be swept"
        assert not os.path.exists(uploads_orphan_old), "old uploads/ orphan should be swept"
        assert os.path.exists(events_recent), "recent events/ file must survive"
        assert os.path.exists(uploads_recent), "recent uploads/ file must survive"
        assert result["deleted_orphans"] == 2

    def test_whitespace_and_empty_entry_tolerance(self, storage, monkeypatch):
        """' events , , uploads ' parses to ['events','uploads']."""
        monkeypatch.setenv("STORAGE_RETENTION_SUBDIRS", " events , , uploads ")
        get_settings.cache_clear()

        db_path, storage_dir = storage

        events_orphan = _mkfile(storage_dir, "events", "node_01", "old.mp4", days_old=45)
        uploads_orphan = _mkfile(storage_dir, "uploads", "node_01", "old.mp4", days_old=45)

        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)

        assert result["errors"] == []
        assert not os.path.exists(events_orphan)
        assert not os.path.exists(uploads_orphan)
        assert result["deleted_orphans"] == 2

    def test_missing_subdir_on_disk_silent_skip(self, storage, monkeypatch):
        """'events,nonexistent' sweeps events; missing subdir does not error."""
        monkeypatch.setenv("STORAGE_RETENTION_SUBDIRS", "events,nonexistent")
        get_settings.cache_clear()

        db_path, storage_dir = storage

        events_orphan = _mkfile(storage_dir, "events", "node_01", "old.mp4", days_old=45)
        # Deliberately DO NOT create storage/nonexistent/

        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)

        assert result["errors"] == [], f"missing subdir should not produce errors, got: {result['errors']}"
        assert not os.path.exists(events_orphan), "events/ orphan should still be swept"
        assert result["deleted_orphans"] == 1
        # And nothing should have been (mis-)created on disk.
        assert not os.path.exists(os.path.join(storage_dir, "nonexistent"))

    def test_explicit_subdirs_param_overrides_env(self, storage, monkeypatch):
        """subdirs=['custom'] beats STORAGE_RETENTION_SUBDIRS='events'."""
        monkeypatch.setenv("STORAGE_RETENTION_SUBDIRS", "events")
        get_settings.cache_clear()

        db_path, storage_dir = storage

        events_orphan = _mkfile(storage_dir, "events", "node_01", "old.mp4", days_old=45)
        custom_orphan = _mkfile(storage_dir, "custom", "node_01", "old.mp4", days_old=45)

        result = run_retention_cleanup(
            db_path, storage_dir, retention_days=30, subdirs=["custom"]
        )

        assert result["errors"] == []
        # Env said "events", but explicit param wins -> only custom is swept.
        assert os.path.exists(events_orphan), "explicit subdirs param must override env"
        assert not os.path.exists(custom_orphan), "explicit subdirs param must sweep 'custom'"
        assert result["deleted_orphans"] == 1

    def test_referenced_files_across_subdirs_all_kept(self, storage, monkeypatch):
        """A DB-referenced MP4 anywhere (even outside the current sweep subdir)
        remains listed in surviving_refs, so its file is never treated as an
        orphan even if a future sweep reaches its directory."""
        monkeypatch.setenv("STORAGE_RETENTION_SUBDIRS", "events,uploads")
        get_settings.cache_clear()

        db_path, storage_dir = storage

        # Recent event references a file inside uploads/ (unusual, but the
        # orphan sweep must respect the DB reference regardless of subdir).
        # Backdate mtime so it would qualify for deletion on age alone —
        # only the DB reference protects it.
        uploads_referenced = _mkfile(storage_dir, "uploads", "node_02", "keep.mp4", days_old=45)

        _insert_event(db_path, "node_02", uploads_referenced, days_old=2)

        # Also an unrelated old orphan in events/ that SHOULD be swept.
        events_orphan = _mkfile(storage_dir, "events", "node_01", "old.mp4", days_old=45)

        result = run_retention_cleanup(db_path, storage_dir, retention_days=30)

        assert result["errors"] == []
        assert os.path.exists(uploads_referenced), (
            "DB-referenced file must survive even if its subdir is being swept"
        )
        assert not os.path.exists(events_orphan)
        assert result["deleted_orphans"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
