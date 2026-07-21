# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Webcam Router (Task 3)
Smart Disaster Prevention Response System

Endpoints the webcam *client PC* talks to (camera registration, HLS segment
upload) plus the dashboard-facing stream control / HLS serve endpoints:

- POST /api/webcam/cameras                     - register cameras (client API key)
- POST /api/webcam/{node_id}/snapshot           - 1Hz JPEG ingest (client API key)
- PUT  /api/webcam/{node_id}/hls/{filename}     - upload HLS segment/playlist (client API key)
- GET  /api/webcam/{node_id}/hls/{filename}     - serve HLS segment/playlist (dashboard session)
- POST /api/webcam/{node_id}/stream/start       - dashboard requests live view (dashboard session)
- POST /api/webcam/{node_id}/stream/renew       - dashboard keeps lease alive (dashboard session)
- POST /api/webcam/{node_id}/stream/stop        - dashboard leaves live view (dashboard session)
- GET  /api/webcam/{node_id}/commands           - client long-polls for commands (client API key)

Task 3b (2026-07-21 audit): viewer LEASE model replaces the raw viewer counter,
and the 1Hz JPEG ingest endpoint shares the edge snapshot buffer.
"""
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from ..auth import get_current_user, verify_webcam_api_key
from ..database import register_webcam_cameras, get_webcam_cameras, touch_webcam_upload
from ..services import hls_service
from ..services.websocket_service import ws_manager
from ..timeutil import utcnow

logger = logging.getLogger("webcam_api")
router = APIRouter(prefix="/webcam", tags=["webcam"])


class CameraRegistration(BaseModel):
    cameras: List[Dict[str, Any]] = Field(..., min_length=1, max_length=10)


@router.post("/cameras", status_code=201)
async def register_cameras(
    body: CameraRegistration,
    request: Request,
    client_node_id: str = Depends(verify_webcam_api_key),
) -> List[Dict[str, Any]]:
    results = register_webcam_cameras(client_node_id, body.cameras)
    return results


@router.put("/{node_id}/hls/{filename}", status_code=status.HTTP_204_NO_CONTENT)
async def upload_hls_segment(
    node_id: str,
    filename: str,
    request: Request,
    client_node_id: str = Depends(verify_webcam_api_key),
):
    # NO verify_node_id() here (audit H3): webcam node_ids are server-assigned
    # at registration and can never be in ALLOWED_NODE_IDS, so the allowlist
    # would 403 every webcam upload. Ownership is enforced below instead.
    if not filename.endswith((".ts", ".m3u8")):
        raise HTTPException(status_code=400, detail="Only .ts and .m3u8 files allowed")
    # Ownership is checked against the identity the dependency already
    # authenticated. Re-hashing the raw X-API-Key header here would duplicate
    # auth logic that verify_webcam_api_key has done, and re-reading a header
    # the dependency owns is how the two drift apart later.
    if not any(c["node_id"] == node_id for c in get_webcam_cameras(client_node_id)):
        raise HTTPException(status_code=403, detail="Camera not owned by this client")
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Empty segment data")
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Segment too large (max 5 MB)")
    try:
        hls_service.store_hls_segment(node_id, filename, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return None


@router.get("/{node_id}/hls/{filename}")
async def serve_hls_file(
    node_id: str,
    filename: str,
    request: Request,
    user: str = Depends(get_current_user),
) -> Response:
    # No verify_node_id() (audit H3): session-authenticated dashboard read of a
    # server-assigned webcam node_id; the allowlist would 403 it. get_hls_file
    # already confines reads to HLS_STORAGE_PATH via is_relative_to().
    data = hls_service.get_hls_file(node_id, filename)
    if data is None:
        raise HTTPException(status_code=404, detail="HLS file not found")
    media_type = "application/vnd.apple.mpegurl" if filename.endswith(".m3u8") else "video/mp2t"
    # No Access-Control-Allow-Origin here. This endpoint is session-authenticated
    # and the SPA fetches it same-origin, so a wildcard would only widen reach
    # without enabling anything the dashboard needs.
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "no-cache, no-store"},
    )


@router.post("/{node_id}/snapshot", status_code=status.HTTP_204_NO_CONTENT)
async def ingest_webcam_snapshot(
    node_id: str,
    request: Request,
    client_node_id: str = Depends(verify_webcam_api_key),
):
    # 1Hz JPEG ingest (spec §303, fixes audit C1: the client's 1Hz path used to
    # hit the edge route and 401 silently). NO verify_node_id() (audit H3):
    # webcam node_ids are server-assigned at registration and can never be in
    # ALLOWED_NODE_IDS. Ownership is enforced against the authenticated client.
    if not any(c["node_id"] == node_id for c in get_webcam_cameras(client_node_id)):
        raise HTTPException(status_code=403, detail="Camera not owned by this client")
    jpeg_bytes = await request.body()
    if not jpeg_bytes:
        raise HTTPException(status_code=400, detail="Empty snapshot data")
    if len(jpeg_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Snapshot too large (max 5 MB)")
    # Share the SAME in-memory buffer the edge path uses, so the existing
    # GET /api/edge/{node_id}/snapshot/latest read path serves webcams unchanged
    # (spec §303 point 3). Webcam JPEGs come from cv2.imencode and carry no EXIF,
    # so the edge path's _strip_exif is unnecessary here.
    request.app.state.latest_snapshots[node_id] = {"jpeg": jpeg_bytes, "timestamp": utcnow()}
    touch_webcam_upload(node_id)  # fixes C2; writes webcam_cameras only, never nodes (C3)
    return None


@router.post("/{node_id}/stream/start")
async def start_webcam_stream(
    node_id: str,
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    # Lease semantics (audit H1/H2): arm the single per-node viewer lease. Only a
    # 0->1 transition commands the client + broadcasts, so a second concurrent
    # viewer does not re-issue stream_start. No verify_node_id() (audit H3).
    fresh = hls_service.touch_lease(node_id)
    if fresh:
        await hls_service.enqueue_command(node_id, "stream_start", {"fps": 8})
        await ws_manager.broadcast({"type": "webcam_stream_started", "data": {"node_id": node_id}})
    return {"message": "Stream start requested", "node_id": node_id,
            "viewers": hls_service.get_viewer_count(node_id)}


@router.post("/{node_id}/stream/renew")
async def renew_webcam_stream(
    node_id: str,
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    # Dashboard calls this every 30s while a tile is live. If the lease had
    # lapsed (network gap > LEASE_TTL) the cleanup scan already stopped the
    # client, so a re-arm (fresh == True) re-issues stream_start to resume
    # encoding. No verify_node_id() (audit H3).
    fresh = hls_service.touch_lease(node_id)
    if fresh:
        await hls_service.enqueue_command(node_id, "stream_start", {"fps": 8})
        await ws_manager.broadcast({"type": "webcam_stream_started", "data": {"node_id": node_id}})
    return {"node_id": node_id, "viewers": hls_service.get_viewer_count(node_id)}


@router.post("/{node_id}/stream/stop")
async def stop_webcam_stream(
    node_id: str,
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    # Explicit stop releases the lease immediately. Only command the client if a
    # live lease actually existed. No verify_node_id() (audit H3).
    was_live = hls_service.release_lease(node_id)
    if was_live:
        await hls_service.enqueue_command(node_id, "stream_stop")
        await ws_manager.broadcast({"type": "webcam_stream_stopped", "data": {"node_id": node_id}})
    return {"message": "Stream stop requested", "node_id": node_id,
            "viewers": hls_service.get_viewer_count(node_id)}


@router.get("/{node_id}/commands")
async def poll_commands(
    node_id: str,
    request: Request,
    timeout: float = 5.0,
    client_node_id: str = Depends(verify_webcam_api_key),
) -> Dict[str, Any]:
    # No verify_node_id() (audit H3): server-assigned webcam node_id, ownership
    # is enforced by the guard below.
    # Same ownership guard as upload_hls_segment (~line 59). Without it, any
    # valid webcam API key can long-poll ANY camera's command queue by name,
    # and since asyncio.Queue.get() is single-consumer FIFO, a rogue poller
    # racing the legitimate client would silently steal the stream_start/stop
    # meant for it.
    if not any(c["node_id"] == node_id for c in get_webcam_cameras(client_node_id)):
        raise HTTPException(status_code=403, detail="Camera not owned by this client")
    timeout = min(max(timeout, 1.0), 30.0)
    cmd = await hls_service.dequeue_command(node_id, timeout=timeout)
    if cmd is None:
        return {"command": None}
    return cmd


__all__ = ["router"]
