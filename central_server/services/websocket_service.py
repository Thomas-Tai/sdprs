# -*- coding: utf-8 -*-
"""
SDPRS Central Server - WebSocket Service
Smart Disaster Prevention Response System

This module provides WebSocket management for real-time event broadcasting
to all connected dashboard clients.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

# Configure logging
logger = logging.getLogger("websocket_service")

# Per-client send timeout for broadcast. A slow client that cannot accept a
# frame within this window (e.g. a hung operator laptop whose TCP send buffer
# is full) is treated as disconnected and removed, so it cannot block or stall
# delivery to the other clients.
SEND_TIMEOUT_SECONDS = 5.0

# API-F6: how often an already-open socket re-checks that its session is
# still valid. Connect-time auth is a point-in-time check only; without this,
# a session that expires or is logged out server-side keeps receiving live
# telemetry/pump-state broadcasts until the transport happens to drop.
SESSION_REVALIDATION_INTERVAL_SECONDS = 45.0


def _get_session_user(websocket: WebSocket) -> Optional[str]:
    """
    Extract the authenticated username from a WebSocket's session.

    This is the SAME check the connect handler (`websocket_endpoint`) uses
    to accept/reject a new connection, factored out so the periodic
    re-validation loop can reuse it verbatim instead of duplicating the
    session-lookup logic.
    """
    session = websocket.scope.get("session") or getattr(websocket, "session", None) or {}
    return session.get("user")


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
        self._ping_task: Optional[asyncio.Task] = None
        self._ping_interval_seconds: float = 5.0
    
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
            should_start_pings = self._ping_task is None or self._ping_task.done()
        if should_start_pings:
            self._ping_task = asyncio.create_task(self._ping_loop())
        logger.info(f"WebSocket connected. Total connections: {self.connection_count}")

    async def _ping_loop(self) -> None:
        """
        Periodically broadcast a ping frame so clients can detect a dead connection.

        The browser already handles RFC-6455 ping/pong at the protocol level, but
        application-level ping lets the dashboard JS show a connection-status pill
        ('Live' / 'Reconnecting' / 'Disconnected') based on observed liveness.
        """
        try:
            while True:
                await asyncio.sleep(self._ping_interval_seconds)
                if not self._connections:
                    return
                await self.broadcast({
                    "type": "ping",
                    "data": {"server_time": asyncio.get_event_loop().time()},
                })
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Ping loop error (will restart on next connection): {e}")
    
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

        Sends are dispatched CONCURRENTLY with a per-client timeout so a single
        slow or stalled client cannot block (head-of-line) delivery to the
        others. If a client's send fails or times out, it is treated as
        disconnected and removed from the pool.

        Args:
            data: The message data to broadcast (will be JSON serialized)
        """
        if not self._connections:
            return

        # Create a list of connections to iterate (copy to avoid modification during iteration)
        async with self._lock:
            connections = list(self._connections)

        async def _send(ws: WebSocket) -> Optional[WebSocket]:
            """Send to one client; return the ws on failure, None on success."""
            try:
                await asyncio.wait_for(ws.send_json(data), timeout=SEND_TIMEOUT_SECONDS)
                return None
            except asyncio.TimeoutError:
                logger.warning(
                    f"WebSocket send timed out after {SEND_TIMEOUT_SECONDS}s; "
                    "treating client as disconnected"
                )
                return ws
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                return ws

        # Run all sends concurrently. _send swallows its own exceptions and
        # returns the failed ws, so return_exceptions=True is belt-and-suspenders.
        results = await asyncio.gather(
            *(_send(ws) for ws in connections),
            return_exceptions=True,
        )

        # A returned ws (non-None, non-exception) means that client failed.
        disconnected = [r for r in results if r is not None and not isinstance(r, BaseException)]

        # Remove disconnected clients
        if disconnected:
            async with self._lock:
                for ws in disconnected:
                    self._connections.discard(ws)
            logger.info(f"Removed {len(disconnected)} disconnected WebSockets")
    
    async def broadcast_alert_acknowledged(
        self,
        alert_id: int,
        acknowledged_by: str,
        acknowledged_at: str,
    ) -> None:
        """
        Broadcast that an operator has taken ownership of an alert.

        Other operators' dashboards use this to: (a) stop the repeating audio
        alert, (b) show "認領 by alice 14:32" so duplicate dispatch is avoided.
        """
        await self.broadcast({
            "type": "alert_acknowledged",
            "data": {
                "alert_id": alert_id,
                "acknowledged_by": acknowledged_by,
                "acknowledged_at": acknowledged_at,
            }
        })
        logger.debug(f"Broadcast alert_acknowledged: alert_id={alert_id} by {acknowledged_by}")

# Global singleton instance
ws_manager = WebSocketManager()


# Create APIRouter for WebSocket endpoint
router = APIRouter(tags=["websocket"])


async def _session_revalidation_loop(websocket: WebSocket) -> None:
    """
    API-F6: periodically re-check the session backing an already-open socket.

    Connect-time auth only proves the session was valid at the moment the
    handshake completed. Without this loop, a session that later expires or
    is logged out keeps the socket receiving live broadcasts (alerts, pump
    state, ...) until the transport happens to drop — which can be a long
    time for an idle-but-open dashboard tab.

    Runs as its own task, independent of the receive loop in
    `websocket_endpoint`, so a client that never sends anything (the normal
    case — we don't expect client-initiated messages) still gets checked,
    and so this check can never stall broadcast delivery or message
    dispatch on the main loop.

    Reuses `_get_session_user()` — the exact same session/user lookup the
    connect handler runs — so "valid" means the same thing at connect time
    and on every re-check. On failure it mirrors the connect-time rejection
    path exactly: send the existing `auth_expired` frame, then close with
    code 1008 (no new WS message type introduced).
    """
    try:
        while True:
            await asyncio.sleep(SESSION_REVALIDATION_INTERVAL_SECONDS)
            if _get_session_user(websocket):
                continue
            logger.warning(
                "WebSocket session no longer valid on periodic re-check; closing"
            )
            try:
                await websocket.send_json({
                    "type": "auth_expired",
                    "message": "session expired",
                })
            except Exception as e:
                logger.debug(f"auth_expired re-check send failed: {e}")
            try:
                await websocket.close(code=1008, reason="session_expired")
            except Exception as e:
                logger.debug(f"auth-expired re-check close failed: {e}")
            return
    except asyncio.CancelledError:
        # Normal shutdown path: the connection closed/dropped for some other
        # reason and websocket_endpoint's finally block cancelled us.
        raise


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time event notifications.

    Authenticates using session cookie. If not authenticated (or the
    session has since expired) we FIRST send an application-level
    `{"type": "auth_expired", ...}` frame and THEN close with code 1008
    and a `session_expired` reason. The SPA reads both signals to
    distinguish an auth-expiry (stop reconnecting, prompt for re-login)
    from a transport hiccup (backoff + reconnect).

    All close paths carry a machine-readable `reason` string so log
    aggregation can bucket disconnect causes.

    The connection remains open once accepted, receiving messages to
    keep it alive. All event notifications are sent via broadcast from
    the server side.
    """
    # Check session authentication
    # SessionMiddleware populates session in scope for both HTTP and WebSocket
    user = _get_session_user(websocket)

    if not user:
        logger.warning("WebSocket connection rejected - not authenticated")
        # Accept the socket only long enough to send an application-level
        # signal, then close. The SPA (api.jsx onclose handler) uses the
        # pre-close JSON + the close reason to know this is an auth issue
        # rather than a transport error, so it stops the reconnect loop.
        try:
            await websocket.accept()
            await websocket.send_json({
                "type": "auth_expired",
                "message": "session expired",
            })
        except Exception as e:
            # If accept/send fails (client already gave up), fall through to
            # close — the close() itself is defensive too.
            logger.debug(f"auth_expired pre-close send failed: {e}")
        try:
            await websocket.close(code=1008, reason="session_expired")
        except Exception as e:
            logger.debug(f"auth-expired close failed: {e}")
        return

    # Add connection to manager
    await ws_manager.add(websocket)

    # API-F6: periodic session re-validation runs as an independent task so
    # it can never block/delay the receive loop below (and vice versa).
    revalidation_task = asyncio.create_task(_session_revalidation_loop(websocket))

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
        # Server-side error — try to close cleanly with a reason so the
        # dashboard log surfaces "server_error" instead of a bare 1006.
        try:
            await websocket.close(code=1011, reason="server_error")
        except Exception:
            pass
    finally:
        revalidation_task.cancel()
        try:
            await revalidation_task
        except (asyncio.CancelledError, Exception):
            pass
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