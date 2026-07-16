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
# get_node is aliased to db_get_node: the GET /nodes/{node_id} route below is
# also named get_node (name kept — it's part of FastAPI's operation ids) and
# rebinds the module global, so an un-aliased import would make every
# get_node(node_id) call recurse into the route (TypeError -> HTTP 500).
from ..database import (
    get_all_nodes,
    get_node as db_get_node,
    set_node_location,
    get_pump_readings,
    get_pump_readings_multi,
)
from ..services.mqtt_service import get_mqtt_service
from ..timeutil import utcnow

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
    visual_health: Optional[str] = None
    audio_health: Optional[str] = None
    stream_status: Optional[Dict[str, Any]] = None
    pump_state: Optional[str] = None
    water_level: Optional[float] = None
    raining: Optional[bool] = None
    sensor_conflict: Optional[bool] = None
    dry_run_protect: Optional[bool] = None
    is_stale: bool = False
    snapshot_timestamp: Optional[str] = None
    # Surfaced from the DB so the V2 dashboard can render pump health without
    # extra round-trips. battery_voltage/power_source land here from the
    # ESP32 firmware (Sprint B item 12); snoozed_until is set by /snooze.
    battery_voltage: Optional[float] = None
    power_source: Optional[str] = None
    snoozed_until: Optional[str] = None
    # Populated by list_nodes from a batch operator_actions lookup — most
    # recent ACTION_SNOOZE row per node (see audit_service.get_snooze_provenance).
    # Absent for nodes never snoozed. SPA renders the "who snoozed and when"
    # chip only when the node is currently snoozed (snoozed_until non-null in
    # the future), so stale provenance for a since-unsnoozed node does not
    # visibly leak. No schema migration — derived from the append-only
    # operator_actions table.
    snoozed_by: Optional[str] = None
    snoozed_at: Optional[str] = None


class NodeListResponse(BaseModel):
    """Node list response model."""
    nodes: List[NodeStatus]
    total: int


class NodePatch(BaseModel):
    """Editable node fields. All optional — only provided keys are written."""
    location: Optional[str] = Field(default=None, max_length=120)


def _load_node_db() -> Dict[str, Dict[str, Any]]:
    """Snapshot of node_id -> full DB row. Used to enrich in-memory states
    with persistent fields (location, battery, snooze)."""
    try:
        return {n["node_id"]: dict(n) for n in (get_all_nodes() or [])}
    except Exception as e:
        logger.warning(f"Failed to load nodes from DB: {e}")
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

    db_nodes = _load_node_db()

    # Batch fetch snooze provenance for every db node in one query — avoids
    # N+1 vs a per-node lookup. Result is a {node_id: {by, at}} dict; nodes
    # that have never been snoozed are absent (rendered as None on the wire).
    from ..services.audit_service import get_snooze_provenance
    _snooze_prov = get_snooze_provenance(list(db_nodes.keys()))

    def _snooze_by(nid: str) -> Optional[str]:
        entry = _snooze_prov.get(nid)
        return entry["by"] if entry else None

    def _snooze_at(nid: str) -> Optional[str]:
        entry = _snooze_prov.get(nid)
        return entry["at"] if entry else None

    result = []
    now = utcnow()

    def _ts_to_iso(v):
        if isinstance(v, datetime):
            return v.isoformat()
        return v

    for node_id, state in node_states.items():
        node_type = state.get("type", "glass")
        db_row = db_nodes.get(node_id, {})

        # Check if snapshot is stale
        is_stale = False
        snapshot_timestamp = None

        if node_type == "glass" and state.get("status") == "ONLINE":
            snapshot_data = latest_snapshots.get(node_id)
            if snapshot_data:
                snapshot_ts = snapshot_data.get("timestamp")
                if snapshot_ts:
                    # Append 'Z' to indicate UTC time for proper timezone conversion in JS
                    snapshot_timestamp = snapshot_ts.isoformat() + 'Z' if isinstance(snapshot_ts, datetime) else snapshot_ts
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
            location=db_row.get("location"),
            last_heartbeat=_ts_to_iso(state.get("last_heartbeat")),
            cpu_temp=state.get("cpu_temp"),
            memory_usage_percent=state.get("memory_usage_percent"),
            uptime_seconds=state.get("uptime_seconds"),
            buffer_health=state.get("buffer_health"),
            visual_health=state.get("visual_health"),
            audio_health=state.get("audio_health"),
            stream_status=state.get("stream_status"),
            pump_state=state.get("pump_state") if node_type == "pump" else None,
            water_level=state.get("water_level") if node_type == "pump" else None,
            raining=state.get("raining") if node_type == "pump" else None,
            sensor_conflict=state.get("sensor_conflict") if node_type == "pump" else None,
            dry_run_protect=state.get("dry_run_protect") if node_type == "pump" else None,
            is_stale=is_stale,
            snapshot_timestamp=snapshot_timestamp,
            battery_voltage=db_row.get("battery_voltage"),
            power_source=db_row.get("power_source"),
            snoozed_until=_ts_to_iso(db_row.get("snoozed_until")),
            snoozed_by=_snooze_by(node_id),
            snoozed_at=_snooze_at(node_id),
        )

        result.append(node_status)

    # Include nodes that exist in the DB but have no MQTT state yet
    seen = {n.node_id for n in result}
    for nid, row in db_nodes.items():
        if nid in seen:
            continue
        result.append(NodeStatus(
            node_id=nid,
            node_type=row.get("node_type", "glass"),
            status="OFFLINE",
            location=row.get("location"),
            battery_voltage=row.get("battery_voltage"),
            power_source=row.get("power_source"),
            snoozed_until=_ts_to_iso(row.get("snoozed_until")),
            snoozed_by=_snooze_by(nid),
            snoozed_at=_snooze_at(nid),
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
    now = utcnow()
    
    if node_type == "glass" and state.get("status") == "ONLINE":
        latest_snapshots = getattr(request.app.state, "latest_snapshots", {})
        snapshot_data = latest_snapshots.get(node_id)
        if snapshot_data:
            snapshot_ts = snapshot_data.get("timestamp")
            if snapshot_ts:
                # Append 'Z' to indicate UTC time for proper timezone conversion in JS
                snapshot_timestamp = snapshot_ts.isoformat() + 'Z' if isinstance(snapshot_ts, datetime) else snapshot_ts
                elapsed = (now - snapshot_ts).total_seconds() if isinstance(snapshot_ts, datetime) else 0
                if elapsed > STALE_THRESHOLD_SECONDS:
                    is_stale = True
        else:
            is_stale = True
    
    db_node = db_get_node(node_id) or {}
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
        visual_health=state.get("visual_health"),
        audio_health=state.get("audio_health"),
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
    create: bool = False,
) -> Dict[str, Any]:
    """
    Update editable fields of a node (currently: deployment location).

    By default an unknown `node_id` returns 404 — this prevents typos in the
    URL from silently upserting phantom `glass` rows that then clutter the
    dashboard. Pass `?create=true` to opt into the pre-deployment labelling
    flow (e.g. "label a node before its first heartbeat").

    `location` is stripped of leading/trailing whitespace before write; an
    empty-after-strip value returns 400 rather than silently clearing the
    label.
    """
    # Strip whitespace on location up-front. A bare `" "` payload used to
    # silently clear the label because Pydantic accepted the raw string.
    # Bind to a local so we don't rely on model mutability across Pydantic
    # versions (frozen-model configs would raise on `patch.location = ...`).
    location: Optional[str] = None
    if patch.location is not None:
        location = patch.location.strip()
        if not location:
            raise HTTPException(
                status_code=400,
                detail="location cannot be empty (send null to omit; whitespace-only rejected)",
            )

    # Reject unknown node_id unless the caller explicitly opts into auto-create.
    # Auto-upsert was the previous default; typos silently created phantom
    # `glass` rows. The `?create=true` flag preserves the legitimate flow of
    # labelling a node before its first heartbeat lands.
    if db_get_node(node_id) is None:
        if not create:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Node {node_id!r} not found. Pass ?create=true to auto-create "
                    "(intended for pre-deployment labelling)."
                ),
            )
        from ..database import upsert_node as _upsert
        logger.warning(
            f"Auto-creating node {node_id!r} on PATCH ?create=true by user {user}"
        )
        _upsert(node_id, "glass", "OFFLINE", None)

    if location is not None:
        set_node_location(node_id, location)
        logger.info(f"Node {node_id} location set to {location!r} by {user}")
        # Audit log (item 15)
        from ..services.audit_service import log_action, ACTION_LOCATION_EDIT
        log_action(user, ACTION_LOCATION_EDIT, target_id=node_id, details={"location": location})

    db_node = db_get_node(node_id) or {}
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


class SnoozeRequest(BaseModel):
    """Item 17: snooze a node's audio-only triggers for N minutes.

    Visual+audio AND-gate is still allowed to fire — only pure-audio
    triggers (which generate typhoon false-positives) are suppressed.
    """
    minutes: int = Field(..., ge=1, le=480, description="Snooze duration in minutes (1-480)")
    reason: Optional[str] = Field(None, max_length=200)


@router.post("/nodes/{node_id}/snooze")
async def snooze_node(
    node_id: str,
    body: SnoozeRequest,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Item 17: Store snooze on the node row AND push to edge via MQTT.

    The server-side DB flag is used by event_service.py when processing
    incoming audio-only alerts. The MQTT push allows edge nodes to
    suppress audio triggers locally (requires edge firmware update to
    subscribe and process sdprs/edge/{node_id}/cmd/snooze topic).
    """
    from datetime import timedelta as _td
    from ..database import set_node_snooze
    from ..services.mqtt_service import get_mqtt_service

    if db_get_node(node_id) is None:
        from ..database import upsert_node as _upsert
        _upsert(node_id, "glass", "OFFLINE", None)

    until = (utcnow() + _td(minutes=body.minutes)).isoformat()
    ok = set_node_snooze(node_id, until, body.reason)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to set snooze")

    # Item 17: Push snooze config to edge node via MQTT
    mqtt_svc = get_mqtt_service()
    if mqtt_svc:
        mqtt_svc.send_snooze_config(node_id, until, body.reason)

    from ..services.audit_service import log_action, ACTION_SNOOZE
    log_action(user, ACTION_SNOOZE, target_id=node_id, details={"minutes": body.minutes, "reason": body.reason})
    return {"node_id": node_id, "snoozed_until": until, "snooze_reason": body.reason}


@router.delete("/nodes/{node_id}/snooze")
async def unsnooze_node(
    node_id: str,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Clear an active snooze AND push cleared config to edge via MQTT."""
    from ..database import set_node_snooze
    from ..services.mqtt_service import get_mqtt_service

    set_node_snooze(node_id, None, None)

    # Item 17: Push cleared snooze config to edge node
    mqtt_svc = get_mqtt_service()
    if mqtt_svc:
        mqtt_svc.send_snooze_config(node_id, None, None)

    from ..services.audit_service import log_action, ACTION_UNSNOOZE
    log_action(user, ACTION_UNSNOOZE, target_id=node_id)
    return {"node_id": node_id, "snoozed_until": None}


# Cycle-count threshold (item 12 spec): more than this many ON->OFF
# transitions in the window is an operator-worthy signal.
PUMP_CYCLE_ALERT_THRESHOLD = 20

# window -> seconds. Defined once so the single-node and batch endpoints
# agree on how each label maps to a lookback span. Unknown labels -> 1h.
_PUMP_CYCLE_WINDOWS = {"15m": 900, "1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}


def _count_pump_cycles(rows) -> int:
    """Count ON->OFF pump transitions in a time-ordered readings list."""
    transitions = 0
    prev_state = None
    for r in rows:
        st = r.get("pump_state")
        if prev_state == "ON" and st == "OFF":
            transitions += 1
        if st in ("ON", "OFF"):
            prev_state = st
    return transitions


@router.get("/pump/{node_id}/cycles")
async def pump_cycles(
    node_id: str,
    window: str = "1h",
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Item 12: count ON->OFF transitions in `window` (e.g. 1h).

    A high cycle count under heavy rain is a useful operator signal — it
    can indicate a near-saturated sump, debris causing chatter, or a
    failing float switch.
    """
    from datetime import timedelta as _td
    seconds = _PUMP_CYCLE_WINDOWS.get(window, 3600)
    end_dt = utcnow()
    start_dt = end_dt - _td(seconds=seconds)
    rows = get_pump_readings(node_id, start_dt.isoformat(), end_dt.isoformat(), 50000)

    count = _count_pump_cycles(rows)
    return {
        "node_id": node_id,
        "window": window,
        "count": count,
        "alert": count > PUMP_CYCLE_ALERT_THRESHOLD,  # threshold from item 12 spec
    }


@router.get("/pumps/cycles")
async def pump_cycles_batch(
    window: str = "1h",
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Batch of /pump/{id}/cycles for EVERY pump node — one HTTP round-trip
    instead of the dashboard's per-pump N+1. Same ON->OFF counting + >20 alert
    threshold as the single-node endpoint (shared _count_pump_cycles).

    Note: PLURAL "pumps" path with no node_id — does not collide with the
    3-segment /pump/{node_id}/cycles route.
    """
    from datetime import timedelta as _td
    seconds = _PUMP_CYCLE_WINDOWS.get(window, 3600)
    end_dt = utcnow()
    start_dt = end_dt - _td(seconds=seconds)
    start_iso, end_iso = start_dt.isoformat(), end_dt.isoformat()

    result: Dict[str, Any] = {}
    # dashboard-audit-2026-07-15 fix: one SELECT for every pump node instead
    # of the per-pump loop. Collect pump node_ids first, then batch-fetch via
    # get_pump_readings_multi (WHERE node_id IN (...) or = ANY(:ids) on PG),
    # grouped in Python. _count_pump_cycles runs over each group unchanged.
    pump_node_ids: list[str] = []
    for n in (get_all_nodes() or []):
        row = dict(n)
        # `node_type` is the canonical pump/glass discriminator (nodes table
        # schema + get_all_nodes SELECT *); tolerate a legacy `type` alias.
        ntype = row.get("node_type") or row.get("type")
        if ntype != "pump":
            continue
        nid = row.get("node_id")
        if not nid:
            continue
        pump_node_ids.append(nid)

    grouped = get_pump_readings_multi(pump_node_ids, start_iso, end_iso, 50000)
    for nid in pump_node_ids:
        rows = grouped.get(nid, [])
        count = _count_pump_cycles(rows)
        result[nid] = {"count": count, "alert": count > PUMP_CYCLE_ALERT_THRESHOLD}

    return {"window": window, "nodes": result}


# Export router
__all__ = ["router"]
