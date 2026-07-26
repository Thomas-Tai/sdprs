# webcam_client/tests/test_status_window.py
"""The window's TEXT is pure and testable; only the Tk rendering needs a display,
so the logic worth protecting lives in build_status_lines().

NOTHING here may construct a real tk.Tk(): this suite has to pass on a build
machine with no display, and a test run must never pop a window at someone.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.gui.status_window import (
    build_status_lines, open_log_folder, _banner_color, _HEX)
from webcam_client.gui.tray_app import _health_color
from webcam_client.status import Health


def test_running_line_reports_camera_count():
    title, detail, action = build_status_lines(Health.RUNNING, 2, [])
    assert "監控中" in title
    assert "2" in detail
    assert action == ""


def test_camera_down_names_the_failing_cameras():
    title, detail, action = build_status_lines(
        Health.CAMERA_DOWN, 2, ["前門攝影機"])
    assert "前門攝影機" in detail
    assert "USB" in action, "the guard needs the physical action"


def test_bad_key_tells_the_guard_to_call_the_admin():
    _, _, action = build_status_lines(Health.BAD_KEY, 2, [])
    assert "管理員" in action


def test_no_status_codes_anywhere():
    for state in Health:
        joined = " ".join(build_status_lines(state, 2, ["前門攝影機"]))
        assert "401" not in joined and "403" not in joined and "500" not in joined


def test_camera_down_with_no_resolvable_names_still_reads_as_a_sentence():
    """main.py drops fault sources it cannot map to a camera the operator knows
    (see _camera_display_names), so an empty list is a REAL call, not a
    theoretical one. Joining [] gives "", which would override strings.py's
    generic fallback with nothing and render ' 目前沒有畫面。' -- a sentence
    starting mid-air. The `or None` in build_status_lines keeps the fallback."""
    _, detail, action = build_status_lines(Health.CAMERA_DOWN, 2, [])
    assert not detail.startswith(" "), f"blank subject leaked through: {detail!r}"
    assert "攝影機" in detail
    assert action.strip(), "a fault with no action is unactionable"


def test_zero_cameras_never_renders_the_self_contradiction():
    """strings.py carries a whole fallback (_RUNNING_DETAIL_UNKNOWN_COUNT) to
    stop "0 支攝影機運作正常。" ever being shown -- zero cameras working while
    claiming everything is fine. But describe() only reaches that fallback when
    camera_count IS None, so a literal 0 sails straight past it. The window is
    handed len(enabled cameras), which is 0 whenever the live config has none
    left, so this is a real call and not a theoretical one."""
    _, detail, _ = build_status_lines(Health.RUNNING, 0, [])
    assert "0" not in detail, f"self-contradictory count leaked: {detail!r}"
    assert "運作正常" in detail


def test_every_state_has_its_own_banner_colour_rather_than_a_fallback():
    """Both lookups in the chain default silently: tray_app._health_color()
    falls back to "grey" for an unknown state, and _banner_color()'s .get()
    falls back to grey again. So asserting only that the result is a colour
    passes even when the tray and the window DISAGREE -- add
    Health.X: "blue" to tray_app._HEALTH_COLORS and the tray paints green (its
    own palette fallback) while the banner paints grey, which is the exact
    disagreement importing _health_color was meant to make impossible. Pin the
    mapping itself: every colour the tray can name must exist in _HEX."""
    for state in Health:
        color = _health_color(state)
        assert color in _HEX, \
            f"{state} paints {color!r} on the tray, which the banner cannot render"
        assert _banner_color(state).startswith("#")


def test_banner_agrees_with_the_tray_light():
    """The window and the tray icon must never disagree about severity: a red
    tray light with a green banner is worse than no window at all."""
    assert _banner_color(Health.RUNNING) != _banner_color(Health.BAD_KEY)
    assert _banner_color(Health.NO_SERVER) == _banner_color(Health.BAD_KEY)
    assert _banner_color(Health.STARTING) != _banner_color(Health.RUNNING)


def test_open_log_folder_opens_the_log_directory(monkeypatch, tmp_path):
    import webcam_client.gui.status_window as sw
    opened = []
    monkeypatch.setattr(sw, "get_log_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(sw.os, "startfile", lambda p: opened.append(p),
                        raising=False)
    assert open_log_folder() is True
    assert opened and str(tmp_path / "logs") in str(opened[0])


def test_open_log_folder_returns_false_when_the_shell_refuses(monkeypatch, tmp_path):
    """os.startfile does not exist off Windows and raises when the shell has no
    handler. This is a technician convenience bolted to a guard-facing button;
    it must degrade to False + a log line, never take the window down."""
    import webcam_client.gui.status_window as sw

    def boom(path):
        raise OSError("no application is associated with this file")

    monkeypatch.setattr(sw, "get_log_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(sw.os, "startfile", boom, raising=False)
    assert open_log_folder() is False
