# -*- coding: utf-8 -*-
"""
SDPRS Central Server - WebSocket Service
Smart Disaster Prevention Response System

This module provides WebSocket management for real-time event broadcasting
to all connected dashboard clients.
"""

import asyncio
import logging
from typing import Any, Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

# Configure logging
logger = logging.getLogger("websocket_service")


class WebSocketManager:
    """
    Manages WebSocket connections and broadcasts messages to all clients.
    
    Provides real-time event notifications for:
    - new_alert: New alert created
    - alert_updated: Alert video uploaded
    - alert_resolved: Alert marked as resolved
    - node_status: Node online/offline status change
    - pump_status: Pump state update
    """
    
    def __init__(self):
        """Initialize the WebSocket manager."""
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
    
    @property
    def connection_count(self) -> int:
        """Return the number of active connections."""
        return len(self._connections)
    
    async def add(self, websocket: WebSocket) -> None:
        """
        Accept a new WebSocket connection and add it to the pool.
        
        Args:
            websocket: The WebSocket connection to add
        """
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info(f"WebSocket connected. Total connections: {self.connection_count}")
    
    async def remove(self, websocket: WebSocket) -> None:
        """
        Remove a WebSocket connection from the pool.
        
        Args:
            websocket: The WebSocket connection to remove
        """
        async with self._lock:
            self._connections.discard(websocket)
        logger.info(f"WebSocket disconnected. Total connections: {self.connection_count}")
    
    async def broadcast(self, data: Dict[str, Any]) -> None:
        """
        Broadcast a message to all connected clients.
        
        If a client's send fails (disconnected), it will be removed from the pool.
        
        Args:
            data: The message data to broadcast (will be JSON serialized)
        """
        if not self._connections:
            return
        
        # Create a list of connections to iterate (copy to avoid modification during iteration)
        async with self._lock:
            connections = list(self._connections)
        
        disconnected = []
        
        for websocket in connections:
            try:
                await websocket.send_json(data)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                disconnected.append(websocket)
        
        # Remove disconnected clients
        if disconnected:
            async with self._lock:
                for ws in disconnected:
                    self._connections.discard(ws)
            logger.info(f"Removed {len(disconnected)} disconnected WebSockets")
    
    async def broadcast_new_alert(self, alert_id: int, node_id: str, timestamp: str, status: str = "PENDING_VIDEO") -> None:
        """
        Broadcast a new alert notification.
        
        Args:
            alert_id: The alert ID
            node_id: The node that triggered the alert
            timestamp: ISO 8601 timestamp
            status: Alert status (default: PENDING_VIDEO)
        """
        await self.broadcast({
            "type": "new_alert",
            "data": {
                "alert_id": alert_id,
                "node_id": node_id,
                "timestamp": timestamp,
                "status": status
            }
        })
        logger.debug(f"Broadcast new_alert: alert_id={alert_id}")
    
    async def broadcast_alert_updated(self, alert_id: int, status: str = "PENDING") -> None:
        """
        Broadcast an alert update notification.
        
        Args:
            alert_id: The alert ID
            status: New status (default: PENDING)
        """
        await self.broadcast({
            "type": "alert_updated",
            "data": {
                "alert_id": alert_id,
                "status": status
            }
        })
        logger.debug(f"Broadcast alert_updated: alert_id={alert_id}")
    
    async def broadcast_alert_resolved(self, alert_id: int, resolved_by: str) -> None:
        """
        Broadcast an alert resolved notification.
        
        Args:
            alert_id: The alert ID
            resolved_by: Username who resolved the alert
        """
        await self.broadcast({
            "type": "alert_resolved",
            "data": {
                "alert_id": alert_id,
                "resolved_by": resolved_by
            }
        })
        logger.debug(f"Broadcast alert_resolved: alert_id={alert_id}")
    
    async def broadcast_node_status(
        self,
        node_id: str,
        status: str,
        cpu_temp: float = None,
        memory_usage_percent: float = None
    ) -> None:
        """
        Broadcast a node status change notification.
        
        Args:
            node_id: The node identifier
            status: Node status (ONLINE/OFFLINE)
            cpu_temp: CPU temperature (optional)
            memory_usage_percent: Memory usage percentage (optional)
        """
        data = {
            "node_id": node_id,
            "status": status
        }
        if cpu_temp is not None:
            data["cpu_temp"] = cpu_temp
        if memory_usage_percent is not None:
            data["memory_usage_percent"] = memory_usage_percent
        
        await self.broadcast({
            "type": "node_status",
            "data": data
        })
        logger.debug(f"Broadcast node_status: node_id={node_id}, status={status}")
    
    async def broadcast_pump_status(
        self,
        node_id: str,
        pump_state: str,
        water_level: float
    ) -> None:
        """
        Broadcast a pump status update notification.
        
        Args:
            node_id: The pump node identifier
            pump_state: Pump state (ON/OFF)
            water_level: Water level percentage
        """
        await self.broadcast({
            "type": "pump_status",
            "data": {
                "node_id": node_id,
                "pump_state": pump_state,
                "water_level": water_level
            }
        })
        logger.debug(f"Broadcast pump_status: node_id={node_id}, state={pump_state}")


# Global singleton instance
ws_manager = WebSocketManager()


# Create APIRouter for WebSocket endpoint
router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time event notifications.
    
    Authenticates using session cookie. If not authenticated,
    the connection is rejected with code 1008.
    
    The connection remains open, receiving messages to keep it alive.
    All event notifications are sent via broadcast from the server side.
    """
    # Check session authentication
    # SessionMiddleware populates session in scope for both HTTP and WebSocket
    session = websocket.scope.get("session") or getattr(websocket, "session", None) or {}
    user = session.get("user")
    
    if not user:
        logger.warning("WebSocket connection rejected - not authenticated")
        await websocket.close(code=1008, reason="Not authenticated")
        return
    
    # Add connection to manager
    await ws_manager.add(websocket)
    
    try:
        # Keep connection alive, waiting for messages
        # Currently we don't expect client-initiated messages
        # but we need to receive to detect disconnection
        while True:
            try:
                # Wait for any message (ping/pong or close)
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=60.0  # 60 second timeout
                )
                # If we receive any message, log it (for debugging)
                logger.debug(f"WebSocket received: {data}")
            except asyncio.TimeoutError:
                # Send a ping to keep connection alive
                # WebSocket protocol handles ping/pong automatically
                pass
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await ws_manager.remove(websocket)


# Helper function to broadcast from sync context (for MQTT callbacks)
def broadcast_from_sync(loop: asyncio.AbstractEventLoop, data: Dict[str, Any]) -> None:
    """
    Broadcast a message from a synchronous context.
    
    Use this for MQTT callbacks that run in a different thread.
    
    Args:
        loop: The asyncio event loop
        data: The message data to broadcast
    """
    if loop is None:
        logger.warning("No event loop available for broadcast")
        return
    
    asyncio.run_coroutine_threadsafe(ws_manager.broadcast(data), loop)


__all__ = [
    "WebSocketManager",
    "ws_manager",
    "router",
    "broadcast_from_sync"
]