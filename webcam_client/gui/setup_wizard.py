# sdprs/webcam_client/gui/setup_wizard.py
"""Transitional façade over ``gui/wizard/``.

The wizard's code now lives in ``gui/wizard/`` (``connection`` / ``scanning`` /
``window``). This module exists only so the existing callers and tests keep
working unchanged while the move is verified; it is deleted once the new
modules carry their own tests.

``import httpx`` below is load-bearing, NOT a leftover: seven tests patch
``webcam_client.gui.setup_wizard.httpx.post``. That resolves this attribute to
the shared ``httpx`` module object and patches ``post`` on it, so
``connection.register_cameras`` sees the patch. Remove the import and all seven
fail with ``AttributeError``.
"""
import httpx  # noqa: F401  -- see the module docstring; patched by tests

from .wizard import *  # noqa: F401,F403
from .wizard import (  # noqa: F401  -- explicit, so the surface is greppable
    run_setup_wizard,
    normalize_server_url,
    register_cameras,
    _camera_rows_from_config,
    _client_identity_changed,
    _build_cameras_for_registration,
    _scan_cameras_async,
    _load_thumbnail_async,
)
