# sdprs/webcam_client/gui/wizard/__init__.py
"""Setup wizard, split by concern.

``flow``       — which of the three sections is unlocked (pure: no Tk, no I/O)
``connection`` — URL/identity helpers, the connection probe, and registration
``scanning``   — the blocking DSHOW probe and thumbnail prep, off the Tk thread
``window``     — the Tk rendering, and nothing else

The split is what makes the wizard testable: every rule worth pinning ended up
in ``flow`` or ``connection``, which need no display, and ``window`` is the thin
remainder that a bench pass covers.

``gui/setup_wizard.py`` was a transitional façade over this package while the
move was verified; it is gone, and callers import from here.
"""
from .connection import (
    normalize_server_url,
    probe_connection,
    register_cameras,
    register_cameras_async,
    test_connection_async,
    _client_identity_changed,
    _build_cameras_for_registration,
)
from .flow import MODE_EDIT, MODE_FIRST_RUN, WizardFlow
from .scanning import _prepare_thumbnail_async, _scan_cameras_async
from .window import run_setup_wizard, _camera_rows_from_config

# Underscored names are listed deliberately: the wizard's tests reach for these
# private helpers by name.
__all__ = [
    "run_setup_wizard",
    "WizardFlow",
    "MODE_FIRST_RUN",
    "MODE_EDIT",
    "normalize_server_url",
    "probe_connection",
    "register_cameras",
    "register_cameras_async",
    "test_connection_async",
    "_camera_rows_from_config",
    "_client_identity_changed",
    "_build_cameras_for_registration",
    "_scan_cameras_async",
    "_prepare_thumbnail_async",
]
