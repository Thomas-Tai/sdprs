# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Snapshot API Unit Tests
Smart Disaster Prevention Response System

Tests for POST /api/edge/{node_id}/snapshot and GET /api/edge/{node_id}/snapshot/latest endpoints.
"""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

# Set required environment variables before importing the app
os.environ["DASHBOARD_USER"] = "admin"
os.environ["DASHBOARD_PASS"] = "testpass123"
os.environ["EDGE_API_KEY"] = "test-api-key-12345"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing"


# Minimum valid JPEG (smallest valid JPEG structure)
FAKE_JPEG = (
    b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t'
    b'\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f'
    b'\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0'
    b'\x00\x0b\x08\x01\xe0\x03\x58\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00'
    b'\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01'
    b'\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01'
    b'\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04'
    b'\x11\x05\x12!1A\x06\x13Qa\x07"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R'
    b'\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVW'
    b'XYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95'
    b'\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4'
    b'\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3'
    b'\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea'
    b'\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00'
    b'\x00?\x00\xfb\xd3\x28\xa2\x80\x0a\x28\xa2\x80\x0a\x28\xa2\x80\x0a\x28'
    b'\xa2\x80\x0a\x28\xa0\x01\xff\xd9'
)


@pytest.fixture
def test_db():
    """Create a temporary test database."""
    import sqlite3
    import threading
    
    # Create temporary database file
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    # Initialize database
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    
    # Create tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id            TEXT NOT NULL,
            timestamp          DATETIME NOT NULL,
            status             TEXT NOT NULL DEFAULT 'PENDING_VIDEO',
            mp4_path           TEXT,
            visual_confidence  REAL,
            audio_db_peak      REAL,
            audio_freq_peak_hz REAL,
            resolved_by        TEXT,
            resolved_at        DATETIME,
            notes              TEXT,
            created_at         DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    conn.commit()
    
    yield conn
    
    # Cleanup
    conn.close()
    try:
        os.unlink(db_path)
    except:
        pass


@pytest.fixture
def client(test_db):
    """Create a test client with a test database."""
    from fastapi import FastAPI
    from starlette.middleware.sessions import SessionMiddleware
    
    # Create a minimal test app
    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="test-secret-key-for-testing"
    )
    
    # Mock database module
    import central_server.database as db_module
    original_get_db = db_module.get_db
    
    # Override get_db to use test database
    def mock_get_db():
        return test_db
    
    db_module.get_db = mock_get_db
    
    # Import and include routers
    from central_server.api.snapshots import router as snapshots_router
    app.include_router(snapshots_router, prefix="/api")
    
    # Initialize app state with snapshot storage
    app.state.latest_snapshots = {}
    
    with TestClient(app) as test_client:
        yield test_client
    
    # Restore original function
    db_module.get_db = original_get_db


@pytest.fixture
def api_headers():
    """Return headers with valid API key."""
    return {"X-API-Key": "test-api-key-12345"}


class TestPostSnapshot:
    """Tests for POST /api/edge/{node_id}/snapshot endpoint."""
    
    def test_post_snapshot_success(self, client, api_headers):
        """Test successful snapshot upload."""
        node_id = "glass_node_01"
        
        response = client.post(
            f"/api/edge/{node_id}/snapshot",
            content=FAKE_JPEG,
            headers={**api_headers, "Content-Type": "image/jpeg"}
        )
        
        assert response.status_code == 204
    
    def test_post_snapshot_no_api_key(self, client):
        """Test snapshot upload without API key."""
        node_id = "glass_node_01"
        
        response = client.post(
            f"/api/edge/{node_id}/snapshot",
            content=FAKE_JPEG,
            headers={"Content-Type": "image/jpeg"}
        )
        
        assert response.status_code == 401
    
    def test_post_snapshot_wrong_api_key(self, client):
        """Test snapshot upload with wrong API key."""
        node_id = "glass_node_01"
        
        response = client.post(
            f"/api/edge/{node_id}/snapshot",
            content=FAKE_JPEG,
            headers={"X-API-Key": "wrong-key", "Content-Type": "image/jpeg"}
        )
        
        assert response.status_code == 401
    
    def test_post_snapshot_empty(self, client, api_headers):
        """Test snapshot upload with empty body."""
        node_id = "glass_node_01"
        
        response = client.post(
            f"/api/edge/{node_id}/snapshot",
            content=b"",
            headers={**api_headers, "Content-Type": "image/jpeg"}
        )
        
        assert response.status_code == 400


class TestGetSnapshot:
    """Tests for GET /api/edge/{node_id}/snapshot/latest endpoint."""
    
    def test_get_snapshot_after_post(self, client, api_headers):
        """Test getting snapshot after posting it."""
        node_id = "glass_node_01"
        
        # Post a snapshot first
        post_response = client.post(
            f"/api/edge/{node_id}/snapshot",
            content=FAKE_JPEG,
            headers={**api_headers, "Content-Type": "image/jpeg"}
        )
        assert post_response.status_code == 204
        
        # Get the snapshot
        get_response = client.get(f"/api/edge/{node_id}/snapshot/latest")
        
        assert get_response.status_code == 200
        assert get_response.headers["content-type"] == "image/jpeg"
        assert get_response.content == FAKE_JPEG
    
    def test_get_snapshot_no_data(self, client):
        """Test getting snapshot when none has been posted."""
        node_id = "glass_node_99"
        
        response = client.get(f"/api/edge/{node_id}/snapshot/latest")
        
        # Should return placeholder image
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert len(response.content) > 0
    
    def test_snapshot_overwrite(self, client, api_headers):
        """Test that posting a new snapshot overwrites the old one."""
        node_id = "glass_node_01"
        
        # Post first snapshot
        first_jpeg = FAKE_JPEG + b"first"
        client.post(
            f"/api/edge/{node_id}/snapshot",
            content=first_jpeg,
            headers={**api_headers, "Content-Type": "image/jpeg"}
        )
        
        # Post second snapshot
        second_jpeg = FAKE_JPEG + b"second"
        client.post(
            f"/api/edge/{node_id}/snapshot",
            content=second_jpeg,
            headers={**api_headers, "Content-Type": "image/jpeg"}
        )
        
        # Get the snapshot - should be the second one
        response = client.get(f"/api/edge/{node_id}/snapshot/latest")
        
        assert response.content == second_jpeg
    
    def test_get_snapshot_different_nodes(self, client, api_headers):
        """Test that snapshots are kept separate for different nodes."""
        node_01 = "glass_node_01"
        node_02 = "glass_node_02"
        
        # Post snapshot for node_01
        jpeg_01 = FAKE_JPEG + b"node01"
        client.post(
            f"/api/edge/{node_01}/snapshot",
            content=jpeg_01,
            headers={**api_headers, "Content-Type": "image/jpeg"}
        )
        
        # node_02 should return placeholder
        response_02 = client.get(f"/api/edge/{node_02}/snapshot/latest")
        assert response_02.headers.get("x-snapshot-status") == "placeholder"
        
        # node_01 should return posted snapshot
        response_01 = client.get(f"/api/edge/{node_01}/snapshot/latest")
        assert response_01.content == jpeg_01
    
    def test_post_snapshot_updates_timestamp(self, client, api_headers):
        """Test that posting a snapshot updates the timestamp."""
        from datetime import datetime
        
        node_id = "glass_node_01"
        
        # Post snapshot
        response = client.post(
            f"/api/edge/{node_id}/snapshot",
            content=FAKE_JPEG,
            headers={**api_headers, "Content-Type": "image/jpeg"}
        )
        
        assert response.status_code == 204
        
        # Get snapshot and check timestamp header
        get_response = client.get(f"/api/edge/{node_id}/snapshot/latest")
        
        assert get_response.status_code == 200
        assert "x-snapshot-timestamp" in get_response.headers
        
        # Verify timestamp is parseable
        timestamp_str = get_response.headers["x-snapshot-timestamp"]
        # Should be able to parse as ISO format
        try:
            datetime.fromisoformat(timestamp_str)
        except ValueError:
            pytest.fail(f"Invalid timestamp format: {timestamp_str}")


class TestSnapshotStatus:
    """Tests for GET /api/edge/snapshots/status endpoint."""
    
    def test_get_snapshots_status(self, client, api_headers):
        """Test getting snapshot status for all nodes."""
        # Post snapshots for two nodes
        for node_id in ["node_01", "node_02"]:
            client.post(
                f"/api/edge/{node_id}/snapshot",
                content=FAKE_JPEG,
                headers={**api_headers, "Content-Type": "image/jpeg"}
            )
        
        # Get status
        response = client.get(
            "/api/edge/snapshots/status",
            headers=api_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["total_nodes"] == 2
        assert "node_01" in data["nodes"]
        assert "node_02" in data["nodes"]
        assert data["nodes"]["node_01"]["has_snapshot"] is True


class TestClearSnapshot:
    """Tests for DELETE /api/edge/{node_id}/snapshot endpoint."""
    
    def test_clear_snapshot(self, client, api_headers):
        """Test clearing a snapshot."""
        node_id = "glass_node_01"
        
        # Post a snapshot
        client.post(
            f"/api/edge/{node_id}/snapshot",
            content=FAKE_JPEG,
            headers={**api_headers, "Content-Type": "image/jpeg"}
        )
        
        # Clear the snapshot
        delete_response = client.delete(
            f"/api/edge/{node_id}/snapshot",
            headers=api_headers
        )
        assert delete_response.status_code == 204
        
        # Should now return placeholder
        get_response = client.get(f"/api/edge/{node_id}/snapshot/latest")
        assert get_response.headers.get("x-snapshot-status") == "placeholder"
    
    def test_clear_snapshot_nonexistent(self, client, api_headers):
        """Test clearing a snapshot for a node that doesn't have one."""
        node_id = "glass_node_99"
        
        # Clear non-existent snapshot should still succeed
        response = client.delete(
            f"/api/edge/{node_id}/snapshot",
            headers=api_headers
        )
        
        assert response.status_code == 204


if __name__ == "__main__":
    pytest.main([__file__, "-v"])