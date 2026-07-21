# -*- coding: utf-8 -*-
"""
SDPRS Central Server - HLS Relay Service (Task 3)
Smart Disaster Prevention Response System

In-memory + on-disk state for the webcam-client HLS relay:
- HLS segment/playlist storage under HLS_STORAGE_PATH/<node_id>/
- viewer counts per camera node (drives stream start/stop commands)
- per-node command queues for the long-poll GET .../commands endpoint
- idle-stream cleanup (APScheduler job, see main.py)
"""
import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Dict, Optional

from ..config import get_settings

logger = logging.getLogger("hls_service")

_viewer_count: Dict[str, int] = {}
_command_queues: Dict[str, asyncio.Queue] = {}
_last_activity: Dict[str, float] = {}


def get_hls_dir(node_id: str) -> Path:
    settings = get_settings()
    base = Path(settings.HLS_STORAGE_PATH)
    base.mkdir(parents=True, exist_ok=True)
    node_dir = base / node_id
    node_dir.mkdir(parents=True, exist_ok=True)
    return node_dir


def store_hls_segment(node_id: str, filename: str, data: bytes) -> None:
    node_dir = get_hls_dir(node_id)
    target = node_dir / filename
    target.write_bytes(data)
    _last_activity[node_id] = time.time()
    if filename.endswith(".ts"):
        _prune_old_segments(node_dir)


def _prune_old_segments(node_dir: Path) -> None:
    settings = get_settings()
    max_seg = settings.HLS_MAX_SEGMENTS
    ts_files = sorted(node_dir.glob("seg_*.ts"), key=lambda f: f.name)
    while len(ts_files) > max_seg:
        oldest = ts_files.pop(0)
        oldest.unlink(missing_ok=True)


# Only these two extensions are ever served. HLS needs nothing else, and an
# allowlist means a traversal bug alone is not enough to read arbitrary files.
_SERVABLE_SUFFIXES = (".ts", ".m3u8")


def get_hls_file(node_id: str, filename: str) -> Optional[bytes]:
    settings = get_settings()
    base = Path(settings.HLS_STORAGE_PATH).resolve()
    if not filename.endswith(_SERVABLE_SUFFIXES):
        return None
    target = (base / node_id / filename).resolve()
    # is_relative_to(), NOT str.startswith(). A string prefix test passes for
    # a sibling directory whose name merely starts with the base name --
    # base "storage/hls" would happily match "storage/hls-evil/secret.ts".
    if not target.is_relative_to(base):
        return None
    if target.is_file():
        return target.read_bytes()
    return None


def cleanup_hls_dir(node_id: str) -> None:
    settings = get_settings()
    base = Path(settings.HLS_STORAGE_PATH)
    node_dir = base / node_id
    if node_dir.exists():
        shutil.rmtree(node_dir, ignore_errors=True)
    _viewer_count.pop(node_id, None)
    _last_activity.pop(node_id, None)
    # _command_queues must be dropped here too. get_command_queue() creates an
    # entry for ANY node_id that reaches the long-poll endpoint, so leaving it
    # behind lets caller-supplied ids accumulate without bound.
    _command_queues.pop(node_id, None)


def get_viewer_count(node_id: str) -> int:
    return _viewer_count.get(node_id, 0)


def increment_viewer(node_id: str) -> int:
    _viewer_count[node_id] = _viewer_count.get(node_id, 0) + 1
    return _viewer_count[node_id]


def decrement_viewer(node_id: str) -> int:
    count = max(0, _viewer_count.get(node_id, 0) - 1)
    _viewer_count[node_id] = count
    return count


def get_command_queue(node_id: str) -> asyncio.Queue:
    if node_id not in _command_queues:
        _command_queues[node_id] = asyncio.Queue()
    return _command_queues[node_id]


async def enqueue_command(node_id: str, command: str, params: Optional[dict] = None) -> None:
    q = get_command_queue(node_id)
    await q.put({"command": command, "params": params})


async def dequeue_command(node_id: str, timeout: float = 5.0) -> Optional[dict]:
    q = get_command_queue(node_id)
    try:
        return await asyncio.wait_for(q.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


def cleanup_stale_streams() -> None:
    settings = get_settings()
    timeout = settings.HLS_VIEWER_TIMEOUT_SECONDS
    now = time.time()
    stale = [nid for nid, ts in _last_activity.items()
             if now - ts > 60 and _viewer_count.get(nid, 0) == 0]
    for nid in stale:
        logger.info(f"Cleaning stale HLS dir for {nid}")
        cleanup_hls_dir(nid)


__all__ = [
    "get_hls_dir", "store_hls_segment", "get_hls_file", "cleanup_hls_dir",
    "get_viewer_count", "increment_viewer", "decrement_viewer",
    "get_command_queue", "enqueue_command", "dequeue_command",
    "cleanup_stale_streams",
]
