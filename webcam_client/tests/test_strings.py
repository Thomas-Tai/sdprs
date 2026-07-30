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
    happened, since it silently stops covering constants added later.

    The `_`-prefixed constants are included ON PURPOSE. Excluding them was the
    same failure mode this docstring warns about, wearing a different hat:
    _RUNNING_DETAIL_UNKNOWN_COUNT is not an internal detail, it is the text of
    EVERY recovery toast and every RUNNING tooltip -- notifier.py and
    tray_app.py both call describe(state) with no context, so camera_count is
    None and that branch always fires. Meanwhile the two content tests above
    pass camera_count=2, which takes the template branch and never renders it.
    A banned word planted there failed nothing at all.
    """
    constants = {
        name: value
        for name, value in vars(strings).items()
        if name.isupper() and isinstance(value, str)
    }
    assert "_RUNNING_DETAIL_UNKNOWN_COUNT" in constants, \
        "the guard-visible _-prefixed constants must stay inside this scan"
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


def test_bad_key_names_the_button_that_actually_saves_the_new_key():
    """The action told the guard to 填入並儲存 while the setup window's only
    buttons were 開始 and 取消 -- there was no 儲存 anywhere in the app. A guard
    who has just been handed a new key by the administrator, and who is told to
    save it, does not press a button labelled 開始; the cautious ones press 取消
    and lose the key they just typed.

    Asserted against the constants, not literals, because the sibling drift
    tests for BTN_RECONNECT and MENU_STATUS are exactly why those two never
    drifted while this one did."""
    _, _, action = strings.describe(Health.BAD_KEY)
    assert strings.BTN_WIZARD_SAVE in action, \
        f"bad_key must name the button that saves the key: {action!r}"
    assert strings.LBL_API_KEY in action, \
        f"bad_key must name the field the key goes into: {action!r}"
    assert strings.BTN_SETTINGS in action, \
        f"bad_key must name the button that opens that window: {action!r}"


def test_the_setup_window_speaks_the_same_language_as_everything_else():
    """The setup window is the FIRST screen a guard ever meets and it used to
    sit outside this module entirely, so its error paths still shipped a raw
    status code, an English exception repr and the word JSON long after every
    other surface was clean. Now that its copy lives here the scan above covers
    it automatically -- this pins the other half: each of these fires at a
    moment the guard is stuck, so each must name something to DO."""
    stuck = (strings.WIZ_NO_CAMERA_FOUND, strings.WIZ_CANNOT_REACH_SERVER,
             strings.WIZ_KEY_REJECTED, strings.WIZ_SERVER_REFUSED,
             strings.WIZ_BAD_RESPONSE, strings.WIZ_NEED_A_CAMERA,
             strings.WIZ_NEED_URL_AND_KEY)
    for msg in stuck:
        assert msg.strip(), "an empty message is no message"
        assert "請" in msg, f"a stuck guard needs an instruction, not a diagnosis: {msg!r}"
    # The two that a guard genuinely cannot resolve alone must route onward;
    # the ones they CAN fix must not escalate on the first try.
    for msg in (strings.WIZ_SERVER_REFUSED, strings.WIZ_BAD_RESPONSE):
        assert "管理員" in msg, f"an unfixable setup failure must escalate: {msg!r}"
    assert "管理員" not in strings.WIZ_NEED_URL_AND_KEY, \
        "a blank field is the guard's to fix; do not send them to the administrator"


def test_the_pause_instruction_names_the_actual_menu_item():
    """F-2: the paused action tells the guard to right-click the tray icon and
    choose a menu item BY NAME, while tray_app.py spelled that item's label
    independently. The two drifted once already -- the instruction said 恢復推送
    while the menu said 恢復上傳 -- and the only reason it was caught is that a
    test happened to pin the literal. Assert against the constant instead."""
    _, _, action = strings.describe(Health.PAUSED)
    assert strings.MENU_RESUME in action, \
        f"paused must name the menu item that resumes uploading: {action!r}"


def test_already_running_tells_the_guard_how_to_reach_the_status_window():
    """F-4: nothing in the app told the guard that double-clicking the tray icon
    opens 監控狀態, even though that is the menu's `default=True` item. This
    message fires exactly when the guard is hunting for an app that is already
    running, which makes it the only good place to say so -- the tooltip cannot,
    since szTip is capped at 127 characters and already carries the title, the
    detail and the action."""
    assert strings.MENU_STATUS in strings.ALREADY_RUNNING, \
        f"the guard is never told what the icon opens: {strings.ALREADY_RUNNING!r}"
    assert "點兩下" in strings.ALREADY_RUNNING, \
        "naming the window is not enough; say how to open it"


def test_the_destination_has_exactly_one_name():
    """paused said 監控中心 while every other surface said 伺服器. A guard who
    reads 'not reaching the 監控中心' when paused and '無法連線到伺服器' when it
    breaks has no way to know those are the same machine, and reports two
    systems to a technician who has one."""
    for state in ALL_STATES:
        joined = " ".join(strings.describe(state, camera_count=2,
                                           camera_names="前門攝影機"))
        assert "監控中心" not in joined, \
            f"{state} gives the destination a second name: {joined!r}"
