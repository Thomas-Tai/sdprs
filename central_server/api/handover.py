# -*- coding: utf-8 -*-
# SDPRS Central Server - Shift Handover Note (item 16)
#
# Single global note (id=1, last-write-wins). Auto-clears after 24hr by
# the read-time check in get_handover_note() — we don't run a background
# job for this; cheap enough to compute on read.

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..database import get_db_cursor
from ..timeutil import utcnow
from ..services.audit_service import log_action, ACTION_HANDOVER_EDIT

logger = logging.getLogger("api.handover")

router = APIRouter()

NOTE_TTL_HOURS = 24


class HandoverNotePayload(BaseModel):
    note: str = Field(default="", max_length=2000)


def _read_row() -> Optional[Dict[str, Any]]:
    with get_db_cursor() as cur:
        cur.execute("SELECT note, author, updated_at FROM handover_note WHERE id = 1;")
        r = cur.fetchone()
    if r is None:
        return None
    return {"note": r["note"] or "", "author": r["author"], "updated_at": r["updated_at"]}


def _is_expired(updated_at: Optional[str]) -> bool:
    if not updated_at:
        return False
    try:
        ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return False
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    return utcnow() - ts > timedelta(hours=NOTE_TTL_HOURS)


@router.get("/handover/note")
async def get_handover_note(
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    row = _read_row() or {"note": "", "author": None, "updated_at": None}
    expired = _is_expired(row["updated_at"])
    return {
        "note": "" if expired else row["note"],
        "author": None if expired else row["author"],
        "updated_at": row["updated_at"],
        "expired": expired,
    }


@router.put("/handover/note")
async def put_handover_note(
    request: Request,
    payload: HandoverNotePayload,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    note = payload.note.strip()
    with get_db_cursor() as cur:
        cur.execute(
            "UPDATE handover_note SET note = ?, author = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1;",
            (note, user),
        )
    log_action(user, ACTION_HANDOVER_EDIT, target_id=None, details={"len": len(note)})
    return {"ok": True, "note": note, "author": user}


__all__ = ["router"]
