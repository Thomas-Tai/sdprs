# sdprs/webcam_client/gui/tray_app.py
import logging
import threading
from typing import Callable, Optional

from ..status import Health
from ..strings import describe, TRAY_TOOLTIP_PREFIX, MENU_STATUS

logger = logging.getLogger("webcam_client.gui.tray")

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


def _create_icon(color: str = "green") -> "Image.Image":
    # Transparent background needs RGBA + (0,0,0,0); "transparent" is not a valid
    # PIL color and raises ValueError (that crashed startup once).
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    palette = {"green": (0, 200, 0, 255), "red": (220, 50, 50, 255),
               "amber": (230, 160, 0, 255), "grey": (140, 140, 140, 255)}
    c = palette.get(color, palette["green"])
    draw.ellipse([8, 8, 56, 56], fill=c)
    return img


def _pause_label(paused: bool) -> str:
    return "恢復上傳" if paused else "暫停上傳"


# Worst-first colour, matching the meaning the guard needs, not the raw enum
# order: PAUSED is a deliberate operator action (amber, not a fault); the three
# fault states are all red because every one of them means "not uploading and
# the guard cannot fix it alone"; STARTING is grey so the very first frame the
# guard ever sees is never the "everything is fine" green lie.
_HEALTH_COLORS = {
    Health.STARTING: "grey",
    Health.RUNNING: "green",
    Health.PAUSED: "amber",       # deliberate operator action, not a fault
    Health.NO_SERVER: "red",
    Health.BAD_KEY: "red",
    Health.CAMERA_DOWN: "red",
}


def _health_color(state) -> str:
    return _HEALTH_COLORS.get(state, "grey")


def _tooltip(state) -> str:
    title, detail, _ = describe(state)
    return f"{TRAY_TOOLTIP_PREFIX}\n{title}\n{detail}"


class TrayApp:
    def __init__(self, on_open_settings: Callable, on_quit: Callable,
                 on_pause: Callable, on_resume: Callable,
                 on_open_status: Optional[Callable] = None):
        self._on_open_settings = on_open_settings
        self._on_quit = on_quit
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._on_open_status = on_open_status or (lambda: None)
        self._icon: Optional["pystray.Icon"] = None
        self._paused = False
        self._state: Health = Health.STARTING

    @property
    def icon(self):
        """The underlying pystray icon, or None before start() has run.

        The toast notifier sends through this object (pystray._win32.Icon has
        HAS_NOTIFICATION = True), so it needs a way to reach it. Read-only on
        purpose: the icon's lifecycle belongs to start()/stop(). notify_state()
        already treats None as "no toast channel yet", which is the honest
        answer during the startup window before the tray exists.
        """
        return self._icon

    def _refresh_icon(self) -> None:
        if self._icon and TRAY_AVAILABLE:
            self._icon.icon = _create_icon(_health_color(self._state))
            self._icon.title = _tooltip(self._state)

    def set_health(self, state) -> None:
        """Called from the main loop only -- see status.py's threading note.
        Stores the new state, repaints the icon and updates the tooltip."""
        self._state = state
        self._refresh_icon()

    def start(self) -> None:
        if not TRAY_AVAILABLE:
            logger.warning("pystray not available, running without tray icon")
            return
        menu = pystray.Menu(
            # default=True makes this the double-click action (pystray 0.19.5).
            pystray.MenuItem(MENU_STATUS, lambda: self._on_open_status(), default=True),
            # TODO: drift -- status_window's equivalent button is strings.BTN_SETTINGS
            # ("設定"), not "開啟設定". Both labels belong in strings.py (its docstring
            # claims to hold "every operator-facing string, in one place"), but this
            # file hardcodes its own; left as-is to avoid colliding with the in-flight
            # strings.py edit -- ledger separately.
            pystray.MenuItem("開啟設定", lambda: self._on_open_settings()),
            pystray.MenuItem(lambda item: _pause_label(self._paused),
                             lambda: self._toggle_pause()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("離開", lambda: self._quit()),
        )
        self._icon = pystray.Icon("SDPRS Webcam", _create_icon(_health_color(self._state)),
                                  _tooltip(self._state), menu)
        threading.Thread(target=self._icon.run, daemon=True).start()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self._on_pause()
        else:
            self._on_resume()
        if self._icon and TRAY_AVAILABLE:
            self._icon.update_menu()

    def _quit(self) -> None:
        if self._icon:
            self._icon.stop()
        self._on_quit()

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()
