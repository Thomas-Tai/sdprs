# sdprs/webcam_client/gui/tray_app.py
import logging
import threading
from typing import Callable, Optional

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
               "amber": (230, 160, 0, 255)}
    c = palette.get(color, palette["green"])
    draw.ellipse([8, 8, 56, 56], fill=c)
    return img


def _pause_label(paused: bool) -> str:
    return "恢復推送" if paused else "暫停推送"


def _icon_color(paused: bool, connected: bool) -> str:
    # Paused is the operator's most important state, so it wins over connection.
    if paused:
        return "amber"
    return "green" if connected else "red"


class TrayApp:
    def __init__(self, on_open_settings: Callable, on_quit: Callable,
                 on_pause: Callable, on_resume: Callable):
        self._on_open_settings = on_open_settings
        self._on_quit = on_quit
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._icon: Optional["pystray.Icon"] = None
        self._paused = False
        self._connected = False

    def _refresh_icon(self) -> None:
        if self._icon and TRAY_AVAILABLE:
            self._icon.icon = _create_icon(_icon_color(self._paused, self._connected))

    def set_status(self, connected: bool) -> None:
        self._connected = connected
        self._refresh_icon()

    def start(self) -> None:
        if not TRAY_AVAILABLE:
            logger.warning("pystray not available, running without tray icon")
            return
        menu = pystray.Menu(
            pystray.MenuItem("開啟設定", lambda: self._on_open_settings()),
            pystray.MenuItem(lambda item: _pause_label(self._paused),
                             lambda: self._toggle_pause()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("離開", lambda: self._quit()),
        )
        self._icon = pystray.Icon("SDPRS Webcam", _create_icon("green"),
                                  "SDPRS Webcam", menu)
        threading.Thread(target=self._icon.run, daemon=True).start()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self._on_pause()
        else:
            self._on_resume()
        self._refresh_icon()
        if self._icon and TRAY_AVAILABLE:
            self._icon.update_menu()

    def _quit(self) -> None:
        if self._icon:
            self._icon.stop()
        self._on_quit()

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()
