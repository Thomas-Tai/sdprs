# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Authentication Module
Smart Disaster Prevention Response System

This module provides authentication support for three scenarios:
A. API Key authentication (edge node to server)
B. Session authentication (dashboard users)
C. WebSocket authentication
"""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from starlette.websockets import WebSocket

from .config import get_settings

# Configure logging
logger = logging.getLogger("auth")

# API Key header scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


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
    
    if api_key != settings.EDGE_API_KEY:
        logger.warning(f"Invalid API key attempt: {api_key[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    
    return api_key


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


async def get_current_user_optional(request: Request) -> Optional[str]:
    """
    Get the current user if authenticated, otherwise return None.
    
    Useful for routes that work differently for authenticated vs anonymous users.
    
    Args:
        request: The FastAPI request object
        
    Returns:
        The username if authenticated, None otherwise
    """
    return request.session.get("user")


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
    
    if username == settings.DASHBOARD_USER and password == settings.DASHBOARD_PASS:
        logger.info(f"User '{username}' authenticated successfully")
        return True
    
    logger.warning(f"Failed authentication attempt for user '{username}'")
    return False


# ===== WebSocket Authentication =====

async def verify_ws_session(websocket: WebSocket) -> Optional[str]:
    """
    Verify WebSocket session authentication.
    
    Extracts session from WebSocket and validates the user.
    
    Args:
        websocket: The WebSocket connection
        
    Returns:
        The username if authenticated, None otherwise
    """
    # Access session from WebSocket
    # Note: SessionMiddleware must be enabled for this to work
    session = websocket.session if hasattr(websocket, "session") else {}
    user = session.get("user")
    
    if not user:
        logger.warning("WebSocket connection rejected - not authenticated")
        await websocket.close(code=1008, reason="Not authenticated")
        return None
    
    return user


# ===== Path-based Authentication Helper =====

def is_public_path(path: str) -> bool:
    """
    Check if a path should be publicly accessible.
    
    Args:
        path: The request path
        
    Returns:
        True if the path is public, False if it requires authentication
    """
    public_paths = [
        "/login",
        "/api/health",
    ]
    
    # Static files are public
    if path.startswith("/static/"):
        return True
    
    # Snapshot latest endpoint is public (for dashboard img tags)
    if "/snapshot/latest" in path:
        return True
    
    # Check exact matches
    if path in public_paths:
        return True
    
    return False


def is_api_key_path(path: str) -> bool:
    """
    Check if a path requires API key authentication.
    
    Args:
        path: The request path
        
    Returns:
        True if the path requires API key, False otherwise
    """
    api_key_paths = [
        "/api/alerts",
        "/api/edge/",
    ]
    
    # POST /api/alerts requires API key
    # PUT /api/alerts/{id}/video requires API key
    # POST /api/edge/{node_id}/snapshot requires API key
    
    for api_path in api_key_paths:
        if path.startswith(api_path):
            # But not the GET snapshot/latest endpoint
            if "/snapshot/latest" in path:
                return False
            return True
    
    return False


# ===== Combined Authentication Middleware Helper =====

async def get_auth_context(request: Request) -> dict:
    """
    Get authentication context for a request.
    
    Determines the authentication type and returns relevant info.
    
    Args:
        request: The FastAPI request object
        
    Returns:
        Dict with authentication context:
        - type: "api_key" | "session" | "anonymous"
        - user: username (for session auth) or None
        - api_key: validated API key or None
    """
    # Check for API key first
    api_key = request.headers.get("X-API-Key")
    if api_key:
        settings = get_settings()
        if api_key == settings.EDGE_API_KEY:
            return {
                "type": "api_key",
                "user": None,
                "api_key": api_key
            }
    
    # Check for session
    user = request.session.get("user")
    if user:
        return {
            "type": "session",
            "user": user,
            "api_key": None
        }
    
    # Anonymous
    return {
        "type": "anonymous",
        "user": None,
        "api_key": None
    }



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
    if api_key and api_key == settings.EDGE_API_KEY:
        return api_key

    # Try session auth
    user = request.session.get("user")
    if user:
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="API key or session authentication required"
    )


__all__ = [
    "verify_api_key",
    "verify_api_key_or_session",
    "get_current_user",
    "get_current_user_optional",
    "authenticate_user",
    "verify_ws_session",
    "is_public_path",
    "is_api_key_path",
    "get_auth_context",
]