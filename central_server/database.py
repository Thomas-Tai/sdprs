# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Database Module
Smart Disaster Prevention Response System

Dual-mode database support:
  - SQLite (default): used when DATABASE_URL is empty; local LAN deployment
  - PostgreSQL:       used when DATABASE_URL is set; Zeabur cloud deployment

The public API (init_db, get_db, insert_event, etc.) is identical in both modes.
Callers do not need to know which backend is active.
"""

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("database")

# ── Backend state ─────────────────────────────────────────────────────────────
_backend: str = "sqlite"          # "sqlite" | "postgresql"
_db_connection: Optional[sqlite3.Connection] = None
_db_lock: Optional[threading.Lock] = None
_pg_database = None               # databases.Database instance (PostgreSQL)


# =============================================================================
# Initialisation
# =============================================================================

def init_db(db_path: str = "./data/sdprs.db") -> Any:
    """
    Initialise the database.

    Checks DATABASE_URL environment variable:
      - Set  → PostgreSQL via the `databases` library (async-compatible)
      - Empty → SQLite with WAL mode (existing behaviour)

    Returns the connection object (SQLite) or the databases.Database instance (PG).
    """
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        return _init_postgresql(database_url)
    else:
        return _init_sqlite(db_path)


def _init_sqlite(db_path: str) -> sqlite3.Connection:
    global _db_connection, _db_lock, _backend
    _backend = "sqlite"

    data_dir = Path(db_path).parent
    data_dir.mkdir(parents=True, exist_ok=True)

    _db_connection = sqlite3.connect(
        db_path,
        check_same_thread=False,
        timeout=10.0,
    )
    _db_connection.row_factory = sqlite3.Row
    _db_lock = threading.Lock()

    cursor = _db_connection.cursor()
    result = cursor.execute("PRAGMA journal_mode=WAL;")
    journal_mode = result.fetchone()[0]
    logger.info(f"SQLite journal mode: {journal_mode}")
    cursor.execute("PRAGMA busy_timeout=5000;")

    _create_tables_sqlite(cursor)
    _db_connection.commit()
    logger.info(f"SQLite database initialised at {db_path}")
    return _db_connection


def _init_postgresql(database_url: str) -> Any:
    global _pg_database, _backend
    _backend = "postgresql"
    try:
        import databases
        import sqlalchemy
    except ImportError:
        raise RuntimeError(
            "PostgreSQL dependencies not installed. "
            "Run: pip install databases[postgresql] asyncpg sqlalchemy"
        )

    _pg_database = databases.Database(database_url)

    # Create tables using synchronous SQLAlchemy engine (run once at startup)
    engine = sqlalchemy.create_engine(database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
                                      if "postgresql://" in database_url else database_url)
    with engine.connect() as conn:
        _create_tables_postgresql(conn)
        conn.commit()

    logger.info("PostgreSQL database initialised")
    return _pg_database


def _create_tables_sqlite(cursor: sqlite3.Cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id            TEXT NOT NULL,
            timestamp          DATETIME NOT NULL,
            status             TEXT NOT NULL DEFAULT 'PENDING_VIDEO',
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id        TEXT PRIMARY KEY,
            node_type      TEXT NOT NULL,
            last_heartbeat DATETIME,
            status         TEXT DEFAULT 'OFFLINE',
            metadata       TEXT
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_node_timestamp ON events(node_id, timestamp);")


def _create_tables_postgresql(conn):
    import sqlalchemy
    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS events (
            id                 SERIAL PRIMARY KEY,
            node_id            TEXT NOT NULL,
            timestamp          TIMESTAMP NOT NULL,
            status             TEXT NOT NULL DEFAULT 'PENDING_VIDEO',
            mp4_path           TEXT,
            visual_confidence  REAL,
            audio_db_peak      REAL,
            audio_freq_peak_hz REAL,
            resolved_by        TEXT,
            resolved_at        TIMESTAMP,
            notes              TEXT,
            created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """))
    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id        TEXT PRIMARY KEY,
            node_type      TEXT NOT NULL,
            last_heartbeat TIMESTAMP,
            status         TEXT DEFAULT 'OFFLINE',
            metadata       TEXT
        );
    """))
    conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);"))
    conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_events_node_timestamp ON events(node_id, timestamp);"))


# =============================================================================
# Connection helpers
# =============================================================================

def get_db() -> sqlite3.Connection:
    """Return the SQLite connection (raises if not initialised or using PG)."""
    if _db_connection is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db_connection


def close_db():
    """Close the active database connection."""
    global _db_connection, _db_lock, _pg_database
    if _backend == "sqlite" and _db_connection:
        _db_connection.close()
        _db_connection = None
        _db_lock = None
        logger.info("SQLite connection closed")
    elif _backend == "postgresql" and _pg_database:
        # databases.Database.disconnect() is async; caller should await it.
        # For sync shutdown we just clear the reference.
        _pg_database = None
        logger.info("PostgreSQL database reference cleared")


@contextmanager
def get_db_cursor():
    """
    Synchronous context manager for a database cursor (SQLite only).
    Auto-commits on success; rolls back on exception.
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


# =============================================================================
# PostgreSQL async connect / disconnect (called from FastAPI lifespan)
# =============================================================================

async def connect_pg():
    """Connect the databases.Database pool (PostgreSQL only, called on startup)."""
    if _backend == "postgresql" and _pg_database is not None:
        await _pg_database.connect()
        logger.info("PostgreSQL connection pool opened")


async def disconnect_pg():
    """Disconnect the databases.Database pool (PostgreSQL only, called on shutdown)."""
    if _backend == "postgresql" and _pg_database is not None:
        await _pg_database.disconnect()
        logger.info("PostgreSQL connection pool closed")


# =============================================================================
# Event Operations  (unified SQLite / PostgreSQL)
# =============================================================================

def insert_event(
    node_id: str,
    timestamp: str,
    visual_confidence: float,
    audio_db_peak: float,
    audio_freq_peak_hz: float,
    status: str = "PENDING_VIDEO",
) -> int:
    """Insert a new event record. Returns the auto-generated event ID."""
    if _backend == "postgresql":
        return _pg_insert_event_sync(
            node_id, timestamp, visual_confidence, audio_db_peak, audio_freq_peak_hz, status
        )
    with get_db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO events
                (node_id, timestamp, status, visual_confidence, audio_db_peak, audio_freq_peak_hz)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (node_id, timestamp, status, visual_confidence, audio_db_peak, audio_freq_peak_hz))
        event_id = cursor.lastrowid
        logger.info(f"Inserted event {event_id} from node {node_id}")
        return event_id


def _pg_insert_event_sync(node_id, timestamp, visual_confidence, audio_db_peak, audio_freq_peak_hz, status) -> int:
    """Synchronous PostgreSQL insert via raw psycopg2 (fallback for sync callers)."""
    import sqlalchemy
    database_url = os.environ.get("DATABASE_URL", "")
    engine = sqlalchemy.create_engine(database_url)
    with engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text("""
                INSERT INTO events
                    (node_id, timestamp, status, visual_confidence, audio_db_peak, audio_freq_peak_hz)
                VALUES (:node_id, :ts, :status, :vc, :adb, :afq)
                RETURNING id
            """),
            {"node_id": node_id, "ts": timestamp, "status": status,
             "vc": visual_confidence, "adb": audio_db_peak, "afq": audio_freq_peak_hz},
        )
        conn.commit()
        event_id = result.fetchone()[0]
        logger.info(f"Inserted event {event_id} from node {node_id} (PostgreSQL)")
        return event_id


def get_event(alert_id: int) -> Optional[Dict[str, Any]]:
    """Get a single event by ID."""
    if _backend == "postgresql":
        return _pg_fetch_one_sync("SELECT * FROM events WHERE id = :id", {"id": alert_id})
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM events WHERE id = ?", (alert_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def update_event_status(
    alert_id: int,
    status: str,
    mp4_path: Optional[str] = None,
    resolved_by: Optional[str] = None,
    notes: Optional[str] = None,
) -> bool:
    """Update event status and optional fields."""
    if _backend == "postgresql":
        return _pg_update_event_sync(alert_id, status, mp4_path, resolved_by, notes)

    with get_db_cursor() as cursor:
        updates = ["status = ?"]
        params: list = [status]
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
        cursor.execute(f"UPDATE events SET {', '.join(updates)} WHERE id = ?", params)
        success = cursor.rowcount > 0
        if success:
            logger.info(f"Updated event {alert_id} status to {status}")
        return success


def _pg_update_event_sync(alert_id, status, mp4_path, resolved_by, notes) -> bool:
    import sqlalchemy
    database_url = os.environ.get("DATABASE_URL", "")
    engine = sqlalchemy.create_engine(database_url)
    sets = ["status = :status"]
    params: dict = {"status": status, "id": alert_id}
    if mp4_path is not None:
        sets.append("mp4_path = :mp4_path")
        params["mp4_path"] = mp4_path
    if resolved_by is not None:
        sets.append("resolved_by = :resolved_by")
        sets.append("resolved_at = :resolved_at")
        params["resolved_by"] = resolved_by
        params["resolved_at"] = datetime.utcnow().isoformat()
    if notes is not None:
        sets.append("notes = :notes")
        params["notes"] = notes
    with engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text(f"UPDATE events SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        conn.commit()
        return result.rowcount > 0


def get_events_by_status(status: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Get events filtered by status."""
    if _backend == "postgresql":
        return _pg_fetch_many_sync(
            "SELECT * FROM events WHERE status = :status ORDER BY timestamp DESC LIMIT :limit",
            {"status": status, "limit": limit},
        )
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM events WHERE status = ? ORDER BY timestamp DESC LIMIT ?",
        (status, limit),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_all_events(limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """Get all events with pagination."""
    if _backend == "postgresql":
        return _pg_fetch_many_sync(
            "SELECT * FROM events ORDER BY timestamp DESC LIMIT :limit OFFSET :offset",
            {"limit": limit, "offset": offset},
        )
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT * FROM events ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    return [dict(row) for row in cursor.fetchall()]


# =============================================================================
# Node Operations  (unified SQLite / PostgreSQL)
# =============================================================================

def upsert_node(
    node_id: str,
    node_type: str,
    status: str = "ONLINE",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Insert or update a node record."""
    metadata_json = json.dumps(metadata) if metadata else None
    now = datetime.utcnow().isoformat()

    if _backend == "postgresql":
        import sqlalchemy
        database_url = os.environ.get("DATABASE_URL", "")
        engine = sqlalchemy.create_engine(database_url)
        with engine.connect() as conn:
            conn.execute(
                sqlalchemy.text("""
                    INSERT INTO nodes (node_id, node_type, last_heartbeat, status, metadata)
                    VALUES (:node_id, :node_type, :hb, :status, :meta)
                    ON CONFLICT (node_id) DO UPDATE SET
                        last_heartbeat = EXCLUDED.last_heartbeat,
                        status         = EXCLUDED.status,
                        metadata       = EXCLUDED.metadata
                """),
                {"node_id": node_id, "node_type": node_type, "hb": now,
                 "status": status, "meta": metadata_json},
            )
            conn.commit()
        logger.debug(f"Upserted node {node_id} ({node_type})")
        return True

    with get_db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO nodes (node_id, node_type, last_heartbeat, status, metadata)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                last_heartbeat = excluded.last_heartbeat,
                status         = excluded.status,
                metadata       = excluded.metadata
        """, (node_id, node_type, now, status, metadata_json))
        logger.debug(f"Upserted node {node_id} ({node_type})")
        return True


def update_node_heartbeat(node_id: str, metadata: Optional[Dict[str, Any]] = None):
    """Update node last heartbeat timestamp."""
    metadata_json = json.dumps(metadata) if metadata else None
    now = datetime.utcnow().isoformat()

    if _backend == "postgresql":
        import sqlalchemy
        database_url = os.environ.get("DATABASE_URL", "")
        engine = sqlalchemy.create_engine(database_url)
        with engine.connect() as conn:
            if metadata_json:
                conn.execute(
                    sqlalchemy.text("UPDATE nodes SET last_heartbeat=:hb, status='ONLINE', metadata=:meta WHERE node_id=:id"),
                    {"hb": now, "meta": metadata_json, "id": node_id},
                )
            else:
                conn.execute(
                    sqlalchemy.text("UPDATE nodes SET last_heartbeat=:hb, status='ONLINE' WHERE node_id=:id"),
                    {"hb": now, "id": node_id},
                )
            conn.commit()
        return

    with get_db_cursor() as cursor:
        if metadata_json:
            cursor.execute(
                "UPDATE nodes SET last_heartbeat=?, status='ONLINE', metadata=? WHERE node_id=?",
                (now, metadata_json, node_id),
            )
        else:
            cursor.execute(
                "UPDATE nodes SET last_heartbeat=?, status='ONLINE' WHERE node_id=?",
                (now, node_id),
            )


def update_node_status(node_id: str, status: str):
    """Update node status field."""
    if _backend == "postgresql":
        import sqlalchemy
        database_url = os.environ.get("DATABASE_URL", "")
        engine = sqlalchemy.create_engine(database_url)
        with engine.connect() as conn:
            conn.execute(
                sqlalchemy.text("UPDATE nodes SET status=:status WHERE node_id=:id"),
                {"status": status, "id": node_id},
            )
            conn.commit()
        logger.info(f"Updated node {node_id} status to {status}")
        return

    with get_db_cursor() as cursor:
        cursor.execute(
            "UPDATE nodes SET status = ? WHERE node_id = ?",
            (status, node_id),
        )
        logger.info(f"Updated node {node_id} status to {status}")


def get_node(node_id: str) -> Optional[Dict[str, Any]]:
    """Get a single node by ID."""
    if _backend == "postgresql":
        row = _pg_fetch_one_sync("SELECT * FROM nodes WHERE node_id = :id", {"id": node_id})
    else:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
        r = cursor.fetchone()
        row = dict(r) if r else None

    if row and row.get("metadata"):
        try:
            row["metadata"] = json.loads(row["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass
    return row


def get_all_nodes() -> List[Dict[str, Any]]:
    """Get all registered nodes."""
    if _backend == "postgresql":
        rows = _pg_fetch_many_sync("SELECT * FROM nodes", {})
    else:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM nodes")
        rows = [dict(r) for r in cursor.fetchall()]

    for row in rows:
        if row.get("metadata"):
            try:
                row["metadata"] = json.loads(row["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass
    return rows


# =============================================================================
# Utility
# =============================================================================

def check_database_health() -> Dict[str, Any]:
    """Check database health and return status info."""
    try:
        if _backend == "postgresql":
            rows_e = _pg_fetch_many_sync("SELECT COUNT(*) AS c FROM events", {})
            rows_n = _pg_fetch_many_sync("SELECT COUNT(*) AS c FROM nodes", {})
            return {
                "healthy": True,
                "backend": "postgresql",
                "events_count": rows_e[0]["c"] if rows_e else 0,
                "nodes_count": rows_n[0]["c"] if rows_n else 0,
            }

        db = get_db()
        cursor = db.cursor()
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM events;")
        events_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM nodes;")
        nodes_count = cursor.fetchone()[0]
        return {
            "healthy": True,
            "backend": "sqlite",
            "journal_mode": journal_mode,
            "events_count": events_count,
            "nodes_count": nodes_count,
        }
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {"healthy": False, "error": str(e)}


# =============================================================================
# PostgreSQL sync helpers (avoid repeated engine creation in hot paths;
# acceptable for MVP — replace with connection pooling if load increases)
# =============================================================================

def _pg_fetch_one_sync(query: str, params: dict) -> Optional[Dict[str, Any]]:
    import sqlalchemy
    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL", ""))
    with engine.connect() as conn:
        result = conn.execute(sqlalchemy.text(query), params)
        row = result.mappings().fetchone()
        return dict(row) if row else None


def _pg_fetch_many_sync(query: str, params: dict) -> List[Dict[str, Any]]:
    import sqlalchemy
    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL", ""))
    with engine.connect() as conn:
        result = conn.execute(sqlalchemy.text(query), params)
        return [dict(r) for r in result.mappings().fetchall()]
