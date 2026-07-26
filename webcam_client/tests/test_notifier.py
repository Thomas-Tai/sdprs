# webcam_client/tests/test_notifier.py
"""The toast is the only thing that reaches a guard who never looks at the tray.
It must carry the action, and it must never be able to kill the app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.gui.notifier import notify_state
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
