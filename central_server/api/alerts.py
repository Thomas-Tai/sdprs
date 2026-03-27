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
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from ..auth import verify_api_key, verify_api_key_or_session, get_current_user
from ..database import get_db, insert_event, get_event, update_event_status
from ..config import get_settings
from ..services.websocket_service import ws_manager
from ..services.event_service import resolve_event as resolve_event_db

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
    resolved_by: Optional[str] = None
    resolved_at: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None


class ResolveRequest(BaseModel):
    """Request model for resolving an alert."""
    resolved_by: str = Field(..., description="Username who resolved the alert")
    notes: Optional[str] = Field(None, description="Optional resolution notes")


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
        filename = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S") + ".mp4"
    
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
        
        return None  # 204 No Content
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update alert status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update alert status"
        )


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
    - **resolved_by**: Username who resolved the alert
    - **notes**: Optional resolution notes
    """
    logger.info(f"Resolving alert {alert_id} by {body.resolved_by}")
    
    # Get database connection
    db = get_db()
    
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
        
        # Resolve the event
        success = resolve_event_db(
            db=db,
            alert_id=alert_id,
            resolved_by=body.resolved_by,
            notes=body.notes
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to resolve alert"
            )
        
        logger.info(f"Alert {alert_id} resolved by {body.resolved_by}")
        
        # WebSocket broadcast - notify all connected clients
        try:
            await ws_manager.broadcast({
                "type": "alert_resolved",
                "data": {
                    "alert_id": alert_id,
                    "resolved_by": body.resolved_by
                }
            })
        except Exception as ws_error:
            logger.warning(f"WebSocket broadcast failed: {ws_error}")
        
        return {"status": "ok", "alert_id": alert_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resolve alert: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resolve alert"
        )


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
    
    return AlertDetail(
        id=event["id"],
        node_id=event["node_id"],
        timestamp=event["timestamp"],
        status=event["status"],
        mp4_path=event.get("mp4_path"),
        visual_confidence=event.get("visual_confidence"),
        audio_db_peak=event.get("audio_db_peak"),
        audio_freq_peak_hz=event.get("audio_freq_peak_hz"),
        resolved_by=event.get("resolved_by"),
        resolved_at=event.get("resolved_at"),
        notes=event.get("notes"),
        created_at=event.get("created_at")
    )


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
        events = get_events_by_status(status_filter, limit=limit + offset)
        events = events[offset:offset + limit]
    else:
        events = get_all_events(limit=limit, offset=offset)
    
    return [
        AlertDetail(
            id=e["id"],
            node_id=e["node_id"],
            timestamp=e["timestamp"],
            status=e["status"],
            mp4_path=e.get("mp4_path"),
            visual_confidence=e.get("visual_confidence"),
            audio_db_peak=e.get("audio_db_peak"),
            audio_freq_peak_hz=e.get("audio_freq_peak_hz"),
            resolved_by=e.get("resolved_by"),
            resolved_at=e.get("resolved_at"),
            notes=e.get("notes"),
            created_at=e.get("created_at")
        )
        for e in events
    ]


# Export router
__all__ = ["router"]