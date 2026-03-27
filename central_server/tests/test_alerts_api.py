# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Alerts API Unit Tests
Smart Disaster Prevention Response System

Tests for POST /api/alerts and PUT /api/alerts/{alert_id}/video endpoints.
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
    cursor.execute("PRAGMA busy_timeout=5000;")
    
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
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id        TEXT PRIMARY KEY,
            node_type      TEXT NOT NULL,
            last_heartbeat DATETIME,
            status         TEXT DEFAULT 'OFFLINE',
            metadata       TEXT
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
    from central_server.api.alerts import router as alerts_router
    app.include_router(alerts_router, prefix="/api")
    
    # Initialize app state
    app.state.latest_snapshots = {}
    
    with TestClient(app) as test_client:
        yield test_client
    
    # Restore original function
    db_module.get_db = original_get_db


@pytest.fixture
def api_headers():
    """Return headers with valid API key."""
    return {"X-API-Key": "test-api-key-12345"}


@pytest.fixture
def sample_alert():
    """Return a sample alert payload."""
    return {
        "node_id": "glass_node_01",
        "timestamp": "2026-03-03T12:00:00Z",
        "visual_confidence": 0.87,
        "audio_db_peak": 102.3,
        "audio_freq_peak_hz": 4500.0
    }


class TestCreateAlert:
    """Tests for POST /api/alerts endpoint."""
    
    def test_create_alert_success(self, client, api_headers, sample_alert):
        """Test successful alert creation."""
        response = client.post(
            "/api/alerts",
            json=sample_alert,
            headers=api_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "alert_id" in data
        assert data["status"] == "PENDING_VIDEO"
        assert isinstance(data["alert_id"], int)
    
    def test_create_alert_no_api_key(self, client, sample_alert):
        """Test alert creation without API key."""
        response = client.post(
            "/api/alerts",
            json=sample_alert
        )
        
        assert response.status_code == 401
    
    def test_create_alert_wrong_api_key(self, client, sample_alert):
        """Test alert creation with wrong API key."""
        response = client.post(
            "/api/alerts",
            json=sample_alert,
            headers={"X-API-Key": "wrong-key"}
        )
        
        assert response.status_code == 401
    
    def test_create_alert_missing_field(self, client, api_headers):
        """Test alert creation with missing required field."""
        incomplete_alert = {
            "node_id": "glass_node_01",
            "timestamp": "2026-03-03T12:00:00Z"
            # Missing visual_confidence, audio_db_peak, audio_freq_peak_hz
        }
        
        response = client.post(
            "/api/alerts",
            json=incomplete_alert,
            headers=api_headers
        )
        
        assert response.status_code == 422
    
    def test_create_alert_invalid_type(self, client, api_headers):
        """Test alert creation with invalid field type."""
        invalid_alert = {
            "node_id": "glass_node_01",
            "timestamp": "2026-03-03T12:00:00Z",
            "visual_confidence": "not a float",  # Invalid type
            "audio_db_peak": 102.3,
            "audio_freq_peak_hz": 4500.0
        }
        
        response = client.post(
            "/api/alerts",
            json=invalid_alert,
            headers=api_headers
        )
        
        assert response.status_code == 422
    
    def test_create_alert_confidence_out_of_range(self, client, api_headers):
        """Test alert creation with confidence value out of range."""
        invalid_alert = {
            "node_id": "glass_node_01",
            "timestamp": "2026-03-03T12:00:00Z",
            "visual_confidence": 1.5,  # > 1.0
            "audio_db_peak": 102.3,
            "audio_freq_peak_hz": 4500.0
        }
        
        response = client.post(
            "/api/alerts",
            json=invalid_alert,
            headers=api_headers
        )
        
        assert response.status_code == 422


class TestUploadVideo:
    """Tests for PUT /api/alerts/{alert_id}/video endpoint."""
    
    def test_upload_video_success(self, client, api_headers, sample_alert):
        """Test successful video upload."""
        # First create an alert
        create_response = client.post(
            "/api/alerts",
            json=sample_alert,
            headers=api_headers
        )
        assert create_response.status_code == 200
        alert_id = create_response.json()["alert_id"]
        
        # Upload fake MP4 video
        fake_mp4 = io.BytesIO(b"fake mp4 content for testing")
        
        upload_response = client.put(
            f"/api/alerts/{alert_id}/video",
            files={"file": ("test.mp4", fake_mp4, "video/mp4")},
            headers=api_headers
        )
        
        assert upload_response.status_code == 204
    
    def test_upload_video_not_found(self, client, api_headers):
        """Test video upload for non-existent alert."""
        fake_mp4 = io.BytesIO(b"fake mp4 content")
        
        response = client.put(
            "/api/alerts/99999/video",  # Non-existent ID
            files={"file": ("test.mp4", fake_mp4, "video/mp4")},
            headers=api_headers
        )
        
        assert response.status_code == 404
    
    def test_upload_video_wrong_status(self, client, api_headers, sample_alert, test_db):
        """Test video upload when alert already has video."""
        import central_server.database as db_module
        
        # First create an alert
        create_response = client.post(
            "/api/alerts",
            json=sample_alert,
            headers=api_headers
        )
        alert_id = create_response.json()["alert_id"]
        
        # First upload
        fake_mp4_1 = io.BytesIO(b"first video")
        upload_response_1 = client.put(
            f"/api/alerts/{alert_id}/video",
            files={"file": ("test1.mp4", fake_mp4_1, "video/mp4")},
            headers=api_headers
        )
        assert upload_response_1.status_code == 204
        
        # Second upload should fail (status is no longer PENDING_VIDEO)
        fake_mp4_2 = io.BytesIO(b"second video")
        upload_response_2 = client.put(
            f"/api/alerts/{alert_id}/video",
            files={"file": ("test2.mp4", fake_mp4_2, "video/mp4")},
            headers=api_headers
        )
        
        assert upload_response_2.status_code == 409
    
    def test_upload_video_no_api_key(self, client, sample_alert, api_headers):
        """Test video upload without API key."""
        # First create an alert with API key
        create_response = client.post(
            "/api/alerts",
            json=sample_alert,
            headers=api_headers
        )
        alert_id = create_response.json()["alert_id"]
        
        # Try to upload without API key
        fake_mp4 = io.BytesIO(b"fake mp4 content")
        
        response = client.put(
            f"/api/alerts/{alert_id}/video",
            files={"file": ("test.mp4", fake_mp4, "video/mp4")}
        )
        
        assert response.status_code == 401


class TestGetAlert:
    """Tests for GET /api/alerts/{alert_id} endpoint."""
    
    def test_get_alert_success(self, client, api_headers, sample_alert):
        """Test getting an alert by ID."""
        # Create alert
        create_response = client.post(
            "/api/alerts",
            json=sample_alert,
            headers=api_headers
        )
        alert_id = create_response.json()["alert_id"]
        
        # Get alert
        get_response = client.get(
            f"/api/alerts/{alert_id}",
            headers=api_headers
        )
        
        assert get_response.status_code == 200
        data = get_response.json()
        assert data["id"] == alert_id
        assert data["node_id"] == sample_alert["node_id"]
        assert data["status"] == "PENDING_VIDEO"
    
    def test_get_alert_not_found(self, client, api_headers):
        """Test getting a non-existent alert."""
        response = client.get(
            "/api/alerts/99999",
            headers=api_headers
        )
        
        assert response.status_code == 404


class TestListAlerts:
    """Tests for GET /api/alerts endpoint."""
    
    def test_list_alerts_empty(self, client, api_headers):
        """Test listing alerts when none exist."""
        response = client.get(
            "/api/alerts",
            headers=api_headers
        )
        
        assert response.status_code == 200
        assert response.json() == []
    
    def test_list_alerts_multiple(self, client, api_headers, sample_alert):
        """Test listing multiple alerts."""
        # Create multiple alerts
        for i in range(3):
            alert = sample_alert.copy()
            alert["node_id"] = f"node_{i}"
            client.post("/api/alerts", json=alert, headers=api_headers)
        
        # List alerts
        response = client.get(
            "/api/alerts",
            headers=api_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])