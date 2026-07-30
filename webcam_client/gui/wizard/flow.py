# sdprs/webcam_client/gui/wizard/flow.py
"""Which of the setup window's three numbered sections is unlocked.

The window a security guard meets is one window with three numbered sections
that unlock in order:

    1. 連線                    -- 連線位址 / 連線密碼 + 測試連線
    2. 選擇要監控的攝影機      -- locked until section 1's test passes
    3. 開始監控                -- the confirm button (``can_confirm``)

This module is the state machine behind that, and nothing else: no Tk, no
httpx, no file I/O, no logging. That is deliberate, and it is the same choice
``status.py`` makes for ``StatusHub`` -- the interesting rules become testable
on a headless machine, and the window is left with nothing to do but render
what it is told.

The invariant everything below serves: **"section 2 is unlocked" means these
exact credentials were tested and worked.** This branch exists because the
client's status indicators were telling the operator things that were not
true, so any state that could drift away from that sentence is resolved
toward the locked side, which costs a guard one press of 測試連線, rather than
toward the unlocked side, which costs them a night of uploads that never
happened.
"""

# The two literals ``run_setup_wizard(mode=...)`` already accepts. Named here
# because the error message below has to list them and because tests can then
# pin the values against the window's own literals without importing Tk.
MODE_FIRST_RUN = "first-run"
MODE_EDIT = "edit"

_VALID_MODES = (MODE_FIRST_RUN, MODE_EDIT)


class WizardFlow:
    """Pure state. Feed it events, read predicates off it, render.

    Beyond the four members the window strictly needs there are two additions,
    each justified where it is defined: ``selected_cameras`` (the window wants
    to show the count it just reported, and it makes "the ticks survive a
    re-lock" assertable) and ``begin_connection_test`` (closes the in-flight
    reply race described on ``on_connection_verified``).
    """

    def __init__(self, mode: str = MODE_FIRST_RUN):
        if mode not in _VALID_MODES:
            # Fail loudly. ``mode`` is chosen by our own code -- the window has
            # exactly two call shapes -- so a bad value is a programming error,
            # and the loud failure lands in a test run instead of in a
            # guardhouse. The tempting alternative, "quietly default to the
            # locked mode", is safe only until the next person picks the other
            # default; raising cannot drift toward unlocked at all.
            raise ValueError(
                f"unknown wizard mode {mode!r}; expected one of {_VALID_MODES}")
        self._mode = mode

        # Edit mode starts verified: the guard opened 設定 on an installation
        # that is running and uploading right now, so the saved credentials are
        # being proven continuously, and making them re-test to rename a camera
        # would be theatre. It is a head start, not an exemption -- the first
        # edit spends it (see on_credentials_edited).
        self._verified = (mode == MODE_EDIT)

        self._selected = 0

        # Bumped by every credentials edit. Its only job is to date a reply
        # from an in-flight 測試連線; see on_connection_verified.
        self._credentials_generation = 0

    # -- read-only view ----------------------------------------------------

    @property
    def mode(self) -> str:
        """Read-only: the mode decides the starting state and nothing may
        rewrite history by flipping it afterwards."""
        return self._mode

    @property
    def section2_unlocked(self) -> bool:
        """True exactly when the credentials currently in section 1 have been
        tested and worked."""
        return self._verified

    @property
    def selected_cameras(self) -> int:
        """How many cameras are ticked right now. Exposed because the window
        renders the count, and because it is what makes "a credentials edit
        does not throw away the guard's ticks" a thing a test can state."""
        return self._selected

    @property
    def can_confirm(self) -> bool:
        """Whether section 3's 開始監控 may be pressed.

        Both halves are required, and each rules out a different bad outcome:
        an untested connection (nothing would upload) and an empty selection
        (nothing would be monitored). Section 3 has no lock of its own -- this
        predicate *is* its lock.
        """
        return self._verified and self._selected > 0

    # -- events ------------------------------------------------------------

    def begin_connection_test(self) -> int:
        """Take a token standing for the credentials as they are right now, to
        hand back to ``on_connection_verified`` when the reply lands.

        Optional, and only meaningful for a caller that tests the connection
        off the Tk thread -- which the window does, because a 10-second HTTP
        timeout on the Tk thread freezes the window.
        """
        return self._credentials_generation

    def on_connection_verified(self, token: int | None = None) -> None:
        """A 測試連線 succeeded: unlock section 2.

        ``token`` is what makes a *late* success safe. 測試連線 runs off the Tk
        thread, so its reply can land after the guard has already gone back and
        changed 連線位址. Honouring that reply would unlock section 2 for
        credentials the app has never tried -- this branch's exact failure mode
        arriving through the back door. A token minted before the edit no
        longer matches, and the stale success is dropped.

        Called with no token it unlocks unconditionally, which keeps the plain
        one-line call in the pinned interface working exactly as written.
        """
        if token is not None and token != self._credentials_generation:
            return          # answers a question about credentials that are gone
        self._verified = True

    def on_credentials_edited(self) -> None:
        """連線位址 or 連線密碼 changed: re-lock section 2, in either mode.

        Unconditional, including for an edit that changes nothing -- the guard
        clicks into a field and out again, or types a character and deletes it.
        This object holds no field text (being pure is the entire point of it),
        so it *cannot* tell a no-op edit from a real one, and the alternative --
        have the Tk layer compare old and new text and report only real changes
        -- moves the truth of the lock back into the layer whose claims stopped
        being trustworthy in the first place.

        The costs are not symmetric. A needless re-lock costs one press of
        測試連線 and tells the guard the truth. A missed re-lock shows a green
        tick for credentials nobody ever tested.

        The ticked cameras survive: re-locking is a statement about the
        connection and says nothing about which cameras the guard wants.
        Nothing untrue is claimed by keeping them, because ``can_confirm`` is
        False for as long as section 2 is locked -- and a guard fixing a
        one-character typo should not have to re-tick every box.
        """
        self._verified = False
        self._credentials_generation += 1

    def on_cameras_selected(self, n: int) -> None:
        """Report how many cameras are ticked *right now*.

        A level, not a running total: ``on_cameras_selected(0)`` after a
        non-zero selection really does mean zero, and takes ``can_confirm``
        back to False. Treating it as a high-water mark would leave 開始監控
        pressable with nothing to monitor.
        """
        if n < 0:
            # Impossible from a count of ticked checkboxes. can_confirm would
            # already be False for a negative count, so swallowing it would
            # surface as an inexplicably dead 開始監控 button instead of as the
            # caller bug it is.
            raise ValueError(f"camera count cannot be negative: {n!r}")
        self._selected = n
