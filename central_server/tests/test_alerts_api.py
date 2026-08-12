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
    """
    Create a temporary test database via the production init_db() path.

    Why init_db (not a hand-rolled sqlite3.connect): production code uses
    `get_db_cursor()` which guards every transaction with `_db_lock`. That
    lock is set up inside `_init_sqlite()`. Skipping init_db leaves it as
    None, and the first `with _db_lock:` raises
    "'NoneType' does not support context manager" — which is exactly the
    pre-existing fixture bug item #21 fixes.
    """
    import central_server.database as db_module

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    conn = db_module.init_db(db_path)
    try:
        yield conn
    finally:
        db_module.close_db()
        try:
            os.unlink(db_path)
        except OSError:
            pass


@pytest.fixture
def client(test_db):
    """Create a test client. test_db has already initialised the global
    database; routers can call get_db()/get_db_cursor() directly."""
    from fastapi import FastAPI
    from starlette.middleware.sessions import SessionMiddleware

    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="test-secret-key-for-testing"
    )

    from central_server.api.alerts import router as alerts_router
    app.include_router(alerts_router, prefix="/api")

    # Routes that broadcast over WebSocket touch app.state — give them stubs
    # so we don't have to spin up the full lifespan.
    app.state.latest_snapshots = {}

    with TestClient(app) as test_client:
        yield test_client


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


class TestCreateAlertIdempotency:
    """DATA-006: POST /api/alerts must be idempotent on (node_id, timestamp).

    An edge node on flaky WiFi retries a queued upload after a dropped ACK,
    replaying the identical (node_id, timestamp) — the capture instant, at
    microsecond precision (edge_glass event_capture). That used to insert a
    duplicate event row and double-page the operator. A retry must return the
    existing alert_id WITHOUT creating a second row or re-broadcasting; two
    DISTINCT detections (different timestamps) must still both be created so a
    real alert is never suppressed.
    """

    @staticmethod
    def _count_events():
        import central_server.database as db
        return len(db.get_events_by_status("PENDING_VIDEO", 1000))

    def test_retry_returns_same_id_no_duplicate_row(self, client, api_headers, sample_alert):
        r1 = client.post("/api/alerts", json=sample_alert, headers=api_headers)
        r2 = client.post("/api/alerts", json=sample_alert, headers=api_headers)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["alert_id"] == r2.json()["alert_id"]
        assert self._count_events() == 1

    def test_distinct_timestamps_create_distinct_alerts(self, client, api_headers, sample_alert):
        a = dict(sample_alert, timestamp="2026-03-03T12:00:00.111111")
        b = dict(sample_alert, timestamp="2026-03-03T12:00:00.222222")
        r1 = client.post("/api/alerts", json=a, headers=api_headers)
        r2 = client.post("/api/alerts", json=b, headers=api_headers)
        assert r1.json()["alert_id"] != r2.json()["alert_id"]
        assert self._count_events() == 2

    def test_retry_does_not_rebroadcast(self, client, api_headers, sample_alert, monkeypatch):
        import central_server.api.alerts as alerts_mod
        calls = []

        async def fake_broadcast(msg):
            calls.append(msg)

        monkeypatch.setattr(alerts_mod.ws_manager, "broadcast", fake_broadcast)

        client.post("/api/alerts", json=sample_alert, headers=api_headers)
        client.post("/api/alerts", json=sample_alert, headers=api_headers)
        # Only the first (real) creation broadcasts new_alert; the retry must not
        # re-page the operator.
        new_alert_broadcasts = [m for m in calls if m.get("type") == "new_alert"]
        assert len(new_alert_broadcasts) == 1, calls


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


class TestUploadVideoNodeBinding:
    """Security fix (2026-08-06): PUT /alerts/{id}/video accepts per-node
    edge API keys (Depends(verify_api_key)) but, before this fix, never
    called verify_node_binding -- so a key provisioned for node X could
    upload a video into node Y's alert. Covers the three binding outcomes:
    cross-node -> 403, own-node -> not 403, shared/unbound key -> not 403.
    """

    def test_cross_node_key_forbidden(self, client, api_headers, sample_alert):
        """A per-node key bound to a DIFFERENT node than the alert's owner
        must be rejected with 403 (this is the RED assertion pre-fix)."""
        import central_server.database as db_module

        # Alert belongs to sample_alert's node_id ("glass_node_01").
        create_response = client.post("/api/alerts", json=sample_alert, headers=api_headers)
        alert_id = create_response.json()["alert_id"]

        # Per-node key provisioned for a DIFFERENT node.
        out = db_module.provision_edge_node_key("glass_node_99")
        fake_mp4 = io.BytesIO(b"fake mp4 content for testing")

        response = client.put(
            f"/api/alerts/{alert_id}/video",
            files={"file": ("test.mp4", fake_mp4, "video/mp4")},
            headers={"X-API-Key": out["api_key"]}
        )

        assert response.status_code == 403

    def test_own_node_key_allowed(self, client, api_headers, sample_alert):
        """A per-node key bound to the SAME node as the alert must proceed
        (not be rejected as a binding mismatch)."""
        import central_server.database as db_module

        create_response = client.post("/api/alerts", json=sample_alert, headers=api_headers)
        alert_id = create_response.json()["alert_id"]

        # Per-node key provisioned for the alert's OWN node.
        out = db_module.provision_edge_node_key(sample_alert["node_id"])
        fake_mp4 = io.BytesIO(b"fake mp4 content for testing")

        response = client.put(
            f"/api/alerts/{alert_id}/video",
            files={"file": ("test.mp4", fake_mp4, "video/mp4")},
            headers={"X-API-Key": out["api_key"]}
        )

        assert response.status_code != 403
        assert response.status_code == 204

    def test_shared_key_allowed_any_node(self, client, api_headers, sample_alert):
        """The shared EDGE_API_KEY leaves edge_auth_node None (grace-period /
        unbound) -- verify_node_binding must remain a no-op for it, or every
        node still on the shared key would regress and stop being able to
        upload video at all."""
        create_response = client.post("/api/alerts", json=sample_alert, headers=api_headers)
        alert_id = create_response.json()["alert_id"]

        fake_mp4 = io.BytesIO(b"fake mp4 content for testing")
        response = client.put(
            f"/api/alerts/{alert_id}/video",
            files={"file": ("test.mp4", fake_mp4, "video/mp4")},
            headers=api_headers  # shared key -> edge_auth_node is None
        )

        assert response.status_code != 403
        assert response.status_code == 204


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


class TestResolveAlert:
    """Tests for PATCH /api/alerts/{alert_id}/resolve endpoint.

    Regression guard for the spoofable-attribution defect (Theme 2, trust
    boundary): the endpoint used to attribute a resolution to whatever
    `resolved_by` the client sent in the request body, even though it
    already authenticates the caller via get_current_user. Any
    authenticated user could therefore forge who resolved an alert,
    including in the tamper-evident audit log. Attribution must now
    always be derived from the authenticated session user; a
    client-supplied resolved_by is accepted (for backward compatibility
    with the legacy template) but silently ignored.
    """

    @pytest.fixture
    def authed_client(self, test_db):
        """Alerts router with get_current_user overridden to "operator",
        mirroring the session-auth bypass pattern in test_nodes_api.py."""
        from fastapi import FastAPI
        from starlette.middleware.sessions import SessionMiddleware
        from central_server.api.alerts import router as alerts_router
        from central_server.auth import get_current_user

        app = FastAPI()
        app.add_middleware(
            SessionMiddleware,
            secret_key="test-secret-key-for-testing"
        )
        app.include_router(alerts_router, prefix="/api")
        app.state.latest_snapshots = {}
        app.dependency_overrides[get_current_user] = lambda: "operator"

        with TestClient(app) as test_client:
            yield test_client

        app.dependency_overrides.clear()

    def _create_resolvable_alert(self, authed_client, api_headers, sample_alert):
        """Create an alert and upload its video so status == PENDING
        (resolvable), returning the alert_id."""
        create_response = authed_client.post(
            "/api/alerts", json=sample_alert, headers=api_headers
        )
        assert create_response.status_code == 200
        alert_id = create_response.json()["alert_id"]

        fake_mp4 = io.BytesIO(b"fake mp4 content for testing")
        upload_response = authed_client.put(
            f"/api/alerts/{alert_id}/video",
            files={"file": ("test.mp4", fake_mp4, "video/mp4")},
            headers=api_headers,
        )
        assert upload_response.status_code == 204
        return alert_id

    def test_resolve_attributes_to_authenticated_user_not_body(
        self, authed_client, api_headers, sample_alert
    ):
        """A malicious/careless client sends resolved_by="attacker" while
        authenticated as "operator". Both the DB write and the audit log
        must record "operator", never the spoofed value."""
        alert_id = self._create_resolvable_alert(authed_client, api_headers, sample_alert)

        with patch("central_server.services.audit_service.log_action") as mock_log_action:
            resolve_response = authed_client.patch(
                f"/api/alerts/{alert_id}/resolve",
                json={"resolved_by": "attacker", "notes": "x"},
            )

        assert resolve_response.status_code == 200

        # Audit log must be attributed to the authenticated user.
        assert mock_log_action.called
        first_call_args = mock_log_action.call_args_list[0][0]
        assert first_call_args[0] == "operator"
        assert first_call_args[0] != "attacker"

        # DB write must also be attributed to the authenticated user.
        from central_server.database import get_event
        event = get_event(alert_id)
        assert event["resolved_by"] == "operator"
        assert event["resolved_by"] != "attacker"

    def test_resolve_without_resolved_by_in_body_still_works(
        self, authed_client, api_headers, sample_alert
    ):
        """resolved_by is now optional/ignored — omitting it must not 422,
        and the resolution is still attributed to the authenticated user."""
        alert_id = self._create_resolvable_alert(authed_client, api_headers, sample_alert)

        resolve_response = authed_client.patch(
            f"/api/alerts/{alert_id}/resolve",
            json={"notes": "no resolved_by field at all"},
        )
        assert resolve_response.status_code == 200

        from central_server.database import get_event
        event = get_event(alert_id)
        assert event["resolved_by"] == "operator"


class TestListAlertsAckFieldMapping:
    """Regression guard for AlertDetail mapping drift (duplicate-merge fix).

    get_alert_detail mapped acknowledged_by/acknowledged_at but list_alerts's
    hand-rolled copy omitted them, so GET /api/alerts returned nulls for
    acknowledged events — blanking the SPA's 認領 badge and stale-ack counter.
    Both endpoints now share the single _row_to_alert_detail() mapper; this
    test acknowledges an event and asserts the LIST endpoint carries the ack
    fields, matching the detail endpoint.
    """

    @pytest.fixture
    def authed_client(self, test_db):
        """Alerts router with session auth bypassed as "operator" (mirrors
        TestResolveAlert.authed_client)."""
        from fastapi import FastAPI
        from starlette.middleware.sessions import SessionMiddleware
        from central_server.api.alerts import router as alerts_router
        from central_server.auth import get_current_user

        app = FastAPI()
        app.add_middleware(
            SessionMiddleware,
            secret_key="test-secret-key-for-testing"
        )
        app.include_router(alerts_router, prefix="/api")
        app.state.latest_snapshots = {}
        app.dependency_overrides[get_current_user] = lambda: "operator"

        with TestClient(app) as test_client:
            yield test_client

        app.dependency_overrides.clear()

    def test_list_alerts_returns_ack_fields(
        self, authed_client, api_headers, sample_alert
    ):
        # Create an alert and upload its video so it is PENDING (ack-able).
        create_response = authed_client.post(
            "/api/alerts", json=sample_alert, headers=api_headers
        )
        assert create_response.status_code == 200
        alert_id = create_response.json()["alert_id"]

        fake_mp4 = io.BytesIO(b"fake mp4 content for testing")
        upload_response = authed_client.put(
            f"/api/alerts/{alert_id}/video",
            files={"file": ("test.mp4", fake_mp4, "video/mp4")},
            headers=api_headers,
        )
        assert upload_response.status_code == 204

        ack_response = authed_client.patch(f"/api/alerts/{alert_id}/acknowledge")
        assert ack_response.status_code == 200

        # The LIST endpoint must surface the ack fields (this is what drifted).
        list_response = authed_client.get("/api/alerts", headers=api_headers)
        assert list_response.status_code == 200
        row = next(e for e in list_response.json() if e["id"] == alert_id)
        assert row["status"] == "ACKNOWLEDGED"
        assert row["acknowledged_by"] == "operator"
        assert isinstance(row["acknowledged_at"], str) and row["acknowledged_at"]

        # And it must agree with the detail endpoint (shared mapper).
        detail = authed_client.get(
            f"/api/alerts/{alert_id}", headers=api_headers
        ).json()
        assert detail["acknowledged_by"] == row["acknowledged_by"]
        assert detail["acknowledged_at"] == row["acknowledged_at"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])