# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Nodes API
Smart Disaster Prevention Response System

This module provides REST API endpoints for node status queries.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import get_current_user
from ..services.mqtt_service import get_mqtt_service

# Configure logging
logger = logging.getLogger("nodes_api")

# Create router
router = APIRouter(tags=["nodes"])


# Stale threshold in seconds (10 seconds)
STALE_THRESHOLD_SECONDS = 10


class NodeStatus(BaseModel):
    """Node status response model."""
    node_id: str
    node_type: str
    status: str
    last_heartbeat: Optional[str] = None
    cpu_temp: Optional[float] = None
    memory_usage_percent: Optional[float] = None
    uptime_seconds: Optional[int] = None
    buffer_health: Optional[str] = None
    stream_status: Optional[Dict[str, Any]] = None
    pump_state: Optional[str] = None
    water_level: Optional[float] = None
    is_stale: bool = False
    snapshot_timestamp: Optional[str] = None


class NodeListResponse(BaseModel):
    """Node list response model."""
    nodes: List[NodeStatus]
    total: int


@router.get("/nodes", response_model=List[NodeStatus])
async def list_nodes(
    request: Request,
    user: str = Depends(get_current_user)
) -> List[NodeStatus]:
    """
    Get status of all registered nodes.
    
    Returns node information including:
    - Online/offline status
    - CPU temperature and memory usage (for glass nodes)
    - Pump state and water level (for pump nodes)
    - Stale detection (snapshot not updating)
    """
    mqtt_service = get_mqtt_service()
    
    if not mqtt_service:
        raise HTTPException(
            status_code=503,
            detail="MQTT service not available"
        )
    
    # Get node states from MQTT service
    node_states = mqtt_service.get_node_states()
    
    # Get snapshot timestamps from app state
    latest_snapshots = getattr(request.app.state, "latest_snapshots", {})
    
    result = []
    now = datetime.utcnow()
    
    for node_id, state in node_states.items():
        node_type = state.get("type", "glass")
        
        # Check if snapshot is stale
        is_stale = False
        snapshot_timestamp = None
        
        if node_type == "glass" and state.get("status") == "ONLINE":
            snapshot_data = latest_snapshots.get(node_id)
            if snapshot_data:
                snapshot_ts = snapshot_data.get("timestamp")
                if snapshot_ts:
                    snapshot_timestamp = snapshot_ts.isoformat() if isinstance(snapshot_ts, datetime) else snapshot_ts
                    elapsed = (now - snapshot_ts).total_seconds() if isinstance(snapshot_ts, datetime) else 0
                    if elapsed > STALE_THRESHOLD_SECONDS:
                        is_stale = True
            else:
                # No snapshot data
                is_stale = True
        
        # Build response
        node_status = NodeStatus(
            node_id=node_id,
            node_type=node_type,
            status=state.get("status", "OFFLINE"),
            last_heartbeat=state.get("last_heartbeat").isoformat() if isinstance(state.get("last_heartbeat"), datetime) else state.get("last_heartbeat"),
            cpu_temp=state.get("cpu_temp"),
            memory_usage_percent=state.get("memory_usage_percent"),
            uptime_seconds=state.get("uptime_seconds"),
            buffer_health=state.get("buffer_health"),
            stream_status=state.get("stream_status"),
            pump_state=state.get("pump_state") if node_type == "pump" else None,
            water_level=state.get("water_level") if node_type == "pump" else None,
            is_stale=is_stale,
            snapshot_timestamp=snapshot_timestamp
        )
        
        result.append(node_status)
    
    logger.debug(f"Returning {len(result)} nodes")
    return result


@router.get("/nodes/summary")
async def get_nodes_summary(
    request: Request,
    user: str = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get a summary of all nodes.
    
    Returns counts of online/offline nodes by type.
    """
    mqtt_service = get_mqtt_service()
    
    if not mqtt_service:
        raise HTTPException(
            status_code=503,
            detail="MQTT service not available"
        )
    
    node_states = mqtt_service.get_node_states()
    
    glass_online = 0
    glass_offline = 0
    pump_online = 0
    pump_offline = 0
    
    for node_id, state in node_states.items():
        node_type = state.get("type", "glass")
        status = state.get("status", "OFFLINE")
        
        if node_type == "glass":
            if status == "ONLINE":
                glass_online += 1
            else:
                glass_offline += 1
        elif node_type == "pump":
            if status == "ONLINE":
                pump_online += 1
            else:
                pump_offline += 1
    
    # Count active pumps
    pump_active = sum(
        1 for state in node_states.values()
        if state.get("type") == "pump" and state.get("pump_state") == "ON"
    )
    
    return {
        "glass_nodes": {
            "online": glass_online,
            "offline": glass_offline,
            "total": glass_online + glass_offline
        },
        "pump_nodes": {
            "online": pump_online,
            "offline": pump_offline,
            "total": pump_online + pump_offline,
            "active": pump_active
        },
        "total_nodes": len(node_states),
        "total_online": glass_online + pump_online,
        "total_offline": glass_offline + pump_offline
    }



@router.get("/nodes/{node_id}", response_model=NodeStatus)
async def get_node(
    node_id: str,
    request: Request,
    user: str = Depends(get_current_user)
) -> NodeStatus:
    """
    Get status of a specific node.
    
    Args:
        node_id: The node identifier
        
    Returns:
        Node status information
    """
    mqtt_service = get_mqtt_service()
    
    if not mqtt_service:
        raise HTTPException(
            status_code=503,
            detail="MQTT service not available"
        )
    
    # Get node state
    state = mqtt_service.get_node_state(node_id)
    
    if not state:
        raise HTTPException(
            status_code=404,
            detail=f"Node {node_id} not found"
        )
    
    node_type = state.get("type", "glass")
    
    # Check if snapshot is stale
    is_stale = False
    snapshot_timestamp = None
    now = datetime.utcnow()
    
    if node_type == "glass" and state.get("status") == "ONLINE":
        latest_snapshots = getattr(request.app.state, "latest_snapshots", {})
        snapshot_data = latest_snapshots.get(node_id)
        if snapshot_data:
            snapshot_ts = snapshot_data.get("timestamp")
            if snapshot_ts:
                snapshot_timestamp = snapshot_ts.isoformat() if isinstance(snapshot_ts, datetime) else snapshot_ts
                elapsed = (now - snapshot_ts).total_seconds() if isinstance(snapshot_ts, datetime) else 0
                if elapsed > STALE_THRESHOLD_SECONDS:
                    is_stale = True
        else:
            is_stale = True
    
    return NodeStatus(
        node_id=node_id,
        node_type=node_type,
        status=state.get("status", "OFFLINE"),
        last_heartbeat=state.get("last_heartbeat").isoformat() if isinstance(state.get("last_heartbeat"), datetime) else state.get("last_heartbeat"),
        cpu_temp=state.get("cpu_temp"),
        memory_usage_percent=state.get("memory_usage_percent"),
        uptime_seconds=state.get("uptime_seconds"),
        buffer_health=state.get("buffer_health"),
        stream_status=state.get("stream_status"),
        pump_state=state.get("pump_state") if node_type == "pump" else None,
        water_level=state.get("water_level") if node_type == "pump" else None,
        is_stale=is_stale,
        snapshot_timestamp=snapshot_timestamp
    )


# Export router
__all__ = ["router"]
