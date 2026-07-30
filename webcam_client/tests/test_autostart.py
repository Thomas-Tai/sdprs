# webcam_client/tests/test_autostart.py
"""Autostart-at-logon: the guard ticks one box and must be able to trust it.

Two failure modes drive this file:

1. A Run value pointing at a onefile ``_MEI`` extraction directory works
   exactly once. That directory is deleted at process exit, so at the next
   logon Windows tries to launch a path that no longer exists, fails silently,
   and logs nothing anywhere the guard will ever look.
2. A checkbox that reads only ``Run`` renders ON after the user disabled the
   entry in Task Manager -- Task Manager writes ``StartupApproved\\Run`` and
   leaves ``Run`` untouched. This branch exists because status indicators were
   lying to the operator, so that is a bug, not a rounding error.

NOTHING here touches the real registry: every registry access in the module
goes through three shims, and the autouse fixture below makes even a forgotten
FakeRegistry install impossible to route to HKCU.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import webcam_client.autostart as autostart
from webcam_client.autostart import (
    RUN_KEY,
    STARTUP_APPROVED_KEY,
    VALUE_NAME,
    approval_is_enabled,
    build_run_command,
    is_enabled,
    set_enabled,
)

# A realistic install path: it contains a space, so an unquoted REG_SZ Run value
# would be parsed as `C:\Program` plus arguments.
FROZEN_EXE = r"C:\Program Files\SDPRS\SDPRS_Webcam.exe"
# What sys._MEIPASS looks like at runtime -- gone by the next boot.
MEI_EXE = r"C:\Users\guard\AppData\Local\Temp\_MEI123456\SDPRS_Webcam.exe"


class FakeRegistry:
    """In-memory stand-in for the three winreg shims.

    Absent values raise FileNotFoundError because that is what winreg really
    raises -- from QueryValueEx/DeleteValue for a missing value, and from
    OpenKey for a missing key -- so the module's real error handling runs.
    """

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []
        self.deletes = []
        self.read_error = None
        self.write_error = None
        self.delete_error = None

    def install(self, monkeypatch):
        monkeypatch.setattr(autostart, "_reg_read", self.read)
        monkeypatch.setattr(autostart, "_reg_write", self.write)
        monkeypatch.setattr(autostart, "_reg_delete", self.delete)
        return self

    def read(self, subkey, name):
        if self.read_error is not None:
            raise self.read_error
        try:
            return self.values[(subkey, name)]
        except KeyError:
            raise FileNotFoundError(2, "registry value not found", name)

    def write(self, subkey, name, value):
        self.writes.append((subkey, name, value))
        if self.write_error is not None:
            raise self.write_error
        self.values[(subkey, name)] = value

    def delete(self, subkey, name):
        self.deletes.append((subkey, name))
        if self.delete_error is not None:
            raise self.delete_error
        try:
            del self.values[(subkey, name)]
        except KeyError:
            raise FileNotFoundError(2, "registry value not found", name)


@pytest.fixture(autouse=True)
def _never_touch_the_real_registry(monkeypatch):
    """Belt and braces. A test that forgets FakeRegistry must still be unable
    to reach HKCU: with the module's winreg reference gone the shims raise
    AttributeError long before any key is opened."""
    monkeypatch.setattr(autostart, "winreg", None, raising=False)


def _freeze(monkeypatch, exe=FROZEN_EXE):
    """Pretend we are the packaged onefile exe."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", exe)


# --- build_run_command: pure ------------------------------------------------

def test_build_run_command_returns_none_when_not_frozen():
    # A source run's sys.executable is python.exe; writing it would launch a
    # bare interpreter at every logon.
    assert build_run_command(r"C:\Python314\python.exe", frozen=False) is None


def test_build_run_command_quotes_a_path_containing_spaces():
    assert build_run_command(FROZEN_EXE, frozen=True) == f'"{FROZEN_EXE}"'


def test_build_run_command_refuses_a_meipass_path():
    # Pinned behaviour: REFUSE. A _MEI path is per-launch and deleted at exit;
    # there is no salvageable value to write, and writing one fails silently at
    # the next boot.
    assert build_run_command(MEI_EXE, frozen=True) is None


def test_build_run_command_never_emits_a_mei_path_in_any_casing():
    for exe in (MEI_EXE, MEI_EXE.lower(), MEI_EXE.replace("\\", "/"),
                r"D:\temp\_mei98765\app.exe"):
        result = build_run_command(exe, frozen=True)
        assert result is None, exe
        assert result is None or "_MEI" not in result.upper()


def test_build_run_command_refuses_an_empty_executable():
    assert build_run_command("", frozen=True) is None
    assert build_run_command("   ", frozen=True) is None
    assert build_run_command(None, frozen=True) is None


def test_build_run_command_refuses_a_relative_path():
    # A Run value is resolved against an unpredictable logon CWD -- the same
    # silent-failure class as _MEI.
    assert build_run_command(r"SDPRS_Webcam.exe", frozen=True) is None
    assert build_run_command(r"dist\SDPRS_Webcam.exe", frozen=True) is None


def test_build_run_command_does_not_double_quote_an_already_quoted_path():
    assert build_run_command(f'"{FROZEN_EXE}"', frozen=True) == f'"{FROZEN_EXE}"'


def test_build_run_command_is_pure_and_ignores_the_running_interpreter(monkeypatch):
    # No sys reads: the caller supplies both inputs.
    monkeypatch.setattr(sys, "executable", r"C:\decoy\python.exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert build_run_command(FROZEN_EXE, frozen=False) is None


# --- approval_is_enabled: pure ----------------------------------------------

def test_approval_absent_blob_means_enabled():
    # Windows' own default: no StartupApproved value == the entry is enabled.
    assert approval_is_enabled(None) is True
    assert approval_is_enabled(b"") is True


def test_approval_even_first_byte_means_enabled():
    assert approval_is_enabled(b"\x02" + b"\x00" * 11) is True


def test_approval_odd_first_byte_means_disabled():
    # 0x03 is what Task Manager writes when the user disables the entry.
    assert approval_is_enabled(b"\x03" + b"\x00" * 11) is False


def test_approval_of_an_unreadable_blob_defaults_to_enabled():
    # Malformed == indistinguishable from absent; do not invent a disable.
    assert approval_is_enabled("not bytes") is True


# --- is_enabled -------------------------------------------------------------

def test_is_enabled_false_when_the_run_value_is_absent(monkeypatch):
    FakeRegistry().install(monkeypatch)
    assert is_enabled() is False


def test_is_enabled_true_when_present_and_approved(monkeypatch):
    FakeRegistry({
        (RUN_KEY, VALUE_NAME): f'"{FROZEN_EXE}"',
        (STARTUP_APPROVED_KEY, VALUE_NAME): b"\x02" + b"\x00" * 11,
    }).install(monkeypatch)
    assert is_enabled() is True


def test_is_enabled_true_when_run_exists_and_approval_is_absent(monkeypatch):
    # The common case on a machine where nobody ever opened Task Manager.
    FakeRegistry({(RUN_KEY, VALUE_NAME): f'"{FROZEN_EXE}"'}).install(monkeypatch)
    assert is_enabled() is True


def test_is_enabled_false_when_startup_approved_marks_it_disabled(monkeypatch):
    """THE anti-lying test.

    Task Manager -> Startup -> Disable leaves the Run value in place. A
    checkbox reading only Run renders ON while autostart is actually OFF.
    """
    FakeRegistry({
        (RUN_KEY, VALUE_NAME): f'"{FROZEN_EXE}"',
        (STARTUP_APPROVED_KEY, VALUE_NAME): b"\x03" + b"\x00" * 11,
    }).install(monkeypatch)
    assert is_enabled() is False


def test_is_enabled_false_for_an_orphan_approval_entry(monkeypatch):
    """StartupApproved outlives Run: uninstalling an app can leave its approval
    blob behind (observed live on the dev machine -- an "IDMan" entry in
    StartupApproved with no matching Run value). Run must be read FIRST so an
    orphan blob marked ENABLED can never report autostart that isn't there."""
    FakeRegistry({
        (STARTUP_APPROVED_KEY, VALUE_NAME): b"\x02" + b"\x00" * 11,
    }).install(monkeypatch)
    assert is_enabled() is False


def test_is_enabled_false_when_the_registry_read_raises(monkeypatch):
    fake = FakeRegistry().install(monkeypatch)
    fake.read_error = PermissionError("access denied")
    assert is_enabled() is False  # must not propagate


# --- set_enabled ------------------------------------------------------------

def test_set_enabled_true_writes_the_quoted_run_value(monkeypatch):
    _freeze(monkeypatch)
    fake = FakeRegistry().install(monkeypatch)
    assert set_enabled(True) is True
    assert (RUN_KEY, VALUE_NAME, f'"{FROZEN_EXE}"') in fake.writes


def test_set_enabled_true_never_writes_a_mei_path(monkeypatch):
    # sys.executable is correct under PyInstaller, but if anything ever hands
    # us an extraction path the write must not happen at all.
    _freeze(monkeypatch, exe=MEI_EXE)
    fake = FakeRegistry().install(monkeypatch)
    assert set_enabled(True) is False
    assert fake.writes == []


def test_set_enabled_true_returns_false_in_a_source_run(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    fake = FakeRegistry().install(monkeypatch)
    assert set_enabled(True) is False
    assert fake.writes == []


def test_set_enabled_true_clears_a_task_manager_disable(monkeypatch):
    """The write-side counterpart of the anti-lying test: with the disable flag
    left in place, is_enabled() would report False right after the guard ticked
    the box."""
    _freeze(monkeypatch)
    fake = FakeRegistry({
        (RUN_KEY, VALUE_NAME): f'"{FROZEN_EXE}"',
        (STARTUP_APPROVED_KEY, VALUE_NAME): b"\x03" + b"\x00" * 11,
    }).install(monkeypatch)
    assert set_enabled(True) is True
    assert (STARTUP_APPROVED_KEY, VALUE_NAME) not in fake.values
    assert is_enabled() is True


def test_set_enabled_false_deletes_the_run_value(monkeypatch):
    fake = FakeRegistry({(RUN_KEY, VALUE_NAME): f'"{FROZEN_EXE}"'}).install(monkeypatch)
    assert set_enabled(False) is True
    assert (RUN_KEY, VALUE_NAME) not in fake.values
    assert is_enabled() is False


def test_set_enabled_false_is_idempotent_when_already_absent(monkeypatch):
    FakeRegistry().install(monkeypatch)
    assert set_enabled(False) is True  # already off == the requested state


def test_set_enabled_returns_false_when_the_write_raises(monkeypatch):
    # main.py:437-440 rule: a nicety must never become a startup dependency.
    _freeze(monkeypatch)
    fake = FakeRegistry().install(monkeypatch)
    fake.write_error = PermissionError("access denied")
    assert set_enabled(True) is False  # returns, does not raise


def test_set_enabled_returns_false_when_the_delete_raises(monkeypatch):
    fake = FakeRegistry({(RUN_KEY, VALUE_NAME): f'"{FROZEN_EXE}"'}).install(monkeypatch)
    fake.delete_error = PermissionError("access denied")
    assert set_enabled(False) is False
    assert (RUN_KEY, VALUE_NAME) in fake.values  # unchanged


def test_set_enabled_true_survives_an_unremovable_approval_flag(monkeypatch):
    """Clearing StartupApproved is best effort, but the RESULT must stay
    truthful: if the disable flag cannot be removed, autostart is still off and
    set_enabled must say so."""
    _freeze(monkeypatch)
    fake = FakeRegistry({
        (STARTUP_APPROVED_KEY, VALUE_NAME): b"\x03" + b"\x00" * 11,
    }).install(monkeypatch)
    fake.delete_error = PermissionError("access denied")
    assert set_enabled(True) is False
    assert (RUN_KEY, VALUE_NAME, f'"{FROZEN_EXE}"') in fake.writes


def test_set_enabled_reports_failure_when_the_write_silently_does_nothing(monkeypatch):
    """set_enabled's contract is 'did the change take effect', not 'did the
    call return'. A no-op write must report False."""
    _freeze(monkeypatch)
    fake = FakeRegistry().install(monkeypatch)
    monkeypatch.setattr(autostart, "_reg_write",
                        lambda subkey, name, value: fake.writes.append((subkey, name, value)))
    assert set_enabled(True) is False


# --- wiring sanity ----------------------------------------------------------

def test_key_paths_are_hkcu_relative_and_correct():
    # winreg.OpenKey(HKEY_CURRENT_USER, ...) takes a RELATIVE subkey; a
    # "HKEY_CURRENT_USER\..." prefix here would silently never match.
    assert RUN_KEY == r"Software\Microsoft\Windows\CurrentVersion\Run"
    assert STARTUP_APPROVED_KEY == (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run")
    for key in (RUN_KEY, STARTUP_APPROVED_KEY):
        assert not key.upper().startswith("HKEY")


def test_run_and_startup_approved_share_one_value_name():
    # Explorer keys the approval blob by the SAME value name as the Run entry;
    # a mismatch is exactly the bug this module is written to avoid.
    assert VALUE_NAME == "SDPRSWebcam"
