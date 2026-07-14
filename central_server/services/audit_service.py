# -*- coding: utf-8 -*-
# SDPRS Central Server - Operator Audit Log (item 15)
#
# Append-only log of operator actions (login, ack, resolve, snooze,
# location-edit, bulk-resolve). Intentionally tolerant: any failure to
# log MUST NOT break the operator action — we log a warning and move on.

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..database import get_db_cursor, get_backend

logger = logging.getLogger("audit_service")

# Action-type constants. Defining them here keeps callers from drifting.
ACTION_LOGIN          = "LOGIN"
ACTION_LOGOUT         = "LOGOUT"
ACTION_ACKNOWLEDGE    = "ACKNOWLEDGE"
ACTION_RESOLVE        = "RESOLVE"
ACTION_BULK_RESOLVE   = "BULK_RESOLVE"
ACTION_SNOOZE         = "SNOOZE"
ACTION_UNSNOOZE       = "UNSNOOZE"
ACTION_LOCATION_EDIT  = "LOCATION_EDIT"
ACTION_HANDOVER_EDIT  = "HANDOVER_EDIT"


def log_action(
    operator: str,
    action_type: str,
    target_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a single audit-log row. Never raises."""
    try:
        details_json = json.dumps(details, ensure_ascii=False) if details else None
        # Dispatch at call time: get_db_cursor() is SQLite-only and raises
        # under PostgreSQL, which this try/except used to swallow — silently
        # losing the entire audit trail on the PG backend.
        if get_backend() == "postgresql":
            _pg_log_action_sync(
                operator or "",
                action_type,
                str(target_id) if target_id is not None else None,
                details_json,
            )
            return
        with get_db_cursor() as cur:
            cur.execute(
                "INSERT INTO operator_actions (operator, action_type, target_id, details_json) "
                "VALUES (?, ?, ?, ?);",
                (operator or "", action_type, str(target_id) if target_id is not None else None, details_json),
            )
    except Exception as e:
        logger.warning(f"Audit log write failed (action={action_type}, op={operator}): {e}")


def list_actions(
    limit: int = 100,
    offset: int = 0,
    operator: Optional[str] = None,
    action_type: Optional[str] = None,
    since: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return audit rows newest-first. Filters are AND-combined."""
    # PG branch dispatched up front so the SQLite path below stays untouched;
    # same tolerant contract (warn + return [] on failure).
    if get_backend() == "postgresql":
        try:
            return _pg_list_actions_sync(limit, offset, operator, action_type, since)
        except Exception as e:
            logger.warning(f"Audit log read failed: {e}")
            return []
    where = []
    params: List[Any] = []
    if operator:
        where.append("operator = ?")
        params.append(operator)
    if action_type:
        where.append("action_type = ?")
        params.append(action_type)
    if since is not None:
        where.append("timestamp >= ?")
        params.append(since.isoformat())
    sql = "SELECT id, timestamp, operator, action_type, target_id, details_json FROM operator_actions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?;"
    params.extend([int(limit), int(offset)])
    try:
        with get_db_cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            details = None
            if r["details_json"]:
                try:
                    details = json.loads(r["details_json"])
                except (ValueError, TypeError):
                    details = {"_raw": r["details_json"]}
            out.append({
                "id": r["id"],
                "timestamp": r["timestamp"],
                "operator": r["operator"],
                "action_type": r["action_type"],
                "target_id": r["target_id"],
                "details": details,
            })
        return out
    except Exception as e:
        logger.warning(f"Audit log read failed: {e}")
        return []


# =============================================================================
# PostgreSQL sync mirrors (throwaway engine + :named params, same idiom as
# database.py's _pg_*_sync helpers; acceptable for MVP)
# =============================================================================

def _pg_log_action_sync(operator, action_type, target_id, details_json) -> None:
    import sqlalchemy
    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL", ""))
    with engine.connect() as conn:
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO operator_actions (operator, action_type, target_id, details_json) "
                "VALUES (:operator, :action_type, :target_id, :details_json)"
            ),
            {"operator": operator, "action_type": action_type,
             "target_id": target_id, "details_json": details_json},
        )
        conn.commit()


def _pg_list_actions_sync(
    limit: int,
    offset: int,
    operator: Optional[str],
    action_type: Optional[str],
    since: Optional[datetime],
) -> List[Dict[str, Any]]:
    import sqlalchemy
    where = []
    params: Dict[str, Any] = {"limit": int(limit), "offset": int(offset)}
    if operator:
        where.append("operator = :operator")
        params["operator"] = operator
    if action_type:
        where.append("action_type = :action_type")
        params["action_type"] = action_type
    if since is not None:
        where.append("timestamp >= :since")
        params["since"] = since.isoformat()
    sql = "SELECT id, timestamp, operator, action_type, target_id, details_json FROM operator_actions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT :limit OFFSET :offset"

    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL", ""))
    with engine.connect() as conn:
        result = conn.execute(sqlalchemy.text(sql), params)
        rows = [dict(r) for r in result.mappings().fetchall()]

    out: List[Dict[str, Any]] = []
    for r in rows:
        details = None
        if r["details_json"]:
            try:
                details = json.loads(r["details_json"])
            except (ValueError, TypeError):
                details = {"_raw": r["details_json"]}
        out.append({
            "id": r["id"],
            "timestamp": r["timestamp"],
            "operator": r["operator"],
            "action_type": r["action_type"],
            "target_id": r["target_id"],
            "details": details,
        })
    return out


__all__ = [
    "log_action", "list_actions",
    "ACTION_LOGIN", "ACTION_LOGOUT",
    "ACTION_ACKNOWLEDGE", "ACTION_RESOLVE", "ACTION_BULK_RESOLVE",
    "ACTION_SNOOZE", "ACTION_UNSNOOZE",
    "ACTION_LOCATION_EDIT", "ACTION_HANDOVER_EDIT",
]
