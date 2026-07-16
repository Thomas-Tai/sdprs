# -*- coding: utf-8 -*-
"""
Shared pytest test environment.

Sets required auth credentials to values that pass the strengthened
central_server.config.validate_settings (>= 32 chars, >= 8 unique chars,
no known-insecure placeholder pattern, no "changeme" substring).

pytest loads conftest.py before importing any test file, so per-file
`os.environ.setdefault()` calls in the existing test suite become no-ops
here — the strong values below are the ones that get picked up.

Post-2026-07-16 SECURITY hardening (see MIGRATION.md): validate_settings
now runs fail-closed at app startup. Tests that instantiate the FastAPI
app via TestClient trigger the lifespan and therefore this validator.
Without conftest.py the whole suite would fail on the pre-existing
`test-*` placeholder values.
"""
import os

os.environ.setdefault("DASHBOARD_USER", "admin")

# 16 chars, mixed classes — passes PASSWORD_MIN_LENGTH (8)
os.environ.setdefault("DASHBOARD_PASS", "PytestSuite2026!")

# 64 hex chars, 16 unique — passes SECRET_MIN_LENGTH (32) and
# SECRET_MIN_UNIQUE_CHARS (8). Synthetic value, never used in a real
# deployment.
os.environ.setdefault(
    "EDGE_API_KEY",
    "a1b2c3d4e5f67890abcdef0123456789abcdef0123456789abcdef012345678f",
)
os.environ.setdefault(
    "SECRET_KEY",
    "9f8e7d6c5b4a3928a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4",
)
