# sdprs/webcam_client/tests/test_tray_app.py
"""Tray icon creation must not crash on startup.

_create_icon used Image.new("RGB", size, "transparent"); "transparent" is not a
valid RGB color, so PIL raised

    ValueError: unknown color specifier: 'transparent'

which killed the app the moment the tray started (right after the wizard). A
transparent background needs RGBA mode with a (0,0,0,0) fill.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.gui.tray_app import _create_icon, TRAY_AVAILABLE


@pytest.mark.skipif(not TRAY_AVAILABLE, reason="PIL/pystray not installed")
def test_create_icon_does_not_raise_and_is_transparent_rgba():
    img = _create_icon("green")
    assert img.size == (64, 64)
    assert img.mode == "RGBA", "transparent background requires an alpha channel"
    assert img.getpixel((0, 0))[3] == 0, "corner must be transparent, not opaque"


@pytest.mark.skipif(not TRAY_AVAILABLE, reason="PIL/pystray not installed")
def test_create_icon_colors_track_status():
    assert _create_icon("green").getpixel((32, 32))[:3] == (0, 200, 0)
    assert _create_icon("red").getpixel((32, 32))[:3] == (220, 50, 50)


def test_pause_label_reflects_state():
    from webcam_client.gui.tray_app import _pause_label
    assert _pause_label(False) == "暫停推送"
    assert _pause_label(True) == "恢復推送"


def test_icon_color_paused_beats_connection():
    from webcam_client.gui.tray_app import _icon_color
    assert _icon_color(paused=False, connected=True) == "green"
    assert _icon_color(paused=False, connected=False) == "red"
    assert _icon_color(paused=True, connected=True) == "amber"
    assert _icon_color(paused=True, connected=False) == "amber"


@pytest.mark.skipif(not TRAY_AVAILABLE, reason="PIL/pystray not installed")
def test_create_icon_amber():
    assert _create_icon("amber").getpixel((32, 32))[:3] == (230, 160, 0)
