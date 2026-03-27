# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Services Package
Smart Disaster Prevention Response System

This package contains background services for the central server:
- mqtt_service: MQTT client for node status management
- websocket_service: WebSocket manager for real-time broadcasting
- event_service: Event CRUD operations
- retention_service: Data retention cleanup
"""

from .mqtt_service import MQTTService, get_mqtt_service, init_mqtt_service
from .websocket_service import WebSocketManager, ws_manager, broadcast_from_sync
from .event_service import (
    list_events,
    get_event,
    create_event,
    update_event_video,
    resolve_event,
    get_event_counts,
    delete_event,
    get_events_for_retention,
    delete_events_before_date,
)
from .retention_service import (
    run_retention_cleanup,
    setup_retention_scheduler,
    get_storage_stats,
)

__all__ = [
    # MQTT
    "MQTTService",
    "get_mqtt_service",
    "init_mqtt_service",
    # WebSocket
    "WebSocketManager",
    "ws_manager",
    "broadcast_from_sync",
    # Event Service
    "list_events",
    "get_event",
    "create_event",
    "update_event_video",
    "resolve_event",
    "get_event_counts",
    "delete_event",
    "get_events_for_retention",
    "delete_events_before_date",
    # Retention
    "run_retention_cleanup",
    "setup_retention_scheduler",
    "get_storage_stats",
]