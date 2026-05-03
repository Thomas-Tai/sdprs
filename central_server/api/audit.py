# -*- coding: utf-8 -*-
# SDPRS Central Server - Audit log API (item 15)

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

from ..services.audit_service import list_actions

logger = logging.getLogger("api.audit")

router = APIRouter()


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


__all__ = ["router"]
