# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Event Service
Smart Disaster Prevention Response System

This module provides CRUD operations for event/alert management.
"""

import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

# Configure logging
logger = logging.getLogger("event_service")


def list_events(
    db: sqlite3.Connection,
    status_filter: Optional[str] = None,
    node_filter: Optional[str] = None,
    page: int = 1,
    page_size: int = 20
) -> Dict[str, Any]:
    """
    List events with optional filtering and pagination.
    
    Args:
        db: SQLite database connection
        status_filter: Filter by status (PENDING_VIDEO, PENDING, RESOLVED)
        node_filter: Filter by node_id
        page: Page number (1-indexed)
        page_size: Number of items per page
        
    Returns:
        Dict with items, total, page, page_size
    """
    cursor = db.cursor()
    
    # Build query
    conditions = []
    params = []
    
    if status_filter:
        conditions.append("status = ?")
        params.append(status_filter)
    
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


def get_event(db: sqlite3.Connection, alert_id: int) -> Optional[Dict[str, Any]]:
    """
    Get a single event by ID.
    
    Args:
        db: SQLite database connection
        alert_id: The event/alert ID
        
    Returns:
        Event dict or None if not found
    """
    cursor = db.cursor()
    cursor.execute("SELECT * FROM events WHERE id = ?", (alert_id,))
    row = cursor.fetchone()
    
    if row:
        return dict(row)
    return None


def create_event(
    db: sqlite3.Connection,
    node_id: str,
    timestamp: str,
    visual_confidence: float,
    audio_db_peak: float,
    audio_freq_peak_hz: float
) -> int:
    """
    Create a new event.
    
    Args:
        db: SQLite database connection
        node_id: The node identifier
        timestamp: ISO 8601 timestamp
        visual_confidence: Visual detection confidence score
        audio_db_peak: Audio peak level in dB
        audio_freq_peak_hz: Audio peak frequency in Hz
        
    Returns:
        The auto-generated alert_id
    """
    cursor = db.cursor()
    cursor.execute("""
        INSERT INTO events (node_id, timestamp, status, visual_confidence, audio_db_peak, audio_freq_peak_hz)
        VALUES (?, ?, 'PENDING_VIDEO', ?, ?, ?)
    """, (node_id, timestamp, visual_confidence, audio_db_peak, audio_freq_peak_hz))
    db.commit()
    
    alert_id = cursor.lastrowid
    logger.info(f"Created event {alert_id} from node {node_id}")
    
    return alert_id


def update_event_video(db: sqlite3.Connection, alert_id: int, mp4_path: str) -> bool:
    """
    Update event with video path and change status to PENDING.
    
    Args:
        db: SQLite database connection
        alert_id: The event/alert ID
        mp4_path: Path to the MP4 file
        
    Returns:
        True if successful, False if event not found or wrong status
    """
    cursor = db.cursor()
    
    # Check current status
    cursor.execute("SELECT status FROM events WHERE id = ?", (alert_id,))
    row = cursor.fetchone()
    
    if not row:
        logger.warning(f"Event {alert_id} not found")
        return False
    
    if row["status"] != "PENDING_VIDEO":
        logger.warning(f"Event {alert_id} status is {row['status']}, expected PENDING_VIDEO")
        return False
    
    # Update
    cursor.execute("""
        UPDATE events SET mp4_path = ?, status = 'PENDING'
        WHERE id = ?
    """, (mp4_path, alert_id))
    db.commit()
    
    logger.info(f"Updated event {alert_id} with video, status -> PENDING")
    return True


def resolve_event(
    db: sqlite3.Connection,
    alert_id: int,
    resolved_by: str,
    notes: Optional[str] = None
) -> bool:
    """
    Mark an event as resolved.
    
    Args:
        db: SQLite database connection
        alert_id: The event/alert ID
        resolved_by: Username who resolved the event
        notes: Optional resolution notes
        
    Returns:
        True if successful, False if event not found or wrong status
    """
    cursor = db.cursor()
    
    # Check current status
    cursor.execute("SELECT status FROM events WHERE id = ?", (alert_id,))
    row = cursor.fetchone()
    
    if not row:
        logger.warning(f"Event {alert_id} not found")
        return False
    
    if row["status"] != "PENDING":
        logger.warning(f"Event {alert_id} status is {row['status']}, expected PENDING")
        return False
    
    # Update
    resolved_at = datetime.utcnow().isoformat()
    cursor.execute("""
        UPDATE events SET 
            status = 'RESOLVED',
            resolved_by = ?,
            resolved_at = ?,
            notes = COALESCE(?, notes)
        WHERE id = ?
    """, (resolved_by, resolved_at, notes, alert_id))
    db.commit()
    
    logger.info(f"Event {alert_id} resolved by {resolved_by}")
    return True


def get_event_counts(db: sqlite3.Connection) -> Dict[str, int]:
    """
    Get counts of events by status.
    
    Args:
        db: SQLite database connection
        
    Returns:
        Dict with pending_video, pending, resolved, total counts
    """
    cursor = db.cursor()
    
    # Get counts by status
    cursor.execute("""
        SELECT 
            status,
            COUNT(*) as count
        FROM events
        GROUP BY status
    """)
    
    counts = {
        "pending_video": 0,
        "pending": 0,
        "resolved": 0,
        "total": 0
    }
    
    for row in cursor.fetchall():
        status = row["status"].lower()
        if status == "pending_video":
            counts["pending_video"] = row["count"]
        elif status == "pending":
            counts["pending"] = row["count"]
        elif status == "resolved":
            counts["resolved"] = row["count"]
    
    counts["total"] = sum([
        counts["pending_video"],
        counts["pending"],
        counts["resolved"]
    ])
    
    return counts


def delete_event(db: sqlite3.Connection, alert_id: int) -> bool:
    """
    Delete an event from the database.
    
    Args:
        db: SQLite database connection
        alert_id: The event/alert ID
        
    Returns:
        True if deleted, False if not found
    """
    cursor = db.cursor()
    cursor.execute("DELETE FROM events WHERE id = ?", (alert_id,))
    db.commit()
    
    deleted = cursor.rowcount > 0
    if deleted:
        logger.info(f"Deleted event {alert_id}")
    
    return deleted


def get_events_for_retention(
    db: sqlite3.Connection,
    cutoff_date: datetime
) -> List[Dict[str, Any]]:
    """
    Get events older than the cutoff date for retention cleanup.
    
    Args:
        db: SQLite database connection
        cutoff_date: Events created before this date will be returned
        
    Returns:
        List of expired events
    """
    cursor = db.cursor()
    cursor.execute(
        "SELECT id, mp4_path FROM events WHERE created_at < ?",
        (cutoff_date.isoformat(),)
    )
    
    return [dict(row) for row in cursor.fetchall()]


def delete_events_before_date(
    db: sqlite3.Connection,
    cutoff_date: datetime
) -> int:
    """
    Delete all events created before the cutoff date.
    
    Args:
        db: SQLite database connection
        cutoff_date: Events created before this date will be deleted
        
    Returns:
        Number of deleted events
    """
    cursor = db.cursor()
    cursor.execute(
        "DELETE FROM events WHERE created_at < ?",
        (cutoff_date.isoformat(),)
    )
    db.commit()
    
    deleted_count = cursor.rowcount
    logger.info(f"Deleted {deleted_count} events before {cutoff_date.isoformat()}")
    
    return deleted_count


__all__ = [
    "list_events",
    "get_event",
    "create_event",
    "update_event_video",
    "resolve_event",
    "get_event_counts",
    "delete_event",
    "get_events_for_retention",
    "delete_events_before_date",
]