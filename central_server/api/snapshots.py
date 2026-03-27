# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Snapshots API
Smart Disaster Prevention Response System

This module provides REST API endpoints for snapshot management:
- POST /api/edge/{node_id}/snapshot: Receive snapshot from edge node
- GET /api/edge/{node_id}/snapshot/latest: Get latest snapshot for dashboard
"""

import logging
from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import Response as FastAPIResponse

from ..auth import verify_api_key

# Configure logging
logger = logging.getLogger("snapshots_api")

# Create router
router = APIRouter(tags=["snapshots"])


# ===== Placeholder Image Generation =====

def generate_placeholder_jpeg() -> bytes:
    """
    Generate a gray placeholder JPEG image with "No Signal" text.
    
    Returns:
        bytes: JPEG image data (854x480, ~2KB)
    """
    # Try to use Pillow if available
    try:
        from PIL import Image, ImageDraw, ImageFont
        
        # Create 854x480 gray image
        img = Image.new('RGB', (854, 480), color=(128, 128, 128))
        draw = ImageDraw.Draw(img)
        
        # Draw "No Signal" text in the center
        text = "No Signal"
        
        # Try to use a default font, fall back to default if not available
        try:
            # Try to load a system font
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 48)
        except (OSError, IOError):
            try:
                # Try Windows font
                font = ImageFont.truetype("arial.ttf", 48)
            except (OSError, IOError):
                # Use default font
                font = ImageFont.load_default()
        
        # Get text bounding box for centering
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # Calculate center position
        x = (854 - text_width) // 2
        y = (480 - text_height) // 2
        
        # Draw text in darker gray
        draw.text((x, y), text, fill=(80, 80, 80), font=font)
        
        # Convert to JPEG bytes
        import io
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=50)
        return buffer.getvalue()
        
    except ImportError:
        # Pillow not available, return minimal valid JPEG
        # This is a minimal valid JPEG (gray 1x1 pixel, very small)
        # In production, Pillow should be installed
        return (
            b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
            b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t'
            b'\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f'
            b'\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0'
            b'\x00\x0b\x08\x01\xe0\x03\x58\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00'
            b'\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01'
            b'\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01'
            b'\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04'
            b'\x11\x05\x12!1A\x06\x13Qa\x07"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R'
            b'\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVW'
            b'XYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95'
            b'\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4'
            b'\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3'
            b'\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea'
            b'\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00'
            b'\x00?\x00\xfb\xd3\x28\xa2\x80\x0a\x28\xa2\x80\x0a\x28\xa2\x80\x0a\x28'
            b'\xa2\x80\x0a\x28\xa0\x01\xff\xd9'
        )


# Cache the placeholder image at module load
_PLACEHOLDER_JPEG: bytes = generate_placeholder_jpeg()


# ===== API Endpoints =====

@router.post("/edge/{node_id}/snapshot", status_code=status.HTTP_204_NO_CONTENT)
async def receive_snapshot(
    node_id: str,
    request: Request,
    api_key: str = Depends(verify_api_key)
):
    """
    Receive a snapshot JPEG from an edge node.
    
    Called by edge nodes every second (1 FPS).
    The snapshot is stored in memory and overwrites any previous snapshot.
    
    - **node_id**: The edge node identifier
    - **Body**: Raw JPEG bytes (Content-Type: image/jpeg)
    """
    # Read the raw JPEG bytes from request body
    jpeg_bytes = await request.body()
    
    if not jpeg_bytes:
        logger.warning(f"Empty snapshot received from {node_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty snapshot data"
        )
    
    # Limit snapshot size to 5 MB
    MAX_SNAPSHOT_SIZE = 5 * 1024 * 1024
    if len(jpeg_bytes) > MAX_SNAPSHOT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Snapshot too large (max 5 MB)"
        )
    
    # Get the global snapshots dict from app state
    snapshots: Dict[str, Dict[str, Any]] = request.app.state.latest_snapshots
    
    # Store snapshot with timestamp
    snapshots[node_id] = {
        "jpeg": jpeg_bytes,
        "timestamp": datetime.utcnow()
    }
    
    logger.debug(f"Snapshot received from {node_id}, size={len(jpeg_bytes)} bytes")
    
    return None  # 204 No Content


@router.get("/edge/{node_id}/snapshot/latest")
async def get_latest_snapshot(
    node_id: str,
    request: Request
) -> Response:
    """
    Get the latest snapshot JPEG for a node.
    
    This endpoint is public (no authentication required) so that
    dashboard <img> tags can load snapshots directly.
    
    - **node_id**: The edge node identifier
    
    Returns:
        - 200: JPEG image (either the latest snapshot or a placeholder)
    """
    # Get the global snapshots dict from app state
    snapshots: Dict[str, Dict[str, Any]] = request.app.state.latest_snapshots
    
    # Check if we have a snapshot for this node
    snapshot_data = snapshots.get(node_id)
    
    if snapshot_data:
        jpeg_bytes = snapshot_data["jpeg"]
        logger.debug(f"Returning snapshot for {node_id}, size={len(jpeg_bytes)} bytes")
        return Response(
            content=jpeg_bytes,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "X-Snapshot-Timestamp": snapshot_data["timestamp"].isoformat()
            }
        )
    else:
        # Return placeholder image
        logger.debug(f"No snapshot for {node_id}, returning placeholder")
        return Response(
            content=_PLACEHOLDER_JPEG,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "X-Snapshot-Status": "placeholder"
            }
        )


@router.get("/edge/snapshots/status")
async def get_snapshots_status(
    request: Request,
    user: str = Depends(verify_api_key)
) -> Dict[str, Any]:
    """
    Get status of all snapshots.
    
    Returns information about which nodes have snapshots and when they were last updated.
    """
    snapshots: Dict[str, Dict[str, Any]] = request.app.state.latest_snapshots
    
    status_data = {}
    for node_id, data in snapshots.items():
        status_data[node_id] = {
            "has_snapshot": True,
            "timestamp": data["timestamp"].isoformat(),
            "size_bytes": len(data["jpeg"])
        }
    
    return {
        "nodes": status_data,
        "total_nodes": len(snapshots)
    }


@router.delete("/edge/{node_id}/snapshot", status_code=status.HTTP_204_NO_CONTENT)
async def clear_snapshot(
    node_id: str,
    request: Request,
    api_key: str = Depends(verify_api_key)
):
    """
    Clear the snapshot for a node.
    
    - **node_id**: The edge node identifier
    """
    snapshots: Dict[str, Dict[str, Any]] = request.app.state.latest_snapshots
    
    if node_id in snapshots:
        del snapshots[node_id]
        logger.info(f"Snapshot cleared for {node_id}")
    
    return None


# Export router
__all__ = ["router"]