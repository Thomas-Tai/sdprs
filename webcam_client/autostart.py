# sdprs/webcam_client/autostart.py
"""Start-at-logon for the webcam client, via HKCU\\...\\CurrentVersion\\Run.

WHY THE REGISTRY AND NOT A STARTUP-FOLDER SHORTCUT
    A real .lnk needs COM IShellLink, i.e. pywin32. The onefile payload sits at
    247.5 MB against a hard 250 MB budget, and onefile re-extracts the ENTIRE
    payload to %TEMP% on every launch -- payload size IS startup time (see
    buildconfig.py). winreg is stdlib and costs zero bytes.

    The cost of that choice, stated so nobody re-derives it: a Startup-folder
    implementation could be tested against REAL artifacts with the
    monkeypatch.setenv("APPDATA", tmp_path) pattern used in tests/test_config.py.
    winreg has no injectable root, so it can only ever be tested against a
    monkeypatched seam. This module mitigates that the way buildconfig.py
    mitigates the un-importable build.spec: every DECISION lives in a pure
    function with real tests (build_run_command, approval_is_enabled), and the
    three winreg shims below are literal one-liners with no logic in them --
    there is nothing in the untested layer left to be wrong.

THE "CHECKBOX CAN LIE" PROBLEM -- CHOSEN APPROACH: READ StartupApproved TOO
    Task Manager -> Startup -> Disable does NOT remove the Run value. It writes
    a flag to ...\\Explorer\\StartupApproved\\Run and leaves Run in place. A
    checkbox that reads only Run therefore renders ON while autostart is
    actually OFF.

    The two options were (a) read StartupApproved as well, or (b) refuse to
    report state at all. (b) was rejected because a checkbox has no way to
    render "unknown" to a security guard -- it is on or it is off, and an
    unchecked box that means "unknown" is the same lie in the other direction.
    So is_enabled() reads both keys, and set_enabled(True) clears the disable
    flag (leaving it would make is_enabled() report False the instant after the
    guard ticked the box). This whole branch exists because the client's status
    indicators were lying to the operator; that standard applies here too.

    Blob format: first byte is the flag -- even = enabled, odd (0x03 is what
    Task Manager writes) = disabled by the user. An absent value means enabled.

THREE TRAPS BAKED INTO build_run_command()
    1. NEVER write sys._MEIPASS. Under onefile that is a per-launch
       %TEMP%\\_MEIxxxxxx directory DELETED at process exit; a Run value
       pointing there works once and then silently fails at every subsequent
       boot, logging nothing anywhere a guard would look. sys.argv[0] is no
       better (it can be relative). sys.executable is the correct value --
       PyInstaller sets it to the real exe path.
    2. Guard on getattr(sys, "frozen", False). In a source run sys.executable
       is python.exe, and writing that gives the user a bare interpreter window
       at every logon.
    3. Quote the path. A REG_SZ Run value is parsed as a command line and the
       install path contains a space ("C:\\Program Files\\...").

FAILURE POLICY
    set_enabled() returns False; it never raises. Same rule main.py:437-440
    applies to setup_logging(): a nicety must never become a startup dependency,
    and a failed registry write must never fail the config save.
"""
import logging
import ntpath
import sys
from typing import Optional

# Windows-only in production. The guarded import keeps the module importable
# (and its pure functions testable) anywhere; if winreg is missing the shims
# below raise AttributeError on None, which the callers already treat as
# "registry unavailable" -> is_enabled() False, set_enabled() False.
try:
    import winreg
except ImportError:  # pragma: no cover - not reachable on the target platform
    winreg = None

logger = logging.getLogger("webcam_client.autostart")

# Relative subkeys: winreg.OpenKey(HKEY_CURRENT_USER, ...) takes the path
# WITHOUT the hive prefix. HKCU, not HKLM -- HKLM needs admin, and the guard's
# account does not have it.
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_APPROVED_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run")

# Explorer keys the approval blob by the SAME value name as the Run entry, so
# both keys must use this one constant. Matches config._APP_NAME (kept as a
# literal rather than imported: config.py pulls in ctypes/DPAPI, and this
# module stays cheap to import).
VALUE_NAME = "SDPRSWebcam"

_MEI_PREFIX = "_MEI"


# --- pure decisions (the tested layer) ---------------------------------------

def build_run_command(executable: str, frozen: bool) -> Optional[str]:
    """The exact string to store as the Run value, or None if autostart must
    not be configured at all.

    Pure: no sys reads, no filesystem, no registry. The caller passes
    sys.executable and getattr(sys, "frozen", False).

    Returns None -- refusing outright -- rather than trying to repair a bad
    path. Every rejection here is a path that would produce a Run entry which
    fails SILENTLY at the next logon, and a silent failure is strictly worse
    for a guard than "autostart could not be turned on". The _MEI check is
    deliberately over-eager (any path component starting with _MEI, any
    casing): a false refusal is visible and recoverable, a false acceptance is
    neither.
    """
    if not frozen:
        return None                     # trap 2: python.exe is not the app
    if not executable or not executable.strip():
        return None
    path = executable.strip().strip('"')
    if not path:
        return None
    if _has_mei_component(path):
        return None                     # trap 1: gone by the next boot
    if not ntpath.isabs(path):
        # A Run value resolves against an unpredictable logon CWD -- same
        # silent-failure class as _MEI.
        return None
    return f'"{path}"'                  # trap 3: the path contains spaces


def _has_mei_component(path: str) -> bool:
    """True if any component of ``path`` looks like a PyInstaller onefile
    extraction directory."""
    for part in path.replace("/", "\\").split("\\"):
        if part.upper().startswith(_MEI_PREFIX):
            return True
    return False


def approval_is_enabled(blob) -> bool:
    """Interpret a StartupApproved\\Run blob. Absent/empty/unreadable == enabled
    (Windows' own default); otherwise the first byte's parity decides: even =
    enabled, odd = disabled by the user.

    An unreadable blob is indistinguishable from an absent one, so it must not
    be allowed to invent a "disabled" the user never asked for.
    """
    try:
        if not blob:
            return True
        return blob[0] % 2 == 0
    except Exception:
        return True


# --- winreg shims (the untestable layer: three calls, zero logic) ------------
# Each raises on any failure. All interpretation lives above.

def _reg_read(subkey: str, name: str):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
        return winreg.QueryValueEx(key, name)[0]


def _reg_write(subkey: str, name: str, value: str) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _reg_delete(subkey: str, name: str) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0,
                        winreg.KEY_SET_VALUE) as key:
        winreg.DeleteValue(key, name)


# --- public API --------------------------------------------------------------

def is_enabled() -> bool:
    """True only if the app will ACTUALLY start at the next logon.

    Reads both keys: a present Run value that StartupApproved marks disabled is
    reported as False. See the module docstring for why.
    """
    try:
        _reg_read(RUN_KEY, VALUE_NAME)
    except Exception:
        return False                    # absent, or the registry is unreachable
    try:
        blob = _reg_read(STARTUP_APPROVED_KEY, VALUE_NAME)
    except OSError:
        # Absent is the overwhelmingly common case here (nobody ever opened
        # Task Manager) and Windows treats absent as enabled. Reaching this
        # line at all proves the first read worked, so the registry is fine.
        return True
    return approval_is_enabled(blob)


def set_enabled(on: bool) -> bool:
    """Turn autostart on or off. Returns whether the requested state is now the
    REAL state -- verified by re-reading, not assumed from a call that returned.

    Never raises: a failed registry write must not fail the config save
    (main.py:437-440).
    """
    try:
        if on:
            command = build_run_command(sys.executable,
                                        getattr(sys, "frozen", False))
            if command is None:
                logger.warning(
                    "Autostart not configured: unusable executable path "
                    "(frozen=%s, executable=%r)",
                    getattr(sys, "frozen", False), sys.executable)
                return False
            _reg_write(RUN_KEY, VALUE_NAME, command)
            # Enabling means enabling. If the user (or a previous admin) had
            # disabled the entry via Task Manager, the Run value alone leaves
            # autostart off -- and is_enabled() would correctly, uselessly,
            # report False right after the guard ticked the box. Best effort:
            # the value is usually absent, which is not an error.
            try:
                _reg_delete(STARTUP_APPROVED_KEY, VALUE_NAME)
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning("Could not clear the StartupApproved flag: %s", e)
        else:
            try:
                _reg_delete(RUN_KEY, VALUE_NAME)
            except FileNotFoundError:
                pass                    # already absent == already disabled
    except Exception as e:
        logger.warning("Autostart set_enabled(%s) failed: %s", on, e)
        return False
    return is_enabled() is bool(on)
