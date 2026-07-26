# sdprs/webcam_client/single_instance.py
"""Windows named-mutex single-instance guard.

Two copies of this client fight over the same DSHOW camera handles. Because the
onefile exe shows nothing for ~20s while it extracts, an operator double-clicking
during the wait is the NORMAL case -- so refusing the second launch matters.

ONEFILE TWO-PID TRAP: a onefile exe runs a small bootloader PARENT that extracts
the payload, then a large CHILD that is the real app. This module is imported by
the package, so it runs in the CHILD -- one mutex per real instance, which is
correct. Do NOT "fix" this by hoisting acquisition into app.py.
"""
import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger("webcam_client.single_instance")

_ERROR_FILE_NOT_FOUND = 2
_ERROR_ACCESS_DENIED = 5
_ERROR_ALREADY_EXISTS = 183
_SYNCHRONIZE = 0x00100000

# Unqualified; SingleInstance adds the Global\ / Local\ prefix itself.
DEFAULT_BASE_NAME = "SDPRSWebcamClient"

# use_last_error=True routes the Win32 error into ctypes.get_last_error(), which
# a later ctypes call cannot clobber -- calling kernel32.GetLastError() directly
# is a classic source of flaky results here.
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
# HANDLE is pointer-sized; leaving the default c_int restype TRUNCATES it on
# 64-bit and then CloseHandle fails on the truncated value.
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.OpenMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def _try_create(name):
    """Create the named mutex. Returns (handle_or_None, already_existed, err)."""
    ctypes.set_last_error(0)
    handle = _kernel32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if handle and err == _ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None, True, err
    if handle:
        return handle, False, 0
    return None, False, err


def _exists(name) -> bool:
    """True if the named mutex is present.

    ACCESS_DENIED here means it EXISTS but this account may not open it (another
    session created it). Only FILE_NOT_FOUND proves genuine absence.
    """
    ctypes.set_last_error(0)
    handle = _kernel32.OpenMutexW(_SYNCHRONIZE, False, name)
    err = ctypes.get_last_error()
    if handle:
        _kernel32.CloseHandle(handle)
        return True
    return err != _ERROR_FILE_NOT_FOUND


class SingleInstance:
    def __init__(self, base_name: str = DEFAULT_BASE_NAME):
        self._global_name = f"Global\\{base_name}"
        self._local_name = f"Local\\{base_name}"
        self._handle = None

    def acquire(self) -> bool:
        """True if this process now owns the single-instance slot.

        Tries Global\\ first: the camera is a MACHINE resource, so a second
        instance in another login/RDP session must also be refused. Falls back to
        Local\\ when the account lacks SeCreateGlobalPrivilege.

        Fails OPEN if both namespaces error -- a guard bug must never keep the
        monitoring client off the air.
        """
        handle, existed, err = _try_create(self._global_name)
        if existed:
            return False
        if handle:
            self._handle = handle
            return True

        # Global create failed. ACCESS_DENIED is ambiguous: either the object
        # exists and belongs to another session (refuse!) or we lack the
        # privilege to create a global object (fall back). Disambiguate.
        if err == _ERROR_ACCESS_DENIED and _exists(self._global_name):
            return False

        logger.info("Global mutex unavailable (err=%s); using session-local", err)
        handle, existed, err = _try_create(self._local_name)
        if existed:
            return False
        if handle:
            self._handle = handle
            return True

        logger.warning("Mutex creation failed (err=%s); allowing launch", err)
        return True

    def release(self) -> None:
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None
