# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Database Module
Smart Disaster Prevention Response System

This module provides SQLite database management with WAL mode support
for the central server.
"""

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Configure logging
logger = logging.getLogger("database")

# Global database connection and lock
_db_connection: Optional[sqlite3.Connection] = None
_db_lock: Optional[threading.Lock] = None


def init_db(db_path: str = "./data/sdprs.db") -> sqlite3.Connection:
    """
    Initialize the SQLite database connection and create tables.
    
    Args:
        db_path: Path to the SQLite database file
        
    Returns:
        The database connection object
    """
    global _db_connection, _db_lock
    
    # Ensure data directory exists
    data_dir = Path(db_path).parent
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Create connection with thread safety
    _db_connection = sqlite3.connect(
        db_path,
        check_same_thread=False,
        timeout=10.0
    )
    _db_connection.row_factory = sqlite3.Row
    
    # Create lock for thread-safe write operations
    _db_lock = threading.Lock()
    
    cursor = _db_connection.cursor()
    
    # Enable WAL mode for better concurrency
    result = cursor.execute("PRAGMA journal_mode=WAL;")
    journal_mode = result.fetchone()[0]
    logger.info(f"SQLite journal mode: {journal_mode}")
    
    # Set busy timeout for concurrent access
    cursor.execute("PRAGMA busy_timeout=5000;")
    
    # Create events table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id            TEXT NOT NULL,
            timestamp          DATETIME NOT NULL,
            status             TEXT NOT NULL DEFAULT 'PENDING_VIDEO',
                               -- PENDING_VIDEO | PENDING | RESOLVED
            mp4_path           TEXT,
            visual_confidence  REAL,
            audio_db_peak      REAL,
            audio_freq_peak_hz REAL,
            resolved_by        TEXT,
            resolved_at        DATETIME,
            notes              TEXT,
            created_at         DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    # Create nodes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id        TEXT PRIMARY KEY,
            node_type      TEXT NOT NULL,
                               -- 'glass' | 'pump'
            last_heartbeat DATETIME,
            status         TEXT DEFAULT 'OFFLINE',
                               -- ONLINE | OFFLINE
            metadata       TEXT
                               -- JSON blob (cpu_temp, memory_usage_percent, etc.)
        );
    """)
    
    # Create indexes for common queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_node_timestamp ON events(node_id, timestamp);
    """)
    
    _db_connection.commit()
    logger.info(f"Database initialized at {db_path}")
    
    return _db_connection


def get_db() -> sqlite3.Connection:
    """
    Get the database connection.
    
    Returns:
        The global database connection
        
    Raises:
        RuntimeError: If database has not been initialized
    """
    if _db_connection is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db_connection


def close_db():
    """Close the database connection."""
    global _db_connection, _db_lock
    if _db_connection:
        _db_connection.close()
        _db_connection = None
        _db_lock = None
        logger.info("Database connection closed")


@contextmanager
def get_db_cursor():
    """
    Context manager for getting a database cursor with automatic commit/rollback.
    
    Yields:
        sqlite3.Cursor: A database cursor
    """
    db = get_db()
    with _db_lock:
        cursor = db.cursor()
        try:
            yield cursor
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Database transaction failed: {e}")
            raise


# ===== Event Operations =====

def insert_event(
    node_id: str,
    timestamp: str,
    visual_confidence: float,
    audio_db_peak: float,
    audio_freq_peak_hz: float,
    status: str = "PENDING_VIDEO"
) -> int:
    """
    Insert a new event record.
    
    Args:
        node_id: The node identifier
        timestamp: ISO format timestamp
        visual_confidence: Visual detection confidence score
        audio_db_peak: Audio peak level in dB
        audio_freq_peak_hz: Audio peak frequency in Hz
        status: Event status (default: PENDING_VIDEO)
        
    Returns:
        The auto-incremented event ID
    """
    with get_db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO events (node_id, timestamp, status, visual_confidence, audio_db_peak, audio_freq_peak_hz)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (node_id, timestamp, status, visual_confidence, audio_db_peak, audio_freq_peak_hz))
        event_id = cursor.lastrowid
        logger.info(f"Inserted event {event_id} from node {node_id}")
        return event_id


def get_event(alert_id: int) -> Optional[Dict[str, Any]]:
    """
    Get a single event by ID.
    
    Args:
        alert_id: The event/alert ID
        
    Returns:
        Event dict or None if not found
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM events WHERE id = ?", (alert_id,))
    row = cursor.fetchone()
    if row:
        return dict(row)
    return None


def update_event_status(
    alert_id: int,
    status: str,
    mp4_path: Optional[str] = None,
    resolved_by: Optional[str] = None,
    notes: Optional[str] = None
) -> bool:
    """
    Update event status and optional fields.
    
    Args:
        alert_id: The event/alert ID
        status: New status value
        mp4_path: Path to MP4 file (optional)
        resolved_by: Username who resolved the event (optional)
        notes: Resolution notes (optional)
        
    Returns:
        True if update was successful, False otherwise
    """
    with get_db_cursor() as cursor:
        # Build dynamic update query
        updates = ["status = ?"]
        params = [status]
        
        if mp4_path is not None:
            updates.append("mp4_path = ?")
            params.append(mp4_path)
        
        if resolved_by is not None:
            updates.append("resolved_by = ?")
            params.append(resolved_by)
            updates.append("resolved_at = ?")
            params.append(datetime.utcnow().isoformat())
        
        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)
        
        params.append(alert_id)
        
        query = f"UPDATE events SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Updated event {alert_id} status to {status}")
        return success


def get_events_by_status(status: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Get events by status.
    
    Args:
        status: The status to filter by
        limit: Maximum number of records to return
        
    Returns:
        List of event dicts
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM events WHERE status = ? ORDER BY timestamp DESC LIMIT ?",
        (status, limit)
    )
    return [dict(row) for row in cursor.fetchall()]


def get_all_events(limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """
    Get all events with pagination.
    
    Args:
        limit: Maximum number of records to return
        offset: Number of records to skip
        
    Returns:
        List of event dicts
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM events ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (limit, offset)
    )
    return [dict(row) for row in cursor.fetchall()]


# ===== Node Operations =====

def upsert_node(
    node_id: str,
    node_type: str,
    status: str = "ONLINE",
    metadata: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Insert or update a node record.
    
    Args:
        node_id: The node identifier
        node_type: Type of node ('glass' or 'pump')
        status: Node status (ONLINE/OFFLINE)
        metadata: Optional metadata dict (stored as JSON)
        
    Returns:
        True if operation was successful
    """
    with get_db_cursor() as cursor:
        metadata_json = json.dumps(metadata) if metadata else None
        cursor.execute("""
            INSERT INTO nodes (node_id, node_type, last_heartbeat, status, metadata)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                last_heartbeat = excluded.last_heartbeat,
                status = excluded.status,
                metadata = excluded.metadata
        """, (node_id, node_type, datetime.utcnow().isoformat(), status, metadata_json))
        
        logger.debug(f"Upserted node {node_id} ({node_type})")
        return True


def update_node_heartbeat(node_id: str, metadata: Optional[Dict[str, Any]] = None):
    """
    Update node's last heartbeat timestamp.
    
    Args:
        node_id: The node identifier
        metadata: Optional metadata dict to update
    """
    with get_db_cursor() as cursor:
        if metadata:
            metadata_json = json.dumps(metadata)
            cursor.execute("""
                UPDATE nodes SET
                    last_heartbeat = ?,
                    status = 'ONLINE',
                    metadata = ?
                WHERE node_id = ?
            """, (datetime.utcnow().isoformat(), metadata_json, node_id))
        else:
            cursor.execute("""
                UPDATE nodes SET
                    last_heartbeat = ?,
                    status = 'ONLINE'
                WHERE node_id = ?
            """, (datetime.utcnow().isoformat(), node_id))


def update_node_status(node_id: str, status: str):
    """
    Update node's status.
    
    Args:
        node_id: The node identifier
        status: New status (ONLINE/OFFLINE)
    """
    with get_db_cursor() as cursor:
        cursor.execute(
            "UPDATE nodes SET status = ? WHERE node_id = ?",
            (status, node_id)
        )
        logger.info(f"Updated node {node_id} status to {status}")


def get_node(node_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a single node by ID.
    
    Args:
        node_id: The node identifier
        
    Returns:
        Node dict or None if not found
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
    row = cursor.fetchone()
    if row:
        node = dict(row)
        # Parse metadata JSON
        if node.get("metadata"):
            try:
                node["metadata"] = json.loads(node["metadata"])
            except json.JSONDecodeError:
                pass
        return node
    return None


def get_all_nodes() -> List[Dict[str, Any]]:
    """
    Get all registered nodes.
    
    Returns:
        List of node dicts
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM nodes")
    nodes = []
    for row in cursor.fetchall():
        node = dict(row)
        # Parse metadata JSON
        if node.get("metadata"):
            try:
                node["metadata"] = json.loads(node["metadata"])
            except json.JSONDecodeError:
                pass
        nodes.append(node)
    return nodes


# ===== Utility Functions =====

def check_database_health() -> Dict[str, Any]:
    """
    Check database health and return status info.
    
    Returns:
        Dict with health status information
    """
    try:
        db = get_db()
        cursor = db.cursor()
        
        # Check journal mode
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        
        # Get counts
        cursor.execute("SELECT COUNT(*) FROM events;")
        events_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM nodes;")
        nodes_count = cursor.fetchone()[0]
        
        return {
            "healthy": True,
            "journal_mode": journal_mode,
            "events_count": events_count,
            "nodes_count": nodes_count
        }
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {
            "healthy": False,
            "error": str(e)
        }