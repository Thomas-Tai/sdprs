# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Webcam Router (Task 3)
Smart Disaster Prevention Response System

Endpoints the webcam *client PC* talks to (camera registration, HLS segment
upload) plus the dashboard-facing stream control / HLS serve endpoints:

- POST /api/webcam/cameras                     - register cameras (client API key)
- PUT  /api/webcam/{node_id}/hls/{filename}     - upload HLS segment/playlist (client API key)
- GET  /api/webcam/{node_id}/hls/{filename}     - serve HLS segment/playlist (dashboard session)
- POST /api/webcam/{node_id}/stream/start       - dashboard requests live view (dashboard session)
- POST /api/webcam/{node_id}/stream/stop        - dashboard leaves live view (dashboard session)
- GET  /api/webcam/{node_id}/commands           - client long-polls for commands (client API key)
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from ..auth import get_current_user, verify_webcam_api_key, verify_node_id
from ..database import register_webcam_cameras, get_webcam_cameras
from ..services import hls_service
from ..services.websocket_service import ws_manager

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
    verify_node_id(node_id)
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
    hls_service.store_hls_segment(node_id, filename, data)
    return None


@router.get("/{node_id}/hls/{filename}")
async def serve_hls_file(
    node_id: str,
    filename: str,
    request: Request,
    user: str = Depends(get_current_user),
) -> Response:
    verify_node_id(node_id)
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


@router.post("/{node_id}/stream/start")
async def start_webcam_stream(
    node_id: str,
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    verify_node_id(node_id)
    count = hls_service.increment_viewer(node_id)
    if count == 1:
        await hls_service.enqueue_command(node_id, "stream_start", {"fps": 8})
    await ws_manager.broadcast({"type": "webcam_stream_started", "data": {"node_id": node_id}})
    return {"message": "Stream start requested", "node_id": node_id, "viewers": count}


@router.post("/{node_id}/stream/stop")
async def stop_webcam_stream(
    node_id: str,
    request: Request,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    verify_node_id(node_id)
    count = hls_service.decrement_viewer(node_id)
    if count == 0:
        await hls_service.enqueue_command(node_id, "stream_stop")
        await ws_manager.broadcast({"type": "webcam_stream_stopped", "data": {"node_id": node_id}})
    return {"message": "Stream stop requested", "node_id": node_id, "viewers": count}


@router.get("/{node_id}/commands")
async def poll_commands(
    node_id: str,
    request: Request,
    timeout: float = 5.0,
    client_node_id: str = Depends(verify_webcam_api_key),
) -> Dict[str, Any]:
    verify_node_id(node_id)
    timeout = min(max(timeout, 1.0), 30.0)
    cmd = await hls_service.dequeue_command(node_id, timeout=timeout)
    if cmd is None:
        return {"command": None}
    return cmd


__all__ = ["router"]
