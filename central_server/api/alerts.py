# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Alerts API
Smart Disaster Prevention Response System

This module provides REST API endpoints for alert management:
- POST /api/alerts: Create new alert from edge node
- PUT /api/alerts/{alert_id}/video: Upload MP4 video for an alert
- PATCH /api/alerts/{alert_id}/resolve: Mark alert as resolved
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from ..auth import verify_api_key, verify_api_key_or_session, get_current_user, verify_node_id
from ..database import (
    insert_event,
    get_event,
    update_event_status,
    get_event_created_ats,
    get_events_by_statuses,
    get_events_by_ids,
)
from ..config import get_settings
from ..timeutil import utcnow
from ..services.websocket_service import ws_manager
from ..services.event_service import (
    resolve_event as resolve_event_db,
    acknowledge_event as acknowledge_event_db,
    bulk_acknowledge_events as bulk_acknowledge_events_db,
    bulk_resolve_events as bulk_resolve_events_db,
)

# Configure logging
logger = logging.getLogger("alerts_api")

# Create router
router = APIRouter(tags=["alerts"])


# ===== Pydantic Models =====

class AlertCreate(BaseModel):
    """Request model for creating a new alert."""
    node_id: str = Field(..., description="Edge node identifier")
    timestamp: str = Field(..., description="ISO 8601 timestamp of the event")
    visual_confidence: float = Field(..., ge=0.0, le=1.0, description="Visual detection confidence (0-1)")
    audio_db_peak: float = Field(..., description="Audio peak level in dB")
    audio_freq_peak_hz: float = Field(..., ge=0.0, description="Audio peak frequency in Hz")


class AlertResponse(BaseModel):
    """Response model for alert creation."""
    alert_id: int
    status: str


class AlertDetail(BaseModel):
    """Detailed alert information."""
    id: int
    node_id: str
    timestamp: str
    status: str
    mp4_path: Optional[str] = None
    visual_confidence: Optional[float] = None
    audio_db_peak: Optional[float] = None
    audio_freq_peak_hz: Optional[float] = None
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None


class ResolveRequest(BaseModel):
    """Request model for resolving an alert.

    Attribution is derived server-side from the authenticated user
    (get_current_user) so a client cannot spoof who resolved an alert in
    the DB, the WebSocket broadcast, or the tamper-evident audit log.
    See Theme 2 (trust boundary) finding.

    Note: legacy clients may still send `resolved_by` in the body — Pydantic
    ignores extra fields by default, so those requests remain accepted; the
    value is dropped rather than trusted. The schema no longer advertises
    the field so new callers cannot mistake it for a supported input.
    """
    notes: Optional[str] = Field(None, description="Optional resolution notes")


class BulkResolveRequest(BaseModel):
    """Frozen contract shape (dashboard-audit-2026-07-15): single-transaction
    bulk resolve. ``ids`` accepts int or str on the wire (the SPA serialises
    AlertDetail.id — a JS number — as JSON integers, but external callers may
    send strings; `_coerce_int_ids` normalises both). ``note`` overwrites the
    DB ``notes`` column via COALESCE when non-null; None keeps existing notes.
    """
    ids: list[Union[int, str]] = Field(..., description="Alert IDs to resolve (int or numeric string)")
    note: Optional[str] = Field(None, max_length=500)


class BulkAckRequest(BaseModel):
    """Frozen contract shape: single-transaction bulk acknowledge. Same wire
    shape as BulkResolveRequest; only PENDING rows get flipped."""
    ids: list[Union[int, str]] = Field(..., description="Alert IDs to acknowledge (int or numeric string)")
    note: Optional[str] = Field(None, max_length=500)


def _row_to_alert_detail(event: dict) -> AlertDetail:
    """Map a DB event row to AlertDetail.

    Single mapping site shared by get_alert_detail AND list_alerts so the two
    endpoints cannot drift: the list endpoint's hand-rolled copy used to omit
    acknowledged_by/acknowledged_at, which nulled the SPA's 認領 badge and
    stale-ack counter for anything read via GET /api/alerts.
    """
    return AlertDetail(
        id=event["id"],
        node_id=event["node_id"],
        timestamp=event["timestamp"],
        status=event["status"],
        mp4_path=event.get("mp4_path"),
        visual_confidence=event.get("visual_confidence"),
        audio_db_peak=event.get("audio_db_peak"),
        audio_freq_peak_hz=event.get("audio_freq_peak_hz"),
        acknowledged_by=event.get("acknowledged_by"),
        acknowledged_at=event.get("acknowledged_at"),
        resolved_by=event.get("resolved_by"),
        resolved_at=event.get("resolved_at"),
        notes=event.get("notes"),
        created_at=event.get("created_at"),
    )


# ===== API Endpoints =====

@router.post("/alerts", response_model=AlertResponse, status_code=status.HTTP_200_OK)
async def create_alert(
    alert: AlertCreate,
    request: Request,
    api_key: str = Depends(verify_api_key)
) -> AlertResponse:
    """
    Create a new alert event.
    
    Called by edge nodes when a glass break event is detected.
    Creates an event record in PENDING_VIDEO status, waiting for MP4 upload.
    
    - **node_id**: Edge node identifier
    - **timestamp**: ISO 8601 timestamp when the event occurred
    - **visual_confidence**: Visual detection confidence score (0-1)
    - **audio_db_peak**: Audio peak level in decibels
    - **audio_freq_peak_hz**: Audio peak frequency in Hertz
    """
    # Enforce the edge node_id allowlist on the client-supplied node_id.
    # No-op (allow all) when ALLOWED_NODE_IDS is empty -> backward compatible.
    verify_node_id(alert.node_id)

    logger.info(f"Creating alert from node {alert.node_id}")
    
    # Insert event into database
    try:
        alert_id = insert_event(
            node_id=alert.node_id,
            timestamp=alert.timestamp,
            visual_confidence=alert.visual_confidence,
            audio_db_peak=alert.audio_db_peak,
            audio_freq_peak_hz=alert.audio_freq_peak_hz,
            status="PENDING_VIDEO"
        )
        
        logger.info(f"New alert created: alert_id={alert_id}, node_id={alert.node_id}")
        
        # WebSocket broadcast - notify all connected clients
        try:
            await ws_manager.broadcast({
                "type": "new_alert",
                "data": {
                    "alert_id": alert_id,
                    "node_id": alert.node_id,
                    "timestamp": alert.timestamp,
                    "status": "PENDING_VIDEO"
                }
            })
        except Exception as ws_error:
            logger.warning(f"WebSocket broadcast failed: {ws_error}")
        
        return AlertResponse(alert_id=alert_id, status="PENDING_VIDEO")
        
    except Exception as e:
        logger.error(f"Failed to create alert: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create alert"
        )


@router.put("/alerts/{alert_id}/video", status_code=status.HTTP_204_NO_CONTENT)
async def upload_video(
    alert_id: int,
    file: UploadFile = File(...),
    request: Request = None,
    api_key: str = Depends(verify_api_key)
):
    """
    Upload MP4 video for an existing alert.
    
    Called by edge nodes after the alert JSON is created.
    The video is stored on disk and the event status is updated to PENDING.
    
    - **alert_id**: The alert ID returned from POST /api/alerts
    - **file**: MP4 video file (multipart/form-data)
    """
    logger.info(f"Uploading video for alert {alert_id}, filename={file.filename}")
    
    # Validate file size (max 100 MB)
    MAX_VIDEO_SIZE = 100 * 1024 * 1024
    if file.size and file.size > MAX_VIDEO_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Video file too large (max {MAX_VIDEO_SIZE // (1024*1024)} MB)"
        )
    
    # Validate MIME type
    allowed_types = {"video/mp4", "application/octet-stream"}
    if file.content_type and file.content_type not in allowed_types:
        logger.warning(f"Invalid content type: {file.content_type}")
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {file.content_type}. Expected video/mp4"
        )
    
    # Check if alert exists and is in correct status
    event = get_event(alert_id)
    
    if event is None:
        logger.warning(f"Alert {alert_id} not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert {alert_id} not found"
        )
    
    if event["status"] != "PENDING_VIDEO":
        logger.warning(f"Alert {alert_id} has status {event['status']}, expected PENDING_VIDEO")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Alert already has video uploaded (status: {event['status']})"
        )
    
    # Get settings for storage path
    settings = get_settings()
    storage_path = Path(settings.STORAGE_PATH)
    
    # Create storage directory structure: storage/events/{node_id}/
    node_id = event["node_id"]
    event_timestamp = event["timestamp"]
    
    # Parse timestamp and format filename
    try:
        # Handle ISO format timestamp
        if "T" in event_timestamp:
            dt = datetime.fromisoformat(event_timestamp.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(event_timestamp, "%Y-%m-%d %H:%M:%S")
        
        filename = dt.strftime("%Y-%m-%d_%H-%M-%S") + ".mp4"
    except (ValueError, TypeError):
        # Fallback to current time if parsing fails
        filename = utcnow().strftime("%Y-%m-%d_%H-%M-%S") + ".mp4"
    
    # Build full path
    node_dir = storage_path / "events" / node_id
    node_dir.mkdir(parents=True, exist_ok=True)
    
    mp4_path = node_dir / filename
    full_path_str = str(mp4_path)
    
    # Stream write the file to disk (64KB chunks)
    chunk_size = 64 * 1024  # 64 KB
    
    try:
        with open(mp4_path, "wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
        
        file_size = mp4_path.stat().st_size
        logger.info(f"Video saved: {full_path_str} ({file_size} bytes)")
        
    except Exception as e:
        logger.error(f"Failed to save video file: {e}")
        # Clean up partial file if it exists
        if mp4_path.exists():
            mp4_path.unlink()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save video file"
        )
    
    # Update event status in database
    try:
        success = update_event_status(
            alert_id=alert_id,
            status="PENDING",
            mp4_path=full_path_str
        )
        
        if not success:
            logger.error(f"Failed to update alert {alert_id} status")
            # Clean up the uploaded file
            mp4_path.unlink()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update alert status"
            )
        
        logger.info(f"Alert {alert_id} video uploaded, status updated to PENDING")
        
        # WebSocket broadcast - notify all connected clients
        try:
            await ws_manager.broadcast({
                "type": "alert_updated",
                "data": {
                    "alert_id": alert_id,
                    "status": "PENDING"
                }
            })
        except Exception as ws_error:
            logger.warning(f"WebSocket broadcast failed: {ws_error}")

        # Item 13: video upload also bumps last_upload_at — same data path,
        # same WiFi, same failure modes as snapshot upload.
        try:
            from ..database import touch_node_upload, get_event as _get_event
            ev = _get_event(alert_id)
            if ev and ev.get("node_id"):
                touch_node_upload(ev["node_id"])
        except Exception as e:
            logger.debug(f"touch_node_upload after video failed: {e}")

        return None  # 204 No Content
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update alert status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update alert status"
        )


@router.patch("/alerts/{alert_id}/acknowledge", status_code=status.HTTP_200_OK)
async def acknowledge_alert(
    alert_id: int,
    request: Request,
    user: str = Depends(get_current_user),
) -> dict:
    """
    Mark an alert as ACKNOWLEDGED — operator is on it but resolution is pending.

    Distinct from RESOLVED: the alert stays in the active list (other operators
    see it), but the repeating audio alert stops and a "認領 by X" badge appears.
    Eliminates duplicate dispatch when multiple operators are on shift.
    """
    logger.info(f"Acknowledging alert {alert_id} by {user}")

    event = get_event(alert_id)

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert {alert_id} not found"
        )

    if event["status"] == "ACKNOWLEDGED":
        # Idempotent: same operator re-clicking is a no-op; different operator
        # gets 409 to surface the conflict.
        if event.get("acknowledged_by") == user:
            return {
                "status": "ok",
                "alert_id": alert_id,
                "acknowledged_by": user,
                "acknowledged_at": event.get("acknowledged_at"),
            }
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Alert already acknowledged by {event.get('acknowledged_by')}"
        )

    if event["status"] != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot acknowledge alert in status {event['status']}"
        )

    result = acknowledge_event_db(alert_id=alert_id, acknowledged_by=user)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to acknowledge alert"
        )

    try:
        await ws_manager.broadcast_alert_acknowledged(
            alert_id=alert_id,
            acknowledged_by=user,
            acknowledged_at=result["acknowledged_at"],
        )
    except Exception as ws_error:
        logger.warning(f"WebSocket broadcast failed: {ws_error}")

    # Audit log (item 15)
    from ..services.audit_service import log_action, ACTION_ACKNOWLEDGE
    log_action(user, ACTION_ACKNOWLEDGE, target_id=alert_id)

    return {"status": "ok", **result}


@router.patch("/alerts/{alert_id}/resolve", status_code=status.HTTP_200_OK)
async def resolve_alert(
    alert_id: int,
    body: ResolveRequest,
    request: Request,
    user: str = Depends(get_current_user)
) -> dict:
    """
    Mark an alert as resolved.
    
    Called by dashboard users (security personnel) to acknowledge
    and mark an alert as handled.
    
    - **alert_id**: The alert ID to resolve
    - **notes**: Optional resolution notes

    Attribution (`resolved_by`) is always the authenticated session user.
    The field is not exposed on the request schema; legacy clients that still
    send it get their value silently dropped (Pydantic extras are ignored).
    """
    logger.info(f"Resolving alert {alert_id} by {user}")

    try:
        # Check if alert exists
        event = get_event(alert_id)
        
        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Alert {alert_id} not found"
            )
        
        # Check current status
        if event["status"] == "RESOLVED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Alert is already resolved"
            )
        
        if event["status"] == "PENDING_VIDEO":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Alert is still waiting for video upload"
            )

        # PENDING and ACKNOWLEDGED are both resolvable; anything else (e.g. an
        # unknown future state) is rejected explicitly rather than silently.
        if event["status"] not in ("PENDING", "ACKNOWLEDGED"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot resolve alert in status {event['status']}"
            )
        
        # Resolve the event
        success = resolve_event_db(
            alert_id=alert_id,
            resolved_by=user,
            notes=body.notes
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to resolve alert"
            )

        logger.info(f"Alert {alert_id} resolved by {user}")

        # WebSocket broadcast - notify all connected clients
        try:
            await ws_manager.broadcast({
                "type": "alert_resolved",
                "data": {
                    "alert_id": alert_id,
                    "resolved_by": user
                }
            })
        except Exception as ws_error:
            logger.warning(f"WebSocket broadcast failed: {ws_error}")

        # Audit log (item 15)
        from ..services.audit_service import log_action, ACTION_RESOLVE
        log_action(user, ACTION_RESOLVE, target_id=alert_id, details={"notes": body.notes})

        return {"status": "ok", "alert_id": alert_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resolve alert: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resolve alert"
        )


@router.get("/alerts/rate")
async def alert_rate(
    request: Request,
    bucket: str = "15m",
    window: str = "4h",
    user: str = Depends(get_current_user),
):
    """Item 11: alert-rate buckets for the sparkline above the stat bar.

    Returns [{bucket_start, count}, ...]. We bucket by created_at so this is
    counting new alert *arrivals*, not state transitions — which is what
    "storm intensifying" actually means.
    """
    # Map shorthand to seconds. Keep the set small; UI only emits 15m/1h/4h.
    bucket_map = {"5m": 300, "15m": 900, "1h": 3600}
    window_map = {"1h": 3600, "4h": 4 * 3600, "24h": 24 * 3600}
    bucket_s = bucket_map.get(bucket, 900)
    window_s = window_map.get(window, 4 * 3600)

    from datetime import timedelta as _td
    end = utcnow()
    start = end - _td(seconds=window_s)
    rows = get_event_created_ats(start.isoformat())

    # Pre-build buckets so empty windows render as 0 (gives the sparkline the
    # full timeline shape; a zero-rate moment is itself information).
    #
    # Bucket alignment is deterministic across hosts: we anchor to `end` snapped
    # UP to the next bucket boundary, then walk backwards `window_s / bucket_s`
    # slots. Two invariants this preserves:
    #   (a) exactly window_s/bucket_s buckets (4h/15m -> 16, not 15) — the
    #       previous `while t < end` loop dropped the final open bucket.
    #   (b) UTC-anchored .timestamp() (via tzinfo=UTC) so host local tz does
    #       not shift the bins. Naive `.timestamp()` treats the datetime as
    #       local time, which drifts on non-UTC hosts.
    buckets: list[dict] = []
    # Snap end UP to the next bucket boundary (no-op when already aligned).
    end_utc_ts = int(end.replace(tzinfo=timezone.utc).timestamp())
    end_snap_ts = end_utc_ts + ((bucket_s - (end_utc_ts % bucket_s)) % bucket_s)
    first_ts = end_snap_ts - window_s
    for offset in range(0, window_s, bucket_s):
        # Naive-UTC datetime (matches timeutil.utcnow() contract) via UTC-aware
        # fromtimestamp then strip tz — keeps the sort/compare invariants.
        bt = datetime.fromtimestamp(first_ts + offset, tz=timezone.utc).replace(tzinfo=None)
        buckets.append({"bucket_start": bt.isoformat() + "Z", "count": 0})

    if buckets:
        # NOTE: get_event_created_ats returns a list of raw created_at strings,
        # so each `r` is the timestamp string itself (not a row mapping).
        b0 = datetime.fromisoformat(buckets[0]["bucket_start"].rstrip("Z"))
        for r in rows:
            try:
                ts = datetime.fromisoformat(str(r).replace("Z", "").replace(" ", "T"))
            except (ValueError, TypeError):
                continue
            idx = int((ts - b0).total_seconds() // bucket_s)
            if 0 <= idx < len(buckets):
                buckets[idx]["count"] += 1

    # Storm-intensifying flag used to be computed here for the legacy Jinja
    # dashboard's #alert-rate-intensifying badge. Legacy dashboard retired
    # 2026-07-16; SPA re-derives its own "加劇中" indicator client-side from
    # the bucket counts (components.jsx Sparkline/Footer) — closes audit LOW #14.
    return {"buckets": buckets, "bucket_seconds": bucket_s}


def _coerce_int_ids(raw_ids: list[str]) -> list[int]:
    """Coerce a list[str] of alert IDs to a list[int] for DB binding.

    The frozen wire contract passes IDs as strings, but the events.id column
    is a SERIAL/AUTOINCREMENT integer. Non-numeric entries are silently
    dropped — they cannot match any row anyway, and a strict raise would let
    a single bad UI value break the whole batch. Empty-list handling stays
    in the endpoint (400 "invalid ids").
    """
    out: list[int] = []
    for x in raw_ids or []:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


@router.post("/alerts/bulk-ack")
async def bulk_ack_alerts(
    body: BulkAckRequest,
    request: Request,
    user: str = Depends(get_current_user),
):
    """Bulk acknowledge (dashboard-audit-2026-07-15 frozen contract).

    Flips every PENDING row in ``body.ids`` to ACKNOWLEDGED in one UPDATE.
    Returns ``{"acked": <int>}`` — the actual rowcount of the UPDATE, i.e.
    only rows whose status was PENDING at commit time. Attribution
    (acknowledged_by) is always the session user; a client-supplied note
    overwrites the row's ``notes`` column via COALESCE.

    Pre-selects the target IDs so we can push per-id WebSocket updates the
    SPA already handles (existing ``alert_updated`` topic). The bulk UPDATE
    is authoritative — a race between the SELECT and UPDATE just means we
    may broadcast slightly stale info (harmless; SPA re-reconciles on the
    next list poll or WebSocket event).
    """
    if not body.ids:
        raise HTTPException(status_code=400, detail="invalid ids")

    int_ids = _coerce_int_ids(body.ids)
    if not int_ids:
        raise HTTPException(status_code=400, detail="invalid ids")

    # Pre-select by id (not top-N of PENDING) so older selected rows still
    # receive their per-id broadcast — audit finding MED #3.
    affected_before: list[int] = []
    try:
        rows = get_events_by_ids(int_ids)
        affected_before = [r["id"] for r in rows if r.get("status") == "PENDING"]
    except Exception as e:
        logger.debug(f"pre-select for bulk-ack broadcast failed: {e}")

    count = bulk_acknowledge_events_db(
        alert_ids=int_ids,
        acknowledged_by=user,
        notes=body.note,
    )

    for aid in affected_before:
        try:
            await ws_manager.broadcast({
                "type": "alert_updated",
                "data": {"alert_id": aid, "status": "ACKNOWLEDGED",
                         "acknowledged_by": user},
            })
        except Exception as ws_error:
            logger.warning(f"WebSocket broadcast failed: {ws_error}")

    from ..services.audit_service import log_action, ACTION_BULK_ACKNOWLEDGE
    log_action(user, ACTION_BULK_ACKNOWLEDGE, target_id=None, details={
        "requested": len(body.ids),
        "acked": count,
        "note": body.note,
    })
    return {"acked": count}


@router.post("/alerts/bulk-resolve")
async def bulk_resolve_alerts(
    body: BulkResolveRequest,
    request: Request,
    user: str = Depends(get_current_user),
):
    """Bulk resolve (dashboard-audit-2026-07-15 frozen contract).

    Flips every PENDING/ACKNOWLEDGED row in ``body.ids`` to RESOLVED in one
    UPDATE. Returns ``{"resolved": <int>}`` — the actual rowcount, so
    already-RESOLVED/PENDING_VIDEO ids don't inflate the total.
    """
    if not body.ids:
        raise HTTPException(status_code=400, detail="invalid ids")

    int_ids = _coerce_int_ids(body.ids)
    if not int_ids:
        raise HTTPException(status_code=400, detail="invalid ids")

    # Pre-select by id (not top-N of PENDING/ACKNOWLEDGED) so older selected
    # rows still receive their per-id broadcast — audit finding MED #3.
    affected_before: list[int] = []
    try:
        rows = get_events_by_ids(int_ids)
        affected_before = [
            r["id"] for r in rows
            if r.get("status") in ("PENDING", "ACKNOWLEDGED")
        ]
    except Exception as e:
        logger.debug(f"pre-select for bulk-resolve broadcast failed: {e}")

    count = bulk_resolve_events_db(
        alert_ids=int_ids,
        resolved_by=user,
        notes=body.note,
    )

    for aid in affected_before:
        try:
            await ws_manager.broadcast({
                "type": "alert_resolved",
                "data": {"alert_id": aid, "resolved_by": user},
            })
        except Exception as ws_error:
            logger.warning(f"WebSocket broadcast failed: {ws_error}")

    from ..services.audit_service import log_action, ACTION_BULK_RESOLVE
    log_action(user, ACTION_BULK_RESOLVE, target_id=None, details={
        "requested": len(body.ids),
        "resolved": count,
        "note": body.note,
    })
    return {"resolved": count}


@router.get("/alerts/{alert_id}", response_model=AlertDetail)
async def get_alert_detail(
    alert_id: int,
    request: Request,
    user: str = Depends(verify_api_key_or_session)
) -> AlertDetail:
    """
    Get detailed information about an alert.
    
    - **alert_id**: The alert ID to retrieve
    """
    event = get_event(alert_id)
    
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert {alert_id} not found"
        )
    
    return _row_to_alert_detail(event)


@router.get("/alerts", response_model=list[AlertDetail])
async def list_alerts(
    request: Request,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: str = Depends(verify_api_key_or_session)
) -> list[AlertDetail]:
    """
    List alerts with optional filtering.
    
    - **status_filter**: Filter by status (PENDING_VIDEO, PENDING, RESOLVED)
    - **limit**: Maximum number of results
    - **offset**: Number of results to skip
    """
    from ..database import get_all_events, get_events_by_status

    if status_filter:
        # Support comma-separated multi-status (e.g. PENDING,ACKNOWLEDGED for active-only)
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
        if len(statuses) > 1:
            # Single-query multi-status (fixes the dashboard-audit-2026-07-15
            # TODO). Prev: fanned out N per-status queries, fetched
            # O(N*(limit+offset)) rows and re-sorted in Python — a real N+1
            # under PG. Now: one `WHERE status IN (...)` (SQLite) or
            # `WHERE status = ANY(:statuses)` (PG) with ORDER BY + LIMIT/OFFSET
            # pushed to the DB. Sort/limit semantics identical to the old code:
            # timestamp DESC, then slice [offset:offset+limit].
            events = get_events_by_statuses(statuses, limit=limit, offset=offset)
        else:
            # Single-status early return — the common case (SPA polls a single
            # status per filter chip). One query, DB does the ORDER BY.
            events = get_events_by_status(statuses[0], limit=limit + offset)
            events = events[offset:offset + limit]
    else:
        events = get_all_events(limit=limit, offset=offset)

    return [_row_to_alert_detail(e) for e in events]


# Export router
__all__ = ["router"]