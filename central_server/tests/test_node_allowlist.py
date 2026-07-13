# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Edge node_id Allowlist Tests
Smart Disaster Prevention Response System

Tests that the ingest endpoints enforce the edge node_id allowlist
(settings.ALLOWED_NODE_IDS) via auth.verify_node_id:
- POST /api/alerts               (client-supplied node_id in the body)
- POST /api/edge/{node_id}/snapshot  (path node_id)

IMPORTANT (cross-module contamination guard):
get_settings() is lru_cached and the app + routers import once. To exercise a
NON-EMPTY allowlist we must set ALLOWED_NODE_IDS in the process env BEFORE the
app imports and clear the settings cache so the app instance picks it up. A
module-scoped autouse fixture restores the env (and clears the cache again)
at teardown so sibling test modules do NOT inherit "glass_node_01".
"""

import os
import sys
import tempfile
from pathlib import Path

# Add the sdprs/ dir to the path so `import central_server` works.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Required settings for the app to import. Use setdefault so we don't clobber a
# value a sibling module may already have set in this process.
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

# Isolate the DB so the accepted-alert insert lands in a throwaway file rather
# than the real ./data/sdprs.db. The full app's lifespan reads DB_PATH and runs
# init_db, which creates the schema, so insert_event() works for the 200 path.
#
# CRITICAL: env is mutated inside the module-scoped `_allowlist_env` fixture
# below (at SETUP time), NOT at import time. Writing ALLOWED_NODE_IDS / DB_PATH
# at module import would run during pytest's collection phase — before any test
# executes — and leak into every sibling module (breaking their default-settings
# assertions, e.g. test_config_auth_settings and test_alerts_api). Only the temp
# file is created here; no os.environ writes at import scope.
_PRIOR_DB_PATH = os.environ.get("DB_PATH")
_TMP_DB_FD, _TMP_DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_TMP_DB_FD)

from central_server.config import get_settings

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from central_server.main import app


API_KEY = "test-api-key-12345"

# Minimal valid JPEG (copied from test_snapshot_api.py's FAKE_JPEG literal).
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


@pytest.fixture(scope="module", autouse=True)
def _allowlist_env():
    """Enable the allowlist + isolated DB for THIS module only, at SETUP time.

    Mutating env here (not at import) keeps pytest's collection phase clean so
    sibling modules keep their default settings. Clearing the lru_cache makes
    per-request get_settings() (inside verify_node_id) re-read the mutated env.
    Everything is restored at teardown and the cache cleared again."""
    _prior_allow = os.environ.get("ALLOWED_NODE_IDS")
    os.environ["ALLOWED_NODE_IDS"] = "glass_node_01"
    os.environ["DB_PATH"] = _TMP_DB_PATH
    get_settings.cache_clear()
    yield
    if _prior_allow is None:
        os.environ.pop("ALLOWED_NODE_IDS", None)
    else:
        os.environ["ALLOWED_NODE_IDS"] = _prior_allow
    if _PRIOR_DB_PATH is None:
        os.environ.pop("DB_PATH", None)
    else:
        os.environ["DB_PATH"] = _PRIOR_DB_PATH
    get_settings.cache_clear()
    try:
        os.unlink(_TMP_DB_PATH)
    except OSError:
        pass


@pytest.fixture(scope="module")
def client(_allowlist_env):
    """TestClient over the full app. Depends on _allowlist_env so the env +
    settings cache are set before the lifespan (init_db) runs. The `with` block
    runs the lifespan so the DB schema is created and latest_snapshots is set."""
    with TestClient(app) as c:
        yield c


# ===== Direct unit tests of the frozen contract (no DB, no HTTP) =====

class TestVerifyNodeIdDirect:
    """Exercise auth.verify_node_id directly against the enabled allowlist."""

    def test_allows_listed_node(self):
        from central_server.auth import verify_node_id
        # Listed node -> no exception raised.
        verify_node_id("glass_node_01")

    def test_rejects_unlisted_node(self):
        from central_server.auth import verify_node_id
        with pytest.raises(HTTPException) as exc_info:
            verify_node_id("rogue_node")
        assert exc_info.value.status_code == 403


# ===== Integration: snapshot ingest (no DB fixture required) =====

class TestSnapshotAllowlist:
    """POST /api/edge/{node_id}/snapshot enforces the path node_id allowlist."""

    def test_snapshot_rejected_for_unlisted_node(self, client):
        resp = client.post(
            "/api/edge/rogue_node/snapshot",
            content=FAKE_JPEG,
            headers={"X-API-Key": API_KEY, "Content-Type": "image/jpeg"},
        )
        assert resp.status_code == 403

    def test_snapshot_accepted_for_listed_node(self, client):
        resp = client.post(
            "/api/edge/glass_node_01/snapshot",
            content=FAKE_JPEG,
            headers={"X-API-Key": API_KEY, "Content-Type": "image/jpeg"},
        )
        assert resp.status_code == 204


# ===== Integration: alert ingest (uses the isolated temp DB) =====

class TestAlertAllowlist:
    """POST /api/alerts enforces the client-supplied node_id allowlist."""

    @staticmethod
    def _alert_body(node_id: str) -> dict:
        # Fields per api/alerts.py::AlertCreate (all required):
        # node_id, timestamp, visual_confidence (0-1), audio_db_peak,
        # audio_freq_peak_hz (>=0).
        return {
            "node_id": node_id,
            "timestamp": "2026-07-13T12:00:00Z",
            "visual_confidence": 0.9,
            "audio_db_peak": 85.0,
            "audio_freq_peak_hz": 4000.0,
        }

    def test_alert_rejected_for_unlisted_node(self, client):
        resp = client.post(
            "/api/alerts",
            json=self._alert_body("rogue_node"),
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 403

    def test_alert_accepted_for_listed_node(self, client):
        resp = client.post(
            "/api/alerts",
            json=self._alert_body("glass_node_01"),
            headers={"X-API-Key": API_KEY},
        )
        # create_alert is decorated status_code=200 on success.
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "PENDING_VIDEO"
        assert isinstance(data["alert_id"], int)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
