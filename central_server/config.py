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
        - MQTT_USERNAME: MQTT broker username (EMQX auth)
        - MQTT_PASSWORD: MQTT broker password (EMQX auth)
        - MQTT_USE_TLS: Enable TLS for MQTT connection (cloud deployment)
        - DATABASE_URL: PostgreSQL connection string (empty = use SQLite)
        - DB_PATH: SQLite database path (used when DATABASE_URL is empty)
        - RETENTION_DAYS: Number of days to retain MP4 files
        - STORAGE_PATH: MP4 storage root directory
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


def validate_settings(settings: Settings) -> bool:
    """
    Validate that all required settings are properly configured.
    """
    required_fields = ["DASHBOARD_USER", "DASHBOARD_PASS", "EDGE_API_KEY", "SECRET_KEY"]

    for field in required_fields:
        value = getattr(settings, field, None)
        if not value:
            raise ValueError(f"Required setting {field} is not configured")
        if value in [
            "changeme", "your-secret-key", "test-key",
            "dev-secret-key-change-in-production",
            "your-edge-api-key-here",
            "your-secret-key-change-in-production",
        ]:
            logger.warning(f"Setting {field} appears to have a placeholder value")

    if settings.MQTT_PORT < 1 or settings.MQTT_PORT > 65535:
        raise ValueError(f"Invalid MQTT_PORT: {settings.MQTT_PORT}")

    if settings.RETENTION_DAYS < 1:
        raise ValueError(f"Invalid RETENTION_DAYS: {settings.RETENTION_DAYS}")

    if settings.SERVER_PORT < 1 or settings.SERVER_PORT > 65535:
        raise ValueError(f"Invalid SERVER_PORT: {settings.SERVER_PORT}")

    logger.info("Settings validation passed")
    return True


__all__ = ["Settings", "get_settings", "validate_settings"]
