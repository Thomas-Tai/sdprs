# -*- coding: utf-8 -*-
"""
SDPRS Central Server - API Package
Smart Disaster Prevention Response System

This package contains all REST API endpoints for the central server.
"""

from .alerts import router as alerts_router
from .snapshots import router as snapshots_router
from .stream import router as stream_router
from .nodes import router as nodes_router

__all__ = [
    "alerts_router",
    "snapshots_router",
    "stream_router",
    "nodes_router",
]