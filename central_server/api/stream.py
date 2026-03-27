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


# Export router
__all__ = ["router"]