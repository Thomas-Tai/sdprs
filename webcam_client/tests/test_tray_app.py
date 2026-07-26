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


@pytest.mark.skipif(not TRAY_AVAILABLE, reason="PIL/pystray not installed")
def test_create_icon_amber():
    assert _create_icon("amber").getpixel((32, 32))[:3] == (230, 160, 0)


def test_health_colors_cover_every_state():
    from webcam_client.gui.tray_app import _health_color
    from webcam_client.status import Health
    for state in Health:
        assert _health_color(state) in {"green", "red", "amber", "grey"}


def test_faults_are_red_and_paused_is_amber():
    from webcam_client.gui.tray_app import _health_color
    from webcam_client.status import Health
    assert _health_color(Health.RUNNING) == "green"
    assert _health_color(Health.PAUSED) == "amber"
    for bad in (Health.NO_SERVER, Health.BAD_KEY, Health.CAMERA_DOWN):
        assert _health_color(bad) == "red", f"{bad} must be red"


def test_starting_is_not_green():
    """Green before anything has reported is the original lie."""
    from webcam_client.gui.tray_app import _health_color
    from webcam_client.status import Health
    assert _health_color(Health.STARTING) != "green"


def test_set_status_is_gone():
    """The always-true connected flag was the U6 defect; it must not survive."""
    from webcam_client.gui.tray_app import TrayApp
    assert not hasattr(TrayApp, "set_status")


def test_tooltip_names_the_state_in_plain_language():
    from webcam_client.gui.tray_app import _tooltip
    from webcam_client.status import Health
    text = _tooltip(Health.BAD_KEY)
    assert "連線密碼" in text
    assert "401" not in text and "403" not in text
