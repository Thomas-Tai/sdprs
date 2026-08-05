# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Authentication Module
Smart Disaster Prevention Response System

This module provides authentication support for three scenarios:
A. API Key authentication (edge node to server)
B. Session authentication (dashboard users)
C. WebSocket authentication
"""

import hashlib
import logging
import re
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from .config import get_settings

# Configure logging
logger = logging.getLogger("auth")

# API Key header scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Storage-B defense-in-depth: character-class + length gate for edge-supplied
# node_id values. Runs unconditionally in verify_node_id() regardless of the
# ALLOWED_NODE_IDS allowlist state, so a misconfigured deployment (empty
# allowlist) cannot be walked out of the storage/events/<node_id>/ tree via
# path-traversal (`..`) or absolute-path (`/etc/foo`) segments. `Path(a) / b`
# discards `a` when `b` is absolute, and treats `..` as a real parent
# reference — both are neutralised by rejecting anything outside this pattern.
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _ct_equal(a: str, b: str) -> bool:
    """Constant-time string equality.

    Compares the UTF-8 byte encodings rather than the str values directly:
    ``secrets.compare_digest`` raises TypeError on str operands containing
    non-ASCII characters, so a legitimately non-ASCII credential (this is a
    Traditional-Chinese deployment — an operator may set a non-ASCII
    DASHBOARD_PASS) would otherwise 500 the login instead of comparing safely.
    """
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ===== API Key Authentication =====

async def verify_api_key(
    request: Request,
    api_key: Optional[str] = Depends(api_key_header),
) -> str:
    """
    Verify API key from X-API-Key header.

    Used for authenticating edge node requests. A per-node key (Track 1:
    per-node edge API keys) is checked FIRST and, when it matches, binds the
    request to that node via ``request.state.edge_auth_node``. The shared
    EDGE_API_KEY remains a fallback for as long as it is configured (grace
    period during per-node rollout); a shared-key match leaves
    ``request.state.edge_auth_node`` as None (unbound / not node-specific).

    Args:
        request: The FastAPI request object (stamped with the resolved
            edge-auth identity for downstream consumers such as
            ``verify_node_binding``).
        api_key: The API key from the header

    Returns:
        The validated API key (unchanged so existing callers keep working)

    Raises:
        HTTPException: 401 if API key is missing or invalid
    """
    settings = get_settings()

    if api_key is None:
        logger.warning("API key missing in request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required"
        )

    # Per-node key first: a hash-equality SQL lookup, not a raw string
    # comparison, so constant-time comparison does not apply here.
    from .database import get_edge_node_by_key
    rec = get_edge_node_by_key(api_key)
    if rec is not None:
        request.state.edge_auth_node = rec["node_id"]
        return api_key

    # Shared-key fallback (grace period while nodes are migrated to
    # per-node keys). Constant-time comparison still applies here.
    shared = getattr(settings, "EDGE_API_KEY", "") or ""
    if shared and _ct_equal(api_key, shared):
        request.state.edge_auth_node = None
        return api_key

    # Auth-G1 (2026-07-16): the EDGE_API_KEY floor is now >=32 chars /
    # >=8 unique chars, so `api_key[:8]` would leak ~32 bits of the
    # rejected credential to anyone reading the log. Log a SHA-256
    # digest instead: no key material is disclosed, but distinct wrong
    # keys still produce distinct digests so operators can correlate
    # repeated attempts from the same offender.
    key_digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
    logger.warning(f"Invalid API key attempt (sha256={key_digest})")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key"
    )


def verify_node_id(node_id: str) -> None:
    """Validate an edge-supplied node_id against the configured allowlist.

    Trust-boundary control: without this, any client with the shared API key
    (or none, on some ingest paths) can POST telemetry/alerts under an
    arbitrary node_id and pollute the node registry / storage tree.

    Reads settings.ALLOWED_NODE_IDS (comma-separated). An EMPTY value disables
    the allowlist (allow all) for backward compatibility with existing
    single-node deployments. Whitespace around each id is stripped; empty
    entries are ignored.

    Raises HTTPException 400 if node_id is empty, too long, or contains
    characters outside ``[A-Za-z0-9._-]`` — this defense-in-depth check runs
    unconditionally so a downstream ``storage_path / "events" / node_id``
    cannot be walked out of the events tree via ``..`` or an absolute path,
    even when the allowlist is disabled (Storage-B, 2026-07-16).

    Raises HTTPException 403 if the allowlist is non-empty and node_id is not in it.
    """
    # Storage-B: always enforce char-class + length, regardless of allowlist.
    # `Path("./storage/events") / "/etc/foo"` == `Path("/etc/foo")` (Path
    # discards the left operand on an absolute right operand) and `..`
    # segments walk out of the events dir; both are neutralised here.
    if not isinstance(node_id, str) or not _NODE_ID_RE.match(node_id):
        logger.warning(f"Rejected alert/telemetry with invalid node_id: {node_id!r}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid node_id",
        )

    settings = get_settings()
    raw = getattr(settings, "ALLOWED_NODE_IDS", "") or ""
    allowed = {n.strip() for n in raw.split(",") if n.strip()}
    if not allowed:
        return  # allowlist disabled -> allow all
    if node_id not in allowed:
        logger.warning(f"Rejected alert/telemetry from unlisted node_id: {node_id!r}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Node not allowed",
        )


def verify_node_binding(request: Request, claimed_node_id: str) -> None:
    """Enforce that a per-node key may only act under its own node_id.

    request.state.edge_auth_node is the authenticated node (set by
    verify_api_key). If it is a specific node and differs from the claimed
    node_id, reject with 403. A None value (shared-key / grace period, or
    session auth) skips the check. Call AFTER verify_node_id."""
    bound = getattr(getattr(request, "state", None), "edge_auth_node", None)
    if bound is not None and bound != claimed_node_id:
        logger.warning(f"Node binding mismatch: key bound to {bound!r}, claimed {claimed_node_id!r}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Node key/id mismatch")


# ===== Session Authentication =====

# SEC-001: absolute session lifetime. ``login_at`` is stamped once at login and
# is the fixed anchor; a session older than this cap (or missing the anchor
# entirely) is treated as expired regardless of activity. /api/session/extend
# may slide the cookie within this cap but must never move ``login_at``.
SESSION_MAX_AGE_SECONDS = 86400  # 24 hours


def _session_expired(request: Request) -> bool:
    """True when the session has no ``login_at`` anchor or is older than the cap."""
    login_at = request.session.get("login_at")
    if not login_at:
        return True
    from datetime import datetime
    from .timeutil import utcnow
    try:
        age = (utcnow() - datetime.fromisoformat(login_at)).total_seconds()
    except (ValueError, TypeError):
        return True
    return age > SESSION_MAX_AGE_SECONDS


async def get_current_user(request: Request) -> str:
    """
    Get the current authenticated user from session.
    
    Used for protecting dashboard routes.
    
    Args:
        request: The FastAPI request object
        
    Returns:
        The authenticated username
        
    Raises:
        HTTPException: 302 redirect to login if not authenticated
    """
    user = request.session.get("user")
    
    if not user or _session_expired(request):
        # Not authenticated, or the SEC-001 absolute 24h cap is exceeded / the
        # session has no login_at anchor. Clear it so a stale cookie can't be
        # reused, then bounce: HTML -> /login redirect, API -> 401.
        request.session.clear()
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            raise HTTPException(
                status_code=status.HTTP_302_FOUND,
                headers={"Location": "/login"}
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated"
            )
    
    return user


def authenticate_user(username: str, password: str) -> bool:
    """
    Authenticate a user with username and password.
    
    Args:
        username: The username to verify
        password: The password to verify
        
    Returns:
        True if credentials are valid, False otherwise
    """
    settings = get_settings()
    
    # Evaluate both comparisons unconditionally (no short-circuit) so response
    # timing does not reveal which field was wrong.
    user_ok = _ct_equal(username, settings.DASHBOARD_USER)
    pass_ok = _ct_equal(password, settings.DASHBOARD_PASS)
    if user_ok and pass_ok:
        logger.info(f"User '{username}' authenticated successfully")
        return True
    
    logger.warning(f"Failed authentication attempt for user '{username}'")
    return False


async def verify_api_key_or_session(
    request: Request,
    api_key: Optional[str] = Depends(api_key_header)
) -> str:
    """
    Accept either API key or session authentication.
    Used for GET endpoints that both edge nodes and dashboard users need.

    A per-node key (Track 1: per-node edge API keys) is checked FIRST and,
    when it matches, binds the request to that node via
    ``request.state.edge_auth_node``. The shared EDGE_API_KEY remains a
    fallback for as long as it is configured; a shared-key match leaves
    ``request.state.edge_auth_node`` as None (unbound). Read paths do not
    enforce binding.
    """
    settings = get_settings()

    # Per-node key first: a hash-equality SQL lookup
    if api_key:
        from .database import get_edge_node_by_key
        rec = get_edge_node_by_key(api_key)
        if rec is not None:
            request.state.edge_auth_node = rec["node_id"]
            return api_key

    # Shared-key fallback (grace period while nodes are migrated to per-node keys)
    if api_key and _ct_equal(api_key, settings.EDGE_API_KEY):
        request.state.edge_auth_node = None
        return api_key

    # Try session auth
    user = request.session.get("user")
    if user:
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="API key or session authentication required"
    )


# ===== Webcam API Key Authentication =====

async def verify_webcam_api_key(request: Request) -> str:
    """Verify X-API-Key against webcam_clients table. Returns client node_id."""
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
        )
    from .database import get_webcam_client_by_key
    client = get_webcam_client_by_key(api_key)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webcam API key",
        )
    return client["node_id"]


__all__ = [
    "verify_api_key",
    "verify_node_id",
    "verify_node_binding",
    "verify_api_key_or_session",
    "verify_webcam_api_key",
    "get_current_user",
    "authenticate_user",
]