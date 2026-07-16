# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Configuration Module
Smart Disaster Prevention Response System

This module provides configuration management using environment variables
with Pydantic BaseSettings for validation and type conversion.
"""

import logging
import os
from functools import lru_cache
from typing import Optional

# Configure logging
logger = logging.getLogger("config")

# Try to use pydantic-settings, fall back to dataclass if unavailable
try:
    from pydantic_settings import BaseSettings
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    logger.warning("pydantic-settings not available, using dataclass fallback")

# Try to load .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    logger.debug("Loaded .env file using python-dotenv")
except ImportError:
    logger.debug("python-dotenv not available, using system environment only")


if PYDANTIC_AVAILABLE:
    class Settings(BaseSettings):
        """
        Central server configuration from environment variables.

        Required variables (will raise error if not set):
        - DASHBOARD_USER: Dashboard login username
        - DASHBOARD_PASS: Dashboard login password
        - EDGE_API_KEY: Shared API key for edge nodes
        - SECRET_KEY: Secret key for session cookie signing

        Optional variables (have defaults):
        - MQTT_BROKER: MQTT broker address
        - MQTT_PORT: MQTT broker port
        - MQTT_USERNAME: MQTT broker username (Mosquitto auth on Zeabur cloud deploy)
        - MQTT_PASSWORD: MQTT broker password (Mosquitto auth on Zeabur cloud deploy)
        - MQTT_USE_TLS: Enable TLS for MQTT connection (cloud deployment)
        - DATABASE_URL: PostgreSQL connection string (empty = use SQLite)
        - DB_PATH: SQLite database path (used when DATABASE_URL is empty)
        - RETENTION_DAYS: Number of days to retain MP4 files
        - STORAGE_PATH: MP4 storage root directory
        - COOKIE_SECURE: Add Secure attribute to session cookie (True behind HTTPS)
        - ALLOWED_NODE_IDS: Comma-separated edge node_id allowlist (empty = allow all)
        - LOGIN_MAX_ATTEMPTS: Failed logins per IP before lockout
        - LOGIN_LOCKOUT_SECONDS: Lockout duration after too many failed logins
        """

        # Required settings
        DASHBOARD_USER: str
        DASHBOARD_PASS: str
        EDGE_API_KEY: str
        SECRET_KEY: str

        # MQTT settings
        MQTT_BROKER: str = "localhost"
        MQTT_PORT: int = 1883
        MQTT_USERNAME: str = ""
        MQTT_PASSWORD: str = ""
        MQTT_USE_TLS: bool = False

        # Database settings
        # If DATABASE_URL is set, PostgreSQL is used; otherwise SQLite via DB_PATH
        DATABASE_URL: str = ""
        DB_PATH: str = "./data/sdprs.db"

        # Storage & retention
        RETENTION_DAYS: int = 30
        STORAGE_PATH: str = "./storage"

        # Server binding
        SERVER_HOST: str = "0.0.0.0"
        SERVER_PORT: int = 8000

        # Auth hardening (T2 trust-boundary slice)
        # COOKIE_SECURE: set True in production behind HTTPS so the session
        #   cookie carries the Secure attribute. Default False for HTTP LAN.
        # ALLOWED_NODE_IDS: comma-separated allowlist of edge node_ids permitted
        #   to POST alerts/snapshots. Empty = disabled (accept any node_id).
        # LOGIN_MAX_ATTEMPTS / LOGIN_LOCKOUT_SECONDS: per-IP login throttle.
        COOKIE_SECURE: bool = False
        ALLOWED_NODE_IDS: str = ""
        LOGIN_MAX_ATTEMPTS: int = 5
        LOGIN_LOCKOUT_SECONDS: int = 300

        # Weather integration (CWA Open Data) — see Plan/weather_integration.md
        # Empty CWA_API_KEY disables the entire weather feature (gate).
        CWA_API_KEY: str = ""
        CWA_STATION_ID: str = "C0Z100"
        CWA_TOWNSHIP: str = "新北市新店區"
        SITE_LAT: float = 24.967
        SITE_LON: float = 121.541
        WEATHER_REFRESH_SECONDS: int = 600
        WEATHER_CACHE_STALE_SECONDS: int = 3600

        # mediamtx Prometheus scrape (item 14) — empty = stream-health UI hidden
        MEDIAMTX_METRICS_URL: str = "http://localhost:9998/metrics"

        class Config:
            env_file = ".env"
            env_file_encoding = "utf-8"
            case_sensitive = True

else:
    from dataclasses import dataclass

    def _get_env_str(key: str, default: Optional[str] = None, required: bool = False) -> str:
        value = os.environ.get(key, default)
        if required and value is None:
            raise ValueError(f"Required environment variable {key} is not set")
        return value

    def _get_env_int(key: str, default: int) -> int:
        value = os.environ.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            logger.warning(f"Invalid integer value for {key}, using default {default}")
            return default

    def _get_env_bool(key: str, default: bool) -> bool:
        value = os.environ.get(key)
        if value is None:
            return default
        return value.lower() in ("true", "1", "yes")

    def _get_env_float(key: str, default: float) -> float:
        value = os.environ.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            logger.warning(f"Invalid float value for {key}, using default {default}")
            return default

    @dataclass
    class Settings:
        DASHBOARD_USER: str
        DASHBOARD_PASS: str
        EDGE_API_KEY: str
        SECRET_KEY: str
        MQTT_BROKER: str
        MQTT_PORT: int
        MQTT_USERNAME: str
        MQTT_PASSWORD: str
        MQTT_USE_TLS: bool
        DATABASE_URL: str
        DB_PATH: str
        RETENTION_DAYS: int
        STORAGE_PATH: str
        SERVER_HOST: str
        SERVER_PORT: int
        COOKIE_SECURE: bool
        ALLOWED_NODE_IDS: str
        LOGIN_MAX_ATTEMPTS: int
        LOGIN_LOCKOUT_SECONDS: int
        CWA_API_KEY: str
        CWA_STATION_ID: str
        CWA_TOWNSHIP: str
        SITE_LAT: float
        SITE_LON: float
        WEATHER_REFRESH_SECONDS: int
        WEATHER_CACHE_STALE_SECONDS: int
        MEDIAMTX_METRICS_URL: str

        def __init__(self):
            self.DASHBOARD_USER = _get_env_str("DASHBOARD_USER", required=True)
            self.DASHBOARD_PASS = _get_env_str("DASHBOARD_PASS", required=True)
            self.EDGE_API_KEY = _get_env_str("EDGE_API_KEY", required=True)
            self.SECRET_KEY = _get_env_str("SECRET_KEY", required=True)
            self.MQTT_BROKER = _get_env_str("MQTT_BROKER", "localhost")
            self.MQTT_PORT = _get_env_int("MQTT_PORT", 1883)
            self.MQTT_USERNAME = _get_env_str("MQTT_USERNAME", "")
            self.MQTT_PASSWORD = _get_env_str("MQTT_PASSWORD", "")
            self.MQTT_USE_TLS = _get_env_bool("MQTT_USE_TLS", False)
            self.DATABASE_URL = _get_env_str("DATABASE_URL", "")
            self.DB_PATH = _get_env_str("DB_PATH", "./data/sdprs.db")
            self.RETENTION_DAYS = _get_env_int("RETENTION_DAYS", 30)
            self.STORAGE_PATH = _get_env_str("STORAGE_PATH", "./storage")
            self.SERVER_HOST = _get_env_str("SERVER_HOST", "0.0.0.0")
            self.SERVER_PORT = _get_env_int("SERVER_PORT", 8000)
            self.COOKIE_SECURE = _get_env_bool("COOKIE_SECURE", False)
            self.ALLOWED_NODE_IDS = _get_env_str("ALLOWED_NODE_IDS", "")
            self.LOGIN_MAX_ATTEMPTS = _get_env_int("LOGIN_MAX_ATTEMPTS", 5)
            self.LOGIN_LOCKOUT_SECONDS = _get_env_int("LOGIN_LOCKOUT_SECONDS", 300)
            self.CWA_API_KEY = _get_env_str("CWA_API_KEY", "")
            self.CWA_STATION_ID = _get_env_str("CWA_STATION_ID", "C0Z100")
            self.CWA_TOWNSHIP = _get_env_str("CWA_TOWNSHIP", "新北市新店區")
            self.SITE_LAT = _get_env_float("SITE_LAT", 24.967)
            self.SITE_LON = _get_env_float("SITE_LON", 121.541)
            self.WEATHER_REFRESH_SECONDS = _get_env_int("WEATHER_REFRESH_SECONDS", 600)
            self.WEATHER_CACHE_STALE_SECONDS = _get_env_int("WEATHER_CACHE_STALE_SECONDS", 3600)
            self.MEDIAMTX_METRICS_URL = _get_env_str("MEDIAMTX_METRICS_URL", "http://localhost:9998/metrics")


@lru_cache()
def get_settings() -> Settings:
    """
    Get the settings singleton instance.

    Uses lru_cache to ensure Settings is only created once.
    """
    settings = Settings()
    db_mode = "PostgreSQL" if settings.DATABASE_URL else "SQLite"
    logger.info(
        f"Configuration loaded - MQTT_BROKER={settings.MQTT_BROKER}, "
        f"DB_MODE={db_mode}, MQTT_USE_TLS={settings.MQTT_USE_TLS}"
    )
    return settings


# Known-insecure placeholder credential values. Prior versions of
# scripts/setup_server.sh wrote the first three by default and did not
# enforce rotation — startup with any of these permits auth bypass.
KNOWN_INSECURE_VALUES = frozenset({
    "changeme-strong-password",     # setup_server.sh legacy default
    "changeme-session-secret",      # setup_server.sh legacy default
    "changeme-random-secret-key",   # setup_server.sh legacy default
    "changeme",
    "your-secret-key",
    "your-secret-key-change-in-production",
    "your-edge-api-key-here",
    "dev-secret-key-change-in-production",
    "test-key",
})

SECRET_MIN_LENGTH = 32          # openssl rand -hex 32 → 64 chars
SECRET_MIN_UNIQUE_CHARS = 8     # catches "aaaaaa...", "abababab..."
PASSWORD_MIN_LENGTH = 8


def validate_settings(settings: Settings) -> bool:
    """
    Validate that all required settings are properly configured.

    Raises ValueError on any of:
      * Missing / empty required field
      * Value in KNOWN_INSECURE_VALUES
      * Value containing "changeme" substring (case-insensitive)
      * SECRET_KEY / EDGE_API_KEY shorter than SECRET_MIN_LENGTH
      * SECRET_KEY / EDGE_API_KEY with fewer than SECRET_MIN_UNIQUE_CHARS
      * DASHBOARD_PASS shorter than PASSWORD_MIN_LENGTH
      * Out-of-range MQTT_PORT / RETENTION_DAYS / SERVER_PORT

    Called from main.py lifespan startup — failing closed here prevents
    the app from serving requests with insecure credentials.
    """
    required_fields = ["DASHBOARD_USER", "DASHBOARD_PASS", "EDGE_API_KEY", "SECRET_KEY"]

    for field in required_fields:
        value = getattr(settings, field, None)
        if not value:
            raise ValueError(f"Required setting {field} is not configured")
        if value in KNOWN_INSECURE_VALUES:
            raise ValueError(
                f"{field} is set to a known-insecure placeholder value. "
                f"Generate a real value (e.g. `openssl rand -hex 32`) "
                f"and update your .env or deployment env vars. "
                f"See MIGRATION.md for rotation guidance."
            )
        if "changeme" in value.lower():
            raise ValueError(
                f"{field} contains the placeholder text 'changeme'. "
                f"Rotate to a random value before starting the server."
            )

    for field in ("SECRET_KEY", "EDGE_API_KEY"):
        value = getattr(settings, field)
        if len(value) < SECRET_MIN_LENGTH:
            raise ValueError(
                f"{field} too short ({len(value)} chars, "
                f"need >= {SECRET_MIN_LENGTH}). "
                f"Generate with `openssl rand -hex 32`."
            )
        if len(set(value)) < SECRET_MIN_UNIQUE_CHARS:
            raise ValueError(
                f"{field} has insufficient entropy "
                f"(only {len(set(value))} unique chars, "
                f"need >= {SECRET_MIN_UNIQUE_CHARS}). "
                f"Generate with `openssl rand -hex 32`."
            )

    if len(settings.DASHBOARD_PASS) < PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"DASHBOARD_PASS too short "
            f"({len(settings.DASHBOARD_PASS)} chars, "
            f"minimum {PASSWORD_MIN_LENGTH})."
        )

    if settings.MQTT_PORT < 1 or settings.MQTT_PORT > 65535:
        raise ValueError(f"Invalid MQTT_PORT: {settings.MQTT_PORT}")

    if settings.RETENTION_DAYS < 1:
        raise ValueError(f"Invalid RETENTION_DAYS: {settings.RETENTION_DAYS}")

    if settings.SERVER_PORT < 1 or settings.SERVER_PORT > 65535:
        raise ValueError(f"Invalid SERVER_PORT: {settings.SERVER_PORT}")

    logger.info("Settings validation passed")
    return True


__all__ = [
    "Settings", "get_settings", "validate_settings",
    "KNOWN_INSECURE_VALUES",
    "SECRET_MIN_LENGTH", "SECRET_MIN_UNIQUE_CHARS", "PASSWORD_MIN_LENGTH",
]
