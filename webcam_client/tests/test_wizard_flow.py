# webcam_client/tests/test_wizard_flow.py
"""WizardFlow owns which of the setup window's three numbered sections is
unlocked. Like StatusHub it is deliberately pure -- no Tk, no httpx, no I/O --
so the rules a guard actually meets are testable without a display.

The rule these tests exist to defend is the same one this whole branch exists
for: the window must never claim a state it has not earned. "Section 2 is
unlocked" means "these exact credentials were tested and worked", and every
test below is a way that claim could quietly become false.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.gui.wizard.flow import (WizardFlow, MODE_EDIT,
                                           MODE_FIRST_RUN)


# --------------------------------------------------------------------------
# The five stated rules.
# --------------------------------------------------------------------------

def test_first_run_starts_with_section_two_locked():
    flow = WizardFlow()
    assert flow.section2_unlocked is False


def test_first_run_is_the_default_mode():
    """run_setup_wizard's own default is "first-run"; the safe mode is the one
    you get by forgetting to pass anything."""
    assert WizardFlow().mode == MODE_FIRST_RUN
    assert WizardFlow(MODE_FIRST_RUN).section2_unlocked is False


def test_a_verified_connection_unlocks_section_two():
    flow = WizardFlow()
    flow.on_connection_verified()
    assert flow.section2_unlocked is True


def test_editing_credentials_relocks_section_two_even_after_a_good_test():
    """The heart of the branch. Once 連線位址 or 連線密碼 changes, the earlier
    green tick describes credentials that no longer exist."""
    flow = WizardFlow()
    flow.on_connection_verified()
    flow.on_credentials_edited()
    assert flow.section2_unlocked is False


def test_edit_mode_starts_already_verified():
    """A guard who opens 設定 to rename a camera must not be made to re-test a
    connection that is demonstrably working -- the client is running."""
    flow = WizardFlow(MODE_EDIT)
    assert flow.section2_unlocked is True


def test_cannot_confirm_with_zero_cameras_selected():
    flow = WizardFlow()
    flow.on_connection_verified()
    assert flow.can_confirm is False, "nothing would be monitored"


def test_cannot_confirm_while_section_two_is_locked():
    """Even in edit mode, where cameras are already ticked from the saved
    config: an untested connection must not be confirmable past section 2."""
    flow = WizardFlow(MODE_EDIT)
    flow.on_cameras_selected(2)
    flow.on_credentials_edited()
    assert flow.can_confirm is False


def test_confirming_needs_both_a_verified_connection_and_a_camera():
    flow = WizardFlow()
    flow.on_connection_verified()
    flow.on_cameras_selected(1)
    assert flow.can_confirm is True


# --------------------------------------------------------------------------
# Decision 1: an edit that changes nothing.
#
# The guard clicks into 連線密碼 and clicks straight out, or types a character
# and deletes it. WizardFlow re-locks anyway, because it is the only answer
# that cannot lie: this object holds no field text (that is the entire point of
# it being pure), so it CANNOT distinguish a no-op edit from a real one. The
# alternative -- have the window compare old and new text and only report real
# changes -- puts the truth of the lock in the Tk layer, which is exactly where
# it was when the status indicators started lying.
#
# The costs are asymmetric and not close. Re-locking a connection that was in
# fact untouched costs the guard one press of 測試連線, which takes a second and
# tells them the truth. Failing to re-lock shows a green tick for credentials
# nobody has ever tested, and the guard finds out at 3am when nothing uploaded.
# --------------------------------------------------------------------------

def test_any_credentials_edit_relocks_even_when_the_text_ends_up_identical():
    """WizardFlow is told an edit happened, never what was typed, so a revert
    and a real change are indistinguishable here BY DESIGN. Re-lock is the only
    answer that cannot show a tick for untested credentials."""
    flow = WizardFlow()
    flow.on_connection_verified()
    flow.on_credentials_edited()   # typed "x"
    flow.on_credentials_edited()   # ...and deleted it again
    assert flow.section2_unlocked is False, (
        "the flow cannot know the text came back to where it started, and "
        "guessing 'unchanged' is how a window starts claiming a green tick it "
        "never earned")


def test_repeated_credentials_edits_are_idempotent():
    """Every keystroke fires this in the wired window. It must be cheap and it
    must not toggle anything back on."""
    flow = WizardFlow()
    for _ in range(10):
        flow.on_credentials_edited()
    assert flow.section2_unlocked is False
    flow.on_connection_verified()
    assert flow.section2_unlocked is True


def test_edit_modes_head_start_is_spent_by_the_first_edit():
    """Edit mode's pre-verification is a grace for a connection that is proving
    itself right now, not a permanent exemption. Touching either field spends
    it, and only a real 測試連線 buys it back -- reconstructing the flow is not
    something the guard can do."""
    flow = WizardFlow(MODE_EDIT)
    flow.on_credentials_edited()
    assert flow.section2_unlocked is False
    flow.on_credentials_edited()
    assert flow.section2_unlocked is False, "still spent, not restored"
    flow.on_connection_verified()
    assert flow.section2_unlocked is True


def test_repeated_verifications_are_idempotent():
    flow = WizardFlow()
    flow.on_connection_verified()
    flow.on_connection_verified()
    assert flow.section2_unlocked is True


# --------------------------------------------------------------------------
# Decision 2: on_cameras_selected(0) after a non-zero selection.
#
# It really means zero. The count is a level, not an event -- the window
# reports "N are ticked right now", so un-ticking the last box must take
# can_confirm back to False. The opposite (a high-water mark) would let 開始監控
# stay pressable with nothing to monitor, and the guard would walk away from a
# client that uploads nothing.
# --------------------------------------------------------------------------

def test_unticking_the_last_camera_forbids_confirming_again():
    flow = WizardFlow()
    flow.on_connection_verified()
    flow.on_cameras_selected(3)
    assert flow.can_confirm is True, "precondition"
    flow.on_cameras_selected(0)
    assert flow.can_confirm is False, (
        "the count is what is ticked NOW, not the most that ever was -- a "
        "high-water mark leaves 開始監控 pressable with nothing to monitor")
    assert flow.selected_cameras == 0


def test_the_selection_is_recorded_even_while_section_two_is_locked():
    """The lock is the window's business to enforce visually. The flow just
    records what it is told, so an out-of-order call cannot wedge it."""
    flow = WizardFlow()
    flow.on_cameras_selected(2)
    assert flow.selected_cameras == 2
    assert flow.can_confirm is False, "still locked, so still not confirmable"


# --------------------------------------------------------------------------
# Decision 3: a credentials edit after cameras were already selected.
#
# The selection SURVIVES. Re-locking section 2 is a statement about the
# connection, and it says nothing at all about which cameras the guard wants.
# Clearing the ticks would punish the guard for fixing a typo -- they would
# re-test, then discover their work was silently thrown away and re-tick every
# box. Nothing is claimed untruthfully by keeping them: can_confirm is False
# throughout, so the surviving selection can never be acted on until the
# connection is genuinely re-verified.
# --------------------------------------------------------------------------

def test_a_credentials_edit_keeps_the_camera_selection():
    flow = WizardFlow()
    flow.on_connection_verified()
    flow.on_cameras_selected(3)
    flow.on_credentials_edited()
    assert flow.selected_cameras == 3, (
        "re-locking is a statement about the CONNECTION; throwing away the "
        "ticks makes a one-character typo fix cost the guard all their work")
    assert flow.can_confirm is False, "...but it is still not confirmable"


def test_fixing_a_typo_and_re_testing_restores_confirm_without_re_ticking():
    """The whole guard journey this decision exists for, end to end."""
    flow = WizardFlow()
    flow.on_connection_verified()
    flow.on_cameras_selected(2)
    assert flow.can_confirm is True, "precondition: they were ready to start"

    flow.on_credentials_edited()          # spotted a typo in 連線位址
    assert flow.can_confirm is False

    flow.on_connection_verified()         # pressed 測試連線 again; it passed
    assert flow.section2_unlocked is True
    assert flow.can_confirm is True, (
        "the guard must not have to re-tick two cameras they never untouched")


# --------------------------------------------------------------------------
# Decision 4: an unknown mode string.
#
# Raise. The mode is passed by OUR code (run_setup_wizard has exactly two call
# shapes), never by the guard, so a bad value is a programming error and the
# loud failure lands in a test run rather than in a guardhouse.
#
# The tempting alternative -- "default to first-run, which is the safe/locked
# mode" -- silently converts a typo like "edit " into a wizard that makes a
# guard re-test a working connection, and it leaves the next person free to
# pick the OTHER default. Raising cannot drift toward unlocked, which is the
# direction that lies; the second test pins that no unlocked object is ever
# produced, so the guarantee survives even if the exception type changes.
# --------------------------------------------------------------------------

def test_an_unknown_mode_is_rejected_loudly():
    for bad in ("edit ", "Edit", "first_run", "firstrun", "", None, 0):
        try:
            WizardFlow(bad)
        except ValueError:
            continue
        raise AssertionError(f"mode {bad!r} was accepted silently")


def test_an_unknown_mode_never_yields_an_unlocked_flow():
    """The guarantee behind the exception type: whatever a bad mode does, it
    must never be the unlocked direction."""
    try:
        flow = WizardFlow("something-else")
    except ValueError:
        return                      # rejected outright -- nothing to unlock
    assert flow.section2_unlocked is False
    assert flow.can_confirm is False


def test_the_mode_constants_are_the_literals_the_window_already_passes():
    """run_setup_wizard(mode=...) is called with these exact strings today and
    lives in a file this change does not touch. Pinning the values here means a
    later rename cannot silently desynchronise the two."""
    assert MODE_FIRST_RUN == "first-run"
    assert MODE_EDIT == "edit"


# --------------------------------------------------------------------------
# Guards on the remaining inputs.
# --------------------------------------------------------------------------

def test_a_negative_camera_count_is_rejected_loudly():
    """A count of ticked checkboxes cannot be negative. can_confirm would be
    False for it anyway, so the failure would surface as an inexplicably dead
    開始監控 button rather than as the caller bug it is."""
    flow = WizardFlow()
    flow.on_connection_verified()
    try:
        flow.on_cameras_selected(-1)
    except ValueError:
        pass
    else:
        raise AssertionError("a negative camera count was accepted")
    assert flow.selected_cameras == 0, "the rejected call must not have landed"


def test_edit_mode_starts_unable_to_confirm_until_the_prefill_is_reported():
    """WIRING CONTRACT, pinned so it cannot be forgotten: the flow does not read
    config, so an edit-mode window MUST call on_cameras_selected(len(prefilled))
    while building its rows. Skip it and a guard who opened 設定 to rename one
    camera finds 開始監控 dead with every box visibly ticked."""
    flow = WizardFlow(MODE_EDIT)
    assert flow.section2_unlocked is True
    assert flow.can_confirm is False
    flow.on_cameras_selected(2)          # what the window owes the flow
    assert flow.can_confirm is True


# --------------------------------------------------------------------------
# The in-flight 測試連線 race.
#
# 測試連線 is an HTTP call the window runs off the Tk thread; its reply can
# land AFTER the guard has gone back and changed the address. Delivering that
# stale success unlocks section 2 for credentials the app has never tried --
# the branch's exact failure mode, arriving through the back door. The optional
# token closes it without changing the plain call's meaning.
# --------------------------------------------------------------------------

def test_a_reply_that_predates_a_credentials_edit_cannot_unlock():
    flow = WizardFlow()
    token = flow.begin_connection_test()
    flow.on_credentials_edited()          # guard retypes the address mid-probe
    flow.on_connection_verified(token)    # the OLD probe finally answers "ok"
    assert flow.section2_unlocked is False, (
        "that success describes the address the guard just replaced; honouring "
        "it unlocks section 2 for credentials the app has never tried")


def test_a_reply_for_the_current_credentials_unlocks_normally():
    flow = WizardFlow()
    token = flow.begin_connection_test()
    flow.on_connection_verified(token)
    assert flow.section2_unlocked is True


def test_a_fresh_test_after_an_edit_unlocks_again():
    """The stale-reply guard must not wedge the flow: the NEXT probe, started
    after the edit, is current and must be honoured."""
    flow = WizardFlow()
    flow.begin_connection_test()
    flow.on_credentials_edited()
    token = flow.begin_connection_test()
    flow.on_connection_verified(token)
    assert flow.section2_unlocked is True


def test_a_tokenless_verification_still_unlocks():
    """The pinned interface is on_connection_verified() with no arguments, and
    a caller that does not opt into tokens must keep working unchanged."""
    flow = WizardFlow()
    flow.begin_connection_test()
    flow.on_credentials_edited()
    flow.on_connection_verified()
    assert flow.section2_unlocked is True


# --------------------------------------------------------------------------
# Purity. The reason any of the above can be asserted on a headless machine.
# --------------------------------------------------------------------------

def test_the_flow_module_imports_nothing_that_needs_a_display_or_a_socket():
    """Structural, not behavioural: a single `import tkinter` added here for
    convenience would make this whole file need a display, and a `import httpx`
    would put a network call back inside the state machine. Checked against the
    source rather than sys.modules, because sibling tests import Tk anyway."""
    from webcam_client.gui.wizard import flow as flow_module

    tree = ast.parse(Path(flow_module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    allowed = {"typing"}       # type hints only; nothing with a side effect
    assert imported <= allowed, (
        f"flow.py must stay pure state; it now imports {sorted(imported - allowed)}")
