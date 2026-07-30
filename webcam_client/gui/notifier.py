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


def notify_state(icon, state, camera_names=None) -> bool:
    """Toast the given health state. Returns True if a toast was attempted.

    camera_names: DISPLAY names (never node_ids, never CONTROL_SOURCE --
    main.py's _camera_display_names() is what guarantees that) for
    camera_down's "{camera_names}目前沒有畫面。" template. Joined exactly like
    the status window's own build_status_lines(), so the toast and the window
    never disagree about how a list of names is punctuated. An empty or
    missing list falls through to describe()'s generic "攝影機" default rather
    than rendering a sentence with no subject -- the pre-fix behaviour, kept
    as the fallback rather than the only behaviour.
    """
    if icon is None or not hasattr(icon, "notify"):
        return False
    names = "、".join(str(n) for n in (camera_names or []) if n)
    title, detail, action = describe(state, camera_names=names or None)
    message = detail if not action else f"{detail}\n{action}"
    try:
        icon.notify(message, title)
        return True
    except Exception:
        logger.warning("Toast notification failed", exc_info=True)
        return False
