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
    """Asserted against the constants, not literals. Pinning the literal here is
    what let 推送/上傳 drift between this label and the instruction that names
    it -- the test went green on the old spelling either way."""
    from webcam_client.gui.tray_app import _pause_label
    from webcam_client import strings
    assert _pause_label(False) == strings.MENU_PAUSE
    assert _pause_label(True) == strings.MENU_RESUME


@pytest.mark.skipif(not TRAY_AVAILABLE, reason="PIL/pystray not installed")
def test_the_tray_menu_labels_all_come_from_strings(monkeypatch):
    """F-2: the tray is the app's ONLY permanent surface, and its labels lived
    here rather than in strings.py, whose docstring claims to hold every
    operator-facing string. It showed: the menu said 「開啟設定」 while the status
    window's button said 「設定」 and bad_key's action told the guard to press
    「設定」 -- three spellings for one control, two of them wrong.

    Pin the whole visible label set, in order, against the constants. A label
    retyped in this file then fails here instead of drifting silently."""
    import types
    from webcam_client.gui import tray_app as ta
    from webcam_client import strings

    labels = []

    class FakeMenuItem:
        def __init__(self, text, action=None, default=False):
            # The pause item's label is a callable of the pystray item.
            labels.append(text(None) if callable(text) else text)

    class FakeMenu:
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = items

    class FakeIcon:
        def __init__(self, *a, **kw):
            pass

        def run(self):
            pass

    monkeypatch.setattr(ta, "pystray", types.SimpleNamespace(
        Menu=FakeMenu, MenuItem=FakeMenuItem, Icon=FakeIcon))
    # start() hands icon.run to a daemon thread; keep it off the test runner.
    monkeypatch.setattr(ta.threading, "Thread",
                        lambda *a, **kw: types.SimpleNamespace(start=lambda: None))

    app = ta.TrayApp(on_open_settings=lambda: None, on_quit=lambda: None,
                     on_pause=lambda: None, on_resume=lambda: None)
    app.start()

    assert labels == [strings.MENU_STATUS, strings.BTN_SETTINGS,
                      strings.MENU_PAUSE, strings.MENU_QUIT], (
        f"tray labels must be the shared constants, in menu order; got {labels}")


@pytest.mark.skipif(not TRAY_AVAILABLE, reason="PIL/pystray not installed")
def test_create_icon_amber():
    assert _create_icon("amber").getpixel((32, 32))[:3] == (230, 160, 0)


def test_health_colors_cover_every_state():
    from webcam_client.gui.tray_app import _health_color
    from webcam_client.status import Health
    for state in Health:
        assert _health_color(state) in {"green", "red", "amber", "grey"}


def test_every_health_state_has_an_explicit_colour_entry():
    """Ledger rows 7+8 / m-5: _health_color() ends in .get(state, "grey"), and
    grey is the STARTING colour. A Health member added with no _HEALTH_COLORS
    entry therefore paints the tray "still starting up" forever -- the exact
    silent lie this whole branch exists to remove -- while every existing
    assertion stays green, because they only check that the result is SOME
    valid colour and grey always is one.

    Pin the table itself, in both directions: no state without a colour, and no
    colour entry for a state that no longer exists."""
    from webcam_client.gui.tray_app import _HEALTH_COLORS
    from webcam_client.status import Health

    assert set(_HEALTH_COLORS) == set(Health), (
        "every Health state needs an explicit colour; the .get() fallback "
        "would silently paint a new state grey (= 啟動中) forever")


def test_faults_are_red_and_paused_is_amber():
    from webcam_client.gui.tray_app import _health_color
    from webcam_client.status import Health
    assert _health_color(Health.RUNNING) == "green"
    assert _health_color(Health.PAUSED) == "amber"
    for bad in (Health.NO_SERVER, Health.BAD_KEY, Health.CAMERA_DOWN):
        assert _health_color(bad) == "red", f"{bad} must be red"


def test_starting_is_grey():
    """Green before anything has reported is the original lie.

    Asserted positively (== "grey") rather than as != "green": the negative
    form is also satisfied by the .get() fallback, so it could not tell a
    deliberate grey from a missing table entry."""
    from webcam_client.gui.tray_app import _health_color
    from webcam_client.status import Health
    assert _health_color(Health.STARTING) == "grey"


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


def test_the_tooltip_carries_the_fix_not_just_the_diagnosis():
    """The tooltip is the only fault surface that is ALWAYS present -- the toast
    is transient and Focus Assist can suppress it outright, and the status
    window has to be opened. It discarded describe()'s action, so a guard who
    hovered a red icon read 攝影機沒有畫面 then 攝影機目前沒有畫面: the same
    sentence twice, and never the cable-and-重新連線 fix one field away."""
    from webcam_client.gui.tray_app import _tooltip, _TOOLTIP_MAX
    from webcam_client.status import Health
    from webcam_client import strings

    for state in (Health.NO_SERVER, Health.BAD_KEY, Health.CAMERA_DOWN):
        _, _, action = strings.describe(state)
        text = _tooltip(state)
        assert action and action in text, \
            f"{state} tooltip drops the only actionable line: {text!r}"
        # szTip is WCHAR * 128 and ctypes RAISES on overflow rather than
        # truncating, so an over-long tooltip would kill the tray icon outright.
        assert len(text) <= _TOOLTIP_MAX, \
            f"{state} tooltip is {len(text)} chars, over the Win32 szTip limit"


def test_a_healthy_tooltip_has_no_dangling_action_line():
    """Healthy states carry an empty action, so the join must not leave a bare
    trailing newline the way the toast's separator logic once did."""
    from webcam_client.gui.tray_app import _tooltip
    from webcam_client.status import Health
    text = _tooltip(Health.RUNNING)
    assert text == text.strip()
    assert not text.endswith("\n")
