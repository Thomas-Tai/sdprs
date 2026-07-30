# sdprs/webcam_client/gui/wizard/__init__.py
"""Setup wizard, split by concern.

``connection`` — URL/identity helpers + the registration POST (no Tk, no threads)
``scanning``   — the blocking DSHOW probe and thumbnail grab, off the Tk thread
``window``     — the Tk rendering

This package is the real home; ``gui/setup_wizard.py`` is a transitional façade
that re-exports everything below and will be deleted once the new modules carry
their own tests.
"""
from .connection import (
    normalize_server_url,
    register_cameras,
    _client_identity_changed,
    _build_cameras_for_registration,
)
from .scanning import _scan_cameras_async, _load_thumbnail_async
from .window import run_setup_wizard, _camera_rows_from_config

# Underscored names are listed deliberately: the façade re-exports this package
# with ``import *``, and the wizard's callers/tests reach for these private
# helpers by name. Dropping them here silently breaks ``setup_wizard.*``.
__all__ = [
    "run_setup_wizard",
    "normalize_server_url",
    "register_cameras",
    "_camera_rows_from_config",
    "_client_identity_changed",
    "_build_cameras_for_registration",
    "_scan_cameras_async",
    "_load_thumbnail_async",
]
