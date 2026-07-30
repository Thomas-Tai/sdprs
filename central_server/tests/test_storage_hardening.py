# -*- coding: utf-8 -*-
"""Storage-endpoint security pass (2026-07-30).

The earlier Storage A–G sweep (2026-07-16) closed path traversal into
``storage/events/`` by gating ``node_id`` at ``verify_node_id``, stripped EXIF
from snapshots, and added ``nosniff``. This file covers what that pass did not:
**the upload side of the video endpoint, and the HLS directory helpers.**

Findings pinned here:

* **S-1 — unbounded request body.** ``PUT /api/alerts/{id}/video`` checked
  ``file.size > 100 MB`` *inside the handler*. By the time a FastAPI handler
  runs, Starlette has already parsed the whole multipart body and spooled it to
  a temp file — and ``MultiPartParser.max_part_size`` (1 MB) applies **only to
  non-file parts** (``formparsers.py``: ``if self._current_part.file is None``),
  so file parts stream with no cap at all. The old check therefore prevented
  *storing* an oversized upload, never *receiving* one: a client holding the
  shared edge key could fill the server disk and be told 413 afterwards. The
  fix has to sit at ASGI level, before the body is buffered.

* **S-2 — a 0-byte upload was accepted and marked the alert complete.**
  ``if file.size and file.size > MAX`` skips the check entirely when
  ``file.size == 0`` (falsy). An edge node whose encoder failed could mark an
  alert as having video with an empty file, and the operator would open a clip
  that does not exist. On a branch whose thesis is that status must not lie,
  that is the same class of bug as a tray icon showing green while offline.

* **S-3 — the HLS directory helpers trusted ``node_id``.** ``get_hls_dir`` did
  ``mkdir(parents=True)`` on ``base / node_id`` and ``cleanup_hls_dir`` did
  ``shutil.rmtree`` on it, neither validating. ``store_hls_segment`` calls
  ``get_hls_dir`` *before* its containment check on the target file, so that
  check never protected directory creation. Not reachable today — every router
  caller enforces camera ownership first and webcam node_ids are
  server-assigned — but it is one new caller away from arbitrary mkdir/rmtree.
"""
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from central_server.database import init_db, close_db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DB_PATH", db_path)
    init_db(db_path)
    yield
    close_db()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASS", "testpass123")
    monkeypatch.setenv("EDGE_API_KEY", "test-edge-key-12345678901234567890")
    monkeypatch.setenv("SECRET_KEY", "a" * 32)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("HLS_STORAGE_PATH", str(tmp_path / "hls"))
    # Small cap so the test moves kilobytes, not 100 MB. The production default
    # stays 100 MB; what is under test is that the limit is ENFORCED before the
    # body is buffered, not the particular number.
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "4096")
    from central_server.config import get_settings
    get_settings.cache_clear()
    from central_server.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


EDGE_KEY = "test-edge-key-12345678901234567890"


async def _make_alert(client) -> int:
    resp = await client.post(
        "/api/alerts",
        json={"node_id": "glass_node_01", "timestamp": "2026-07-30T10:00:00",
              "visual_confidence": 0.9, "audio_db_peak": 70.0,
              "audio_freq_peak_hz": 3000.0},
        headers={"X-API-Key": EDGE_KEY},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["alert_id"]


def _stored_mp4s(tmp_path) -> list:
    events = Path(tmp_path) / "storage" / "events"
    return list(events.rglob("*.mp4")) if events.exists() else []


# ===== S-1: the body limit must bite BEFORE the body is buffered ===========

@pytest.mark.anyio
async def test_oversized_upload_is_rejected(client, tmp_path):
    """A body past the cap must be refused, and must leave nothing on disk.

    The old handler-level check could only ever run after Starlette had already
    spooled the entire upload to a temp file, so 'rejected' and 'not received'
    were different things. This asserts the request is refused; the companion
    test below asserts the route never even ran.
    """
    alert_id = await _make_alert(client)
    oversized = b"\x00" * (4096 * 4)

    resp = await client.put(
        f"/api/alerts/{alert_id}/video",
        files={"file": ("clip.mp4", oversized, "video/mp4")},
        headers={"X-API-Key": EDGE_KEY},
    )

    assert resp.status_code == 413, resp.text
    assert _stored_mp4s(tmp_path) == [], "an over-cap upload must not be stored"


@pytest.mark.anyio
async def test_the_route_never_runs_for_an_oversized_body(app, client, monkeypatch):
    """The whole point of S-1: rejection happens before the endpoint is entered,
    which is the only way the body is not buffered to disk first.

    Asserted by making the endpoint itself explode -- if the 413 still comes
    back, the request was stopped upstream of it.
    """
    alert_id = await _make_alert(client)

    import central_server.api.alerts as alerts_mod

    async def _must_not_run(*a, **kw):  # pragma: no cover - must never execute
        raise AssertionError("the endpoint ran; the body was already buffered")

    monkeypatch.setattr(alerts_mod, "upload_video", _must_not_run, raising=True)

    resp = await client.put(
        f"/api/alerts/{alert_id}/video",
        files={"file": ("clip.mp4", b"\x00" * (4096 * 4), "video/mp4")},
        headers={"X-API-Key": EDGE_KEY},
    )
    assert resp.status_code == 413, resp.text


@pytest.mark.anyio
async def test_a_chunked_body_with_no_declared_length_is_also_capped(client, tmp_path):
    """Gate 2, and the reason gate 1 alone is not enough.

    A chunked request declares no Content-Length, so the cheap header check has
    nothing to test and waves it through. If the bytes were not also counted as
    they arrive, omitting a single header would defeat the whole limit -- which
    is exactly what an attacker does and an honest client never does. This is
    the case that must not silently pass.

    The body must be real multipart, not raw bytes: with a body shape it does
    not expect, FastAPI rejects on validation WITHOUT ever reading the stream,
    so nothing is buffered and nothing is counted (a 422, and a test that would
    have proved nothing). The counter deliberately measures only the bytes the
    app actually consumes -- which is exactly the set of bytes that get spooled
    to disk.
    """
    boundary = "----sdprsTestBoundary"

    async def _endless_multipart():
        yield (f"--{boundary}\r\n"
               'Content-Disposition: form-data; name="file"; filename="clip.mp4"\r\n'
               "Content-Type: video/mp4\r\n\r\n").encode()
        for _ in range(8):
            yield b"\x00" * 4096
        yield f"\r\n--{boundary}--\r\n".encode()

    resp = await client.put(
        "/api/alerts/1/video",
        content=_endless_multipart(),
        headers={"X-API-Key": EDGE_KEY,
                 "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    assert "content-length" not in {k.lower() for k in resp.request.headers}, \
        "the test must exercise the chunked path, or it proves nothing about gate 2"
    assert resp.status_code == 413, resp.text
    assert _stored_mp4s(tmp_path) == []


@pytest.mark.anyio
async def test_a_normal_upload_still_works(client, tmp_path):
    """Regression guard: the limit must not break the real edge-node flow."""
    alert_id = await _make_alert(client)
    resp = await client.put(
        f"/api/alerts/{alert_id}/video",
        files={"file": ("clip.mp4", b"\x00" * 1024, "video/mp4")},
        headers={"X-API-Key": EDGE_KEY},
    )
    assert resp.status_code == 204, resp.text
    assert len(_stored_mp4s(tmp_path)) == 1


@pytest.mark.anyio
async def test_the_limit_applies_to_unauthenticated_requests_too(client):
    """The cap must sit in front of auth, not behind it. A limit that only
    applies after the key is checked still lets an anonymous client push an
    unbounded body through the parser before being told 401."""
    resp = await client.put(
        "/api/alerts/1/video",
        files={"file": ("clip.mp4", b"\x00" * (4096 * 4), "video/mp4")},
    )
    assert resp.status_code == 413, resp.text


# ===== S-2: an empty upload is not a video =================================

@pytest.mark.anyio
async def test_empty_upload_is_rejected_and_leaves_the_alert_pending(client, tmp_path):
    """`if file.size and file.size > MAX` skipped the check for size 0, so a
    failed encode could mark an alert as having video. The operator then opens
    a clip that is not there -- the alert lies about its own completeness."""
    alert_id = await _make_alert(client)

    resp = await client.put(
        f"/api/alerts/{alert_id}/video",
        files={"file": ("clip.mp4", b"", "video/mp4")},
        headers={"X-API-Key": EDGE_KEY},
    )

    assert resp.status_code == 400, resp.text
    assert _stored_mp4s(tmp_path) == [], "a 0-byte upload must not be stored"

    from central_server.database import get_event
    assert get_event(alert_id)["status"] == "PENDING_VIDEO", \
        "the alert must stay claimable so a real upload can still arrive"


# ===== S-3: the HLS directory helpers must not trust node_id ===============

def test_get_hls_dir_refuses_a_traversing_node_id(tmp_path, monkeypatch):
    """store_hls_segment calls get_hls_dir BEFORE its containment check on the
    target file, so that check never protected directory creation. Unreachable
    today (every router caller enforces camera ownership first), but one new
    caller away from arbitrary mkdir."""
    monkeypatch.setenv("HLS_STORAGE_PATH", str(tmp_path / "hls"))
    from central_server.config import get_settings
    get_settings.cache_clear()
    from central_server.services import hls_service

    for hostile in ("../evil", "..", "a/../../b", "/etc", "..\\evil", ""):
        with pytest.raises(ValueError):
            hls_service.get_hls_dir(hostile)

    assert not (tmp_path / "evil").exists()
    assert not (tmp_path / "hls" / "..").resolve().joinpath("evil").exists()


def test_cleanup_hls_dir_refuses_a_traversing_node_id(tmp_path, monkeypatch):
    """Same helper family, but this one calls shutil.rmtree -- so an unvalidated
    node_id here deletes a tree rather than creating one."""
    monkeypatch.setenv("HLS_STORAGE_PATH", str(tmp_path / "hls"))
    from central_server.config import get_settings
    get_settings.cache_clear()
    from central_server.services import hls_service

    victim = tmp_path / "victim"
    victim.mkdir(parents=True)
    (victim / "keepme.txt").write_text("data", encoding="utf-8")

    with pytest.raises(ValueError):
        hls_service.cleanup_hls_dir("../victim")

    assert (victim / "keepme.txt").exists(), "rmtree escaped the HLS root"


def test_a_legitimate_node_id_still_works(tmp_path, monkeypatch):
    """Regression guard: server-assigned webcam ids must keep working."""
    monkeypatch.setenv("HLS_STORAGE_PATH", str(tmp_path / "hls"))
    from central_server.config import get_settings
    get_settings.cache_clear()
    from central_server.services import hls_service

    d = hls_service.get_hls_dir("webcam_b4a4f203")
    assert d.is_dir()
    assert d.parent.resolve() == (tmp_path / "hls").resolve()

    hls_service.store_hls_segment("webcam_b4a4f203", "seg_001.ts", b"data")
    assert hls_service.get_hls_file("webcam_b4a4f203", "seg_001.ts") == b"data"

    hls_service.cleanup_hls_dir("webcam_b4a4f203")
    assert not d.exists()
