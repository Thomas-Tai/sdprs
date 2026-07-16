# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Snapshot Hardening Tests

Covers two follow-up security findings on central_server/api/snapshots.py:

Storage-D (LOW-MEDIUM):
    GET /edge/{node_id}/snapshot/latest must set
    ``X-Content-Type-Options: nosniff`` so a hostile edge cannot smuggle
    JPEG-prefixed HTML/script through IE/Edge/older Safari content sniffing.

Storage-E (LOW):
    receive_snapshot must best-effort strip EXIF (GPS, camera serial, etc.)
    from ingested JPEGs before storing them. The strip is defensive — a
    Pillow-absent deployment must not fail ingest, only skip the scrub.

Credentials for these tests come from ``conftest.py`` (strong defaults set
via ``os.environ.setdefault``); we do not redefine them here.
"""

import importlib
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

# Make the project importable when this file is run directly.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server.api import snapshots as snapshots_module
from central_server.api.snapshots import router as snapshots_router, _strip_exif


# Minimum valid JPEG (mirrors test_snapshot_api.FAKE_JPEG). Used for cases
# where a synthetic-but-parseable JPEG is enough.
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


try:  # pragma: no cover - trivial guard
    from PIL import Image  # noqa: F401
    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False


# ----- Fixtures -------------------------------------------------------------

@pytest.fixture
def test_db():
    """Bare temp SQLite handle so central_server.database.get_db is patchable
    without triggering the real schema-init path. Nothing in the snapshots
    endpoints reads from it, but the module import touches database."""
    import sqlite3

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def client(test_db):
    """FastAPI TestClient wired the same way as test_snapshot_api.py — real
    SessionMiddleware so auth deps evaluate, and a fresh in-memory
    latest_snapshots dict so tests are isolated."""
    import central_server.database as db_module

    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key=os.environ["SECRET_KEY"],
    )

    original_get_db = db_module.get_db
    db_module.get_db = lambda: test_db

    app.include_router(snapshots_router, prefix="/api")
    app.state.latest_snapshots = {}

    with TestClient(app) as test_client:
        yield test_client

    db_module.get_db = original_get_db


@pytest.fixture
def api_headers():
    """X-API-Key headers derived from whichever EDGE_API_KEY conftest
    (or the process env) established — never hardcode the value here."""
    return {"X-API-Key": os.environ["EDGE_API_KEY"]}


# ----- Storage-D: nosniff header on GET responses ---------------------------

class TestNosniffHeader:
    """GET /edge/{node_id}/snapshot/latest must set X-Content-Type-Options:
    nosniff on BOTH the snapshot-present branch and the placeholder branch
    (LOW-MEDIUM finding Storage-D)."""

    def test_nosniff_on_stored_snapshot(self, client, api_headers):
        """When a snapshot has been ingested, the served JPEG carries the
        nosniff header alongside the existing Cache-Control."""
        node_id = "hardening_node_01"

        post_response = client.post(
            f"/api/edge/{node_id}/snapshot",
            content=FAKE_JPEG,
            headers={**api_headers, "Content-Type": "image/jpeg"},
        )
        assert post_response.status_code == 204

        get_response = client.get(
            f"/api/edge/{node_id}/snapshot/latest", headers=api_headers
        )

        assert get_response.status_code == 200
        assert get_response.headers.get("x-content-type-options") == "nosniff"
        # Sanity: the header we already ship stays in place.
        assert "cache-control" in get_response.headers

    def test_nosniff_on_placeholder(self, client, api_headers):
        """When no snapshot exists for a node, the placeholder branch also
        emits the nosniff header — a hostile edge cannot skip protection
        simply by never uploading."""
        response = client.get(
            "/api/edge/hardening_ghost_node/snapshot/latest",
            headers=api_headers,
        )
        assert response.status_code == 200
        assert response.headers.get("x-snapshot-status") == "placeholder"
        assert response.headers.get("x-content-type-options") == "nosniff"


# ----- Storage-E: EXIF is stripped at ingest --------------------------------

def _build_exif_jpeg() -> bytes:
    """Build a real JPEG that contains a recognisable EXIF APP1 segment
    with the 'Exif\\x00\\x00' magic plus a synthetic GPS-shaped marker
    string. We look for both markers after round-trip to confirm the
    strip actually removed the APP1 payload."""
    from PIL import Image

    img = Image.new("RGB", (32, 32), color=(200, 20, 20))
    buf = io.BytesIO()
    # 'Exif\x00\x00' is the APP1 identifier. The remainder is a minimal but
    # syntactically legal TIFF header (II = little-endian, magic 42, offset
    # 8) followed by a distinctive sentinel we can later grep for.
    exif_bytes = b"Exif\x00\x00II*\x00\x08\x00\x00\x00SDPRS_GPS_SECRET_MARKER"
    img.save(buf, format="JPEG", exif=exif_bytes)
    return buf.getvalue()


class TestExifStrip:
    """receive_snapshot must best-effort strip EXIF before storing."""

    @pytest.mark.skipif(not _HAS_PILLOW, reason="Pillow not installed")
    def test_exif_is_removed_after_roundtrip(self, client, api_headers):
        """POST a JPEG carrying an EXIF APP1 segment; GET it back and
        confirm the EXIF magic + our sentinel string are gone."""
        raw = _build_exif_jpeg()
        # Precondition: the raw bytes we constructed actually contain the
        # marker — otherwise this test would trivially pass.
        assert b"Exif\x00\x00" in raw
        assert b"SDPRS_GPS_SECRET_MARKER" in raw

        node_id = "hardening_exif_node"
        post_response = client.post(
            f"/api/edge/{node_id}/snapshot",
            content=raw,
            headers={**api_headers, "Content-Type": "image/jpeg"},
        )
        assert post_response.status_code == 204

        get_response = client.get(
            f"/api/edge/{node_id}/snapshot/latest", headers=api_headers
        )
        assert get_response.status_code == 200
        served = get_response.content

        # The re-encoded JPEG must still be a JPEG (SOI marker) but neither
        # the EXIF magic nor our sentinel string may survive.
        assert served.startswith(b"\xff\xd8")
        assert b"Exif\x00\x00" not in served, "EXIF APP1 segment leaked"
        assert b"SDPRS_GPS_SECRET_MARKER" not in served, "EXIF payload leaked"

    @pytest.mark.skipif(not _HAS_PILLOW, reason="Pillow not installed")
    def test_strip_exif_falls_back_when_pillow_raises(self):
        """_strip_exif is a best-effort helper: if Pillow itself raises
        (e.g. corrupt JPEG), the original bytes must be returned so ingest
        never fails on metadata scrubbing alone."""
        payload = b"not a real jpeg but must pass through"

        def _boom(*args, **kwargs):
            raise ValueError("simulated corrupt-image error")

        with patch("PIL.Image.open", side_effect=_boom):
            result = _strip_exif(payload)

        assert result == payload

    def test_strip_exif_falls_back_when_pillow_missing(self):
        """When PIL is not importable at all, _strip_exif must still return
        the original bytes rather than raising. We simulate the missing
        module by making the `from PIL import Image` line inside the helper
        blow up via a sys.modules stub."""
        payload = FAKE_JPEG

        # Save any existing PIL entries so we can restore them cleanly.
        saved = {k: v for k, v in sys.modules.items() if k == "PIL" or k.startswith("PIL.")}

        class _Boom:
            def __getattr__(self, name):
                raise ImportError("Pillow deliberately hidden for this test")

        # Purge and replace PIL with a stub that fails on any submodule
        # access; `from PIL import Image` translates to attribute lookup on
        # the PIL package object, which will raise ImportError below.
        for key in list(sys.modules):
            if key == "PIL" or key.startswith("PIL."):
                del sys.modules[key]
        sys.modules["PIL"] = _Boom()

        try:
            result = _strip_exif(payload)
        finally:
            # Restore the real PIL modules for downstream tests.
            del sys.modules["PIL"]
            for key, mod in saved.items():
                sys.modules[key] = mod

        assert result == payload


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
