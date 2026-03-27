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

# Import shared MQTT topic constants
try:
    import sys as _sys
    from pathlib import Path as _Path
    _shared_path = str(_Path(__file__).parent.parent.parent)
    if _shared_path not in _sys.path:
        _sys.path.insert(0, _shared_path)
    from shared.mqtt_topics import SUB_ALL_HEARTBEAT, SUB_ALL_PUMP_STATUS, SUB_ALL_STREAM_STATUS
    _TOPICS_IMPORTED = True
except ImportError:
    _TOPICS_IMPORTED = False
from ..database import upsert_node, update_node_heartbeat, update_node_status

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
    
    def __init__(self, db_module=None):
        """
        Initialize the MQTT service.
        
        Args:
            db_module: Database module for node operations (optional, for testing)
        """
        self.settings = get_settings()
        self.db = db_module
        
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
        
        # Create MQTT client
        self.client = mqtt.Client(client_id="central_server")
        
        # Set up callbacks
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        
        # Configure reconnection
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        
        try:
            # Connect to broker
            logger.info(f"Connecting to MQTT broker at {self.settings.MQTT_BROKER}:{self.settings.MQTT_PORT}")
            self.client.connect(
                self.settings.MQTT_BROKER,
                self.settings.MQTT_PORT,
                keepalive=60
            )
            
            # Start the network loop in a background thread
            self.client.loop_start()
            self._running = True
            
            # Start offline detection timer
            self._start_offline_detection()
            
            logger.info("MQTT service started")
            
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
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
            
            # Subscribe to all edge node topics
            if _TOPICS_IMPORTED:
                topics = [
                    (SUB_ALL_HEARTBEAT, 1),
                    (SUB_ALL_PUMP_STATUS, 1),
                    (SUB_ALL_STREAM_STATUS, 1),
                ]
            else:
                topics = [
                    ("sdprs/edge/+/heartbeat", 1),
                    ("sdprs/edge/+/pump_status", 1),
                    ("sdprs/edge/+/stream_status", 1),
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
            
            with self._lock:
                # Update node state in memory
                self.node_states[node_id] = {
                    "type": "glass",
                    "status": "ONLINE",
                    "last_heartbeat": datetime.utcnow(),
                    "cpu_temp": data.get("cpu_temp"),
                    "memory_usage_percent": data.get("memory_usage_percent"),
                    "buffer_health": data.get("buffer_health", "ok"),
                    "uptime_seconds": data.get("uptime_seconds"),
                    "stream_status": self.node_states.get(node_id, {}).get("stream_status")
                }
            
            # Update database
            metadata = {
                "cpu_temp": data.get("cpu_temp"),
                "memory_usage_percent": data.get("memory_usage_percent"),
                "buffer_health": data.get("buffer_health"),
                "uptime_seconds": data.get("uptime_seconds")
            }
            
            if self.db:
                self.db.upsert_node(node_id, "glass", "ONLINE", metadata)
            else:
                upsert_node(node_id, "glass", "ONLINE", metadata)
            
            logger.debug(f"Heartbeat from {node_id}: cpu_temp={data.get('cpu_temp')}°C")
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid heartbeat JSON from {node_id}: {e}")
    
    def _handle_pump_status(self, node_id: str, payload: str):
        """
        Handle pump status message from edge node.
        """
        try:
            data = json.loads(payload)
            
            with self._lock:
                # Update node state in memory
                self.node_states[node_id] = {
                    "type": "pump",
                    "status": "ONLINE",
                    "last_heartbeat": datetime.utcnow(),
                    "pump_state": data.get("pump_state", "UNKNOWN"),
                    "water_level": data.get("water_level"),
                }
            
            # Update database
            metadata = {
                "pump_state": data.get("pump_state"),
                "water_level": data.get("water_level")
            }
            
            if self.db:
                self.db.upsert_node(node_id, "pump", "ONLINE", metadata)
            else:
                upsert_node(node_id, "pump", "ONLINE", metadata)
            
            logger.debug(f"Pump status from {node_id}: state={data.get('pump_state')}")
            
            # WebSocket broadcast to connected clients
            try:
                from .websocket_service import broadcast_from_sync
                import asyncio
                
                # Get the event loop from app state or current
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.get_running_loop()
                
                if loop:
                    broadcast_from_sync(loop, {
                        "type": "pump_status",
                        "data": {
                            "node_id": node_id,
                            "pump_state": data.get("pump_state"),
                            "water_level": data.get("water_level"),
                            "timestamp": data.get("timestamp", datetime.utcnow().isoformat())
                        }
                    })
            except Exception as ws_error:
                logger.warning(f"WebSocket broadcast failed: {ws_error}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid pump_status JSON from {node_id}: {e}")
    
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
                        "last_heartbeat": datetime.utcnow(),
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
        topic = f"sdprs/edge/{node_id}/cmd/{command}"
        payload = {"timestamp": datetime.utcnow().isoformat()}
        
        logger.info(f"Sending {command} command to {node_id}")
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
        now = datetime.utcnow()
        
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
        
        Args:
            node_id: The node identifier
            node_type: Type of node ("glass" or "pump")
            elapsed: Seconds since last heartbeat
        """
        with self._lock:
            if node_id in self.node_states:
                self.node_states[node_id]["status"] = "OFFLINE"
        
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
        try:
            from .websocket_service import broadcast_from_sync
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None
            if loop:
                broadcast_from_sync(loop, {
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


def init_mqtt_service(db_module=None) -> MQTTService:
    """
    Initialize and start the MQTT service.
    
    Args:
        db_module: Database module for node operations
        
    Returns:
        The MQTTService instance
    """
    global _mqtt_service
    _mqtt_service = MQTTService(db_module=db_module)
    return _mqtt_service


__all__ = ["MQTTService", "get_mqtt_service", "init_mqtt_service"]