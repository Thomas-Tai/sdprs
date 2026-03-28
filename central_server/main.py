# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Main FastAPI Application
Smart Disaster Prevention Response System

This is the main entry point for the central server, providing:
- REST API (alerts, snapshots, stream control)
- WebSocket (real-time push - M3)
- Jinja2 templates (dashboard pages)
- MQTT client (node status management - M3)
"""

import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .api import alerts, snapshots, stream, nodes
from .services.websocket_service import router as ws_router
from .services.mqtt_service import init_mqtt_service, get_mqtt_service
from .services.retention_service import setup_retention_scheduler
from .database import (
    init_db as db_init_db, close_db as db_close_db,
    get_db, get_all_events, get_events_by_status, get_event,
    get_all_nodes
)
from .services.event_service import get_event_counts, list_events
import time as _time
from .config import get_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("central_server")




@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager for startup and shutdown events.
    """
    # ===== Startup =====
    logger.info("Starting SDPRS Central Server...")
    
    # Validate settings at startup
    from .config import validate_settings
    settings = get_settings()
    try:
        validate_settings(settings)
    except ValueError as e:
        logger.warning(f"Settings validation warning: {e}")

    # Initialize database (use database.py module - single source of truth)
    db_path = os.environ.get("DB_PATH", "./data/sdprs.db")
    db_init_db(db_path)

    # Initialize in-memory snapshot storage
    app.state.latest_snapshots: Dict[str, Dict[str, Any]] = {}
    logger.info("Initialized latest_snapshots dict")

    # Initialize MQTT service
    mqtt_svc = init_mqtt_service()
    mqtt_svc.start()
    app.state.mqtt_service = mqtt_svc
    logger.info("MQTT service started")

    # Initialize retention scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()
        storage_dir = os.environ.get("STORAGE_DIR", "./storage")
        retention_days = int(os.environ.get("RETENTION_DAYS", "30"))
        setup_retention_scheduler(scheduler, db_path, storage_dir, retention_days)
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info("Retention scheduler started")
    except Exception as e:
        logger.warning(f"Failed to start retention scheduler: {e}")
        app.state.scheduler = None

    logger.info("SDPRS Central Server started successfully")

    yield

    # ===== Shutdown =====
    logger.info("Shutting down SDPRS Central Server...")

    # Stop retention scheduler
    if getattr(app.state, "scheduler", None):
        app.state.scheduler.shutdown(wait=False)

    # Stop MQTT service
    mqtt_svc = get_mqtt_service()
    if mqtt_svc:
        mqtt_svc.stop()

    # Close database connection
    db_close_db()

    logger.info("SDPRS Central Server shutdown complete")


# Get secret key from environment
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

# Create FastAPI application
app = FastAPI(
    title="SDPRS Central Server",
    description="Smart Disaster Prevention Response System - Central Server API",
    version="1.0.0",
    lifespan=lifespan
)

# Add session middleware for cookie-based sessions
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="sdprs_session",
    max_age=86400  # 24 hours
)

# Get the directory where this file is located
BASE_DIR = Path(__file__).resolve().parent

# Mount static files
static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Setup Jinja2 templates
templates_dir = BASE_DIR / "templates"
templates_dir.mkdir(parents=True, exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))


# ===== Health Check Endpoint =====
@app.get("/api/health", tags=["health"])
async def health_check():
    """
    Health check endpoint for monitoring and load balancers.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "sdprs-central-server"
    }


# ===== API Routers =====
app.include_router(alerts.router, prefix="/api")
app.include_router(snapshots.router, prefix="/api")
app.include_router(stream.router, prefix="/api")
app.include_router(nodes.router, prefix="/api")
app.include_router(ws_router)


# ===== Dashboard Page Routes =====

def _get_dashboard_context(request: Request) -> dict:
    """Build common template context for dashboard pages."""
    mqtt_svc = get_mqtt_service()
    node_states = mqtt_svc.get_node_states() if mqtt_svc else {}

    online_count = sum(1 for s in node_states.values() if s.get("status") == "ONLINE")
    offline_count = sum(1 for s in node_states.values() if s.get("status") == "OFFLINE")
    pump_active = sum(
        1 for s in node_states.values()
        if s.get("type") == "pump" and s.get("pump_state") == "ON"
    )

    db = get_db()
    counts = get_event_counts(db)

    return {
        "pending_count": counts.get("pending", 0) + counts.get("pending_video", 0),
        "resolved_count": counts.get("resolved", 0),
        "online_count": online_count,
        "offline_count": offline_count,
        "total_nodes": len(node_states),
        "pump_active_count": pump_active,
    }


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    return templates.TemplateResponse(request, "login.html")


@app.post("/login")
async def login(request: Request):
    """Handle login form submission."""
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    settings = get_settings()
    if username == settings.DASHBOARD_USER and password == settings.DASHBOARD_PASS:
        request.session["user"] = username
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(request, "login.html", {"error": "帳號或密碼錯誤"})


@app.post("/logout")
async def logout(request: Request):
    """Handle logout."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request, status: str = None, node: str = None, page: int = 1):
    """Main dashboard page."""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")

    ctx = _get_dashboard_context(request)

    # Use list_events for pagination and filtering
    db = get_db()
    result = list_events(db, status_filter=status, node_filter=node, page=page, page_size=20)

    ctx["events"] = result["items"]
    ctx["total"] = result["total"]
    ctx["total_pages"] = result["total_pages"]
    ctx["current_page"] = result["page"]
    ctx["current_status_filter"] = status or ""
    ctx["current_node_filter"] = node or ""

    # Get available node IDs for filter dropdown
    try:
        all_nodes_db = get_all_nodes()
        ctx["available_nodes"] = [n["node_id"] for n in all_nodes_db] if all_nodes_db else []
    except Exception:
        ctx["available_nodes"] = []

    ctx["status_filter"] = status
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@app.get("/alerts/{alert_id}", response_class=HTMLResponse)
async def alert_detail_page(request: Request, alert_id: int):
    """Alert detail page."""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")

    event = get_event(alert_id)
    if not event:
        return RedirectResponse(url="/")

    ctx = _get_dashboard_context(request)
    ctx["event"] = event
    return templates.TemplateResponse(request, "alert_detail.html", ctx)


@app.get("/monitor", response_class=HTMLResponse)
async def monitor_page(request: Request):
    """Monitoring wall page."""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")

    ctx = _get_dashboard_context(request)

    mqtt_svc = get_mqtt_service()
    node_states = mqtt_svc.get_node_states() if mqtt_svc else {}
    glass_nodes = [
        {"node_id": nid, **state}
        for nid, state in node_states.items()
        if state.get("type") == "glass"
    ]

    # Also include nodes that have snapshots but no MQTT heartbeat yet
    snapshots = getattr(request.app.state, "latest_snapshots", {})
    mqtt_node_ids = {n["node_id"] for n in glass_nodes}
    for nid, snap_data in snapshots.items():
        if nid not in mqtt_node_ids:
            glass_nodes.append({
                "node_id": nid,
                "status": "ONLINE",
                "type": "glass",
                "snapshot_timestamp": snap_data.get("timestamp", ""),
                "is_stale": False,
            })

    ctx["glass_nodes"] = glass_nodes
    ctx["now_ts"] = int(_time.time())
    return templates.TemplateResponse(request, "monitor.html", ctx)


@app.get("/system", response_class=HTMLResponse)
async def system_status_page(request: Request):
    """System status page."""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")

    ctx = _get_dashboard_context(request)

    mqtt_svc = get_mqtt_service()
    node_states = mqtt_svc.get_node_states() if mqtt_svc else {}

    glass_nodes = [
        {"node_id": nid, **state}
        for nid, state in node_states.items()
        if state.get("type") == "glass"
    ]
    pump_nodes = [
        {"node_id": nid, **state}
        for nid, state in node_states.items()
        if state.get("type") == "pump"
    ]

    ctx["glass_nodes"] = glass_nodes
    ctx["pump_nodes"] = pump_nodes
    return templates.TemplateResponse(request, "system_status.html", ctx)


# ===== Exception Handlers =====
@app.exception_handler(sqlite3.Error)
async def sqlite_exception_handler(request: Request, exc: sqlite3.Error):
    """Handle SQLite database errors."""
    logger.error(f"Database error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Database error occurred"}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unexpected errors."""
    logger.exception(f"Unexpected error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


# ===== Main Entry Point =====
if __name__ == "__main__":
    import uvicorn
    
    # Get configuration from environment
    host = os.environ.get("SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("SERVER_PORT", 8000))
    
    uvicorn.run(
        "central_server.main:app",
        host=host,
        port=port,
        reload=True,
        log_level="info"
    )