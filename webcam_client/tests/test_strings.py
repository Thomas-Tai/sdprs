# webcam_client/tests/test_strings.py
"""Every string a security guard can see lives here, so this file is where we
enforce the rule that NO status code, exception repr, or English error text ever
reaches them. The technician still gets the code -- in the log file."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client import strings
from webcam_client.status import Health


ALL_STATES = list(Health)

# A bare \b boundary is defined by \w, and CJK ideographs ARE \w under
# Unicode-aware `re` -- so `\b[1-5]\d\d\b` fails to catch a status code glued
# directly onto Han text with no separating punctuation (e.g. "伺服器回應401").
# Lookaround on digit-adjacency instead: a status code is 1-5xx as long as it
# is not itself part of a longer run of digits.
_STATUS_CODE_RE = r"(?<!\d)[1-5]\d\d(?!\d)"

_BANNED_WORDS = ("Error", "error", "Exception", "Traceback", "None", "null",
                 "HTTP", "http", "API", "timeout", "socket")


def test_every_state_has_all_three_parts():
    for state in ALL_STATES:
        title, detail, action = strings.describe(state, camera_count=2,
                                                 camera_names="前門攝影機")
        assert title, f"{state} has no title"
        assert detail, f"{state} has no detail"
        # action may legitimately be empty for healthy states
        assert isinstance(action, str)


def test_no_status_codes_reach_the_operator():
    """A bare 3-digit number is almost certainly an HTTP status leaking through.
    Guard text must say what to DO, not what the server returned."""
    for state in ALL_STATES:
        joined = " ".join(strings.describe(state, camera_count=2,
                                           camera_names="前門攝影機"))
        assert not re.search(_STATUS_CODE_RE, joined), \
            f"{state} leaks what looks like a status code: {joined!r}"


def test_status_code_detector_catches_digits_glued_to_han_text():
    """Proves the fix for the \\b blind spot: a status code with no
    punctuation separating it from surrounding Han text must still be
    detected. Under the old `\\b[1-5]\\d\\d\\b` pattern, CJK ideographs count
    as \\w, so '伺服器回應401' and '密碼401錯誤' would NOT match -- only
    '回應：401' (punctuation-separated) would. The digit-adjacency lookaround
    catches all of them."""
    leaking_examples = (
        "回應：401",
        "伺服器回應401",
        "密碼401錯誤",
    )
    for text in leaking_examples:
        assert re.search(_STATUS_CODE_RE, text), \
            f"detector failed to catch a leaking status code in {text!r}"


def test_no_exception_or_developer_text_reaches_the_operator():
    for state in ALL_STATES:
        joined = " ".join(strings.describe(state, camera_count=2,
                                           camera_names="前門攝影機"))
        for word in _BANNED_WORDS:
            assert word not in joined, f"{state} leaks developer text {word!r}: {joined!r}"


def test_standalone_operator_constants_avoid_status_codes_and_developer_text():
    """The module docstring claims 'every string a security guard can see
    lives here' -- but the tests above only exercise the _TEXT table.
    WINDOW_TITLE, TRAY_TOOLTIP_PREFIX, ALREADY_RUNNING, MENU_STATUS, and the
    BTN_* labels are separate module-level constants the guard reads
    directly (window title, tray tooltip, a dialog, menu/button labels), and
    were never checked. Discover them dynamically via vars(strings) rather
    than hardcoding the name list -- a hardcoded list is exactly how this gap
    happened, since it silently stops covering constants added later."""
    constants = {
        name: value
        for name, value in vars(strings).items()
        if name.isupper() and not name.startswith("_") and isinstance(value, str)
    }
    assert constants, "no standalone string constants discovered -- test would vacuously pass"
    for name, value in constants.items():
        assert not re.search(_STATUS_CODE_RE, value), \
            f"{name} leaks what looks like a status code: {value!r}"
        for word in _BANNED_WORDS:
            assert word not in value, f"{name} leaks developer text {word!r}: {value!r}"


def test_faulty_states_tell_the_operator_what_to_do():
    """A guard cannot act on 'something went wrong'. Every non-healthy state
    must carry an action line."""
    for state in (Health.NO_SERVER, Health.BAD_KEY, Health.CAMERA_DOWN):
        _, _, action = strings.describe(state, camera_count=2,
                                        camera_names="前門攝影機")
        assert action.strip(), f"{state} gives the operator no action to take"


def test_running_reports_the_camera_count():
    _, detail, _ = strings.describe(Health.RUNNING, camera_count=3,
                                    camera_names="")
    assert "3" in detail


def test_running_without_context_is_not_self_contradictory():
    """describe(Health.RUNNING) with no camera_count in context must not
    render '0 支攝影機運作正常' -- claiming zero cameras while saying
    everything is fine contradicts a state whose entire meaning is
    'monitoring is active'."""
    _, detail, _ = strings.describe(Health.RUNNING)
    assert "0" not in detail
    assert "運作正常" in detail


def test_camera_down_names_the_camera():
    _, detail, _ = strings.describe(Health.CAMERA_DOWN, camera_count=2,
                                    camera_names="前門攝影機")
    assert "前門攝影機" in detail


def test_recoverable_faults_name_the_reconnect_button():
    """I-5: BAD_KEY and CAMERA_DOWN both STOP their worker -- open_camera()
    returning None ends the push engine's thread, a rejected key stops the
    control channel. So the physical fix the text asks for (re-seat the cable,
    have the administrator reset the key) recovers nothing by itself: no worker
    is left alive to reopen the device or retry. 重新連線 is the only action
    that does, and it sits three centimetres below this text -- unnamed, while
    the text sent the guard to escalate instead. Every re-seated cable became
    an unnecessary escalation, on the most likely fault a guard ever meets.

    Asserted against the BTN_RECONNECT constant, not a literal, so the label
    and the instruction that points at it cannot drift apart."""
    for state in (Health.BAD_KEY, Health.CAMERA_DOWN):
        _, _, action = strings.describe(state, camera_count=2,
                                        camera_names="前門攝影機")
        assert strings.BTN_RECONNECT in action, \
            f"{state} never names the one button that recovers it: {action!r}"


def test_the_reconnect_instruction_is_true_in_a_toast_too():
    """The same action line is also the BODY of the toast (notifier.py joins
    detail + action), where there is no button below anything. So it must say
    WHERE the button is, not just "press the one below"."""
    for state in (Health.BAD_KEY, Health.CAMERA_DOWN):
        _, _, action = strings.describe(state, camera_count=2,
                                        camera_names="前門攝影機")
        assert strings.MENU_STATUS in action, \
            f"{state} names the button but not the window holding it: {action!r}"


def test_camera_down_still_names_the_physical_action_first():
    """Naming the button must not have displaced the physical fix: re-seating
    the cable is what the guard does with their hands, and it must come before
    the software action, in both the startup case (camera never opened) and the
    mid-run case (sustained read failure)."""
    _, _, action = strings.describe(Health.CAMERA_DOWN, camera_count=2,
                                    camera_names="前門攝影機")
    assert "USB" in action
    assert action.index("USB") < action.index(strings.BTN_RECONNECT), \
        f"the software action must not precede the physical one: {action!r}"
    assert "管理員" in action, "escalation must remain available as the last resort"


def test_log_folder_failure_is_explained_in_plain_language():
    """Ledger row 15: 開啟記錄 failing used to do nothing at all -- no dialog,
    and its only log line goes to the folder that would not open."""
    assert strings.LOG_FOLDER_FAILED.strip()
    assert "記錄" in strings.LOG_FOLDER_FAILED
    assert "管理員" in strings.LOG_FOLDER_FAILED, "a failure with no action is unactionable"


def test_describe_tolerates_missing_context():
    """Callers in error paths may not have context to hand; a missing key must
    not raise and blank the UI."""
    for state in ALL_STATES:
        title, detail, action = strings.describe(state)
        assert title and detail
