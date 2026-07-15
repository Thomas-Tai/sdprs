# -*- coding: utf-8 -*-
"""
SDPRS Central Server - MQTT Service
Smart Disaster Prevention Response System

This module provides MQTT client functionality for the central server:
- Subscribe to edge node heartbeats and status messages
- Manage node states in memory and database
- Offline detection with configurable timeouts
- Publish commands to edge nodes
"""

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from ..config import get_settings

# Import shared MQTT topic constants. Hard import (no fallback): the path
# insert below puts the directory containing both central_server/ and shared/
# on sys.path in every supported layout (repo checkout, root Dockerfile
# `COPY . .`, deploy/Dockerfile which copies central_server/ and shared/
# side-by-side), so a failure here is a broken deployment we WANT loud.
import sys as _sys
from pathlib import Path as _Path
_shared_path = str(_Path(__file__).parent.parent.parent)
if _shared_path not in _sys.path:
    _sys.path.insert(0, _shared_path)
from shared.mqtt_topics import (
    SUB_ALL_HEARTBEAT,
    SUB_ALL_PUMP_STATUS,
    SUB_ALL_STREAM_STATUS,
    topic_cmd,
)
from ..database import (
    upsert_node, update_node_heartbeat, update_node_status, insert_pump_reading
)
from ..timeutil import utcnow

# Configure logging
logger = logging.getLogger("mqtt_service")


class MQTTService:
    """
    MQTT service for central server.
    
    Subscribes to edge node messages and manages node states.
    """
    
    # Timeout thresholds in seconds
    GLASS_OFFLINE_TIMEOUT = 90  # Glass node offline after 90 seconds
    PUMP_OFFLINE_TIMEOUT = 30   # Pump node offline after 30 seconds (more critical)
    OFFLINE_CHECK_INTERVAL = 10 # Check for offline nodes every 10 seconds
    
    def __init__(self, db_module=None, loop=None):
        """
        Initialize the MQTT service.

        Args:
            db_module: Database module for node operations (optional, for testing)
            loop: The asyncio event loop captured at FastAPI startup, used to
                hand WebSocket broadcasts from this sync/thread service over
                to the running event loop (optional, for testing)
        """
        self.settings = get_settings()
        self.db = db_module
        self._loop = loop
        
        # Node states stored in memory
        self.node_states: Dict[str, Dict[str, Any]] = {}
        
        # MQTT client
        self.client = None
        self._running = False
        
        # Offline detection timer
        self._offline_check_timer: Optional[threading.Thread] = None
        self._offline_check_event = threading.Event()
        
        # Lock for thread-safe node_states access
        self._lock = threading.Lock()
        
    def start(self):
        """
        Start the MQTT client and connect to the broker.
        """
        if self._running:
            logger.warning("MQTT service already running")
            return
        
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("paho-mqtt not installed. MQTT service disabled.")
            return
        
        # Unique client_id per process so multiple pods (e.g. during a Zeabur
        # rolling deploy that fails to terminate the old pod) don't kick each
        # other off the broker via MQTT's same-client_id session-takeover rule.
        # Downside: N pods each receive every message → N× message processing.
        # Acceptable at MVP scale (1 camera + 1 pump, ~10 msg/min).
        import os, socket
        pod_tag = f"{socket.gethostname()[:12]}_{os.getpid()}"
        self.client = mqtt.Client(client_id=f"central_server_{pod_tag}")
        
        # Set up callbacks
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        
        # Configure reconnection
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

        # Authentication (Mosquitto on Zeabur cloud deploy)
        if getattr(self.settings, "MQTT_USERNAME", ""):
            self.client.username_pw_set(
                self.settings.MQTT_USERNAME,
                self.settings.MQTT_PASSWORD,
            )

        # TLS (for external broker with TLS enabled)
        if getattr(self.settings, "MQTT_USE_TLS", False):
            self.client.tls_set()

        try:
            # Non-blocking connect — returns immediately; paho retries in background.
            # This prevents blocking the FastAPI startup when the broker isn't ready yet.
            logger.info(f"Connecting to MQTT broker at {self.settings.MQTT_BROKER}:{self.settings.MQTT_PORT}")
            self.client.connect_async(
                self.settings.MQTT_BROKER,
                self.settings.MQTT_PORT,
                keepalive=60,
            )

            # Start the network loop in a background thread (handles connect + reconnect)
            self.client.loop_start()
            self._running = True

            # Start offline detection timer
            self._start_offline_detection()

            logger.info("MQTT service started (connecting in background)")

        except Exception as e:
            logger.error(f"Failed to initiate MQTT connection: {e}")
            logger.warning("App will start without MQTT; client will retry automatically")
            # Keep _running = False so publish() guards work correctly
            self.client = None
    
    def stop(self):
        """
        Stop the MQTT client and disconnect from the broker.
        """
        logger.info("Stopping MQTT service")
        self._running = False
        
        # Stop offline detection
        self._stop_offline_detection()
        
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.client = None
        
        logger.info("MQTT service stopped")
    
    def _on_connect(self, client, userdata, flags, rc):
        """
        Callback when connected to MQTT broker.
        """
        if rc == 0:
            logger.info("Connected to MQTT broker successfully")
            
            # Subscribe to all edge node topics (canonical patterns from
            # shared/mqtt_topics.py — single source of truth, no local copies)
            topics = [
                (SUB_ALL_HEARTBEAT, 1),
                (SUB_ALL_PUMP_STATUS, 1),
                (SUB_ALL_STREAM_STATUS, 1),
            ]
            
            for topic, qos in topics:
                client.subscribe(topic, qos=qos)
                logger.debug(f"Subscribed to {topic}")
            
        else:
            logger.error(f"Failed to connect to MQTT broker, rc={rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """
        Callback when disconnected from MQTT broker.
        """
        if rc != 0:
            logger.warning(f"Unexpected disconnection from MQTT broker, rc={rc}")
        else:
            logger.info("Disconnected from MQTT broker")
    
    def _on_message(self, client, userdata, msg):
        """
        Callback when a message is received.
        """
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            logger.debug(f"Received message on {topic}: {payload[:100]}...")
            
            # Parse the topic to get node_id and message type
            parts = topic.split('/')
            if len(parts) >= 4:
                node_id = parts[2]
                message_type = parts[3]
                
                # Dispatch to appropriate handler
                if message_type == "heartbeat":
                    self._handle_heartbeat(node_id, payload)
                elif message_type == "pump_status":
                    self._handle_pump_status(node_id, payload)
                elif message_type == "stream_status":
                    self._handle_stream_status(node_id, payload)
                else:
                    logger.warning(f"Unknown message type: {message_type}")
            
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")
    
    def _handle_heartbeat(self, node_id: str, payload: str):
        """
        Handle heartbeat message from edge node.
        """
        try:
            data = json.loads(payload)

            # LWT / offline marker: the edge node's Last-Will publishes to its own
            # heartbeat topic when it drops ungracefully. Treat it as an immediate
            # OFFLINE instead of a live heartbeat (which would wrongly mark ONLINE).
            # This is an optimization on top of the ~90s heartbeat-timeout fallback.
            if data.get("online") is False or data.get("status") == "OFFLINE":
                self._handle_lwt_offline(node_id)
                return

            with self._lock:
                # Capture the prior status BEFORE overwriting so we can detect a
                # transition (unknown-node first contact, or OFFLINE->ONLINE
                # recovery). Steady-state heartbeats must NOT broadcast.
                prev_status = self.node_states.get(node_id, {}).get("status")
                # Update node state in memory
                self.node_states[node_id] = {
                    "type": "glass",
                    "status": "ONLINE",
                    "last_heartbeat": utcnow(),
                    "cpu_temp": data.get("cpu_temp"),
                    "memory_usage_percent": data.get("memory_usage_percent"),
                    "buffer_health": data.get("buffer_health", "ok"),
                    "visual_health": data.get("visual_health"),
                    "audio_health": data.get("audio_health"),
                    "uptime_seconds": data.get("uptime_seconds"),
                    "stream_status": self.node_states.get(node_id, {}).get("stream_status")
                }

            # Update database
            metadata = {
                "cpu_temp": data.get("cpu_temp"),
                "memory_usage_percent": data.get("memory_usage_percent"),
                "buffer_health": data.get("buffer_health"),
                "visual_health": data.get("visual_health"),
                "audio_health": data.get("audio_health"),
                "uptime_seconds": data.get("uptime_seconds")
            }

            if self.db:
                self.db.upsert_node(node_id, "glass", "ONLINE", metadata)
            else:
                upsert_node(node_id, "glass", "ONLINE", metadata)

            logger.debug(f"Heartbeat from {node_id}: cpu_temp={data.get('cpu_temp')}°C")

            # Recovery broadcast (mirror _mark_node_offline / _handle_lwt_offline
            # idiom, inverse direction). ONLY on a transition to ONLINE — never on
            # every heartbeat — so dashboards learn about recovery instantly instead
            # of waiting for the next REST refresh. Best-effort: guarded by _loop and
            # never allowed to break heartbeat processing.
            if prev_status != "ONLINE" and self._loop is not None:
                try:
                    from .websocket_service import broadcast_from_sync
                    ws_data = {"node_id": node_id, "status": "ONLINE"}
                    # Include telemetry only when present (mirror the shape the old
                    # broadcast_node_status method produced).
                    cpu_temp = data.get("cpu_temp")
                    if cpu_temp is not None:
                        ws_data["cpu_temp"] = cpu_temp
                    memory_usage_percent = data.get("memory_usage_percent")
                    if memory_usage_percent is not None:
                        ws_data["memory_usage_percent"] = memory_usage_percent
                    broadcast_from_sync(self._loop, {
                        "type": "node_status",
                        "data": ws_data,
                    })
                except Exception as ws_err:
                    logger.debug(f"WebSocket broadcast for online recovery failed: {ws_err}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid heartbeat JSON from {node_id}: {e}")

    def _handle_lwt_offline(self, node_id: str):
        """Force a node OFFLINE from its Last-Will marker (ungraceful drop).
        Unlike the heartbeat-timeout path this does NOT re-validate staleness —
        an LWT means the node is definitively gone right now."""
        with self._lock:
            state = self.node_states.get(node_id)
            node_type = (state or {}).get("type", "glass")
            if state is None:
                # Unknown node: create a minimal OFFLINE state.
                self.node_states[node_id] = {"type": node_type, "status": "OFFLINE"}
            elif state.get("status") == "OFFLINE":
                return  # already offline — nothing to do
            else:
                state["status"] = "OFFLINE"

        # DB update (mirror _mark_node_offline)
        if self.db:
            self.db.update_node_status(node_id, "OFFLINE")
        else:
            update_node_status(node_id, "OFFLINE")

        if node_type == "pump":
            logger.critical(f"Node {node_id} OFFLINE via Last-Will (ungraceful disconnect)")
        else:
            logger.warning(f"Node {node_id} OFFLINE via Last-Will (ungraceful disconnect)")

        # WebSocket broadcast (mirror _mark_node_offline's exact idiom)
        if self._loop is not None:
            try:
                from .websocket_service import broadcast_from_sync
                broadcast_from_sync(self._loop, {
                    "type": "node_status",
                    "data": {"node_id": node_id, "status": "OFFLINE"}
                })
            except Exception as ws_err:
                logger.debug(f"WebSocket broadcast for LWT offline failed: {ws_err}")

    def _handle_pump_status(self, node_id: str, payload: str):
        """
        Handle pump status message from edge node.
        """
        try:
            data = json.loads(payload)
        except ValueError as e:
            logger.error(f"Invalid pump_status JSON from {node_id}: {e}")
            with self._lock:
                st = self.node_states.get(node_id, {"type": "pump", "status": "ONLINE"})
                st["last_heartbeat"] = utcnow()  # garbled-but-alive != offline
                self.node_states[node_id] = st
            return
        if not isinstance(data, dict):
            logger.error(f"pump_status payload not an object from {node_id}")
            with self._lock:
                st = self.node_states.get(node_id, {"type": "pump", "status": "ONLINE"})
                st["last_heartbeat"] = utcnow()  # glitchy-but-alive != offline
                self.node_states[node_id] = st
            return

        with self._lock:
            # Capture prior status BEFORE overwriting to detect a transition to
            # ONLINE (unknown-node first contact or OFFLINE->ONLINE recovery).
            prev_status = self.node_states.get(node_id, {}).get("status")
            self.node_states[node_id] = {
                "type": "pump", "status": "ONLINE",
                "last_heartbeat": utcnow(),
                "pump_state": data.get("pump_state", "UNKNOWN"),
                "water_level": data.get("water_level"),
                "raining": data.get("raining"),
                "float_safe": data.get("float_safe"),
                "high_water": data.get("high_water"),
                "sensor_conflict": data.get("sensor_conflict"),
                "dry_run_protect": data.get("dry_run_protect"),
                "reason": data.get("reason"),
            }

        metadata = {"pump_state": data.get("pump_state"), "water_level": data.get("water_level"),
                    "raining": data.get("raining"), "sensor_conflict": data.get("sensor_conflict")}
        if self.db:
            self.db.upsert_node(node_id, "pump", "ONLINE", metadata)
        else:
            upsert_node(node_id, "pump", "ONLINE", metadata)

        # Recovery broadcast: only on transition to ONLINE (never every message).
        # Pumps carry no cpu_temp/memory telemetry, so the payload is node_id +
        # status only. This is distinct from the pump_status telemetry broadcast
        # below. Best-effort; guarded by _loop and never fatal to processing.
        if prev_status != "ONLINE" and self._loop is not None:
            try:
                from .websocket_service import broadcast_from_sync
                broadcast_from_sync(self._loop, {
                    "type": "node_status",
                    "data": {"node_id": node_id, "status": "ONLINE"},
                })
            except Exception as ws_err:
                logger.debug(f"WebSocket broadcast for online recovery failed: {ws_err}")

        try:
            ts = data.get("timestamp") or utcnow().isoformat()
            insert_pump_reading(node_id, ts, data.get("water_level"), data.get("pump_state"),
                                raining=data.get("raining"), sensor_conflict=data.get("sensor_conflict"))
        except Exception as ts_err:
            logger.warning(f"Failed to persist pump reading for {node_id}: {ts_err}")

        logger.debug(f"Pump status from {node_id}: state={data.get('pump_state')}")

        self._broadcast_pump_status(node_id, data)  # see Task 10

    def _broadcast_pump_status(self, node_id, data):
        if self._loop is None:
            return
        try:
            from .websocket_service import broadcast_from_sync
            broadcast_from_sync(self._loop, {
                "type": "pump_status",
                "data": {
                    "node_id": node_id,
                    "pump_state": data.get("pump_state"),
                    "water_level": data.get("water_level"),
                    "raining": data.get("raining"),
                    "sensor_conflict": data.get("sensor_conflict"),
                    "dry_run_protect": data.get("dry_run_protect"),
                    "timestamp": data.get("timestamp", utcnow().isoformat()),
                },
            })
        except Exception as ws_error:
            logger.warning(f"WebSocket broadcast failed: {ws_error}")
    
    def _handle_stream_status(self, node_id: str, payload: str):
        """
        Handle stream status message from edge node.
        """
        try:
            data = json.loads(payload)
            
            with self._lock:
                if node_id in self.node_states:
                    self.node_states[node_id]["stream_status"] = data
                else:
                    # Node not yet registered, create minimal entry
                    self.node_states[node_id] = {
                        "type": "glass",
                        "status": "ONLINE",
                        "last_heartbeat": utcnow(),
                        "stream_status": data
                    }
            
            logger.info(f"Stream status from {node_id}: {data.get('status')}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid stream_status JSON from {node_id}: {e}")
    
    def get_node_states(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all node states.
        
        Returns:
            Dict of node_id -> node state
        """
        with self._lock:
            return dict(self.node_states)
    
    def get_node_state(self, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Get state for a specific node.
        
        Args:
            node_id: The node identifier
            
        Returns:
            Node state dict or None if not found
        """
        with self._lock:
            return self.node_states.get(node_id)
    
    def publish(self, topic: str, payload: dict, qos: int = 1) -> bool:
        """
        Publish a message to MQTT.
        
        Args:
            topic: The topic to publish to
            payload: The payload dict (will be JSON serialized)
            qos: Quality of Service level (default: 1)
            
        Returns:
            True if published successfully, False otherwise
        """
        if not self.client or not self._running:
            logger.warning("MQTT client not connected, cannot publish")
            return False
        
        try:
            result = self.client.publish(
                topic,
                json.dumps(payload),
                qos=qos
            )
            logger.debug(f"Published to {topic}: {payload}")
            return result.rc == 0
        except Exception as e:
            logger.error(f"Failed to publish to {topic}: {e}")
            return False
    
    def send_stream_command(self, node_id: str, command: str) -> bool:
        """
        Send a stream control command to an edge node.
        
        Args:
            node_id: The target node identifier
            command: "stream_start" or "stream_stop"
            
        Returns:
            True if command was sent successfully
        """
        topic = topic_cmd(node_id, command)
        payload = {"timestamp": utcnow().isoformat()}
        
        logger.info(f"Sending {command} command to {node_id}")
        return self.publish(topic, payload, qos=1)

    def send_snooze_config(self, node_id: str, snooze_until: Optional[str], snooze_reason: Optional[str] = None) -> bool:
        """
        Item 17: Push snooze config to an edge node so it can suppress audio-only triggers.

        The edge node firmware should subscribe to sdprs/edge/{node_id}/cmd/snooze
        and, when receiving a payload with snooze_until set, suppress pure-audio
        alerts until that timestamp (UTC ISO). Visual+audio AND-gate alerts are
        NOT suppressed — only audio-only triggers (which are typhoon false-positives).

        Args:
            node_id: The target node identifier
            snooze_until: UTC ISO timestamp when snooze expires, or None to clear
            snooze_reason: Optional human-readable reason for the snooze

        Returns:
            True if command was sent successfully

        NOTE: This method exists but the edge-side firmware stop-condition is not
        implemented yet. Until edge nodes subscribe and process this topic, the
        server-side snooze flag is checked only when processing incoming alerts
        (event_service.py). Full edge-side suppression requires firmware update.
        """
        topic = topic_cmd(node_id, "snooze")
        payload = {
            "snooze_until": snooze_until,
            "snooze_reason": snooze_reason,
            "timestamp": utcnow().isoformat()
        }
        logger.info(f"Sending snooze config to {node_id}: until={snooze_until}")
        return self.publish(topic, payload, qos=1)

    # ===== Offline Detection =====
    
    def _start_offline_detection(self):
        """Start the offline detection timer."""
        self._offline_check_event.clear()
        self._offline_check_timer = threading.Thread(
            target=self._offline_check_loop,
            daemon=True
        )
        self._offline_check_timer.start()
        logger.debug("Offline detection started")
    
    def _stop_offline_detection(self):
        """Stop the offline detection timer."""
        self._offline_check_event.set()
        if self._offline_check_timer:
            self._offline_check_timer.join(timeout=2)
        logger.debug("Offline detection stopped")
    
    def _offline_check_loop(self):
        """Check for offline nodes periodically."""
        while not self._offline_check_event.wait(self.OFFLINE_CHECK_INTERVAL):
            self._check_offline_nodes()
    
    def _check_offline_nodes(self):
        """
        Check for nodes that have not sent a heartbeat recently.
        """
        now = utcnow()
        
        with self._lock:
            nodes_to_update = []
            
            for node_id, state in self.node_states.items():
                if state["status"] == "OFFLINE":
                    continue
                
                last_heartbeat = state.get("last_heartbeat")
                if not last_heartbeat:
                    continue
                
                elapsed = (now - last_heartbeat).total_seconds()
                node_type = state.get("type", "glass")
                
                timeout = self.PUMP_OFFLINE_TIMEOUT if node_type == "pump" else self.GLASS_OFFLINE_TIMEOUT
                
                if elapsed > timeout:
                    nodes_to_update.append((node_id, node_type, elapsed))
            
        # Update offline nodes outside the lock
        for node_id, node_type, elapsed in nodes_to_update:
            self._mark_node_offline(node_id, node_type, elapsed)
    
    def _mark_node_offline(self, node_id: str, node_type: str, elapsed: float):
        """
        Mark a node as offline.

        Re-validates staleness under the lock BEFORE committing the OFFLINE
        transition. `_check_offline_nodes` selects stale nodes under the lock,
        then RELEASES it before calling this method — so between the scan and
        this lock acquisition a heartbeat handler (`_handle_heartbeat` /
        `_handle_pump_status`, which update `last_heartbeat` while holding
        `self._lock`) can refresh the node. Re-checking under the same lock
        makes the check-and-set atomic w.r.t. those handlers and prevents
        false-offline flapping (spurious CRITICAL pump logs, operator noise,
        WebSocket churn) when a fresh heartbeat arrived in the gap.

        Args:
            node_id: The node identifier
            node_type: Type of node ("glass" or "pump")
            elapsed: Seconds since last heartbeat (from the initial scan; the
                value used below is recomputed under the lock so the log/DB
                reflect the node's state at commit time)
        """
        with self._lock:
            state = self.node_states.get(node_id)
            if state is None:
                # Node was removed in the gap — nothing to do.
                return
            if state["status"] == "OFFLINE":
                # Already offline (e.g. duplicate scan) — no re-transition.
                return

            # Re-read heartbeat + timeout and recompute elapsed under the lock.
            last_heartbeat = state.get("last_heartbeat")
            node_type = state.get("type", node_type)
            timeout = self.PUMP_OFFLINE_TIMEOUT if node_type == "pump" else self.GLASS_OFFLINE_TIMEOUT

            if last_heartbeat is None:
                # No heartbeat recorded — can't confirm staleness; skip.
                return
            elapsed = (utcnow() - last_heartbeat).total_seconds()
            if elapsed <= timeout:
                # A fresh heartbeat arrived in the gap — abort the false offline.
                logger.debug(
                    f"Offline mark aborted for {node_id} — fresh heartbeat "
                    f"({elapsed:.0f}s <= {timeout}s timeout)"
                )
                return

            # Still genuinely stale — commit the OFFLINE transition in the lock.
            state["status"] = "OFFLINE"

        # Update database
        if self.db:
            self.db.update_node_status(node_id, "OFFLINE")
        else:
            update_node_status(node_id, "OFFLINE")
        
        # Log with appropriate severity
        if node_type == "pump":
            logger.critical(f"PUMP node {node_id} OFFLINE (no heartbeat for {elapsed:.0f}s)")
        else:
            logger.warning(f"Node {node_id} marked OFFLINE (no heartbeat for {elapsed:.0f}s)")
        
        # WebSocket broadcast for offline status
        if self._loop is not None:
            try:
                from .websocket_service import broadcast_from_sync
                broadcast_from_sync(self._loop, {
                    "type": "node_status",
                    "data": {"node_id": node_id, "status": "OFFLINE"}
                })
            except Exception as ws_err:
                logger.debug(f"WebSocket broadcast for offline failed: {ws_err}")


# Singleton instance
_mqtt_service: Optional[MQTTService] = None


def get_mqtt_service() -> Optional[MQTTService]:
    """
    Get the MQTT service singleton instance.
    
    Returns:
        The MQTTService instance or None if not initialized
    """
    return _mqtt_service


def init_mqtt_service(db_module=None, loop=None) -> MQTTService:
    """
    Initialize and start the MQTT service.

    Args:
        db_module: Database module for node operations
        loop: The asyncio event loop captured at FastAPI startup, used for
            WebSocket broadcasts from the MQTT background thread

    Returns:
        The MQTTService instance
    """
    global _mqtt_service
    _mqtt_service = MQTTService(db_module=db_module, loop=loop)
    return _mqtt_service


__all__ = ["MQTTService", "get_mqtt_service", "init_mqtt_service"]