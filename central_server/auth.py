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

async def verify_api_key(api_key: Optional[str] = Depends(api_key_header)) -> str:
    """
    Verify API key from X-API-Key header.
    
    Used for authenticating edge node requests.
    
    Args:
        api_key: The API key from the header
        
    Returns:
        The validated API key
        
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
    
    if not _ct_equal(api_key, settings.EDGE_API_KEY):
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
    
    return api_key


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


# ===== Session Authentication =====

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
    
    if not user:
        # For HTML requests, redirect to login
        # For API requests, return 401
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
    """
    settings = get_settings()

    # Try API key first
    if api_key and _ct_equal(api_key, settings.EDGE_API_KEY):
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
    "verify_api_key_or_session",
    "verify_webcam_api_key",
    "get_current_user",
    "authenticate_user",
]