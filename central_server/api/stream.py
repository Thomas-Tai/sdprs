# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Stream Control API
Smart Disaster Prevention Response System

This module provides REST API endpoints for stream control:
- POST /api/stream/{node_id}/start: Start streaming from an edge node
- POST /api/stream/{node_id}/stop: Stop streaming from an edge node
- GET /api/stream/{node_id}/status: Get current stream status
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ..auth import get_current_user
from ..services.mqtt_service import get_mqtt_service

# Configure logging
logger = logging.getLogger("stream_api")

# Create router with prefix
router = APIRouter(prefix="/stream", tags=["stream"])


# ===== Pydantic Models =====

class StreamCommandResponse(BaseModel):
    """Response model for stream commands."""
    message: str
    node_id: str


class StreamStatusResponse(BaseModel):
    """Response model for stream status."""
    node_id: str
    stream_status: Optional[Dict[str, Any]] = None


# ===== API Endpoints =====

@router.post("/{node_id}/start", response_model=StreamCommandResponse)
async def start_stream(
    node_id: str,
    request: Request,
    user: str = Depends(get_current_user)
) -> StreamCommandResponse:
    """
    Start streaming from an edge node.
    
    Sends a stream_start command to the edge node via MQTT.
    The edge node will start mediamtx and SSH reverse tunnel.
    
    - **node_id**: The edge node identifier
    """
    logger.info(f"Stream start requested for {node_id} by {user}")
    
    # Get MQTT service
    mqtt_service = get_mqtt_service()
    
    if not mqtt_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MQTT service not available"
        )
    
    # Check if node exists and is online
    node_state = mqtt_service.get_node_state(node_id)
    
    if not node_state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node {node_id} not found"
        )
    
    if node_state.get("status") != "ONLINE":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Node {node_id} is offline"
        )
    
    # Send stream start command
    success = mqtt_service.send_stream_command(node_id, "stream_start")
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send stream start command"
        )
    
    logger.info(f"Stream start command sent to {node_id}")
    
    return StreamCommandResponse(
        message="Stream start command sent",
        node_id=node_id
    )


@router.post("/{node_id}/stop", response_model=StreamCommandResponse)
async def stop_stream(
    node_id: str,
    request: Request,
    user: str = Depends(get_current_user)
) -> StreamCommandResponse:
    """
    Stop streaming from an edge node.
    
    Sends a stream_stop command to the edge node via MQTT.
    The edge node will stop mediamtx and SSH reverse tunnel.
    
    - **node_id**: The edge node identifier
    """
    logger.info(f"Stream stop requested for {node_id} by {user}")
    
    # Get MQTT service
    mqtt_service = get_mqtt_service()
    
    if not mqtt_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MQTT service not available"
        )
    
    # Send stream stop command (even if node is offline)
    success = mqtt_service.send_stream_command(node_id, "stream_stop")
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send stream stop command"
        )
    
    logger.info(f"Stream stop command sent to {node_id}")
    
    return StreamCommandResponse(
        message="Stream stop command sent",
        node_id=node_id
    )


@router.get("/{node_id}/status", response_model=StreamStatusResponse)
async def get_stream_status(
    node_id: str,
    request: Request,
    user: str = Depends(get_current_user)
) -> StreamStatusResponse:
    """
    Get the current stream status for an edge node.
    
    Returns the stream_status from the node's state, which includes:
    - status: "active" or None
    - tunnel_port: The SSH tunnel port (if active)
    - format: The stream format (usually "hls")
    
    - **node_id**: The edge node identifier
    """
    # Get MQTT service
    mqtt_service = get_mqtt_service()
    
    if not mqtt_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MQTT service not available"
        )
    
    # Get node state
    node_state = mqtt_service.get_node_state(node_id)
    
    if not node_state:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node {node_id} not found"
        )
    
    stream_status = node_state.get("stream_status")
    
    logger.debug(f"Stream status for {node_id}: {stream_status}")
    
    return StreamStatusResponse(
        node_id=node_id,
        stream_status=stream_status
    )


@router.get("", response_model=Dict[str, Any])
async def list_stream_statuses(
    request: Request,
    user: str = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get stream status for all nodes.
    
    Returns a dict of node_id -> stream_status for all registered nodes.
    """
    # Get MQTT service
    mqtt_service = get_mqtt_service()
    
    if not mqtt_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MQTT service not available"
        )
    
    # Get all node states
    node_states = mqtt_service.get_node_states()
    
    # Extract stream statuses
    stream_statuses = {}
    for node_id, state in node_states.items():
        stream_status = state.get("stream_status")
        if stream_status:
            stream_statuses[node_id] = stream_status
    
    return {
        "active_streams": len(stream_statuses),
        "streams": stream_statuses
    }


@router.get("/health")
async def stream_health(user: str = Depends(get_current_user)):
    """Item 14: scrape mediamtx Prometheus endpoint and surface per-node metrics.

    mediamtx exposes a `/metrics` endpoint with a small set of counters/gauges:
        rtsp_session_bytes_received{path="..."}
        frames_dropped_total{path="..."}
        num_viewers{path="..."}  (newer versions: rtsp_sessions{state="read"})

    We compute kbps as a first-derivative on the bytes counter between scrapes
    and cache the previous reading per-path. mediamtx labels the path with
    the stream name (e.g. glass_node_01) — that becomes our node_id.
    """
    from ..config import get_settings
    settings = get_settings()
    url = getattr(settings, "MEDIAMTX_METRICS_URL", "")
    if not url:
        return {"enabled": False}

    import httpx, time, re
    cache = getattr(stream_health, "_cache", {})

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"enabled": True, "reachable": False, "status_code": resp.status_code, "nodes": {}}
            text = resp.text
    except Exception as e:
        return {"enabled": True, "reachable": False, "error": str(e), "nodes": {}}

    # Parse Prometheus text format. Lines look like:
    #   metric_name{label="value",...} number
    #   metric_name 42
    # We only care about a handful of metric names.
    pattern = re.compile(r'^(\w+)(?:\{([^}]*)\})?\s+([0-9eE+\-.]+)\s*$')
    nodes: dict[str, dict] = {}
    bytes_now: dict[str, float] = {}
    now_ts = time.time()
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        m = pattern.match(line)
        if not m:
            continue
        name, labels_raw, value_raw = m.group(1), (m.group(2) or ""), m.group(3)
        try:
            value = float(value_raw)
        except ValueError:
            continue
        # Extract path label if present.
        path = ""
        if labels_raw:
            lm = re.search(r'(?:path|name)="([^"]+)"', labels_raw)
            if lm:
                path = lm.group(1)
        if not path:
            continue
        node = nodes.setdefault(path, {"viewers": 0, "dropped": 0, "bitrate_kbps": 0})
        if name in ("rtsp_session_bytes_received", "rtsp_bytes_received_total"):
            bytes_now[path] = value
        elif name in ("frames_dropped_total", "rtsp_session_frames_dropped"):
            node["dropped"] = int(value)
        elif name in ("num_viewers", "rtsp_sessions", "hls_muxer_readers"):
            # rtsp_sessions has a state="read" label we ignore for the count.
            node["viewers"] += int(value)

    # Compute kbps via first derivative.
    prev = cache.get("bytes", {})
    prev_ts = cache.get("ts", now_ts)
    dt = max(0.001, now_ts - prev_ts)
    for path, b in bytes_now.items():
        prev_b = prev.get(path, b)
        delta_bits = max(0.0, (b - prev_b) * 8.0)
        nodes[path]["bitrate_kbps"] = int(delta_bits / dt / 1000)
    stream_health._cache = {"bytes": bytes_now, "ts": now_ts}

    return {"enabled": True, "reachable": True, "nodes": nodes}


# Export router
__all__ = ["router"]