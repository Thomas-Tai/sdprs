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
        assert not re.search(r"\b[1-5]\d\d\b", joined), \
            f"{state} leaks what looks like a status code: {joined!r}"


def test_no_exception_or_developer_text_reaches_the_operator():
    banned = ("Error", "error", "Exception", "Traceback", "None", "null",
              "HTTP", "http", "API", "timeout", "socket")
    for state in ALL_STATES:
        joined = " ".join(strings.describe(state, camera_count=2,
                                           camera_names="前門攝影機"))
        for word in banned:
            assert word not in joined, f"{state} leaks developer text {word!r}: {joined!r}"


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


def test_camera_down_names_the_camera():
    _, detail, _ = strings.describe(Health.CAMERA_DOWN, camera_count=2,
                                    camera_names="前門攝影機")
    assert "前門攝影機" in detail


def test_describe_tolerates_missing_context():
    """Callers in error paths may not have context to hand; a missing key must
    not raise and blank the UI."""
    for state in ALL_STATES:
        title, detail, action = strings.describe(state)
        assert title and detail
