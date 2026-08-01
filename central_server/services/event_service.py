# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Event Service
Smart Disaster Prevention Response System

This module provides CRUD operations for event/alert management.
"""

import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

from ..database import get_backend, get_db
from ..timeutil import utcnow

# Configure logging
logger = logging.getLogger("event_service")


def list_events(
    db: Optional[sqlite3.Connection] = None,
    status_filter: Optional[str] = None,
    node_filter: Optional[str] = None,
    page: int = 1,
    page_size: int = 20
) -> Dict[str, Any]:
    """
    List events with optional filtering and pagination.

    Args:
        db: SQLite connection (optional; fetched via get_db() when None and the
            active backend is SQLite). Ignored under PostgreSQL.
        status_filter: Filter by status (PENDING_VIDEO, PENDING, RESOLVED)
        node_filter: Filter by node_id
        page: Page number (1-indexed)
        page_size: Number of items per page

    Returns:
        Dict with items, total, page, page_size
    """
    if get_backend() == "postgresql":
        return _list_events_pg(status_filter, node_filter, page, page_size)

    if db is None:
        db = get_db()
    cursor = db.cursor()

    # Build query
    conditions = []
    params = []

    if status_filter:
        # Support comma-separated multi-status filter (Sprint A item 5):
        #   ?status=PENDING_VIDEO,PENDING,ACKNOWLEDGED  -> active-only view
        #   ?status=PENDING                              -> single status (legacy form)
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
        if len(statuses) == 1:
            conditions.append("status = ?")
            params.append(statuses[0])
        elif len(statuses) > 1:
            placeholders = ",".join("?" for _ in statuses)
            conditions.append(f"status IN ({placeholders})")
            params.extend(statuses)

    if node_filter:
        conditions.append("node_id = ?")
        params.append(node_filter)
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    # Get total count
    count_query = f"SELECT COUNT(*) FROM events WHERE {where_clause}"
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]
    
    # Get paginated results
    offset = (page - 1) * page_size
    query = f"""
        SELECT * FROM events 
        WHERE {where_clause}
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
    """
    cursor.execute(query, params + [page_size, offset])
    
    rows = cursor.fetchall()
    items = [dict(row) for row in rows]
    
    total_pages = (total + page_size - 1) // page_size

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages
    }


def _list_events_pg(
    status_filter: Optional[str],
    node_filter: Optional[str],
    page: int,
    page_size: int,
) -> Dict[str, Any]:
    """PostgreSQL branch of list_events (throwaway-engine idiom, :named params)."""
    import sqlalchemy
    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL", ""))

    conditions = []
    params: Dict[str, Any] = {}
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
        if len(statuses) == 1:
            conditions.append("status = :status")
            params["status"] = statuses[0]
        elif len(statuses) > 1:
            names = []
            for i, s in enumerate(statuses):
                key = f"status{i}"
                params[key] = s
                names.append(f":{key}")
            conditions.append(f"status IN ({','.join(names)})")
    if node_filter:
        conditions.append("node_id = :node_id")
        params["node_id"] = node_filter

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    offset = (page - 1) * page_size
    with engine.connect() as conn:
        total = conn.execute(
            sqlalchemy.text(f"SELECT COUNT(*) FROM events WHERE {where_clause}"),
            params,
        ).scalar() or 0
        rows = conn.execute(
            sqlalchemy.text(
                f"SELECT * FROM events WHERE {where_clause} "
                "ORDER BY timestamp DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": page_size, "offset": offset},
        ).mappings().fetchall()

    items = [dict(r) for r in rows]
    total_pages = (total + page_size - 1) // page_size
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def acknowledge_event(
    db: Optional[sqlite3.Connection] = None,
    *,
    alert_id: int,
    acknowledged_by: str,
) -> Optional[Dict[str, Any]]:
    """
    Mark an event as ACKNOWLEDGED — operator is on it but hasn't completed handling yet.
    Distinct from RESOLVED. Suppresses repeating audio (item 3) but keeps the event
    visible in the active list. Idempotent: re-ack by same operator is a no-op.

    ``db`` is optional (fetched via get_db() when None under SQLite; ignored
    under PostgreSQL). ``alert_id``/``acknowledged_by`` are keyword-only.

    DATA-002 fix (2026-08-01): the UPDATE itself carries ``AND status =
    'PENDING'`` — mirroring bulk_acknowledge_events — so the atomicity lives
    in the single UPDATE statement's WHERE clause + rowcount, not in a
    separate SELECT-then-check. The previous version SELECTed the status,
    checked it in Python, and only THEN ran an UPDATE matching solely on
    ``id`` (no status predicate). Two callers who both read PENDING before
    either committed would both pass that Python check and both UPDATE
    unconditionally, so whichever committed second silently overwrote the
    first caller's acknowledged_by/acknowledged_at. Matching on status
    inside the UPDATE closes the window: only the UPDATE that lands while
    the row is still PENDING can match any rows at all.

    Returns the updated event dict on success, or None if not found / the
    row was no longer PENDING by the time the UPDATE ran (caller should
    treat None as "re-check current status" — typically a 404 or 409).
    """
    if get_backend() == "postgresql":
        return _acknowledge_event_pg(alert_id, acknowledged_by)

    if db is None:
        db = get_db()
    cursor = db.cursor()

    acked_at = utcnow().isoformat()
    cursor.execute("""
        UPDATE events SET
            status = 'ACKNOWLEDGED',
            acknowledged_by = ?,
            acknowledged_at = ?
        WHERE id = ? AND status = 'PENDING'
    """, (acknowledged_by, acked_at, alert_id))
    updated = cursor.rowcount
    db.commit()

    if not updated:
        logger.warning(
            f"Event {alert_id} acknowledge no-op (not found, or status was "
            "no longer PENDING when the UPDATE ran)"
        )
        return None

    logger.info(f"Event {alert_id} acknowledged by {acknowledged_by}")

    return {
        "alert_id": alert_id,
        "acknowledged_by": acknowledged_by,
        "acknowledged_at": acked_at,
    }


def _acknowledge_event_pg(alert_id: int, acknowledged_by: str) -> Optional[Dict[str, Any]]:
    """PostgreSQL branch of acknowledge_event (throwaway-engine idiom).

    DATA-002 fix: same atomic ``WHERE id = :id AND status = 'PENDING'`` +
    rowcount check as the SQLite branch — see acknowledge_event docstring.
    """
    import sqlalchemy
    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL", ""))
    acked_at = utcnow().isoformat()
    with engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text(
                "UPDATE events SET status = 'ACKNOWLEDGED', "
                "acknowledged_by = :by, acknowledged_at = :at "
                "WHERE id = :id AND status = 'PENDING'"
            ),
            {"by": acknowledged_by, "at": acked_at, "id": alert_id},
        )
        conn.commit()
    if not result.rowcount:
        logger.warning(
            f"Event {alert_id} acknowledge no-op (not found, or status was "
            "no longer PENDING when the UPDATE ran) (PostgreSQL)"
        )
        return None
    logger.info(f"Event {alert_id} acknowledged by {acknowledged_by}")
    return {
        "alert_id": alert_id,
        "acknowledged_by": acknowledged_by,
        "acknowledged_at": acked_at,
    }


def resolve_event(
    db: Optional[sqlite3.Connection] = None,
    *,
    alert_id: int,
    resolved_by: str,
    notes: Optional[str] = None
) -> bool:
    """
    Mark an event as resolved.

    Args:
        db: SQLite connection (optional; fetched via get_db() when None under
            SQLite; ignored under PostgreSQL).
        alert_id: The event/alert ID (keyword-only)
        resolved_by: Username who resolved the event (keyword-only)
        notes: Optional resolution notes

    DATA-002 fix (2026-08-01): the UPDATE itself carries ``AND status IN
    ('PENDING', 'ACKNOWLEDGED')`` — mirroring bulk_resolve_events — so
    atomicity lives in the UPDATE's WHERE clause + rowcount, not in a
    separate SELECT-then-check. See acknowledge_event's docstring for the
    full race this closes: two callers who both read a resolvable status
    before either committed used to both pass the Python check and both
    UPDATE unconditionally, letting the second commit silently overwrite
    the first caller's resolved_by/resolved_at/notes.

    Returns:
        True if successful, False if the event was not found or the row was
        no longer PENDING/ACKNOWLEDGED by the time the UPDATE ran (caller
        should re-check current status — typically a 404 or 409).
    """
    if get_backend() == "postgresql":
        return _resolve_event_pg(alert_id, resolved_by, notes)

    if db is None:
        db = get_db()
    cursor = db.cursor()

    resolved_at = utcnow().isoformat()
    cursor.execute("""
        UPDATE events SET
            status = 'RESOLVED',
            resolved_by = ?,
            resolved_at = ?,
            notes = COALESCE(?, notes)
        WHERE id = ? AND status IN ('PENDING', 'ACKNOWLEDGED')
    """, (resolved_by, resolved_at, notes, alert_id))
    updated = cursor.rowcount
    db.commit()

    if not updated:
        logger.warning(
            f"Event {alert_id} resolve no-op (not found, or status was no "
            "longer PENDING/ACKNOWLEDGED when the UPDATE ran)"
        )
        return False

    logger.info(f"Event {alert_id} resolved by {resolved_by}")
    return True


def _resolve_event_pg(alert_id: int, resolved_by: str, notes: Optional[str]) -> bool:
    """PostgreSQL branch of resolve_event (throwaway-engine idiom).

    DATA-002 fix: same atomic ``WHERE id = :id AND status IN (...)`` +
    rowcount check as the SQLite branch — see resolve_event docstring.
    """
    import sqlalchemy
    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL", ""))
    resolved_at = utcnow().isoformat()
    with engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text(
                "UPDATE events SET status = 'RESOLVED', resolved_by = :by, "
                "resolved_at = :at, notes = COALESCE(:notes, notes) "
                "WHERE id = :id AND status IN ('PENDING', 'ACKNOWLEDGED')"
            ),
            {"by": resolved_by, "at": resolved_at, "notes": notes, "id": alert_id},
        )
        conn.commit()
    if not result.rowcount:
        logger.warning(
            f"Event {alert_id} resolve no-op (not found, or status was no "
            "longer PENDING/ACKNOWLEDGED when the UPDATE ran) (PostgreSQL)"
        )
        return False
    logger.info(f"Event {alert_id} resolved by {resolved_by}")
    return True


def _event_counts_rows_pg() -> List[Dict[str, Any]]:
    """PostgreSQL branch of get_event_counts: grouped (status, count) rows."""
    import sqlalchemy
    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL", ""))
    with engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text("SELECT status, COUNT(*) AS count FROM events GROUP BY status")
        )
        return [dict(r) for r in result.mappings().fetchall()]


def get_event_counts(db: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
    """
    Get counts of events by status.

    Args:
        db: SQLite connection (optional; fetched via get_db() when None under
            SQLite; ignored under PostgreSQL).

    Returns:
        Dict with pending_video, pending, resolved, total counts
    """
    if get_backend() == "postgresql":
        rows = _event_counts_rows_pg()
    else:
        if db is None:
            db = get_db()
        cursor = db.cursor()
        # Get counts by status
        cursor.execute("""
            SELECT
                status,
                COUNT(*) as count
            FROM events
            GROUP BY status
        """)
        rows = cursor.fetchall()

    counts = {
        "pending_video": 0,
        "pending": 0,
        "acknowledged": 0,
        "resolved": 0,
        "total": 0
    }

    for row in rows:
        status = row["status"].lower()
        if status == "pending_video":
            counts["pending_video"] = row["count"]
        elif status == "pending":
            counts["pending"] = row["count"]
        elif status == "acknowledged":
            counts["acknowledged"] = row["count"]
        elif status == "resolved":
            counts["resolved"] = row["count"]

    counts["total"] = sum([
        counts["pending_video"],
        counts["pending"],
        counts["acknowledged"],
        counts["resolved"]
    ])

    return counts


def bulk_acknowledge_events(
    alert_ids: List[int],
    acknowledged_by: str,
    notes: Optional[str] = None,
) -> int:
    """Flip a batch of events from PENDING to ACKNOWLEDGED in one UPDATE.

    Contract mirrors bulk_resolve_events. Returns the number of rows actually
    mutated (i.e. rows whose status was PENDING at the moment of the UPDATE);
    ids already ACKNOWLEDGED/RESOLVED/PENDING_VIDEO are silently skipped by
    the WHERE clause. Attribution and timestamp are stamped server-side.

    ``notes=COALESCE(:notes, notes)`` — provided notes overwrite existing;
    None keeps the row's existing notes untouched.
    """
    if not alert_ids:
        return 0
    acked_at = utcnow().isoformat()
    if get_backend() == "postgresql":
        return _bulk_acknowledge_events_pg(alert_ids, acknowledged_by, acked_at, notes)

    db = get_db()
    cursor = db.cursor()
    placeholders = ",".join("?" for _ in alert_ids)
    cursor.execute(
        f"UPDATE events SET status = 'ACKNOWLEDGED', "
        f"acknowledged_by = ?, acknowledged_at = ?, "
        f"notes = COALESCE(?, notes) "
        f"WHERE id IN ({placeholders}) AND status = 'PENDING'",
        (acknowledged_by, acked_at, notes, *alert_ids),
    )
    count = cursor.rowcount
    db.commit()
    logger.info(f"Bulk-acknowledged {count} events by {acknowledged_by}")
    return count


def _bulk_acknowledge_events_pg(
    alert_ids: List[int], acknowledged_by: str, acked_at: str, notes: Optional[str]
) -> int:
    """PostgreSQL branch of bulk_acknowledge_events (id = ANY(:ids) list binding)."""
    import sqlalchemy
    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL", ""))
    with engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text(
                "UPDATE events SET status = 'ACKNOWLEDGED', "
                "acknowledged_by = :by, acknowledged_at = :at, "
                "notes = COALESCE(:notes, notes) "
                "WHERE id = ANY(:ids) AND status = 'PENDING'"
            ),
            {"by": acknowledged_by, "at": acked_at, "notes": notes, "ids": list(alert_ids)},
        )
        conn.commit()
    count = result.rowcount or 0
    logger.info(f"Bulk-acknowledged {count} events by {acknowledged_by} (PostgreSQL)")
    return count


def bulk_resolve_events(
    alert_ids: List[int],
    resolved_by: str,
    notes: Optional[str] = None,
) -> int:
    """Flip a batch of events from PENDING/ACKNOWLEDGED to RESOLVED in one UPDATE.

    Returns the number of rows actually mutated; already-RESOLVED and
    PENDING_VIDEO rows are silently skipped by the WHERE predicate. Attribution
    and timestamp are stamped server-side (never taken from the request body).
    """
    if not alert_ids:
        return 0
    resolved_at = utcnow().isoformat()
    if get_backend() == "postgresql":
        return _bulk_resolve_events_pg(alert_ids, resolved_by, resolved_at, notes)

    db = get_db()
    cursor = db.cursor()
    placeholders = ",".join("?" for _ in alert_ids)
    cursor.execute(
        f"UPDATE events SET status = 'RESOLVED', "
        f"resolved_by = ?, resolved_at = ?, "
        f"notes = COALESCE(?, notes) "
        f"WHERE id IN ({placeholders}) "
        f"AND status IN ('PENDING', 'ACKNOWLEDGED')",
        (resolved_by, resolved_at, notes, *alert_ids),
    )
    count = cursor.rowcount
    db.commit()
    logger.info(f"Bulk-resolved {count} events by {resolved_by}")
    return count


def _bulk_resolve_events_pg(
    alert_ids: List[int], resolved_by: str, resolved_at: str, notes: Optional[str]
) -> int:
    """PostgreSQL branch of bulk_resolve_events."""
    import sqlalchemy
    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL", ""))
    with engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text(
                "UPDATE events SET status = 'RESOLVED', "
                "resolved_by = :by, resolved_at = :at, "
                "notes = COALESCE(:notes, notes) "
                "WHERE id = ANY(:ids) "
                "AND status IN ('PENDING', 'ACKNOWLEDGED')"
            ),
            {"by": resolved_by, "at": resolved_at, "notes": notes, "ids": list(alert_ids)},
        )
        conn.commit()
    count = result.rowcount or 0
    logger.info(f"Bulk-resolved {count} events by {resolved_by} (PostgreSQL)")
    return count


__all__ = [
    "list_events",
    "acknowledge_event",
    "resolve_event",
    "bulk_acknowledge_events",
    "bulk_resolve_events",
    "get_event_counts",
]