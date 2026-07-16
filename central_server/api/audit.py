# -*- coding: utf-8 -*-
# SDPRS Central Server - Audit log API (item 15)

import csv
import io
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..services.audit_service import list_actions
from ..timeutil import utcnow

logger = logging.getLogger("api.audit")

router = APIRouter()

# Hard clamp on CSV export size (dashboard-audit-2026-07-15 frozen contract):
# a mis-typed ?limit=1e9 would OOM the process, so silently clamp instead of
# raising — the browser still gets a valid CSV of the newest 10 000 rows.
_AUDIT_CSV_HARD_CAP = 10000


def _require_session(request: Request) -> str:
    user = request.session.get("user") if hasattr(request, "session") else None
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@router.get("/audit")
async def list_audit(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    operator: Optional[str] = None,
    action_type: Optional[str] = None,
) -> Dict[str, Any]:
    # Admin-only check is intentionally simple for MVP: only the configured
    # DASHBOARD_USER may read. A real RBAC layer is future work.
    user = _require_session(request)
    from ..config import get_settings
    if user != get_settings().DASHBOARD_USER:
        raise HTTPException(status_code=403, detail="Admin only")

    rows = list_actions(
        limit=min(max(int(limit), 1), 500),
        offset=max(int(offset), 0),
        operator=operator,
        action_type=action_type,
    )
    return {"rows": rows, "limit": limit, "offset": offset}


@router.get("/audit/export.csv")
async def export_audit_csv(
    request: Request,
    limit: int = 1000,
    type: Optional[str] = None,
) -> Response:
    """CSV export of the audit log (dashboard-audit-2026-07-15 frozen contract).

    Same admin-only gate as GET /api/audit (DASHBOARD_USER only for MVP; a
    real RBAC layer is future work). `limit` is clamped to
    _AUDIT_CSV_HARD_CAP silently (a mis-typed 1e9 would OOM the process).
    `type` filters to a single action_type when given.

    Encoding: UTF-8 with BOM so Excel opens Traditional-Chinese details
    correctly out of the box; media type declares charset=utf-8 explicitly.
    Content-Disposition names the file audit_YYYYMMDD.csv; X-Content-Type-Options
    nosniff prevents Excel/IE MIME-sniff overrides.
    """
    user = _require_session(request)
    from ..config import get_settings
    if user != get_settings().DASHBOARD_USER:
        raise HTTPException(status_code=403, detail="Admin only")

    # Silent clamp — see _AUDIT_CSV_HARD_CAP comment for rationale.
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 1000
    n = min(max(n, 1), _AUDIT_CSV_HARD_CAP)

    rows = list_actions(limit=n, action_type=type)

    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM so Excel autodetects UTF-8 for CJK details
    writer = csv.writer(buf)
    writer.writerow(["id", "timestamp", "operator", "action_type", "target_id", "details"])
    for r in rows:
        details = r.get("details")
        # Serialize details dict as compact JSON so a single spreadsheet cell
        # captures the payload; ensure_ascii=False preserves Traditional Chinese.
        if details is None:
            details_str = ""
        elif isinstance(details, str):
            details_str = details
        else:
            try:
                details_str = json.dumps(details, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                details_str = str(details)
        writer.writerow([
            r.get("id", ""),
            r.get("timestamp", ""),
            r.get("operator", ""),
            r.get("action_type", ""),
            r.get("target_id") or "",
            details_str,
        ])

    filename = f"audit_{utcnow().strftime('%Y%m%d')}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["router"]
