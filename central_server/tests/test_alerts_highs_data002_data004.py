# -*- coding: utf-8 -*-
"""
SDPRS Central Server -- HIGH-severity findings DATA-002 and DATA-004 (audit
2026-08-01), fixed via TDD.

DATA-002 (HIGH): PATCH /api/alerts/{id}/acknowledge and .../resolve read the
    event's status in Python, then called a DB helper -- which itself
    re-checked status in Python and only THEN ran an UPDATE ... WHERE id = ?
    with NO status predicate. Two concurrent operators who both read PENDING
    both pass every check and both UPDATE; whichever commits second silently
    overwrites the first operator's acknowledged_by/resolved_by. The bulk
    endpoints (bulk_acknowledge_events / bulk_resolve_events) already guard
    the UPDATE itself with `AND status = 'PENDING'` / `AND status IN (...)`;
    the single-row helpers did not. Fixed by making the UPDATE itself the
    single source of truth (predicate + rowcount), and mapping a rowcount of
    0 to HTTP 409 CONFLICT (not 500) in the handler.

DATA-004 (HIGH): PUT /api/alerts/{id}/video only rejected an oversized body
    via `if file.size and file.size > MAX_VIDEO_SIZE`, which is skipped
    whenever `file.size` is falsy/None -- and the streaming write loop that
    copies the upload to its final destination on disk had no cumulative
    byte counter of its own, so nothing stopped it writing an arbitrarily
    large file once the size-based pre-check was bypassed. Fixed by counting
    bytes as they are about to be written and aborting (413, unlink partial
    file, leave the alert PENDING_VIDEO) the moment the running total
    exceeds MAX_VIDEO_SIZE, independent of what `file.size` ever reported.

Test layout:
  * DATA-002 (a): a PENDING alert's acknowledge/resolve succeeds and
    attributes to the session user (endpoint-level, via TestClient).
  * DATA-002 (b): the HANDLER maps a DB-helper no-op (None/False -- the
    atomic UPDATE's status predicate did not match) to 409, never 500.
    Verified by monkeypatching the DB helper the handler calls, isolating
    the handler's response-mapping from the DB layer's own atomicity.
  * DATA-002 (c)/(d): the DB HELPERS themselves are atomic and conditional.
    Two back-to-back calls to acknowledge_event/resolve_event simulate two
    operators' UPDATEs landing one after another (exactly what "concurrent"
    reduces to once serialized by a single-writer DB) and prove the SECOND
    call is a no-op and does NOT overwrite the first operator's committed
    attribution. Pre-fix, the second call blindly succeeds and overwrites --
    that is the RED evidence for the vulnerability.
  * DATA-004 (a): a streamed upload with `file.size is None` (as a chunked
    request with no Content-Length may report) but whose `.read()` yields
    more than MAX_VIDEO_SIZE total is rejected 413, leaves NO file on disk,
    and leaves the alert PENDING_VIDEO. MAX_VIDEO_SIZE is monkeypatched
    small so the test moves kilobytes, not the real 100 MB production cap.
    Driven by calling the `upload_video` coroutine directly (bypassing
    FastAPI's dependency injection, matching the direct-DB-helper-call idiom
    used elsewhere in this suite) so `file.size` can be forced to None.
  * DATA-004 (b): a normal small upload still succeeds end-to-end (regression
    guard for the new cumulative-counter code path).
"""
import asyncio
import io
import os
import sys
import tempfile
from pathlib import Path

# Project root so `central_server` is importable (matches sibling suites).
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

# Strong values so config.validate_settings (fail-closed at app startup)
# doesn't reject the test session -- matches test_alerts_api.py exactly.
os.environ["DASHBOARD_USER"] = "admin"
os.environ["DASHBOARD_PASS"] = "testpass123"
os.environ["EDGE_API_KEY"] = "test-api-key-12345"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing"


@pytest.fixture
def test_db():
    """Temporary SQLite DB via the production init_db() path (see
    test_alerts_api.py's test_db fixture docstring for why not a hand-rolled
    sqlite3.connect -- get_db_cursor() needs the _db_lock init_db sets up)."""
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
def api_headers():
    return {"X-API-Key": "test-api-key-12345"}


@pytest.fixture
def sample_alert():
    return {
        "node_id": "glass_node_01",
        "timestamp": "2026-03-03T12:00:00Z",
        "visual_confidence": 0.87,
        "audio_db_peak": 102.3,
        "audio_freq_peak_hz": 4500.0,
    }


@pytest.fixture
def authed_client(test_db):
    """Alerts router with get_current_user overridden to "operator" (mirrors
    TestResolveAlert.authed_client in test_alerts_api.py)."""
    from fastapi import FastAPI
    from starlette.middleware.sessions import SessionMiddleware
    from central_server.api.alerts import router as alerts_router
    from central_server.auth import get_current_user

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-testing")
    app.include_router(alerts_router, prefix="/api")
    app.state.latest_snapshots = {}
    app.dependency_overrides[get_current_user] = lambda: "operator"

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _create_resolvable_alert(client, api_headers, sample_alert):
    """Create an alert and upload its video so status == PENDING (the shared
    precondition for both acknowledge and resolve)."""
    create_response = client.post("/api/alerts", json=sample_alert, headers=api_headers)
    assert create_response.status_code == 200
    alert_id = create_response.json()["alert_id"]

    fake_mp4 = io.BytesIO(b"fake mp4 content for testing")
    upload_response = client.put(
        f"/api/alerts/{alert_id}/video",
        files={"file": ("test.mp4", fake_mp4, "video/mp4")},
        headers=api_headers,
    )
    assert upload_response.status_code == 204
    return alert_id


# ============================================================================
# DATA-002 -- ack/resolve TOCTOU race
# ============================================================================

class _StaleFirstReadCursor:
    """Wraps a real sqlite3.Cursor. The FIRST fetchone() call returns a
    fabricated PENDING row -- simulating a caller whose SELECT ran (and
    returned PENDING) BEFORE a concurrent writer committed its own change to
    the same row. Every other call is delegated to the real cursor
    untouched (this cannot be done by monkeypatching sqlite3.Cursor itself:
    it's an immutable built-in type)."""

    def __init__(self, real_cursor):
        self._real = real_cursor
        self._faked = False

    def execute(self, *a, **kw):
        return self._real.execute(*a, **kw)

    def fetchone(self):
        if not self._faked:
            self._faked = True
            self._real.fetchone()  # drain the real result, discard it
            return {"status": "PENDING"}
        return self._real.fetchone()

    def __getattr__(self, name):
        return getattr(self._real, name)


class _StaleReadDB:
    """Wraps a real sqlite3.Connection so db.cursor() hands out a
    _StaleFirstReadCursor. Passed directly as the `db` argument the DB
    helpers already accept -- no monkeypatching of get_db()/sqlite3
    internals required."""

    def __init__(self, real_db):
        self._real = real_db

    def cursor(self):
        return _StaleFirstReadCursor(self._real.cursor())

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestAcknowledgeAtomicity:
    def test_a_ack_pending_alert_succeeds_and_attributes_to_user(
        self, authed_client, api_headers, sample_alert
    ):
        alert_id = _create_resolvable_alert(authed_client, api_headers, sample_alert)

        resp = authed_client.patch(f"/api/alerts/{alert_id}/acknowledge")

        assert resp.status_code == 200
        body = resp.json()
        assert body["acknowledged_by"] == "operator"

        from central_server.database import get_event
        event = get_event(alert_id)
        assert event["status"] == "ACKNOWLEDGED"
        assert event["acknowledged_by"] == "operator"

    def test_b_ack_helper_conflict_returns_409_not_500(
        self, authed_client, api_headers, sample_alert, monkeypatch
    ):
        """When the DB helper signals it lost the atomicity race (returns
        None -- its UPDATE's status predicate matched 0 rows), the handler
        must answer 409 CONFLICT. Pre-fix, `if result is None: raise 500`
        turned every lost race into an internal server error."""
        alert_id = _create_resolvable_alert(authed_client, api_headers, sample_alert)

        monkeypatch.setattr(
            "central_server.api.alerts.acknowledge_event_db",
            lambda **kw: None,
        )

        resp = authed_client.patch(f"/api/alerts/{alert_id}/acknowledge")

        assert resp.status_code == 409
        assert resp.status_code != 500

    def test_c_db_helper_is_atomic_and_does_not_overwrite_attribution(self, test_db):
        """The real TOCTOU window: acknowledge_event's OWN internal SELECT
        returns a PENDING snapshot (operator_a's read), but by the time its
        UPDATE runs, operator_b's *entire* ack has already landed and
        committed (this is what "concurrent" means once the two operators'
        requests are serialized onto the same row by the DB). Simulated
        deterministically: operator_b's ack genuinely runs and commits
        first via the real DB helper; operator_a's call is then given a
        `db` handle whose cursor fakes its FIRST fetchone() as a stale
        PENDING row (see _StaleReadDB), so operator_a's Python-level status
        check still believes PENDING even though the row is already
        ACKNOWLEDGED.

        Pre-fix: the UPDATE has no status predicate, so operator_a's stale
        read still results in a successful (non-None) UPDATE that
        overwrites operator_b's already-committed attribution -- this is
        the RED evidence for the vulnerability. Post-fix: the UPDATE's own
        `AND status = 'PENDING'` predicate matches 0 rows (the real current
        status is ACKNOWLEDGED), so it must return None and must NOT touch
        operator_b's row.
        """
        from central_server.database import insert_event, get_event, get_db
        from central_server.services import event_service

        alert_id = insert_event(
            node_id="glass_node_01",
            timestamp="2026-03-03T12:00:00Z",
            visual_confidence=0.9,
            audio_db_peak=100.0,
            audio_freq_peak_hz=4000.0,
            status="PENDING",
        )

        # operator_b's ack genuinely lands and commits first.
        first = event_service.acknowledge_event(alert_id=alert_id, acknowledged_by="operator_b")
        assert first is not None
        assert first["acknowledged_by"] == "operator_b"

        # operator_a's request is "mid-flight": its own SELECT read a stale
        # PENDING snapshot (as if it had run BEFORE operator_b's commit).
        stale_db = _StaleReadDB(get_db())
        second = event_service.acknowledge_event(stale_db, alert_id=alert_id, acknowledged_by="operator_a")

        assert second is None, (
            "acknowledge_event must no-op (return None) when its UPDATE's "
            "status predicate matches 0 rows -- a stale PENDING read must "
            "not be enough to overwrite a row that has already moved on"
        )

        row = get_event(alert_id)
        assert row["acknowledged_by"] == "operator_b", (
            "operator_a's stale-read call must not overwrite operator_b's "
            "already-committed attribution"
        )

    def test_ack_same_operator_replay_stays_idempotent(self, authed_client, api_headers, sample_alert):
        """Sanity guard: re-acking your OWN already-ACKNOWLEDGED alert must
        stay a 200 idempotent no-op at the endpoint layer (unchanged
        behaviour) -- exercises the endpoint's pre-existing same-user
        shortcut, not the new atomicity path."""
        alert_id = _create_resolvable_alert(authed_client, api_headers, sample_alert)

        first = authed_client.patch(f"/api/alerts/{alert_id}/acknowledge")
        assert first.status_code == 200
        second = authed_client.patch(f"/api/alerts/{alert_id}/acknowledge")
        assert second.status_code == 200
        assert second.json()["acknowledged_by"] == "operator"


class TestResolveAtomicity:
    def test_a_resolve_pending_alert_succeeds_and_attributes_to_user(
        self, authed_client, api_headers, sample_alert
    ):
        alert_id = _create_resolvable_alert(authed_client, api_headers, sample_alert)

        resp = authed_client.patch(f"/api/alerts/{alert_id}/resolve", json={"notes": "handled"})

        assert resp.status_code == 200

        from central_server.database import get_event
        event = get_event(alert_id)
        assert event["status"] == "RESOLVED"
        assert event["resolved_by"] == "operator"

    def test_b_resolve_helper_conflict_returns_409_not_500(
        self, authed_client, api_headers, sample_alert, monkeypatch
    ):
        alert_id = _create_resolvable_alert(authed_client, api_headers, sample_alert)

        monkeypatch.setattr(
            "central_server.api.alerts.resolve_event_db",
            lambda **kw: False,
        )

        resp = authed_client.patch(f"/api/alerts/{alert_id}/resolve", json={"notes": "x"})

        assert resp.status_code == 409
        assert resp.status_code != 500

    def test_d_db_helper_is_atomic_and_does_not_overwrite_attribution(self, test_db):
        """Mirrors test_c for resolve_event: operator_b's resolve genuinely
        commits first; operator_a's stale PENDING read (via _StaleReadDB)
        must not be enough to overwrite it."""
        from central_server.database import insert_event, get_event, get_db
        from central_server.services import event_service

        alert_id = insert_event(
            node_id="glass_node_01",
            timestamp="2026-03-03T12:00:00Z",
            visual_confidence=0.9,
            audio_db_peak=100.0,
            audio_freq_peak_hz=4000.0,
            status="PENDING",
        )

        first = event_service.resolve_event(alert_id=alert_id, resolved_by="operator_b", notes="first")
        assert first is True

        stale_db = _StaleReadDB(get_db())
        second = event_service.resolve_event(
            stale_db, alert_id=alert_id, resolved_by="operator_a", notes="second"
        )

        assert second is False, (
            "resolve_event must no-op (return False) when its UPDATE's "
            "status predicate matches 0 rows"
        )

        row = get_event(alert_id)
        assert row["status"] == "RESOLVED"
        assert row["resolved_by"] == "operator_b", (
            "operator_a's stale-read call must not overwrite operator_b's "
            "already-committed attribution"
        )
        assert row["notes"] == "first"


# ============================================================================
# DATA-004 -- video upload size cap bypass via file.size == None
# ============================================================================

class _FakeStreamedUploadFile:
    """Stand-in for an UploadFile in the one shape that matters here:
    `.size` is None (as a chunked upload with no Content-Length may report,
    or as any future UploadFile implementation might), while `.read()`
    streams real bytes. Proves the handler must count bytes itself rather
    than trust `.size`."""

    def __init__(self, chunks, filename="clip.mp4", content_type="video/mp4"):
        self.size = None
        self.filename = filename
        self.content_type = content_type
        self._chunks = list(chunks)
        self._idx = 0

    async def read(self, _n=-1):
        if self._idx >= len(self._chunks):
            return b""
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk


@pytest.fixture
def storage_path(tmp_path, monkeypatch):
    """Point STORAGE_PATH at a throwaway tmp dir and clear the Settings
    lru_cache so it takes effect immediately, then clear it again on
    teardown so later tests in the same session don't inherit a stale
    cached Settings object (mirrors test_storage_hardening.py's `app`
    fixture, but self-contained since this file must also pass standalone)."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    from central_server.config import get_settings
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


class TestVideoUploadSizeCap:
    def test_a_streamed_oversized_upload_rejected_413_no_file_left(
        self, test_db, storage_path, monkeypatch
    ):
        from central_server.database import insert_event, get_event
        import central_server.api.alerts as alerts_mod

        # Shrink the cap for a fast test; the production default stays 100 MB.
        monkeypatch.setattr(alerts_mod, "MAX_VIDEO_SIZE", 1024)

        alert_id = insert_event(
            node_id="glass_node_01",
            timestamp="2026-03-03T12:00:00Z",
            visual_confidence=0.9,
            audio_db_peak=100.0,
            audio_freq_peak_hz=4000.0,
            status="PENDING_VIDEO",
        )

        chunks = [b"\x00" * 512 for _ in range(4)]  # 2048 bytes > 1024 cap
        fake_file = _FakeStreamedUploadFile(chunks)

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                alerts_mod.upload_video(
                    alert_id=alert_id,
                    file=fake_file,
                    request=None,
                    api_key="test-api-key-12345",
                )
            )

        assert exc_info.value.status_code == 413

        storage_events = storage_path / "storage" / "events"
        mp4s = list(storage_events.rglob("*.mp4")) if storage_events.exists() else []
        assert mp4s == [], "an over-cap streamed upload must not be left on disk"

        assert get_event(alert_id)["status"] == "PENDING_VIDEO"

    def test_b_normal_small_upload_still_succeeds(
        self, test_db, storage_path, api_headers, sample_alert
    ):
        from fastapi import FastAPI
        from starlette.middleware.sessions import SessionMiddleware
        from central_server.api.alerts import router as alerts_router

        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-testing")
        app.include_router(alerts_router, prefix="/api")
        app.state.latest_snapshots = {}

        with TestClient(app) as client:
            create_response = client.post("/api/alerts", json=sample_alert, headers=api_headers)
            assert create_response.status_code == 200
            alert_id = create_response.json()["alert_id"]

            fake_mp4 = io.BytesIO(b"small real upload")
            resp = client.put(
                f"/api/alerts/{alert_id}/video",
                files={"file": ("clip.mp4", fake_mp4, "video/mp4")},
                headers=api_headers,
            )
            assert resp.status_code == 204

        storage_events = storage_path / "storage" / "events"
        mp4s = list(storage_events.rglob("*.mp4"))
        assert len(mp4s) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
