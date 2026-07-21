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
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .api import alerts, snapshots, stream, nodes, weather, audit as audit_api, handover
from .api import webcam as webcam_api
from .services.websocket_service import router as ws_router
from .services.mqtt_service import init_mqtt_service, get_mqtt_service
from .services.retention_service import setup_retention_scheduler
from .services.weather_service import init_weather_service, get_weather_service
from .database import init_db as db_init_db, close_db as db_close_db
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
    
    # Validate settings at startup — fail closed on placeholder credentials
    # or weak secrets. Prior behavior wrapped this in try/except ValueError
    # and only logged a warning, which allowed the server to start with
    # known-insecure defaults (auth bypass). See MIGRATION.md 2026-07-16
    # SECURITY entry for rotation guidance on affected deployments.
    from .config import validate_settings
    settings = get_settings()
    validate_settings(settings)

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
        from .services.hls_service import cleanup_stale_streams
        # cleanup_stale_streams is async now (Task 3b): it awaits enqueue_command +
        # ws_manager.broadcast to force a real client stop on lease expiry.
        # AsyncIOScheduler awaits it on the loop. 30s cadence matches the 90s lease
        # TTL so an expired lease is enforced within one scan.
        scheduler.add_job(cleanup_stale_streams, "interval", seconds=30, id="hls_cleanup")
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


# ===== CSRF Origin gate (Auth-E1, 2026-07-16) =====
# Defense-in-depth backup to the SessionMiddleware same_site="lax" cookie flag.
# For POST/PUT/PATCH/DELETE requests to /api/* and /logout, require the Origin
# (or Referer, as fallback) header to match the request's own scheme+host or a
# host listed in the CSRF_TRUSTED_ORIGINS env var. This catches the residual
# risks that a lax-samesite cookie alone does not: same-site subdomain XSS
# forging cross-site POSTs, browser bugs leaking the cookie cross-site, or a
# future middleware misconfig that flips same_site="none" without our noticing.
#
# The whole check is a bypass for GET/HEAD/OPTIONS (never mutating) and for the
# /login endpoint itself (an unauthenticated form POST is the whole point).
# A request that carries NEITHER Origin nor Referer is allowed through — a
# curl script or server-rendered same-origin form legitimately lacks both, and
# blanket-rejecting would break too many real clients. The lax same-site
# cookie is still the primary CSRF defense; this middleware is the belt.
class CSRFOriginMiddleware(BaseHTTPMiddleware):
    """Reject cross-site mutating requests by comparing Origin/Referer to Host."""

    _GUARDED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
    # /login is intentionally NOT guarded — see class docstring.
    _BYPASS_PATHS = frozenset({"/login"})

    def _guards_path(self, path: str) -> bool:
        if path in self._BYPASS_PATHS:
            return False
        return path.startswith("/api/") or path == "/logout"

    @staticmethod
    def _normalize(url: str):
        """Return canonical ``scheme://netloc`` (lowercased) or None on malformed input.

        Parses the URL rather than string-matching — that is exactly the
        naive-filter idiom CSRF-bypass tools defeat by prepending fake schemes
        or embedding the allowed host in the path.
        """
        try:
            parts = urlsplit(url)
        except (ValueError, TypeError):
            return None
        if not parts.scheme or not parts.netloc:
            return None
        return f"{parts.scheme.lower()}://{parts.netloc.lower()}"

    async def dispatch(self, request: Request, call_next):
        if request.method not in self._GUARDED_METHODS:
            return await call_next(request)
        if not self._guards_path(request.url.path):
            return await call_next(request)

        raw = request.headers.get("origin") or request.headers.get("referer")
        # Neither header set: allow. Common for non-browser clients (curl,
        # httpx without follow-redirects). See class docstring.
        if not raw:
            return await call_next(request)

        origin_norm = self._normalize(raw)
        if origin_norm is None:
            return PlainTextResponse("CSRF: malformed origin", status_code=403)

        # Same-origin allowlist: derived from the request's own Host header
        # and URL scheme. This is what makes a same-origin browser POST work.
        # Behind a TLS-terminating reverse proxy (Zeabur, nginx, cloudfront…)
        # request.url.scheme is the INTERNAL scheme (usually http) while the
        # browser's Origin/Referer carries the EXTERNAL scheme (https). Trust
        # X-Forwarded-Proto if present, and additionally allow both schemes on
        # the same host so a proxy that strips the forwarded header doesn't
        # produce a false-positive CSRF rejection for a same-origin request.
        allowed = set()
        host = request.headers.get("host", "").strip()
        if host:
            host_lc = host.lower()
            allowed.add(f"http://{host_lc}")
            allowed.add(f"https://{host_lc}")
            fwd_proto = request.headers.get("x-forwarded-proto", "").strip().lower()
            if fwd_proto:
                # In case a proxy rewrites Host and forwards the original
                # externally-facing host in X-Forwarded-Host.
                fwd_host = request.headers.get("x-forwarded-host", host).strip().lower()
                allowed.add(f"{fwd_proto}://{fwd_host}")

        # Optional extra trusted origins from env (comma-separated). For
        # deployments that front the app under additional hostnames.
        # Read at request time so operational rollout doesn't require restart.
        for extra in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(","):
            extra = extra.strip()
            if not extra:
                continue
            n = self._normalize(extra)
            if n:
                allowed.add(n)

        if origin_norm in allowed:
            return await call_next(request)
        return PlainTextResponse("CSRF: origin not allowed", status_code=403)


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

# CSRF gate is added AFTER SessionMiddleware in code order, which makes it the
# OUTER middleware (Starlette's add_middleware inserts each new entry at the
# front of the stack). Request flow: CSRFOriginMiddleware → SessionMiddleware
# → route. This is what lets the CSRF check reject a cross-site POST before
# any route dependency (get_current_user, DB access, etc.) even runs.
app.add_middleware(CSRFOriginMiddleware)

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
app.include_router(webcam_api.router, prefix="/api")
app.include_router(ws_router)


# ===== Dashboard Page Routes =====
#
# The V2 SPA at `/` is the single dashboard front-end. The legacy Jinja
# dashboard (base.html + 5 pages, plus static/js/dashboard.js and monitor.js)
# was retired 2026-07-16 — SPA covers every page it did (alerts / monitor /
# status / audit) plus new pages (pumps / weather / handover). Legacy routes
# 301-redirect below so any lingering browser bookmarks still land on the SPA.


# ===== Login throttle (per-process, in-memory) =====
# Brute-force mitigation for the single dashboard password. Maps client IP ->
# list of recent FAILED-attempt monotonic timestamps. This state lives only in
# this worker process and RESETS on restart, which is acceptable for the
# single-node, single-worker uvicorn MVP. A multi-worker / multi-node
# deployment would need a shared store (e.g. Redis) instead.
_login_attempts: dict[str, list[float]] = {}
_login_attempts_lock = threading.Lock()


def _safe_next_path(next_param: object) -> str:
    """Open-redirect defense for the /login `?next=` redirect target.

    Only accept plain LOCAL paths — must start with a single '/' and not with
    the protocol-relative '//' or '/\\\\' prefixes (which some browsers
    normalize to an off-site URL). Rejects anything with a scheme or netloc.
    Fallback is '/', matching pre-2026-07-16 hard-coded behavior.

    Added 2026-07-16 to complete the session-expiry state-carrier slice
    (audit H-1). The SPA session-expiry modal encodes {page, selectedId,
    hadDraft} into a `?sdprs_state=<base64>` query string on the target URL,
    then hands it to `/login?next=<encoded target>`. Without honoring
    `next`, the frontend round-trip was a no-op — every login landed on
    `/` regardless of what the operator was doing when the session died.
    """
    if not next_param or not isinstance(next_param, str):
        return "/"
    if not next_param.startswith("/"):
        return "/"
    if next_param.startswith("//") or next_param.startswith("/\\"):
        return "/"
    from urllib.parse import urlparse
    parsed = urlparse(next_param)
    if parsed.scheme or parsed.netloc:
        return "/"
    return next_param


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page. Preserves `?next=` into a hidden form input so the POST
    round-trip carries it back. Jinja auto-escapes the value in the
    rendered HTML; open-redirect validation is enforced at POST time via
    `_safe_next_path` — GET-time echo is not a trust boundary."""
    next_param = request.query_params.get("next", "")
    return templates.TemplateResponse(request, "login.html", {"next": next_param})


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
    next_param = form.get("next", "")

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
        # Auth-I2 (2026-07-16): persist the lockout hit itself. Fires ONCE per
        # attempt-while-locked, which is noisy for a persistent attacker — but
        # the noise IS the signal here: operators reviewing the audit trail
        # see the lockout rows pile up and can identify the source IP.
        # log_action failure must NOT be swallowed at this call site: the
        # audit log is a security invariant, and hiding lockout events would
        # defeat the purpose of persisting them.
        from .services.audit_service import log_action, ACTION_LOGIN_LOCKED
        log_action(username or "<empty>", ACTION_LOGIN_LOCKED, target_id=ip)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "嘗試次數過多，請稍後再試", "next": next_param},
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
        target = _safe_next_path(next_param)
        return RedirectResponse(url=target, status_code=303)

    # Record this failure against the IP.
    with _login_attempts_lock:
        _login_attempts.setdefault(ip, []).append(now)

    # Auth-I1 (2026-07-16): persist the failed-login event alongside the
    # in-memory throttle counter. Grep-across-app-logs forensics is fragile —
    # the operator_actions table is the durable source. Do NOT log the
    # password or the full form body here (would leak credential material
    # into the audit surface).
    from .services.audit_service import log_action, ACTION_LOGIN_FAILED
    log_action(username or "<empty>", ACTION_LOGIN_FAILED, target_id=ip)

    return templates.TemplateResponse(request, "login.html", {"error": "帳號或密碼錯誤", "next": next_param})


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
    """
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")
    spa_index = BASE_DIR / "static" / "spa" / "index.html"
    html = spa_index.read_text(encoding="utf-8")
    html = html.replace("__SDPRS_USER__", _js_safe_json(user))
    return HTMLResponse(html)


# ---- Legacy dashboard redirects (retired 2026-07-16) --------------------
# 301 redirects preserve any browser bookmarks by pointing at the SPA.
# The SPA is a single-URL app — deep-link into a page via hash-router only
# (`/#monitor`, `/#audit`, `/#status`); external inbound links go to `/`
# and land on the default alerts page.

@app.get("/dashboard-legacy")
async def dashboard_legacy_redirect():
    return RedirectResponse(url="/", status_code=301)


@app.get("/alerts/{alert_id}")
async def alert_detail_legacy_redirect(alert_id: int):
    # Old per-alert Jinja page (`/alerts/123`). SPA renders alerts on the
    # default page; the specific ID can't be preserved without a hash-router
    # deep-link contract, so we just land on the alerts list.
    return RedirectResponse(url="/", status_code=301)


@app.get("/monitor")
async def monitor_legacy_redirect():
    return RedirectResponse(url="/", status_code=301)


@app.get("/system")
async def system_legacy_redirect():
    return RedirectResponse(url="/", status_code=301)


@app.get("/audit")
async def audit_legacy_redirect():
    return RedirectResponse(url="/", status_code=301)


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
