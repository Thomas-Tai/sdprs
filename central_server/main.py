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
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .api import alerts, snapshots, stream, nodes, weather, audit as audit_api, handover
from .services.websocket_service import router as ws_router
from .services.mqtt_service import init_mqtt_service, get_mqtt_service
from .services.retention_service import setup_retention_scheduler
from .services.weather_service import init_weather_service, get_weather_service
from .database import (
    init_db as db_init_db, close_db as db_close_db,
    get_all_events, get_events_by_status, get_event,
    get_all_nodes, get_pending_alert_ids, get_effective_handover_note,
)
from .services.event_service import get_event_counts, list_events
import time as _time
from .config import get_settings
from .auth import authenticate_user
from .timeutil import utcnow

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

    # Initialize database (use database.py module - single source of truth).
    # Sourced from Settings (same DB_PATH env var + default) so validation
    # and .env loading are applied consistently.
    db_path = settings.DB_PATH
    db_init_db(db_path)

    # Initialize in-memory snapshot storage
    app.state.latest_snapshots: Dict[str, Dict[str, Any]] = {}
    logger.info("Initialized latest_snapshots dict")

    # Initialize MQTT service
    # Capture uvicorn's running event loop ONCE here (lifespan runs on it) so
    # the MQTT background thread can hand WebSocket broadcasts to it via
    # broadcast_from_sync(self._loop, data) instead of the fragile
    # asyncio.get_event_loop() call from a non-main thread.
    import asyncio
    loop = asyncio.get_running_loop()
    mqtt_svc = init_mqtt_service(loop=loop)
    mqtt_svc.start()
    app.state.mqtt_service = mqtt_svc
    logger.info("MQTT service started")

    # Initialize retention scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()
        # Storage root: STORAGE_PATH (Settings) is authoritative — the same
        # root api/alerts.py writes uploads to, so the orphan-MP4 sweep
        # watches the tree that actually receives files. Fall back to the
        # legacy STORAGE_DIR env var only when STORAGE_PATH was left at its
        # default, so existing deployments keep working (with a warning).
        storage_dir = settings.STORAGE_PATH
        if storage_dir == "./storage" and os.environ.get("STORAGE_DIR"):
            storage_dir = os.environ["STORAGE_DIR"]
            logger.warning("STORAGE_DIR is deprecated; set STORAGE_PATH")
        retention_days = settings.RETENTION_DAYS
        setup_retention_scheduler(scheduler, db_path, storage_dir, retention_days)
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info("Retention scheduler started")
    except Exception as e:
        logger.warning(f"Failed to start retention scheduler: {e}")
        app.state.scheduler = None

    # Weather service (item 9). Hard-gated by CWA_API_KEY; if empty,
    # init_weather_service registers but start() is a no-op.
    try:
        weather_svc = init_weather_service(settings)
        await weather_svc.start()
        app.state.weather_service = weather_svc
    except Exception as e:
        logger.warning(f"Failed to start weather service: {e}")
        app.state.weather_service = None

    logger.info("SDPRS Central Server started successfully")

    yield

    # ===== Shutdown =====
    logger.info("Shutting down SDPRS Central Server...")

    # Stop weather service
    weather_svc = get_weather_service()
    if weather_svc is not None:
        try:
            await weather_svc.stop()
        except Exception as e:
            logger.warning(f"Weather service shutdown error: {e}")

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


# Get secret key from Settings (required + validated there; raw os.environ
# bypassed .env loading and the placeholder-value warning)
SECRET_KEY = get_settings().SECRET_KEY

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
    max_age=86400,  # 24 hours
    same_site="lax",  # mitigate CSRF on the session cookie
    # Only mark the cookie Secure (HTTPS-only) when COOKIE_SECURE is enabled.
    # Default False keeps the current HTTP LAN deployment working; set True
    # once the dashboard is served over TLS. httponly=True is Starlette's
    # SessionMiddleware default (no parameter needed).
    https_only=get_settings().COOKIE_SECURE,
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
        "timestamp": utcnow().isoformat(),
        "service": "sdprs-central-server"
    }


# ===== API Routers =====
app.include_router(alerts.router, prefix="/api")
app.include_router(snapshots.router, prefix="/api")
app.include_router(stream.router, prefix="/api")
app.include_router(nodes.router, prefix="/api")
app.include_router(weather.router, prefix="/api")
app.include_router(audit_api.router, prefix="/api")
app.include_router(handover.router, prefix="/api")
app.include_router(ws_router)


# ===== Dashboard Page Routes =====

def _get_dashboard_context(request: Request) -> dict:
    """Build common template context for dashboard pages."""
    mqtt_svc = get_mqtt_service()
    node_states = mqtt_svc.get_node_states() if mqtt_svc else {}

    # Also count nodes that have snapshots but no MQTT heartbeat
    snapshots = getattr(request.app.state, "latest_snapshots", {})
    snapshot_only_nodes = {nid for nid in snapshots if nid not in node_states}

    online_count = sum(1 for s in node_states.values() if s.get("status") == "ONLINE")
    online_count += len(snapshot_only_nodes)  # snapshot = online
    offline_count = sum(1 for s in node_states.values() if s.get("status") == "OFFLINE")
    total_nodes = len(node_states) + len(snapshot_only_nodes)
    pump_active = sum(
        1 for s in node_states.values()
        if s.get("type") == "pump" and s.get("pump_state") == "ON"
    )

    counts = get_event_counts()

    # IDs of alerts currently demanding operator action (PENDING with no ack yet).
    # Used by item-3 audio loop to seed initial state — without this, an operator
    # opening the dashboard mid-storm would hear no audio for already-queued alerts.
    unacked_alert_ids = get_pending_alert_ids()

    # Item 16: handover note. Cheap to read on every page (single row).
    # The 24hr read-time TTL lives in database.get_effective_handover_note()
    # — one implementation shared with api/handover.py, not an inline copy.
    handover_note = {"note": "", "author": None, "updated_at": None}
    try:
        row = get_effective_handover_note()
        if row and not row["expired"]:
            handover_note = {"note": row["note"], "author": row["author"], "updated_at": row["updated_at"]}
    except Exception as e:
        logger.debug(f"Handover note read failed (non-fatal): {e}")

    # Item 18: surface session expiry to the client so it can warn at T-5min.
    settings = get_settings()
    session_max_age_s = 24 * 3600
    login_at_iso = (request.session.get("login_at") if hasattr(request, "session") else None) or ""

    return {
        "pending_count": counts.get("pending", 0) + counts.get("pending_video", 0),
        "resolved_count": counts.get("resolved", 0),
        "acknowledged_count": counts.get("acknowledged", 0),
        "online_count": online_count,
        "offline_count": offline_count,
        "total_nodes": total_nodes,
        "pump_active_count": pump_active,
        "unacked_alert_ids": unacked_alert_ids,
        "handover_note": handover_note,
        "session_login_at": login_at_iso,
        "session_max_age_s": session_max_age_s,
    }


# ===== Login throttle (per-process, in-memory) =====
# Brute-force mitigation for the single dashboard password. Maps client IP ->
# list of recent FAILED-attempt monotonic timestamps. This state lives only in
# this worker process and RESETS on restart, which is acceptable for the
# single-node, single-worker uvicorn MVP. A multi-worker / multi-node
# deployment would need a shared store (e.g. Redis) instead.
_login_attempts: dict[str, list[float]] = {}
_login_attempts_lock = threading.Lock()


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    return templates.TemplateResponse(request, "login.html")


@app.post("/login")
async def login(request: Request):
    """Handle login form submission.

    Rate-limited per client IP: after LOGIN_MAX_ATTEMPTS failures within
    LOGIN_LOCKOUT_SECONDS, further attempts are rejected with 429 without even
    checking credentials. A successful login clears the IP's failure counter.
    """
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    settings = get_settings()
    max_attempts = getattr(settings, "LOGIN_MAX_ATTEMPTS", 5)
    window = getattr(settings, "LOGIN_LOCKOUT_SECONDS", 300)

    # Use a monotonic clock so lockout windows are immune to wall-clock jumps.
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()

    with _login_attempts_lock:
        # Prune failures older than the lockout window for this IP.
        recent = [t for t in _login_attempts.get(ip, []) if t > now - window]
        _login_attempts[ip] = recent
        locked = len(recent) >= max_attempts

    if locked:
        # Too many recent failures from this IP: reject before touching creds.
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "嘗試次數過多，請稍後再試"},
            status_code=429,
        )

    if authenticate_user(username, password):
        # Success clears this IP's failure history.
        with _login_attempts_lock:
            _login_attempts.pop(ip, None)
        request.session["user"] = username
        request.session["login_at"] = utcnow().isoformat()  # item 18
        from .services.audit_service import log_action, ACTION_LOGIN
        log_action(username, ACTION_LOGIN)
        return RedirectResponse(url="/", status_code=303)

    # Record this failure against the IP.
    with _login_attempts_lock:
        _login_attempts.setdefault(ip, []).append(now)

    return templates.TemplateResponse(request, "login.html", {"error": "帳號或密碼錯誤"})


@app.post("/api/session/extend")
async def extend_session(request: Request):
    """Item 18: refresh the session timer by re-stamping login_at.

    Starlette's SessionMiddleware re-emits the cookie on every response when
    the session contents change, so just touching the dict resets max_age.
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    request.session["login_at"] = utcnow().isoformat()
    return {"ok": True, "login_at": request.session["login_at"]}


@app.post("/logout")
async def logout(request: Request):
    """Handle logout."""
    user = request.session.get("user", "")
    if user:
        from .services.audit_service import log_action, ACTION_LOGOUT
        log_action(user, ACTION_LOGOUT)
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


def _js_safe_json(value) -> str:
    """Serialize *value* as JSON safe for embedding inside an inline <script>.

    ``json.dumps()`` alone is not safe inside a ``<script>`` block: a value
    containing ``</script``, ``<!--``, or ``<script`` can break out of the
    tag and inject arbitrary markup/script (e.g. a crafted dashboard
    username reflected into the SPA shell). Escaping ``<``, ``>``, and ``&``
    neutralizes tag breakout; escaping U+2028/U+2029 avoids the JS line
    separator characters, which are otherwise illegal inside JS string
    literals. The result is still valid JSON, and still valid JS.
    """
    import json as _json
    encoded = _json.dumps(value)
    return (
        encoded
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Serve the SDPRS V2 SPA shell.

    The dashboard is now a React single-page app under /static/spa. This
    route returns the SPA index.html with the logged-in username injected
    so the client-side data layer can use it. All data comes from /api/*.

    The legacy Jinja dashboard logic is kept available at /dashboard-legacy
    so previously-bookmarked links / saved searches still work.
    """
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")
    spa_index = BASE_DIR / "static" / "spa" / "index.html"
    html = spa_index.read_text(encoding="utf-8")
    html = html.replace("__SDPRS_USER__", _js_safe_json(user))
    return HTMLResponse(html)


@app.get("/dashboard-legacy", response_class=HTMLResponse)
async def dashboard_page_legacy(request: Request, status: str = None, node: str = None, page: int = 1):
    """Legacy Jinja dashboard (preserved for direct links from before the V2 rollout)."""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")

    ctx = _get_dashboard_context(request)

    DEFAULT_ACTIVE_FILTER = "PENDING_VIDEO,PENDING,ACKNOWLEDGED"
    if status is None:
        effective_status = DEFAULT_ACTIVE_FILTER
        display_status = DEFAULT_ACTIVE_FILTER
    elif status == "all":
        effective_status = None
        display_status = "all"
    else:
        effective_status = status
        display_status = status

    result = list_events(status_filter=effective_status, node_filter=node, page=page, page_size=20)

    ctx["events"] = result["items"]
    ctx["total"] = result["total"]
    ctx["total_pages"] = result["total_pages"]
    ctx["current_page"] = result["page"]
    ctx["current_status_filter"] = display_status
    ctx["current_node_filter"] = node or ""

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
    snapshots = getattr(request.app.state, "latest_snapshots", {})

    glass_nodes = []
    for nid, state in node_states.items():
        if state.get("type") != "glass":
            continue
        # Merge snapshot timestamp from latest_snapshots if available
        snap_data = snapshots.get(nid)
        node_dict = {"node_id": nid, **state}
        if snap_data:
            ts = snap_data.get("timestamp")
            if ts:
                # Append 'Z' to indicate UTC time for proper timezone conversion in JS
                node_dict["snapshot_timestamp"] = ts.isoformat() + 'Z' if hasattr(ts, 'isoformat') else str(ts)
        glass_nodes.append(node_dict)

    # Also include nodes that have snapshots but no MQTT heartbeat yet
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

    # Item 13/17/12: enrich with DB-backed columns (last_upload_at, snooze, battery)
    db_nodes_by_id = {n["node_id"]: n for n in (get_all_nodes() or [])}

    def _merge(nid: str, state: dict) -> dict:
        merged = {"node_id": nid, **state}
        db_row = db_nodes_by_id.get(nid) or {}
        merged["last_upload_at"] = db_row.get("last_upload_at")
        merged["snoozed_until"] = db_row.get("snoozed_until")
        merged["snooze_reason"] = db_row.get("snooze_reason")
        merged["battery_voltage"] = db_row.get("battery_voltage")
        merged["power_source"] = db_row.get("power_source")
        merged["location"] = db_row.get("location")
        return merged

    glass_nodes = [_merge(nid, state) for nid, state in node_states.items() if state.get("type") == "glass"]
    pump_nodes = [_merge(nid, state) for nid, state in node_states.items() if state.get("type") == "pump"]

    ctx["glass_nodes"] = glass_nodes
    ctx["pump_nodes"] = pump_nodes
    return templates.TemplateResponse(request, "system_status.html", ctx)


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request, operator: str = None, action_type: str = None):
    """Operator-action audit log (admin-only). Item 15."""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")
    settings = get_settings()
    if user != settings.DASHBOARD_USER:
        # Non-admin sees a friendly 403 page rather than a raw error.
        return HTMLResponse("<h1>403 Forbidden</h1><p>Admin only.</p>", status_code=403)

    from .services.audit_service import list_actions
    rows = list_actions(limit=200, operator=operator or None, action_type=action_type or None)
    ctx = _get_dashboard_context(request)
    ctx["rows"] = rows
    ctx["operator"] = operator or ""
    ctx["action_type"] = action_type or ""
    ctx["action_types"] = [
        "LOGIN", "LOGOUT", "ACKNOWLEDGE", "RESOLVE", "BULK_RESOLVE",
        "SNOOZE", "UNSNOOZE", "LOCATION_EDIT", "HANDOVER_EDIT",
    ]
    return templates.TemplateResponse(request, "audit.html", ctx)


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
