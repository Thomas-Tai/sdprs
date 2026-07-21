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

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .timeutil import utcnow

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
            acknowledged_by    TEXT,
            acknowledged_at    DATETIME,
            resolved_by        TEXT,
            resolved_at        DATETIME,
            notes              TEXT,
            created_at         DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Migration: add acknowledgement columns to existing events tables (Sprint A item 2)
    cursor.execute("PRAGMA table_info(events);")
    existing_event_cols = {row[1] for row in cursor.fetchall()}
    if "acknowledged_by" not in existing_event_cols:
        cursor.execute("ALTER TABLE events ADD COLUMN acknowledged_by TEXT;")
    if "acknowledged_at" not in existing_event_cols:
        cursor.execute("ALTER TABLE events ADD COLUMN acknowledged_at DATETIME;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id        TEXT PRIMARY KEY,
            node_type      TEXT NOT NULL,
            last_heartbeat DATETIME,
            last_upload_at DATETIME,
            status         TEXT DEFAULT 'OFFLINE',
            metadata       TEXT,
            location       TEXT,
            snoozed_until  DATETIME,
            snooze_reason  TEXT,
            battery_voltage REAL,
            power_source    TEXT
        );
    """)
    # Migration: add columns to existing nodes tables
    cursor.execute("PRAGMA table_info(nodes);")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "location" not in existing_cols:
        cursor.execute("ALTER TABLE nodes ADD COLUMN location TEXT;")
    if "last_upload_at" not in existing_cols:           # item 13
        cursor.execute("ALTER TABLE nodes ADD COLUMN last_upload_at DATETIME;")
    if "snoozed_until" not in existing_cols:            # item 17
        cursor.execute("ALTER TABLE nodes ADD COLUMN snoozed_until DATETIME;")
    if "snooze_reason" not in existing_cols:            # item 17
        cursor.execute("ALTER TABLE nodes ADD COLUMN snooze_reason TEXT;")
    if "battery_voltage" not in existing_cols:          # item 12
        cursor.execute("ALTER TABLE nodes ADD COLUMN battery_voltage REAL;")
    if "power_source" not in existing_cols:             # item 12
        cursor.execute("ALTER TABLE nodes ADD COLUMN power_source TEXT;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pump_readings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id     TEXT NOT NULL,
            timestamp   DATETIME NOT NULL,
            water_level REAL,
            pump_state  TEXT,
            raining         INTEGER,
            sensor_conflict INTEGER
        );
    """)
    # Migration: add raining/sensor_conflict columns to existing pump_readings tables
    try:
        cursor.execute("ALTER TABLE pump_readings ADD COLUMN raining INTEGER")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE pump_readings ADD COLUMN sensor_conflict INTEGER")
    except Exception:
        pass

    # Operator audit log (item 15). Append-only; no foreign keys (target_id
    # may reference rows that get retention-deleted later).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS operator_actions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            operator     TEXT NOT NULL,
            action_type  TEXT NOT NULL,
            target_id    TEXT,
            details_json TEXT
        );
    """)

    # Shift-handover note (item 16). Single-row, last-write-wins.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS handover_note (
            id        INTEGER PRIMARY KEY CHECK (id = 1),
            note      TEXT NOT NULL DEFAULT '',
            author    TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Seed the singleton row if missing.
    cursor.execute("INSERT OR IGNORE INTO handover_note (id, note) VALUES (1, '');")

    # Weather configuration (item 9: user-configurable location for Open-Meteo)
    # Default: empty (SMG Macau XML is always enabled; Open-Meteo requires user config)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weather_config (
            id        INTEGER PRIMARY KEY CHECK (id = 1),
            site_lat  REAL DEFAULT NULL,
            site_lon  REAL DEFAULT NULL,
            station_name TEXT DEFAULT NULL,
            smg_station TEXT DEFAULT NULL,
            hko_station TEXT DEFAULT NULL,
            fallback_provider TEXT DEFAULT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Option C migration (2026-07-19): add the 3 multi-source selectors
    # to an existing (pre-Option-C) weather_config row. ALTER TABLE ADD
    # COLUMN is safe on SQLite for a NULL-default column — no data loss.
    for new_col in ('smg_station', 'hko_station', 'fallback_provider'):
        try:
            cursor.execute(f"ALTER TABLE weather_config ADD COLUMN {new_col} TEXT DEFAULT NULL;")
        except sqlite3.OperationalError:
            pass  # already exists
    # Migration: if table exists with old NOT NULL columns, recreate it
    try:
        cursor.execute("PRAGMA table_info(weather_config);")
        cols = cursor.fetchall()
        # Check if any column has NOT NULL constraint (pk=0 means not primary key)
        for col in cols:
            if col[1] in ('site_lat', 'site_lon') and col[3] == 1:  # notnull=1
                logger.info("Migrating weather_config table to allow NULL values")
                cursor.execute("ALTER TABLE weather_config RENAME TO weather_config_old;")
                cursor.execute("""
                    CREATE TABLE weather_config (
                        id        INTEGER PRIMARY KEY CHECK (id = 1),
                        site_lat  REAL DEFAULT NULL,
                        site_lon  REAL DEFAULT NULL,
                        station_name TEXT DEFAULT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                # Copy data, converting old defaults to NULL (user should reconfigure)
                cursor.execute("""
                    INSERT INTO weather_config (id, site_lat, site_lon, station_name, updated_at)
                    SELECT id, NULL, NULL, NULL, updated_at FROM weather_config_old;
                """)
                cursor.execute("DROP TABLE weather_config_old;")
                break
    except sqlite3.OperationalError:
        pass  # Table doesn't exist yet, no migration needed

    # Don't insert default row - empty means SMG Macau only

    # Webcam client registry (one key per client PC, multiple cameras)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS webcam_clients (
            node_id      TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            api_key_hash TEXT NOT NULL,
            created_at   DATETIME,
            status       TEXT DEFAULT 'OFFLINE'
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS webcam_cameras (
            node_id       TEXT PRIMARY KEY,
            client_id     TEXT NOT NULL REFERENCES webcam_clients(node_id),
            name          TEXT NOT NULL,
            device_index  INTEGER,
            resolution_w  INTEGER DEFAULT 640,
            resolution_h  INTEGER DEFAULT 480,
            jpeg_quality  INTEGER DEFAULT 40,
            target_fps    INTEGER DEFAULT 8,
            status        TEXT DEFAULT 'OFFLINE',
            last_upload   DATETIME,
            FOREIGN KEY (client_id) REFERENCES webcam_clients(node_id) ON DELETE CASCADE
        );
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_node_timestamp ON events(node_id, timestamp);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pump_readings_node_ts ON pump_readings(node_id, timestamp);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_operator_actions_ts ON operator_actions(timestamp);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_operator_actions_target ON operator_actions(action_type, target_id);")
    # Every webcam-client auth (get_webcam_client_by_key / get_webcam_camera_owner)
    # looks up by api_key_hash — index it so that lookup is not a full scan.
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_webcam_clients_api_key_hash ON webcam_clients(api_key_hash);")


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
            acknowledged_by    TEXT,
            acknowledged_at    TIMESTAMP,
            resolved_by        TEXT,
            resolved_at        TIMESTAMP,
            notes              TEXT,
            created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """))
    # Migration: add acknowledgement columns to existing events tables (Sprint A item 2)
    conn.execute(sqlalchemy.text("ALTER TABLE events ADD COLUMN IF NOT EXISTS acknowledged_by TEXT;"))
    conn.execute(sqlalchemy.text("ALTER TABLE events ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMP;"))
    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id        TEXT PRIMARY KEY,
            node_type      TEXT NOT NULL,
            last_heartbeat TIMESTAMP,
            last_upload_at TIMESTAMP,
            status         TEXT DEFAULT 'OFFLINE',
            metadata       TEXT,
            location       TEXT,
            snoozed_until  TIMESTAMP,
            snooze_reason  TEXT,
            battery_voltage REAL,
            power_source    TEXT
        );
    """))
    # PG supports IF NOT EXISTS on ADD COLUMN since 9.6 — safe migration
    conn.execute(sqlalchemy.text("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS location TEXT;"))
    conn.execute(sqlalchemy.text("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS last_upload_at TIMESTAMP;"))
    conn.execute(sqlalchemy.text("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS snoozed_until TIMESTAMP;"))
    conn.execute(sqlalchemy.text("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS snooze_reason TEXT;"))
    conn.execute(sqlalchemy.text("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS battery_voltage REAL;"))
    conn.execute(sqlalchemy.text("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS power_source TEXT;"))
    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS pump_readings (
            id          SERIAL PRIMARY KEY,
            node_id     TEXT NOT NULL,
            timestamp   TIMESTAMP NOT NULL,
            water_level REAL,
            pump_state  TEXT,
            raining         INTEGER,
            sensor_conflict INTEGER
        );
    """))
    # Migration: add raining/sensor_conflict columns to existing pump_readings tables
    conn.execute(sqlalchemy.text("ALTER TABLE pump_readings ADD COLUMN IF NOT EXISTS raining INTEGER;"))
    conn.execute(sqlalchemy.text("ALTER TABLE pump_readings ADD COLUMN IF NOT EXISTS sensor_conflict INTEGER;"))
    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS operator_actions (
            id           SERIAL PRIMARY KEY,
            timestamp    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            operator     TEXT NOT NULL,
            action_type  TEXT NOT NULL,
            target_id    TEXT,
            details_json TEXT
        );
    """))
    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS handover_note (
            id        INTEGER PRIMARY KEY CHECK (id = 1),
            note      TEXT NOT NULL DEFAULT '',
            author    TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """))
    conn.execute(sqlalchemy.text("INSERT INTO handover_note (id, note) VALUES (1, '') ON CONFLICT (id) DO NOTHING;"))

    # Weather configuration (item 9: user-configurable location for Open-Meteo)
    # Default: empty (SMG Macau XML is always enabled; Open-Meteo requires user config)
    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS weather_config (
            id        INTEGER PRIMARY KEY CHECK (id = 1),
            site_lat  REAL DEFAULT NULL,
            site_lon  REAL DEFAULT NULL,
            station_name TEXT DEFAULT NULL,
            smg_station TEXT DEFAULT NULL,
            hko_station TEXT DEFAULT NULL,
            fallback_provider TEXT DEFAULT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """))
    # Option C migration (2026-07-19): idempotent ALTER TABLE ADD COLUMN
    # for existing installations upgrading to multi-source selectors.
    # Postgres ADD COLUMN IF NOT EXISTS is safe repeat-run.
    for new_col in ('smg_station', 'hko_station', 'fallback_provider'):
        conn.execute(sqlalchemy.text(
            f"ALTER TABLE weather_config ADD COLUMN IF NOT EXISTS {new_col} TEXT DEFAULT NULL;"
        ))
    # Don't insert default row - empty means SMG Macau only.
    # NOTE: do NOT clear site_lat/site_lon/station_name here — this runs on
    # every startup, so an unconditional UPDATE ... = NULL would wipe the
    # operator's configured location on each restart (data-loss bug). New
    # installs are already empty (columns DEFAULT NULL, no default row).

    # Webcam client registry (one key per client PC, multiple cameras)
    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS webcam_clients (
            node_id      TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            api_key_hash TEXT NOT NULL,
            created_at   TIMESTAMP,
            status       TEXT DEFAULT 'OFFLINE'
        );
    """))
    conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS webcam_cameras (
            node_id       TEXT PRIMARY KEY,
            client_id     TEXT NOT NULL REFERENCES webcam_clients(node_id) ON DELETE CASCADE,
            name          TEXT NOT NULL,
            device_index  INTEGER,
            resolution_w  INTEGER DEFAULT 640,
            resolution_h  INTEGER DEFAULT 480,
            jpeg_quality  INTEGER DEFAULT 40,
            target_fps    INTEGER DEFAULT 8,
            status        TEXT DEFAULT 'OFFLINE',
            last_upload   TIMESTAMP
        );
    """))

    conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);"))
    conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_events_node_timestamp ON events(node_id, timestamp);"))
    conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);"))
    conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_pump_readings_node_ts ON pump_readings(node_id, timestamp);"))
    conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_operator_actions_ts ON operator_actions(timestamp);"))
    conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_operator_actions_target ON operator_actions(action_type, target_id);"))
    # Every webcam-client auth (get_webcam_client_by_key / get_webcam_camera_owner)
    # looks up by api_key_hash — index it so that lookup is not a full scan.
    conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_webcam_clients_api_key_hash ON webcam_clients(api_key_hash);"))


# =============================================================================
# Connection helpers
# =============================================================================

def get_backend() -> str:
    """Return the active backend ("sqlite" | "postgresql"), read at call time.

    Other modules MUST call this rather than importing the private ``_backend``
    global: an ``import`` binds the name to its value at import time (before
    ``init_db`` runs, i.e. always "sqlite"), whereas this accessor reads the
    live module state.
    """
    return _backend


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
            params.append(utcnow().isoformat())
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
        params["resolved_at"] = utcnow().isoformat()
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


def get_events_by_statuses(
    statuses: List[str], limit: int = 100, offset: int = 0
) -> List[Dict[str, Any]]:
    """Get events whose status is in ``statuses`` (single query — no N+1 fan-out).

    Replaces the ``for s in statuses: get_events_by_status(s, ...)`` loop that
    dashboard-audit-2026-07-15 called out in api/alerts.py: that pattern fetched
    O(N*(limit+offset)) rows and re-sorted in Python. This helper does the
    filter+ORDER BY+LIMIT/OFFSET on the DB (PG: ``= ANY(:statuses)`` bound
    natively; SQLite: expanded ``IN (?,?,?)`` placeholders).
    """
    if not statuses:
        return []
    if _backend == "postgresql":
        return _pg_fetch_many_sync(
            "SELECT * FROM events WHERE status = ANY(:statuses) "
            "ORDER BY timestamp DESC LIMIT :limit OFFSET :offset",
            {"statuses": list(statuses), "limit": limit, "offset": offset},
        )
    db = get_db()
    cursor = db.cursor()
    placeholders = ",".join("?" for _ in statuses)
    cursor.execute(
        f"SELECT * FROM events WHERE status IN ({placeholders}) "
        f"ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        (*statuses, limit, offset),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_events_by_ids(ids: List[int]) -> List[Dict[str, Any]]:
    """Fetch events by primary key set (single query — no ORDER/LIMIT filter).

    Used by bulk-ack/resolve to pre-select the actual selected rows for per-id
    WebSocket broadcast. Previously `get_events_by_statuses` was mis-used with
    a top-N-of-PENDING window, which silently dropped selected rows older than
    the top window — audit finding MED #3.
    """
    if not ids:
        return []
    if _backend == "postgresql":
        return _pg_fetch_many_sync(
            "SELECT * FROM events WHERE id = ANY(:ids)",
            {"ids": list(ids)},
        )
    db = get_db()
    cursor = db.cursor()
    placeholders = ",".join("?" for _ in ids)
    cursor.execute(
        f"SELECT * FROM events WHERE id IN ({placeholders})",
        tuple(ids),
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
    """Insert or update a node record. Preserves existing location field."""
    metadata_json = json.dumps(metadata) if metadata else None
    now = utcnow().isoformat()

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


def delete_node(node_id: str) -> bool:
    """Remove a node and its time-series rows (pump_readings, events).

    Preserves `operator_actions` — that table is append-only audit history
    where `target_id` is a soft reference that intentionally survives retention
    and deletion of the target row (see table comment at CREATE TABLE
    operator_actions above). Returns True if the node row was removed."""
    if _backend == "postgresql":
        import sqlalchemy
        database_url = os.environ.get("DATABASE_URL", "")
        engine = sqlalchemy.create_engine(database_url)
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("DELETE FROM pump_readings WHERE node_id = :id"),
                         {"id": node_id})
            conn.execute(sqlalchemy.text("DELETE FROM events WHERE node_id = :id"),
                         {"id": node_id})
            result = conn.execute(sqlalchemy.text("DELETE FROM nodes WHERE node_id = :id"),
                                  {"id": node_id})
            conn.commit()
            return result.rowcount > 0

    with get_db_cursor() as cursor:
        cursor.execute("DELETE FROM pump_readings WHERE node_id = ?", (node_id,))
        cursor.execute("DELETE FROM events WHERE node_id = ?", (node_id,))
        cursor.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
        return cursor.rowcount > 0


def set_node_location(node_id: str, location: Optional[str]) -> bool:
    """Set the user-defined deployment location for a node. Pass None or '' to clear."""
    loc = (location or "").strip() or None

    if _backend == "postgresql":
        import sqlalchemy
        database_url = os.environ.get("DATABASE_URL", "")
        engine = sqlalchemy.create_engine(database_url)
        with engine.connect() as conn:
            result = conn.execute(
                sqlalchemy.text("UPDATE nodes SET location = :loc WHERE node_id = :id"),
                {"loc": loc, "id": node_id},
            )
            conn.commit()
            return result.rowcount > 0

    with get_db_cursor() as cursor:
        cursor.execute("UPDATE nodes SET location = ? WHERE node_id = ?", (loc, node_id))
        return cursor.rowcount > 0


def touch_node_upload(node_id: str) -> None:
    """Item 13: stamp last_upload_at = now. Auto-creates the node row if missing
    so a snapshot from a never-seen node still gets recorded.
    Never raises (data-quality column, not load-bearing for ingest)."""
    now = utcnow().isoformat()
    try:
        if _backend == "postgresql":
            import sqlalchemy
            database_url = os.environ.get("DATABASE_URL", "")
            engine = sqlalchemy.create_engine(database_url)
            with engine.connect() as conn:
                conn.execute(
                    sqlalchemy.text(
                        "INSERT INTO nodes (node_id, node_type, last_upload_at) VALUES (:id, 'glass', :ts) "
                        "ON CONFLICT (node_id) DO UPDATE SET last_upload_at = EXCLUDED.last_upload_at"
                    ),
                    {"id": node_id, "ts": now},
                )
                conn.commit()
            return
        with get_db_cursor() as cursor:
            cursor.execute(
                "INSERT INTO nodes (node_id, node_type, last_upload_at) VALUES (?, 'glass', ?) "
                "ON CONFLICT(node_id) DO UPDATE SET last_upload_at = excluded.last_upload_at",
                (node_id, now),
            )
    except Exception as e:
        logger.warning(f"touch_node_upload({node_id}) failed: {e}")


def set_node_snooze(node_id: str, snoozed_until: Optional[str], reason: Optional[str]) -> bool:
    """Item 17: set or clear node snooze. snoozed_until=None clears."""
    if _backend == "postgresql":
        import sqlalchemy
        database_url = os.environ.get("DATABASE_URL", "")
        engine = sqlalchemy.create_engine(database_url)
        with engine.connect() as conn:
            result = conn.execute(
                sqlalchemy.text("UPDATE nodes SET snoozed_until = :u, snooze_reason = :r WHERE node_id = :id"),
                {"u": snoozed_until, "r": reason, "id": node_id},
            )
            conn.commit()
            return result.rowcount > 0
    with get_db_cursor() as cursor:
        cursor.execute(
            "UPDATE nodes SET snoozed_until = ?, snooze_reason = ? WHERE node_id = ?",
            (snoozed_until, reason, node_id),
        )
        return cursor.rowcount > 0


def get_weather_config() -> Dict[str, Any]:
    """Item 9: get weather location config (singleton row).

    Returns None for lat/lon and the 3 Option-C selectors when unset —
    services/weather_service._tick falls back to hardcoded defaults
    (SMG=外港, HKO=Hong Kong Observatory, fallback_provider=both).
    """
    empty = {"site_lat": None, "site_lon": None, "station_name": None,
             "smg_station": None, "hko_station": None, "fallback_provider": None}
    cols = "site_lat, site_lon, station_name, smg_station, hko_station, fallback_provider"
    if _backend == "postgresql":
        import sqlalchemy
        database_url = os.environ.get("DATABASE_URL", "")
        engine = sqlalchemy.create_engine(database_url)
        with engine.connect() as conn:
            result = conn.execute(sqlalchemy.text(f"SELECT {cols} FROM weather_config WHERE id = 1;"))
            row = result.fetchone()
            if row:
                return {"site_lat": row[0], "site_lon": row[1], "station_name": row[2],
                        "smg_station": row[3], "hko_station": row[4], "fallback_provider": row[5]}
        return empty
    with get_db_cursor() as cursor:
        cursor.execute(f"SELECT {cols} FROM weather_config WHERE id = 1;")
        row = cursor.fetchone()
        if row:
            return {"site_lat": row["site_lat"], "site_lon": row["site_lon"],
                    "station_name": row["station_name"], "smg_station": row["smg_station"],
                    "hko_station": row["hko_station"], "fallback_provider": row["fallback_provider"]}
    return empty


def set_weather_config(
    site_lat: Optional[float],
    site_lon: Optional[float],
    station_name: Optional[str] = None,
    smg_station: Optional[str] = None,
    hko_station: Optional[str] = None,
    fallback_provider: Optional[str] = None,
) -> bool:
    """Item 9 + Option C (2026-07-19): update weather location config.

    Kwargs are the Option-C multi-source selectors (SMG station, HKO
    station, fallback provider). Any None passed here clears that field
    — the caller (api/weather.py PUT handler) merges with the existing
    row when a partial update is intended, so we don't ambiguously
    interpret None as "leave unchanged" here.

    If lat/lon is None, disables Open-Meteo forecast (falls back to
    settings.SITE_LAT/SITE_LON).
    """
    if _backend == "postgresql":
        import sqlalchemy
        database_url = os.environ.get("DATABASE_URL", "")
        engine = sqlalchemy.create_engine(database_url)
        with engine.connect() as conn:
            conn.execute(
                sqlalchemy.text(
                    "INSERT INTO weather_config (id, site_lat, site_lon, station_name, "
                    "smg_station, hko_station, fallback_provider) "
                    "VALUES (1, :lat, :lon, :name, :smg, :hko, :fb) "
                    "ON CONFLICT (id) DO UPDATE SET site_lat = :lat, site_lon = :lon, "
                    "station_name = :name, smg_station = :smg, hko_station = :hko, "
                    "fallback_provider = :fb, updated_at = CURRENT_TIMESTAMP;"
                ),
                {"lat": site_lat, "lon": site_lon, "name": station_name,
                 "smg": smg_station, "hko": hko_station, "fb": fallback_provider},
            )
            conn.commit()
        return True
    with get_db_cursor() as cursor:
        cursor.execute(
            "INSERT OR REPLACE INTO weather_config "
            "(id, site_lat, site_lon, station_name, smg_station, hko_station, fallback_provider) "
            "VALUES (1, ?, ?, ?, ?, ?, ?);",
            (site_lat, site_lon, station_name, smg_station, hko_station, fallback_provider),
        )
        return cursor.rowcount > 0


def insert_pump_reading(node_id: str, timestamp: str, water_level: Optional[float],
                        pump_state: Optional[str], raining: Optional[bool] = None,
                        sensor_conflict: Optional[bool] = None) -> None:
    """Append one water-level / pump-state sample for time-series history."""
    raining_val = int(raining) if raining is not None else None
    sensor_conflict_val = int(sensor_conflict) if sensor_conflict is not None else None

    if _backend == "postgresql":
        import sqlalchemy
        database_url = os.environ.get("DATABASE_URL", "")
        engine = sqlalchemy.create_engine(database_url)
        with engine.connect() as conn:
            conn.execute(
                sqlalchemy.text("""
                    INSERT INTO pump_readings (node_id, timestamp, water_level, pump_state, raining, sensor_conflict)
                    VALUES (:id, :ts, :wl, :ps, :rn, :sc)
                """),
                {"id": node_id, "ts": timestamp, "wl": water_level, "ps": pump_state,
                 "rn": raining_val, "sc": sensor_conflict_val},
            )
            conn.commit()
        return

    with get_db_cursor() as cursor:
        cursor.execute(
            "INSERT INTO pump_readings (node_id, timestamp, water_level, pump_state, raining, sensor_conflict) VALUES (?, ?, ?, ?, ?, ?)",
            (node_id, timestamp, water_level, pump_state, raining_val, sensor_conflict_val),
        )


def get_pump_readings(node_id: str, start: str, end: str,
                      limit: int = 5000) -> List[Dict[str, Any]]:
    """Return rows in [start, end] ordered by timestamp ASC. ISO-8601 strings."""
    if _backend == "postgresql":
        return _pg_fetch_many_sync(
            """SELECT timestamp, water_level, pump_state, raining, sensor_conflict FROM pump_readings
               WHERE node_id = :id AND timestamp BETWEEN :s AND :e
               ORDER BY timestamp ASC LIMIT :lim""",
            {"id": node_id, "s": start, "e": end, "lim": limit},
        )

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """SELECT timestamp, water_level, pump_state, raining, sensor_conflict FROM pump_readings
           WHERE node_id = ? AND timestamp BETWEEN ? AND ?
           ORDER BY timestamp ASC LIMIT ?""",
        (node_id, start, end, limit),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_pump_readings_multi(
    node_ids: List[str], start: str, end: str, limit: int = 50000
) -> Dict[str, List[Dict[str, Any]]]:
    """Batch pump-reading fetch grouped by node_id — one SQL round trip.

    Fixes the N+1 in api/nodes.py pump_cycles_batch (dashboard-audit-2026-07-15
    TODO): the previous implementation called get_pump_readings once per pump
    node. This helper issues a single SELECT and groups in Python.

    Return shape: ``{node_id: [{timestamp, water_level, pump_state, raining,
    sensor_conflict}, ...], ...}``. Missing nodes are ABSENT from the dict;
    callers should treat ``dict.get(nid, [])`` as "no readings in window".
    The per-row shape matches ``get_pump_readings`` (node_id is stripped from
    each row after grouping) so ``_count_pump_cycles`` runs unchanged.
    """
    if not node_ids:
        return {}
    if _backend == "postgresql":
        rows = _pg_fetch_many_sync(
            "SELECT node_id, timestamp, water_level, pump_state, raining, sensor_conflict "
            "FROM pump_readings WHERE node_id = ANY(:ids) "
            "AND timestamp BETWEEN :s AND :e "
            "ORDER BY node_id, timestamp ASC LIMIT :lim",
            {"ids": list(node_ids), "s": start, "e": end, "lim": limit},
        )
    else:
        db = get_db()
        cursor = db.cursor()
        placeholders = ",".join("?" for _ in node_ids)
        cursor.execute(
            f"SELECT node_id, timestamp, water_level, pump_state, raining, sensor_conflict "
            f"FROM pump_readings WHERE node_id IN ({placeholders}) "
            f"AND timestamp BETWEEN ? AND ? "
            f"ORDER BY node_id, timestamp ASC LIMIT ?",
            (*node_ids, start, end, limit),
        )
        rows = [dict(r) for r in cursor.fetchall()]

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        nid = r.pop("node_id", None)
        if nid is None:
            continue
        grouped.setdefault(nid, []).append(r)
    return grouped


def update_node_heartbeat(node_id: str, metadata: Optional[Dict[str, Any]] = None):
    """Update node last heartbeat timestamp."""
    metadata_json = json.dumps(metadata) if metadata else None
    now = utcnow().isoformat()

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
# Dashboard read helpers  (unified SQLite / PostgreSQL)
# =============================================================================

def get_pending_alert_ids() -> List[int]:
    """IDs of events still in PENDING status.

    Seeds the dashboard audio-alert loop so an operator opening the page
    mid-storm hears already-queued alerts. No ORDER BY (matches legacy SQL).
    """
    if get_backend() == "postgresql":
        rows = _pg_fetch_many_sync("SELECT id FROM events WHERE status = 'PENDING'", {})
        return [r["id"] for r in rows]
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM events WHERE status = 'PENDING'")
    return [r[0] for r in cursor.fetchall()]


def get_handover_note_row() -> Optional[Dict[str, Any]]:
    """Item 16: raw handover_note singleton (id=1), or None if absent.

    Returns {note, author, updated_at}; the caller applies its own TTL and
    empty-string handling.
    """
    if get_backend() == "postgresql":
        return _pg_fetch_one_sync(
            "SELECT note, author, updated_at FROM handover_note WHERE id = 1", {}
        )
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT note, author, updated_at FROM handover_note WHERE id = 1;")
    row = cursor.fetchone()
    return dict(row) if row else None


def set_handover_note(note: str, author: str, updated_at_iso: str) -> None:
    """Item 16: last-write-wins update of the handover_note singleton (id=1).

    ``updated_at_iso`` is stamped by the caller (naive-UTC isoformat) rather
    than SQL CURRENT_TIMESTAMP so both backends store the same shape.
    """
    if get_backend() == "postgresql":
        _pg_set_handover_note_sync(note, author, updated_at_iso)
        return
    with get_db_cursor() as cursor:
        cursor.execute(
            "UPDATE handover_note SET note = ?, author = ?, updated_at = ? WHERE id = 1;",
            (note, author, updated_at_iso),
        )


def _pg_set_handover_note_sync(note, author, updated_at_iso) -> None:
    import sqlalchemy
    database_url = os.environ.get("DATABASE_URL", "")
    engine = sqlalchemy.create_engine(database_url)
    with engine.connect() as conn:
        conn.execute(
            sqlalchemy.text(
                "UPDATE handover_note SET note = :note, author = :author, updated_at = :ts WHERE id = 1"
            ),
            {"note": note, "author": author, "ts": updated_at_iso},
        )
        conn.commit()


def get_effective_handover_note(ttl_hours: int = 24) -> Optional[Dict[str, Any]]:
    """Item 16: handover note with the read-time TTL applied.

    Wraps get_handover_note_row() + the 24h expiry check so api/handover.py
    and main.py share ONE TTL implementation on both backends (the old
    api/handover.py copy went straight to get_db_cursor(), which raises
    under PostgreSQL). Naive-UTC compare: normalizes 'Z'/space-delimited
    stamps and strips any tzinfo before comparing against utcnow().

    Returns {note, author, updated_at, expired}, or None when no row exists.
    """
    row = get_handover_note_row()
    if row is None:
        return None
    updated_at = row.get("updated_at")
    expired = False
    if updated_at:
        try:
            ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00").replace(" ", "T"))
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)
            expired = (utcnow() - ts) > timedelta(hours=ttl_hours)
        except ValueError:
            expired = False
    return {
        "note": row.get("note") or "",
        "author": row.get("author"),
        "updated_at": updated_at,
        "expired": expired,
    }


def get_event_created_ats(since_iso: str) -> List[str]:
    """Item 11: created_at values (ASC) for events at/after ``since_iso``.

    Feeds the alert-rate sparkline. Returns the raw stored value coerced to
    str (SQLite stores an ISO string; PostgreSQL returns a datetime).
    """
    if get_backend() == "postgresql":
        rows = _pg_fetch_many_sync(
            "SELECT created_at FROM events WHERE created_at >= :since ORDER BY created_at ASC",
            {"since": since_iso},
        )
        return [str(r["created_at"]) for r in rows]
    db = get_db()
    cursor = db.cursor()
    # datetime() on BOTH sides normalizes the stored space-delimited
    # "YYYY-MM-DD HH:MM:SS" (SQLite CURRENT_TIMESTAMP) against the
    # T-delimited since_iso — same idiom as retention_service.py. A raw
    # string compare returns ZERO rows for same-day windows (' ' < 'T'),
    # which blanked the alert-rate sparkline.
    cursor.execute(
        "SELECT created_at FROM events WHERE datetime(created_at) >= datetime(?) ORDER BY created_at ASC;",
        (since_iso,),
    )
    return [str(r["created_at"]) for r in cursor.fetchall()]


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


def _pg_execute_sync(query: str, params: dict) -> None:
    """Synchronous PostgreSQL write (INSERT/UPDATE/DELETE) via SQLAlchemy."""
    import sqlalchemy
    engine = sqlalchemy.create_engine(os.environ.get("DATABASE_URL", ""))
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text(query), params)
        conn.commit()


# =============================================================================
# Webcam Client Operations  (unified SQLite / PostgreSQL)
# =============================================================================

def create_webcam_client(name: str) -> dict:
    """Register a new webcam client PC. Returns {node_id, api_key, api_key_hash}."""
    node_id = f"webcam_{secrets.token_hex(4)}"
    api_key = f"sk-webcam-{secrets.token_urlsafe(32)}"
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    now = utcnow().isoformat()
    if get_backend() == "postgresql":
        _pg_execute_sync(
            "INSERT INTO webcam_clients (node_id, name, api_key_hash, created_at, status) "
            "VALUES (:node_id, :name, :hash, :now, 'OFFLINE')",
            {"node_id": node_id, "name": name, "hash": api_key_hash, "now": now},
        )
    else:
        with get_db_cursor() as cursor:
            cursor.execute(
                "INSERT INTO webcam_clients (node_id, name, api_key_hash, created_at, status) "
                "VALUES (?, ?, ?, ?, 'OFFLINE')",
                (node_id, name, api_key_hash, now),
            )
    return {"node_id": node_id, "api_key": api_key, "api_key_hash": api_key_hash}


def get_webcam_client_by_key(api_key: str) -> Optional[dict]:
    """Look up a webcam client by its raw API key (hashed for comparison)."""
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    if get_backend() == "postgresql":
        return _pg_fetch_one_sync(
            "SELECT node_id, name, status FROM webcam_clients WHERE api_key_hash = :h",
            {"h": api_key_hash},
        )
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT node_id, name, status FROM webcam_clients WHERE api_key_hash = ?",
            (api_key_hash,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def revoke_webcam_key(node_id: str) -> dict:
    """Rotate the API key for a webcam client. Returns new {api_key, api_key_hash}."""
    new_key = f"sk-webcam-{secrets.token_urlsafe(32)}"
    new_hash = hashlib.sha256(new_key.encode()).hexdigest()
    if get_backend() == "postgresql":
        _pg_execute_sync(
            "UPDATE webcam_clients SET api_key_hash = :h WHERE node_id = :id",
            {"h": new_hash, "id": node_id},
        )
    else:
        with get_db_cursor() as cursor:
            cursor.execute(
                "UPDATE webcam_clients SET api_key_hash = ? WHERE node_id = ?",
                (new_hash, node_id),
            )
    return {"api_key": new_key, "api_key_hash": new_hash}


def register_webcam_cameras(client_node_id: str, cameras: list) -> list:
    """Register one or more cameras under a webcam client. Returns list of {node_id, name}."""
    results = []
    for cam in cameras:
        cam_node_id = f"webcam_{secrets.token_hex(4)}"
        resolution = cam.get("resolution", [640, 480])
        if get_backend() == "postgresql":
            _pg_execute_sync(
                "INSERT INTO webcam_cameras (node_id, client_id, name, device_index, "
                "resolution_w, resolution_h, jpeg_quality, target_fps) "
                "VALUES (:nid, :cid, :name, :didx, :rw, :rh, :q, :fps)",
                {"nid": cam_node_id, "cid": client_node_id, "name": cam["name"],
                 "didx": cam.get("device_index", 0), "rw": resolution[0],
                 "rh": resolution[1], "q": cam.get("jpeg_quality", 40),
                 "fps": cam.get("target_fps", 8)},
            )
        else:
            with get_db_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO webcam_cameras (node_id, client_id, name, device_index, "
                    "resolution_w, resolution_h, jpeg_quality, target_fps) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (cam_node_id, client_node_id, cam["name"], cam.get("device_index", 0),
                     resolution[0], resolution[1],
                     cam.get("jpeg_quality", 40), cam.get("target_fps", 8)),
                )
        results.append({"node_id": cam_node_id, "name": cam["name"]})
    return results


def get_webcam_cameras(client_node_id: str) -> list:
    """List all cameras registered under a webcam client."""
    if get_backend() == "postgresql":
        return _pg_fetch_many_sync(
            "SELECT node_id, name, device_index, status FROM webcam_cameras WHERE client_id = :cid",
            {"cid": client_node_id},
        )
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT node_id, name, device_index, status FROM webcam_cameras WHERE client_id = ?",
            (client_node_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_webcam_camera_owner(cam_node_id: str, api_key_hash: str) -> Optional[dict]:
    """Verify a camera belongs to the client identified by api_key_hash.

    Returns {node_id, client_id} if ownership is confirmed, else None.
    """
    if get_backend() == "postgresql":
        return _pg_fetch_one_sync(
            "SELECT wc.node_id, wc.client_id FROM webcam_cameras wc "
            "JOIN webcam_clients wcl ON wc.client_id = wcl.node_id "
            "WHERE wc.node_id = :nid AND wcl.api_key_hash = :h",
            {"nid": cam_node_id, "h": api_key_hash},
        )
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT wc.node_id, wc.client_id FROM webcam_cameras wc "
            "JOIN webcam_clients wcl ON wc.client_id = wcl.node_id "
            "WHERE wc.node_id = ? AND wcl.api_key_hash = ?",
            (cam_node_id, api_key_hash),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def touch_webcam_upload(node_id: str) -> None:
    """Stamp webcam_cameras.last_upload = now for a camera node (fixes audit C2:
    the column existed but had no writer). Writes ONLY webcam_cameras and never
    `nodes` -- reusing the edge `touch_node_upload` is what causes audit C3, as
    that function INSERTs a node_type='glass' row into `nodes`, giving each webcam
    a second identity. Never raises (data-quality column, not load-bearing for
    ingest)."""
    now = utcnow().isoformat()
    try:
        if get_backend() == "postgresql":
            _pg_execute_sync(
                "UPDATE webcam_cameras SET last_upload = :ts WHERE node_id = :nid",
                {"ts": now, "nid": node_id},
            )
            return
        with get_db_cursor() as cursor:
            cursor.execute(
                "UPDATE webcam_cameras SET last_upload = ? WHERE node_id = ?",
                (now, node_id),
            )
    except Exception as e:
        logger.debug(f"touch_webcam_upload failed for {node_id}: {e}")
