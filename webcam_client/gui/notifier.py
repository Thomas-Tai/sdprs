# sdprs/webcam_client/gui/notifier.py
"""Windows toast, via pystray's own notification support (no new dependency --
pystray._win32.Icon has HAS_NOTIFICATION = True).

This is the only channel that reaches an operator who never looks at the tray,
so the message always carries the ACTION, not just the fault. It must never
raise: a notification backend failure cannot be allowed to kill the dispatch
loop that keeps the cameras running.
"""
import logging

from ..strings import describe

logger = logging.getLogger("webcam_client.gui.notifier")


def notify_state(icon, state) -> bool:
    """Toast the given health state. Returns True if a toast was attempted."""
    if icon is None or not hasattr(icon, "notify"):
        return False
    title, detail, action = describe(state)
    message = detail if not action else f"{detail}\n{action}"
    try:
        icon.notify(message, title)
        return True
    except Exception:
        logger.warning("Toast notification failed", exc_info=True)
        return False
