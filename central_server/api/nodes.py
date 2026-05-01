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
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..database import get_all_nodes, get_node, set_node_location, get_pump_readings
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
    location: Optional[str] = None
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


class NodePatch(BaseModel):
    """Editable node fields. All optional — only provided keys are written."""
    location: Optional[str] = Field(default=None, max_length=120)


def _load_locations() -> Dict[str, Optional[str]]:
    """Snapshot of node_id -> location from DB. Used to enrich in-memory states."""
    try:
        return {n["node_id"]: n.get("location") for n in get_all_nodes()}
    except Exception as e:
        logger.warning(f"Failed to load node locations: {e}")
        return {}


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

    locations = _load_locations()

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
            location=locations.get(node_id),
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

    # Include nodes that exist in DB (have a location set) but no MQTT state yet
    seen = {n.node_id for n in result}
    for nid, loc in locations.items():
        if nid in seen:
            continue
        db_node = get_node(nid) or {}
        result.append(NodeStatus(
            node_id=nid,
            node_type=db_node.get("node_type", "glass"),
            status="OFFLINE",
            location=loc,
        ))

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
    
    db_node = get_node(node_id) or {}
    return NodeStatus(
        node_id=node_id,
        node_type=node_type,
        status=state.get("status", "OFFLINE"),
        location=db_node.get("location"),
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


@router.patch("/nodes/{node_id}", response_model=Dict[str, Any])
async def update_node(
    node_id: str,
    patch: NodePatch,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Update editable fields of a node (currently: deployment location).

    Auto-creates the node row if not seen yet (e.g. user labels it before first heartbeat).
    """
    # Auto-create row if absent so location can be set pre-deployment
    if get_node(node_id) is None:
        from ..database import upsert_node as _upsert
        _upsert(node_id, "glass", "OFFLINE", None)

    if patch.location is not None:
        set_node_location(node_id, patch.location)
        logger.info(f"Node {node_id} location set to {patch.location!r} by {user}")

    db_node = get_node(node_id) or {}
    return {"node_id": node_id, "location": db_node.get("location")}


@router.get("/pump/{node_id}/history")
async def pump_history(
    node_id: str,
    start: str,
    end: str,
    limit: int = 5000,
    user: str = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    """
    Get historical water-level readings for a pump node.

    Args:
        node_id: The pump node identifier
        start: ISO-8601 start timestamp (e.g. 2026-05-01T00:00:00)
        end: ISO-8601 end timestamp
        limit: Maximum number of rows to return (default 5000, max 20000)

    Returns:
        List of {timestamp, water_level, pump_state} dicts ordered by timestamp ASC
    """
    # Validate ISO-8601 format
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid ISO-8601 timestamp: {e}")

    # Cap limit to prevent excessive memory use
    limit = min(max(1, limit), 20000)

    rows = get_pump_readings(node_id, start, end, limit)
    logger.debug(f"Pump history for {node_id}: {len(rows)} rows between {start} and {end}")
    return rows


# Export router
__all__ = ["router"]
