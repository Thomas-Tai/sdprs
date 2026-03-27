# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Retention Service
Smart Disaster Prevention Response System

This module provides data retention cleanup for managing disk space
by removing old event records and MP4 files.
"""

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Configure logging
logger = logging.getLogger("retention_service")


def run_retention_cleanup(
    db_path: str,
    storage_dir: str,
    retention_days: int = 30
) -> Dict[str, Any]:
    """
    Execute retention cleanup for expired events.
    
    Removes:
    - MP4 files older than retention_days
    - Database records older than retention_days
    - Empty directories after cleanup
    
    Args:
        db_path: Path to SQLite database
        storage_dir: Root storage directory
        retention_days: Number of days to retain (default: 30)
        
    Returns:
        Dict with cleanup statistics:
        - deleted_events: Number of deleted database records
        - deleted_files: Number of deleted MP4 files
        - deleted_dirs: Number of deleted empty directories
        - errors: List of error messages
    """
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    cutoff_str = cutoff.isoformat()
    
    logger.info(f"Starting retention cleanup: cutoff={cutoff_str}, retention_days={retention_days}")
    
    errors = []
    deleted_files = 0
    
    # Connect to database
    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        return {
            "deleted_events": 0,
            "deleted_files": 0,
            "deleted_dirs": 0,
            "errors": [f"Database connection failed: {e}"]
        }
    
    try:
        # Query expired events
        cursor = db.cursor()
        cursor.execute(
            "SELECT id, mp4_path FROM events WHERE created_at < ?",
            (cutoff_str,)
        )
        expired_events = cursor.fetchall()
        
        logger.info(f"Found {len(expired_events)} expired events")
        
        # Collect file paths before deleting DB records
        file_paths_to_delete = []
        for event in expired_events:
            mp4_path = event["mp4_path"]
            if mp4_path and os.path.exists(mp4_path):
                file_paths_to_delete.append(mp4_path)
        
        # Delete database records first (rollback-safe)
        cursor.execute("DELETE FROM events WHERE created_at < ?", (cutoff_str,))
        deleted_events = cursor.rowcount
        db.commit()
        
        # Then delete MP4 files
        for mp4_path in file_paths_to_delete:
            try:
                os.remove(mp4_path)
                deleted_files += 1
                logger.debug(f"Deleted MP4: {mp4_path}")
            except OSError as e:
                error_msg = f"Failed to delete {mp4_path}: {e}"
                errors.append(error_msg)
                logger.error(error_msg)
        
        logger.info(f"Deleted {deleted_events} database records")
        
    except Exception as e:
        logger.error(f"Database operation failed: {e}")
        errors.append(f"Database operation failed: {e}")
        deleted_events = 0
    finally:
        db.close()
    
    # Clean up empty directories
    deleted_dirs = 0
    events_dir = os.path.join(storage_dir, "events")
    
    if os.path.exists(events_dir):
        try:
            for node_dir_name in os.listdir(events_dir):
                node_path = os.path.join(events_dir, node_dir_name)
                if os.path.isdir(node_path):
                    # Check if directory is empty
                    if not os.listdir(node_path):
                        try:
                            os.rmdir(node_path)
                            deleted_dirs += 1
                            logger.debug(f"Removed empty directory: {node_path}")
                        except OSError as e:
                            logger.warning(f"Failed to remove directory {node_path}: {e}")
        except Exception as e:
            logger.error(f"Directory cleanup failed: {e}")
            errors.append(f"Directory cleanup failed: {e}")
    
    # Log summary
    logger.info(
        f"Retention cleanup complete: "
        f"deleted_events={deleted_events}, "
        f"deleted_files={deleted_files}, "
        f"deleted_dirs={deleted_dirs}, "
        f"errors={len(errors)}"
    )
    
    return {
        "deleted_events": deleted_events,
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "errors": errors
    }


def setup_retention_scheduler(
    scheduler: AsyncIOScheduler,
    db_path: str,
    storage_dir: str,
    retention_days: int = 30
) -> None:
    """
    Configure APScheduler to run retention cleanup daily at 3:00 AM.
    
    Args:
        scheduler: The APScheduler instance
        db_path: Path to SQLite database
        storage_dir: Root storage directory
        retention_days: Number of days to retain (default: 30)
    """
    scheduler.add_job(
        run_retention_cleanup,
        trigger=CronTrigger(hour=3, minute=0),
        args=[db_path, storage_dir, retention_days],
        id="retention_cleanup",
        name="Daily retention cleanup",
        replace_existing=True
    )
    
    logger.info(
        f"Scheduled daily retention cleanup at 03:00 "
        f"(db={db_path}, storage={storage_dir}, retention={retention_days} days)"
    )


def get_storage_stats(storage_dir: str) -> Dict[str, Any]:
    """
    Get storage statistics.
    
    Args:
        storage_dir: Root storage directory
        
    Returns:
        Dict with storage statistics:
        - total_files: Total MP4 files
        - total_size_bytes: Total size in bytes
        - total_size_mb: Total size in MB
        - nodes: Per-node statistics
    """
    events_dir = os.path.join(storage_dir, "events")
    
    if not os.path.exists(events_dir):
        return {
            "total_files": 0,
            "total_size_bytes": 0,
            "total_size_mb": 0.0,
            "nodes": {}
        }
    
    total_files = 0
    total_size = 0
    nodes = {}
    
    try:
        for node_dir_name in os.listdir(events_dir):
            node_path = os.path.join(events_dir, node_dir_name)
            if os.path.isdir(node_path):
                node_files = 0
                node_size = 0
                
                for filename in os.listdir(node_path):
                    if filename.endswith(".mp4"):
                        file_path = os.path.join(node_path, filename)
                        try:
                            file_size = os.path.getsize(file_path)
                            node_files += 1
                            node_size += file_size
                        except OSError:
                            pass
                
                if node_files > 0:
                    nodes[node_dir_name] = {
                        "files": node_files,
                        "size_bytes": node_size,
                        "size_mb": round(node_size / (1024 * 1024), 2)
                    }
                    total_files += node_files
                    total_size += node_size
    
    except Exception as e:
        logger.error(f"Failed to get storage stats: {e}")
    
    return {
        "total_files": total_files,
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "nodes": nodes
    }


__all__ = [
    "run_retention_cleanup",
    "setup_retention_scheduler",
    "get_storage_stats",
]