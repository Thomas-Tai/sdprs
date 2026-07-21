# -*- coding: utf-8 -*-
"""
SDPRS Central Server - HLS Relay Service (Task 3)
Smart Disaster Prevention Response System

In-memory + on-disk state for the webcam-client HLS relay:
- HLS segment/playlist storage under HLS_STORAGE_PATH/<node_id>/
- per-node viewer LEASE (single lease per camera; drives stream start/stop)
- per-node command queues for the long-poll GET .../commands endpoint
- idle-stream cleanup + lease-expiry enforcement (AsyncIOScheduler job, see main.py)
"""
import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Dict, Optional

from ..config import get_settings

logger = logging.getLogger("hls_service")

# --- Viewer lease model (fixes audit H1/H2) --------------------------------
# The dashboard sends no per-viewer token, so start/renew/stop are per-node. We
# model ONE lease per camera: "is anyone watching?" (0/1). Two operators viewing
# the same camera share the lease; either one's renew keeps it alive; an explicit
# stop from one releases it and the other's next 30s renew re-arms it (brief
# re-start). The failure mode this MUST kill -- a forgotten tab pinning a field
# PC's uplink forever -- is fixed because the lease expires without renews. Do
# not silently "improve" this into per-viewer refcounting without updating spec.
_stream_leases: Dict[str, float] = {}       # node_id -> lease expiry (epoch seconds)
_stream_stopped_at: Dict[str, float] = {}   # node_id -> when we last forced/observed a stop
_command_queues: Dict[str, asyncio.Queue] = {}
_last_activity: Dict[str, float] = {}

LEASE_TTL_SECONDS = 90   # spec §391: survives two missed 30s renews (one network blip)
# Last-touch time for command queues specifically. get_command_queue() will
# auto-create a queue for any node_id reaching the long-poll endpoint, and
# that node_id may never call store_hls_segment (so it would never appear in
# _last_activity). Tracking this separately means every queue we create has
# a reclamation path, not just ones that also upload segments.
_command_queue_activity: Dict[str, float] = {}


def get_hls_dir(node_id: str) -> Path:
    settings = get_settings()
    base = Path(settings.HLS_STORAGE_PATH)
    base.mkdir(parents=True, exist_ok=True)
    node_dir = base / node_id
    node_dir.mkdir(parents=True, exist_ok=True)
    return node_dir


def store_hls_segment(node_id: str, filename: str, data: bytes) -> None:
    settings = get_settings()
    base = Path(settings.HLS_STORAGE_PATH).resolve()
    node_dir = get_hls_dir(node_id)
    target = (node_dir / filename).resolve()
    # Mirror the containment check get_hls_file() already does on the read
    # side. filename is fully client-controlled and only gated by extension
    # in the router; on Windows pathlib treats "\" as a separator while
    # Starlette's single-segment route match only excludes "/", so
    # filename="..\\..\\evil.ts" would otherwise escape the node directory.
    if not target.is_relative_to(base):
        raise ValueError(f"Refusing to write outside HLS storage root: {filename!r}")
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
    _stream_leases.pop(node_id, None)
    _stream_stopped_at.pop(node_id, None)
    _last_activity.pop(node_id, None)
    # _command_queues must be dropped here too. get_command_queue() creates an
    # entry for ANY node_id that reaches the long-poll endpoint, so leaving it
    # behind lets caller-supplied ids accumulate without bound.
    _command_queues.pop(node_id, None)
    _command_queue_activity.pop(node_id, None)


def touch_lease(node_id: str) -> bool:
    """Arm or extend the viewer lease (start/renew). Returns True on a 0->1
    transition (no live lease before) so the caller enqueues stream_start +
    broadcasts webcam_stream_started."""
    now = time.time()
    was_live = _stream_leases.get(node_id, 0.0) > now
    _stream_leases[node_id] = now + LEASE_TTL_SECONDS
    _stream_stopped_at.pop(node_id, None)
    return not was_live


def release_lease(node_id: str) -> bool:
    """Explicit stop. Returns True if a live lease existed (caller enqueues
    stream_stop + broadcasts webcam_stream_stopped)."""
    now = time.time()
    was_live = _stream_leases.pop(node_id, 0.0) > now
    if was_live:
        _stream_stopped_at[node_id] = now
    return was_live


def has_active_lease(node_id: str) -> bool:
    return _stream_leases.get(node_id, 0.0) > time.time()


def get_viewer_count(node_id: str) -> int:
    # Single-lease-per-node model: 1 while a viewer lease is live, else 0.
    return 1 if has_active_lease(node_id) else 0


def get_command_queue(node_id: str) -> asyncio.Queue:
    # Touch last-activity on every access (not just creation) so a client
    # that keeps long-polling never looks stale, while one that stops
    # entirely becomes reclaimable by cleanup_stale_streams below.
    _command_queue_activity[node_id] = time.time()
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


async def cleanup_stale_streams() -> None:
    """Runs every 30s ON the event loop (AsyncIOScheduler async job). Because it
    is async and single-threaded on the loop, it can `await enqueue_command` /
    `ws_manager.broadcast` and mutate the module dicts with NO lock. Two duties
    (spec §391 + §377):
      1. Lease expiry -> a lease past expiry means every viewer left WITHOUT a
         clean stop (closed tab / crash / lid). Force the stream down for real:
         enqueue stream_stop to the CLIENT and broadcast webcam_stream_stopped,
         then mark it for directory reclaim (fixes audit H2 -- the shipped
         version merely deleted dirs and never commanded the client).
      2. Directory reclaim -> 60s after a stop, or an orphan dir/queue idle past
         HLS_VIEWER_TIMEOUT_SECONDS, drop the HLS dir + in-memory queue state.
    """
    from .websocket_service import ws_manager  # local import avoids an import cycle
    settings = get_settings()
    now = time.time()

    # (1) Expire leases -> force a real stop.
    for nid in [n for n, exp in list(_stream_leases.items()) if exp <= now]:
        _stream_leases.pop(nid, None)
        _stream_stopped_at[nid] = now
        logger.info(f"Viewer lease expired for {nid}; forcing stream stop")
        await enqueue_command(nid, "stream_stop")
        await ws_manager.broadcast({"type": "webcam_stream_stopped", "data": {"node_id": nid}})

    # (2) Reclaim directories / queue state (no active lease only).
    DIR_GRACE = 60
    orphan_grace = settings.HLS_VIEWER_TIMEOUT_SECONDS
    candidates = (set(_stream_stopped_at) | set(_last_activity)
                  | set(_command_queue_activity) | set(_command_queues))
    for nid in candidates:
        if has_active_lease(nid):
            continue
        stopped_at = _stream_stopped_at.get(nid, 0.0)
        last_act = max(_last_activity.get(nid, 0.0), _command_queue_activity.get(nid, 0.0))
        recently_stopped = bool(stopped_at) and (now - stopped_at) > DIR_GRACE
        idle_orphan = bool(last_act) and (now - last_act) > orphan_grace
        if recently_stopped or idle_orphan:
            logger.info(f"Reclaiming HLS state for {nid}")
            cleanup_hls_dir(nid)


__all__ = [
    "get_hls_dir", "store_hls_segment", "get_hls_file", "cleanup_hls_dir",
    "get_viewer_count", "touch_lease", "release_lease", "has_active_lease",
    "get_command_queue", "enqueue_command", "dequeue_command",
    "cleanup_stale_streams",
]
