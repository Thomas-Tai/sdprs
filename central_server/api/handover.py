# -*- coding: utf-8 -*-
# SDPRS Central Server - Shift Handover Note (item 16)
#
# Single global note (id=1, last-write-wins). Auto-clears after 24hr by
# the read-time check in get_handover_note() — we don't run a background
# job for this; cheap enough to compute on read.
#
# All DB access goes through the dual-backend helpers in database.py
# (set_handover_note / get_effective_handover_note): the old direct
# get_db_cursor() + "?" placeholders path crashed under PostgreSQL,
# where get_db() raises.

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..database import get_effective_handover_note, set_handover_note
from ..timeutil import utcnow
from ..services.audit_service import log_action, ACTION_HANDOVER_EDIT

logger = logging.getLogger("api.handover")

router = APIRouter()

NOTE_TTL_HOURS = 24


class HandoverNotePayload(BaseModel):
    note: str = Field(default="", max_length=2000)
    # WHA-M8 optimistic concurrency: the client echoes back the updated_at
    # it last saw from GET /handover/note. Omitted/null preserves the old
    # last-write-wins behavior for clients that haven't been updated yet.
    expected_updated_at: Optional[str] = Field(default=None)


@router.get("/handover/note")
async def get_handover_note(
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    row = get_effective_handover_note(ttl_hours=NOTE_TTL_HOURS)
    if row is None:
        row = {"note": "", "author": None, "updated_at": None, "expired": False}
    expired = row["expired"]
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

    # WHA-M8: optimistic concurrency check. Older clients never send
    # expected_updated_at, so they fall straight through to the
    # last-write-wins save below (unchanged behavior). Clients that do
    # send it are asserting "I last saw this version" — if the stored
    # updated_at has since moved on, someone else saved in between and we
    # reject with 409 rather than silently clobbering their edit. The 409
    # body carries the server's current note + updated_at so the client
    # can render a conflict diff.
    if payload.expected_updated_at is not None:
        current = get_effective_handover_note(ttl_hours=NOTE_TTL_HOURS)
        if current is None:
            current = {"note": "", "author": None, "updated_at": None, "expired": False}
        current_updated_at = current["updated_at"]
        if payload.expected_updated_at != current_updated_at:
            current_note = "" if current["expired"] else current["note"]
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Handover note was changed by another operator",
                    "current": current_note,
                    "updated_at": current_updated_at,
                },
            )

    # Timestamp stamped here (naive-UTC isoformat) rather than SQL
    # CURRENT_TIMESTAMP so SQLite and PostgreSQL store the same shape.
    new_updated_at = utcnow().isoformat()
    set_handover_note(note, user, new_updated_at)
    log_action(user, ACTION_HANDOVER_EDIT, target_id=None, details={"len": len(note)})
    return {"ok": True, "note": note, "author": user, "updated_at": new_updated_at}


__all__ = ["router"]
