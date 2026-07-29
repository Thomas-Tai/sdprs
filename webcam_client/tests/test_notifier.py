# webcam_client/tests/test_notifier.py
"""The toast is the only thing that reaches a guard who never looks at the tray.
It must carry the action, and it must never be able to kill the app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.gui.notifier import notify_state
from webcam_client.gui.tray_app import _pause_label
from webcam_client.status import Health


class FakeIcon:
    def __init__(self, boom=False):
        self.calls = []
        self._boom = boom

    def notify(self, message, title=None):
        if self._boom:
            raise RuntimeError("notification backend exploded")
        self.calls.append((title, message))


def test_notifies_with_title_and_action():
    icon = FakeIcon()
    assert notify_state(icon, Health.BAD_KEY) is True
    title, message = icon.calls[0]
    assert "連線密碼" in title
    assert "管理員" in message, "the toast must tell the guard what to do"


def test_no_status_code_in_the_toast():
    icon = FakeIcon()
    notify_state(icon, Health.NO_SERVER)
    title, message = icon.calls[0]
    assert "401" not in message and "500" not in message


def test_backend_failure_is_swallowed():
    assert notify_state(FakeIcon(boom=True), Health.NO_SERVER) is False


def test_missing_icon_is_safe():
    assert notify_state(None, Health.NO_SERVER) is False


# --------------------------------------------------------------------------
# Ledger row 11: the two untested paths.
# --------------------------------------------------------------------------

def test_a_healthy_state_toasts_without_an_action_line():
    """This is the path EVERY recovery toast takes -- the most frequent toast
    in normal operation -- and nothing drove it. Healthy states carry an empty
    action, so notify_state must send the detail alone rather than gluing a
    bare newline onto the end of the message."""
    icon = FakeIcon()
    assert notify_state(icon, Health.RUNNING) is True
    title, message = icon.calls[0]
    assert "監控中" in title
    assert "運作正常" in message
    assert message == message.strip(), \
        f"an empty action leaked a dangling separator into the toast: {message!r}"
    assert "\n" not in message, "a healthy state has no action line to append"


def test_paused_toasts_its_action():
    """PAUSED is healthy but DOES carry an action (how to resume), so the
    separator logic must still join the two halves for it. That action also
    names a tray menu item verbatim, so a rename on either side without the
    other sends the guard hunting for a menu entry that does not exist."""
    icon = FakeIcon()
    assert notify_state(icon, Health.PAUSED) is True
    _, message = icon.calls[0]
    resume_label = _pause_label(True)
    assert resume_label in message, \
        f"the PAUSED toast must name the tray menu item {resume_label!r}: {message!r}"


def test_an_object_without_notify_is_not_a_toast_channel():
    """The None check is not enough on its own: TrayApp.icon is a pystray Icon
    only on Windows, and the guard clause exists precisely because some backends
    expose no notify(). A non-None object lacking it must return False, not
    raise AttributeError into the dispatch loop."""
    class NotAToastChannel:
        pass

    assert notify_state(NotAToastChannel(), Health.NO_SERVER) is False
    assert notify_state(object(), Health.CAMERA_DOWN) is False
