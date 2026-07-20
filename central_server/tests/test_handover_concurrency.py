# -*- coding: utf-8 -*-
"""
Route-level tests for the WHA-M8 handover optimistic-concurrency fix.

The DB layer (get_effective_handover_note / set_handover_note) is already
covered by test_dual_backend_dispatch.py. What had NO coverage was the new
409 decision logic in PUT /handover/note itself — the branch that rejects a
save whose `expected_updated_at` no longer matches the stored version. These
tests execute that branch directly through a TestClient, mocking only the DB
helpers so no real database is needed.

Covers:
- mismatch  -> 409 with {detail, current, updated_at}, and NO save performed
- match     -> save proceeds, returns the new updated_at
- omitted   -> backward-compatible last-write-wins save (older clients)
- GET       -> surfaces updated_at
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from central_server.api import handover as handover_api
from central_server.auth import get_current_user


@pytest.fixture
def saved():
    """Records every set_handover_note(...) call the route performs."""
    return []


@pytest.fixture
def stored():
    """The server's 'currently stored' handover row, mutable per test."""
    return {
        "note": "伺服器目前的交接內容",
        "author": "bob",
        "updated_at": "2026-07-20T10:00:00",
        "expired": False,
    }


@pytest.fixture
def client(monkeypatch, saved, stored):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-testing")
    app.include_router(handover_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: "test_user"

    monkeypatch.setattr(
        handover_api, "get_effective_handover_note",
        lambda ttl_hours=24: dict(stored),
    )
    monkeypatch.setattr(
        handover_api, "set_handover_note",
        lambda note, author, updated_at: saved.append(
            {"note": note, "author": author, "updated_at": updated_at}
        ),
    )
    monkeypatch.setattr(handover_api, "log_action", lambda *a, **k: None)

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_mismatch_returns_409_with_server_state_and_does_not_save(client, saved, stored):
    """Stale expected_updated_at -> 409, body carries the server's current
    note + updated_at, and crucially the save is NOT performed."""
    resp = client.put(
        "/api/handover/note",
        json={"note": "我的草稿", "expected_updated_at": "2026-07-20T09:00:00"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["current"] == stored["note"]
    assert body["updated_at"] == stored["updated_at"]
    assert "detail" in body
    assert saved == []  # nothing clobbered


def test_matching_token_saves_and_returns_new_updated_at(client, saved, stored):
    """expected_updated_at == stored -> save proceeds with a fresh token."""
    resp = client.put(
        "/api/handover/note",
        json={"note": "已對齊的更新", "expected_updated_at": stored["updated_at"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["note"] == "已對齊的更新"
    # A new token was minted and differs from the one we echoed back.
    assert body["updated_at"] and body["updated_at"] != stored["updated_at"]
    assert len(saved) == 1
    assert saved[0]["note"] == "已對齊的更新"
    assert saved[0]["updated_at"] == body["updated_at"]


def test_omitted_token_is_backward_compatible_last_write_wins(client, saved, stored):
    """An older client that never sends expected_updated_at still saves,
    regardless of the stored version (no 409)."""
    resp = client.put("/api/handover/note", json={"note": "舊版用戶端的儲存"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(saved) == 1
    assert saved[0]["note"] == "舊版用戶端的儲存"


def test_null_token_also_backward_compatible(client, saved):
    """Explicit null is treated the same as omitted (not a mismatch)."""
    resp = client.put(
        "/api/handover/note",
        json={"note": "顯式 null", "expected_updated_at": None},
    )
    assert resp.status_code == 200
    assert len(saved) == 1


def test_get_surfaces_updated_at(client, stored):
    resp = client.get("/api/handover/note")
    assert resp.status_code == 200
    assert resp.json()["updated_at"] == stored["updated_at"]
