# -*- coding: utf-8 -*-
"""DATA-003 (Medium): the audit CSV export must NOT impose a UTC-dated filename.

The SPA (api.jsx `exportAuditCsv`, SHL-16) sets `a.download = audit_<LOCALDATE>.csv`
and triggers a plain anchor click. A `Content-Disposition: attachment; filename=`
header overrides the anchor's `download` attribute, so the backend's UTC-stamped
`audit_YYYYMMDD.csv` silently won — for Macau's 00:00-08:00 night shift the UTC
date is still YESTERDAY, filing an incident export under the wrong day and making
the SHL-16 fix dead code. The fix drops the server-imposed filename (bare
`attachment`), letting the SPA's correct local-date name govern; direct/curl hits
fall back to the URL basename `export.csv` instead of a wrong date.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")


@pytest.fixture
def client(monkeypatch):
    import central_server.api.audit as audit
    from central_server.config import get_settings

    # No DB: the export just needs some rows; an empty set is enough to exercise
    # the header/encoding contract.
    monkeypatch.setattr(audit, "list_actions", lambda **kw: [])
    # Satisfy the admin gate without the login dance — return exactly the
    # configured DASHBOARD_USER so `user == get_settings().DASHBOARD_USER`. Auth
    # itself has its own suites; this test targets the response headers.
    admin_user = get_settings().DASHBOARD_USER
    monkeypatch.setattr(audit, "_require_session", lambda request: admin_user)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-testing")
    app.include_router(audit.router, prefix="/api")
    with TestClient(app) as c:
        yield c


def test_export_csv_does_not_impose_a_dated_filename(client):
    r = client.get("/api/audit/export.csv")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    # Still forces a download...
    assert cd.strip().lower().startswith("attachment"), cd
    # ...but must NOT pin a server-side filename, which would override the SPA's
    # local-date `download` attribute (the DATA-003 bug).
    assert "filename" not in cd.lower(), cd


def test_export_csv_still_downloads_as_csv(client):
    r = client.get("/api/audit/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers.get("x-content-type-options") == "nosniff"
    # UTF-8 BOM + header row still present (unchanged contract).
    assert r.text.startswith("﻿")
    assert "action_type" in r.text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
