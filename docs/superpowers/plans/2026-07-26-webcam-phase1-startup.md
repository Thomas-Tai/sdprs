# Webcam Client Phase 1 — Startup Acceleration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut `SDPRS_Webcam.exe` warm start from 39.4 s to ≤20 s, and replace ~40 s of blank screen with a splash inside 3 s — without changing any existing UI layout.

**Architecture:** Two independent tracks. (1) *Packaging* — the onefile payload is 409 MB and is re-extracted to `%TEMP%` on every launch, so payload size **is** startup time; we cut it to ≤250 MB (**revised from the ≤200 MB drafted here** — see spec §5.3 and the Task 6 Step 3 note below) by swapping ffmpeg full→essentials and dropping binaries this client provably never calls, then add a PyInstaller `Splash` so the bootloader paints something during extraction. (2) *Runtime* — a named-mutex single-instance guard, rotating file logging with API-key redaction, and reordering `main()` so the tray icon appears before cameras are opened.

**Tech Stack:** Python 3.14, PyInstaller 6.21, ctypes/kernel32, `logging.handlers.RotatingFileHandler`, Pillow (asset generation only), pytest.

## Global Constraints

- **Packaging stays onefile.** No `COLLECT(...)`. Standing user requirement from [[2026-07-25-webcam-settings-ux-design]]; `test_build_spec_stays_onefile` already pins it.
- **`upx=False` stays.** Pinned by `test_build_spec_disables_upx_for_faster_launch`.
- **Python invocation is `/c/Python314/python`** — there is no `python3` alias.
- **pytest must be run per-file from `webcam_client/`, with `-p no:cacheprovider`.** A bare `pytest` from the repo root fails with *"path cannot contain [] parametrization"* because `[Cloud]` in the absolute path is parsed as a test id. Correct form:
  `cd webcam_client && /c/Python314/python -m pytest tests/test_x.py -q -p no:cacheprovider`
- **Git root is `sdprs/`**, not the parent directory. Run every git command from `sdprs/`.
- **Branch: `feat/webcam-startup-and-guard-ux`** (already created, spec committed at `432f09f`).
- **Write/Edit tools must use absolute Windows paths** beginning `C:\D\WorkSpace\[Cloud]_Company_Sync\...`.
- **The API key must never appear in the log file.** New risk introduced by this phase; Task 2 tests it.
- **Never hardcode credentials.** `Msc@***` and `MSC-***` must not appear anywhere; `broker.emqx.io` must not appear on a production path.
- **Do not add any downlink command interface to edge devices** beyond the existing `stream_start` / `stream_stop`. This phase is client-side only and touches none of it.

## File Structure

| File | Responsibility |
|---|---|
| `webcam_client/single_instance.py` | *new* — named-mutex guard, fail-open |
| `webcam_client/logging_setup.py` | *new* — rotating file handler + secret redaction |
| `webcam_client/assets/make_assets.py` | *new* — regenerates icon/splash from code |
| `webcam_client/assets/sdprs.ico`, `splash.png` | *new* — generated, committed |
| `webcam_client/tools/payload_audit.py` | *new* — per-component payload sizing (makes the payload criterion re-runnable) |
| `webcam_client/main.py` | *modify* — startup order, splash close |
| `webcam_client/build.spec` | *modify* — ffmpeg resolution + size guard, binary excludes, Splash, icon |
| `webcam_client/tests/test_single_instance.py` | *new* |
| `webcam_client/tests/test_logging_setup.py` | *new* |
| `webcam_client/tests/test_packaging.py` | *modify* — pin the new spec guarantees |

---

### Task 1: Single-instance guard

**Files:**
- Create: `webcam_client/single_instance.py`
- Test: `webcam_client/tests/test_single_instance.py`

**Interfaces:**
- Consumes: nothing
- Produces: `SingleInstance(base_name: str = DEFAULT_BASE_NAME)` with `.acquire() -> bool` and `.release() -> None`; module constant `DEFAULT_BASE_NAME: str`

**Why fail-open:** a bug in the guard must never stop the monitoring client from running. If the mutex machinery itself fails, we allow the launch.

**Why Global-then-Local:** the camera is a *machine* resource, so `Global\` is the semantically correct scope — it also blocks a second instance started from a second login or RDP session, which would fight for the same DSHOW device. But `Global\` needs `SeCreateGlobalPrivilege`, which a standard (non-admin) guard account may not hold. So: try `Global\`, fall back to `Local\`.

**The ambiguity that must be handled:** `CreateMutexW` on a `Global\` name returns `ERROR_ACCESS_DENIED` (5) in **two different** situations — (a) the object exists but was created by another session and we can't open it → *another instance is running, refuse*; (b) we lack the privilege to create a global object → *fall back to Local*. These demand opposite responses, so `acquire()` disambiguates with `OpenMutexW`: only `ERROR_FILE_NOT_FOUND` (2) proves the object is genuinely absent.

- [ ] **Step 1: Write the failing test**

Create `webcam_client/tests/test_single_instance.py`:

```python
# webcam_client/tests/test_single_instance.py
"""A second copy of the client fights the first for the same DSHOW camera
handles. Since the exe gives no feedback during onefile extraction, an operator
double-clicking while they wait is NORMAL behaviour -- so this guard is load
bearing, not a nicety."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import webcam_client.single_instance as si_mod
from webcam_client.single_instance import SingleInstance, DEFAULT_BASE_NAME


def _unique(tag):
    # Unique per test AND per run, so a crashed prior run cannot leak a held
    # mutex into this one and make the suite flaky.
    return f"SDPRSTest_{tag}_{os.getpid()}"


def test_first_acquire_succeeds():
    si = SingleInstance(_unique("first"))
    try:
        assert si.acquire() is True
    finally:
        si.release()


def test_second_acquire_fails_while_first_holds():
    base = _unique("second")
    a, b = SingleInstance(base), SingleInstance(base)
    try:
        assert a.acquire() is True
        assert b.acquire() is False, "second instance must be refused"
    finally:
        a.release()
        b.release()


def test_slot_is_reusable_after_release():
    base = _unique("reuse")
    a = SingleInstance(base)
    assert a.acquire() is True
    a.release()
    b = SingleInstance(base)
    try:
        assert b.acquire() is True, "releasing must free the slot"
    finally:
        b.release()


def test_release_without_acquire_is_safe():
    SingleInstance(_unique("norelease")).release()  # must not raise


def test_falls_back_to_local_when_global_is_not_permitted(monkeypatch):
    """A standard (non-admin) account may lack SeCreateGlobalPrivilege. That
    must degrade to a session-local guard, NOT disable the guard."""
    tried = []
    real = si_mod._try_create

    def fake(name):
        tried.append(name)
        if name.startswith("Global\\"):
            return None, False, si_mod._ERROR_ACCESS_DENIED
        return real(name)

    monkeypatch.setattr(si_mod, "_try_create", fake)
    # Global create was denied AND the object does not exist -> privilege issue.
    monkeypatch.setattr(si_mod, "_exists", lambda name: False)

    si = SingleInstance(_unique("fallback"))
    try:
        assert si.acquire() is True
        assert any(n.startswith("Global\\") for n in tried), "must try Global first"
        assert any(n.startswith("Local\\") for n in tried), "must fall back to Local"
    finally:
        si.release()


def test_access_denied_on_an_EXISTING_global_refuses_the_launch(monkeypatch):
    """The dangerous ambiguity: ACCESS_DENIED means either 'no privilege' or
    'another session owns it'. When the object EXISTS, a second instance must be
    refused -- falling back to Local here would let two copies fight the camera."""
    monkeypatch.setattr(
        si_mod, "_try_create",
        lambda name: (None, False, si_mod._ERROR_ACCESS_DENIED))
    monkeypatch.setattr(si_mod, "_exists", lambda name: True)
    assert SingleInstance(_unique("denied")).acquire() is False


def test_fails_open_when_both_namespaces_error(monkeypatch):
    """A guard bug must never keep the monitoring client off the air."""
    monkeypatch.setattr(si_mod, "_try_create", lambda name: (None, False, 1337))
    monkeypatch.setattr(si_mod, "_exists", lambda name: False)
    assert SingleInstance(_unique("failopen")).acquire() is True


def test_exists_is_false_for_an_absent_object():
    assert si_mod._exists(f"Local\\{_unique('absent')}") is False


def test_default_base_name_is_unqualified():
    # The class adds the Global\ / Local\ prefixes itself.
    assert not DEFAULT_BASE_NAME.startswith(("Global\\", "Local\\"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_single_instance.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'webcam_client.single_instance'`

- [ ] **Step 3: Write minimal implementation**

Create `webcam_client/single_instance.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_single_instance.py -q -p no:cacheprovider`
Expected: PASS — 9 passed

Note which namespace won: if the log line `Global mutex unavailable ... using session-local` appears during a real run, this account lacks `SeCreateGlobalPrivilege` and the guard is session-scoped. That is the accepted fallback, not a failure — record it in the Task 6 notes.

- [ ] **Step 5: Commit**

```bash
cd sdprs
git add webcam_client/single_instance.py webcam_client/tests/test_single_instance.py
git commit -m "feat(webcam): single-instance mutex guard (Global with Local fallback, fail-open)"
```

---

### Task 2: Rotating file logging with API-key redaction

**Files:**
- Create: `webcam_client/logging_setup.py`
- Test: `webcam_client/tests/test_logging_setup.py`

**Interfaces:**
- Consumes: `webcam_client.config.get_config_dir() -> Path`
- Produces: `setup_logging(level=logging.INFO) -> logging.Handler`, `add_secret(secret: str) -> None`, `get_log_dir() -> Path`, `LOG_FILENAME`, `REDACTED`

**Ordering note:** logging is configured *before* `load_config()` runs, so the API key is not known at setup time. `add_secret()` is therefore a separate call made once the config is loaded. The filter lives on the **handler**, so it covers records from every module.

- [ ] **Step 1: Write the failing test**

Create `webcam_client/tests/test_logging_setup.py`:

```python
# webcam_client/tests/test_logging_setup.py
"""console=False means basicConfig()'s stdout handler writes into a void -- when
an operator says "it stopped working" there is no artifact. These tests pin the
file sink AND the security rule that the API key never reaches it."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import webcam_client.logging_setup as ls


def _fresh(monkeypatch, tmp_path):
    """Point the log dir at tmp_path and reset module state between tests."""
    monkeypatch.setattr(ls, "get_config_dir", lambda: tmp_path)
    ls.reset_for_tests()
    return tmp_path / "logs" / ls.LOG_FILENAME


def test_creates_log_file_and_writes_records(monkeypatch, tmp_path):
    logfile = _fresh(monkeypatch, tmp_path)
    handler = ls.setup_logging()
    logging.getLogger("webcam_client.test").info("hello from the client")
    handler.flush()
    assert logfile.exists()
    assert "hello from the client" in logfile.read_text(encoding="utf-8")


def test_api_key_is_redacted(monkeypatch, tmp_path):
    logfile = _fresh(monkeypatch, tmp_path)
    handler = ls.setup_logging()
    ls.add_secret("SUPERSECRETKEY123")
    logging.getLogger("webcam_client.test").warning(
        "auth failed for key SUPERSECRETKEY123")
    handler.flush()
    body = logfile.read_text(encoding="utf-8")
    assert "SUPERSECRETKEY123" not in body, "API KEY LEAKED INTO THE LOG FILE"
    assert ls.REDACTED in body


def test_redaction_survives_lazy_percent_args(monkeypatch, tmp_path):
    # logger.warning("key %s", secret) formats at emit time -- redacting only
    # record.msg without consuming args would let the secret through.
    logfile = _fresh(monkeypatch, tmp_path)
    handler = ls.setup_logging()
    ls.add_secret("LAZYSECRET999")
    logging.getLogger("webcam_client.test").warning("key is %s", "LAZYSECRET999")
    handler.flush()
    assert "LAZYSECRET999" not in logfile.read_text(encoding="utf-8")


def test_empty_secret_is_ignored(monkeypatch, tmp_path):
    # An unconfigured client has api_key == "". Redacting "" would replace
    # every character boundary in every message.
    logfile = _fresh(monkeypatch, tmp_path)
    handler = ls.setup_logging()
    ls.add_secret("")
    logging.getLogger("webcam_client.test").info("perfectly normal message")
    handler.flush()
    assert "perfectly normal message" in logfile.read_text(encoding="utf-8")


def test_setup_is_idempotent(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    first = ls.setup_logging()
    second = ls.setup_logging()
    assert first is second, "repeated setup must not stack duplicate handlers"


def test_rotation_is_configured(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    handler = ls.setup_logging()
    assert handler.maxBytes == ls.MAX_BYTES
    assert handler.backupCount == ls.BACKUP_COUNT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_logging_setup.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'webcam_client.logging_setup'`

- [ ] **Step 3: Write minimal implementation**

Create `webcam_client/logging_setup.py`:

```python
# sdprs/webcam_client/logging_setup.py
"""Rotating file logging for the frozen client.

The exe is built console=False, so logging.basicConfig()'s stdout handler writes
into a void -- there is no artifact to inspect when an operator reports a fault.
Logs land in %APPDATA%\\SDPRSWebcam\\logs\\webcam.log.

SECURITY: the API key must never reach this file. _RedactFilter is installed on
the HANDLER so it scrubs records from every module, and it consumes record.args
so lazy %-formatting cannot smuggle a secret past it.
"""
import logging
import logging.handlers

from .config import get_config_dir

LOG_FILENAME = "webcam.log"
# A failing client logs a warning per failed snapshot push (~1Hz), which rolls a
# small log in under an hour and destroys the ORIGIN of the fault -- the part
# that is actually diagnostic. 2MB x 5 = 10MB buys roughly a day of noisy
# failure and is trivial on any disk.
MAX_BYTES = 2_000_000
BACKUP_COUNT = 5
REDACTED = "***REDACTED***"

_handler = None
_redactor = None


def get_log_dir():
    return get_config_dir() / "logs"


class _RedactFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self._secrets = []

    def add(self, secret: str) -> None:
        # An unconfigured client has api_key == "": redacting the empty string
        # would match at every character boundary and destroy every message.
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    def filter(self, record):
        if not self._secrets:
            return True
        msg = record.getMessage()          # applies args NOW
        hit = False
        for s in self._secrets:
            if s in msg:
                msg = msg.replace(s, REDACTED)
                hit = True
        if hit:
            record.msg = msg
            record.args = ()               # already applied above
        return True


def setup_logging(level=logging.INFO):
    """Install the rotating file handler on the root logger. Idempotent."""
    global _handler, _redactor
    if _handler is not None:
        return _handler
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    _redactor = _RedactFilter()
    handler = logging.handlers.RotatingFileHandler(
        log_dir / LOG_FILENAME, maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    handler.addFilter(_redactor)
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    _handler = handler
    return handler


def add_secret(secret: str) -> None:
    """Register a value that must never appear in the log.

    Separate from setup_logging() because logging is configured BEFORE
    load_config() runs, so the API key is not known yet at that point.
    """
    if _redactor is not None:
        _redactor.add(secret)


def reset_for_tests() -> None:
    """Detach the handler so each test starts from a clean root logger."""
    global _handler, _redactor
    if _handler is not None:
        logging.getLogger().removeHandler(_handler)
        _handler.close()
    _handler = None
    _redactor = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_logging_setup.py -q -p no:cacheprovider`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
cd sdprs
git add webcam_client/logging_setup.py webcam_client/tests/test_logging_setup.py
git commit -m "feat(webcam): rotating file log with API-key redaction"
```

---

### Task 3: Startup ordering in `main.py`

**Files:**
- Modify: `webcam_client/main.py:11-13` (logging), `:59-89` (`main()`)
- Test: `webcam_client/tests/test_main_dispatch.py` (extend)

**Interfaces:**
- Consumes: `SingleInstance` (constructed with its default base name), `setup_logging`, `add_secret`
- Produces: `_close_splash() -> None` (idempotent, safe when not frozen)

**Two behaviour changes:**
1. Tray icon is created and started **before** `controller.start_engines()`. Opening cameras costs 0.5–2 s each and currently delays the only sign of life (S6). `AppController.__init__` does not touch hardware, so constructing it early is safe — only `start_engines()` opens cameras.
2. `_close_splash()` is called at both points where the first real UI appears: before the first-run wizard, and after `tray.start()` on the normal path.

- [ ] **Step 1: Write the failing test**

Append to `webcam_client/tests/test_main_dispatch.py`:

```python
def test_close_splash_is_safe_without_pyi_splash():
    """pyi_splash only exists inside a frozen build that declared a Splash. In
    dev, and in any build without one, importing it raises -- _close_splash must
    swallow that rather than killing startup."""
    import webcam_client.main as m
    m._close_splash()
    m._close_splash()  # idempotent


def test_close_splash_swallows_a_failing_close(monkeypatch):
    """A splash that errors on close must not take the app down with it."""
    import types
    import webcam_client.main as m

    fake = types.ModuleType("pyi_splash")

    def boom():
        raise RuntimeError("splash already gone")

    fake.close = boom
    monkeypatch.setitem(sys.modules, "pyi_splash", fake)
    monkeypatch.setattr(m, "_splash_closed", False)
    m._close_splash()  # must not raise


def test_tray_starts_before_engines(monkeypatch):
    """S6: opening cameras takes 0.5-2s each; the tray icon is the only sign of
    life, so it must exist BEFORE engines start, not after."""
    import webcam_client.main as m

    order = []

    class FakeCtrl:
        def __init__(self, cfg):
            self._config = cfg

        @property
        def config(self):
            return self._config

        def start_engines(self):
            order.append("engines")

        def shutdown(self):
            pass

        pause_all = resume_all = lambda self: None

    class FakeTray:
        def __init__(self, **kw):
            pass

        def start(self):
            order.append("tray")

        def set_status(self, ok):
            pass

    monkeypatch.setattr(m, "AppController", FakeCtrl)
    monkeypatch.setattr(m, "TrayApp", FakeTray)
    monkeypatch.setattr(m, "load_config", lambda: {
        "server_url": "http://x", "api_key": "k",
        "cameras": [{"device_index": 0, "enabled": True, "node_id": "n"}]})
    monkeypatch.setattr(m, "is_first_run", lambda: False)
    monkeypatch.setattr(m, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(m, "add_secret", lambda s: None)
    monkeypatch.setattr(m, "_acquire_single_instance", lambda: True)
    monkeypatch.setattr(m, "_running", False)  # exit the dispatch loop at once

    m.main()
    assert order == ["tray", "engines"], f"got {order}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_main_dispatch.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: module 'webcam_client.main' has no attribute '_close_splash'`

- [ ] **Step 3: Write minimal implementation**

Replace `webcam_client/main.py` lines 1–13 (imports + `basicConfig`) with:

```python
# sdprs/webcam_client/main.py
import logging
import queue
import signal

from .config import load_config, save_config, is_first_run
from .app_controller import AppController
from .gui.setup_wizard import run_setup_wizard
from .gui.tray_app import TrayApp
from .logging_setup import setup_logging, add_secret
from .single_instance import SingleInstance

logger = logging.getLogger("webcam_client.main")

_running = True
_splash_closed = False
_instance = SingleInstance()


def _close_splash() -> None:
    """Dismiss the PyInstaller splash once real UI is up. Idempotent.

    pyi_splash is injected ONLY into a frozen build that declared a Splash, so
    the import failing is the normal dev case, not an error.
    """
    global _splash_closed
    if _splash_closed:
        return
    _splash_closed = True
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass


def _acquire_single_instance() -> bool:
    """False when another copy already owns the slot."""
    return _instance.acquire()
```

Then replace the body of `main()` (currently `main.py:59-89`) down to and including the `logger.info(f"SDPRS Webcam Client running ...")` line with:

```python
def main():
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    if not _acquire_single_instance():
        _close_splash()
        logger.info("Another instance is already running; exiting")
        try:
            from tkinter import messagebox
            messagebox.showinfo("SDPRS 監控", "SDPRS 監控已在執行中。")
        except Exception:
            pass
        return

    setup_logging()

    config = load_config()
    add_secret(config.get("api_key", ""))
    if is_first_run() or not config.get("server_url"):
        _close_splash()                    # wizard is the first real UI
        new_config = run_setup_wizard(config, mode="first-run")
        if new_config is None:
            logger.info("Setup cancelled, exiting")
            return
        config = new_config
        save_config(config)
        add_secret(config.get("api_key", ""))

    enabled = [c for c in config.get("cameras", []) if c.get("enabled", True)]
    if not enabled:
        logger.error("No cameras configured")
        return

    controller = AppController(config)

    q: "queue.Queue[str]" = queue.Queue()
    tray = TrayApp(
        on_open_settings=lambda: q.put("OPEN_SETTINGS"),
        on_quit=lambda: q.put("QUIT"),
        on_pause=controller.pause_all,
        on_resume=controller.resume_all,
    )
    # S6: the tray icon is the ONLY sign of life, and start_engines() opens each
    # camera (0.5-2s apiece). Show the icon first, then do the slow work.
    # AppController.__init__ touches no hardware, so building it above is free.
    tray.start()
    tray.set_status(True)
    _close_splash()

    controller.start_engines()
    logger.info(f"SDPRS Webcam Client running ({len(enabled)} cameras)")
```

Leave the `while running and _running:` dispatch loop and everything after it unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_main_dispatch.py -q -p no:cacheprovider`
Expected: PASS — 9 passed

Then confirm nothing else regressed:
Run: `cd webcam_client && for f in tests/test_*.py; do /c/Python314/python -m pytest "$f" -q -p no:cacheprovider; done`
Expected: every file passes

- [ ] **Step 5: Commit**

```bash
cd sdprs
git add webcam_client/main.py webcam_client/tests/test_main_dispatch.py
git commit -m "feat(webcam): tray before engines, file logging, single-instance at startup"
```

---

### Task 4: Payload slimming in `build.spec`

**Files:**
- Modify: `webcam_client/build.spec:9-43`
- Create: `webcam_client/tools/payload_audit.py`
- Test: `webcam_client/tests/test_packaging.py` (extend)

**Interfaces:**
- Consumes: nothing
- Produces: build-time env var contract `SDPRS_FFMPEG`; `tools/payload_audit.py` CLI taking a `PKG-00.toc` path

**Measured basis (2026-07-26):** payload 409.4 MB → `ffmpeg.EXE` 227.4 MB (55.5%), `cv2.pyd` 74.5 MB, `opencv_videoio_ffmpeg4130_64.dll` 28.6 MB, `libscipy_openblas64` 20.4 MB, `PIL\_avif.pyd` 7.8 MB.

**Prerequisite — install the essentials ffmpeg build (verified available):**

```bash
winget install Gyan.FFmpeg.Essentials
```

The build machine currently has `Gyan.FFmpeg` (the **full** 227 MB build) on PATH. With both installed, `shutil.which` may return either — which is exactly why the spec now prefers an explicit `SDPRS_FFMPEG` and warns loudly on an oversized binary.

- [ ] **Step 1: Write the failing test**

Append to `webcam_client/tests/test_packaging.py`:

```python
def test_build_spec_excludes_opencv_videoio_ffmpeg():
    """28.6MB of OpenCV's own ffmpeg backend, for video FILE i/o. This client
    only does VideoCapture(index, CAP_DSHOW) + resize/imencode/cvtColor/
    GaussianBlur/absdiff -- it never opens a video file."""
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "opencv_videoio_ffmpeg" in spec


def test_build_spec_excludes_pil_avif():
    """PIL is used for a 64x64 tray circle and one thumbnail. The AVIF codec is
    7.8MB extracted on every launch for nothing."""
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "_avif" in spec


def test_build_spec_filters_the_binaries_list():
    """The excludes must actually be applied to a.binaries -- naming them in a
    constant while never filtering would silently ship them anyway."""
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "a.binaries = [" in spec


def test_build_spec_warns_on_oversized_ffmpeg():
    """The 2026-07-25 round assumed ffmpeg was ~80MB and shipped a 227MB full
    build without noticing. Turn that silent size regression into a build-time
    warning."""
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "_FFMPEG_MAX_MB" in spec
    assert "SDPRS_FFMPEG" in spec, "build must allow an explicit ffmpeg override"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_packaging.py -q -p no:cacheprovider`
Expected: FAIL — 4 failed, 6 passed

- [ ] **Step 3: Write minimal implementation**

Replace `webcam_client/build.spec` lines 9–43 (from the `# Bundle ffmpeg...` comment through the closing paren of `Analysis(...)`) with:

```python
# --- ffmpeg -------------------------------------------------------------------
# onefile re-extracts the ENTIRE payload to %TEMP% on every launch, so payload
# size IS startup time. Measured 2026-07-26: the build machine's PATH ffmpeg was
# the 227MB *full* build = 55.5% of a 409MB payload, and nobody noticed because
# nothing checked. The `essentials` build (~85MB) has everything h264/HLS needs:
#     winget install Gyan.FFmpeg.Essentials
# Resolution order makes the choice explicit instead of "whatever is first on
# PATH", and an oversized binary now warns at build time.
_FFMPEG_MAX_MB = 120


def _resolve_ffmpeg():
    explicit = os.environ.get('SDPRS_FFMPEG')
    if explicit and Path(explicit).is_file():
        return explicit
    vendored = Path(SPECPATH) / 'vendor' / 'ffmpeg.exe'
    if vendored.is_file():
        return str(vendored)
    return shutil.which('ffmpeg')


_ffmpeg = _resolve_ffmpeg()
_binaries = []
if _ffmpeg:
    _binaries = [(_ffmpeg, '.')]
    _mb = Path(_ffmpeg).stat().st_size / 1e6
    if _mb > _FFMPEG_MAX_MB:
        print('=' * 78)
        print(f'[build.spec] WARNING: ffmpeg is {_mb:.0f} MB -- that is a FULL build.')
        print(f'[build.spec] It is re-extracted on EVERY launch. Expected <= {_FFMPEG_MAX_MB} MB.')
        print('[build.spec]   winget install Gyan.FFmpeg.Essentials')
        print('[build.spec]   set SDPRS_FFMPEG=<path to the essentials ffmpeg.exe>')
        print('=' * 78)
    else:
        print(f'[build.spec] ffmpeg {_mb:.0f} MB from {_ffmpeg}')
else:
    print('[build.spec] WARNING: ffmpeg not found on PATH; exe will require '
          'ffmpeg on the target PC PATH for live view (snapshots still work)')

# Binaries this client provably never calls. Each one is decompressed and written
# to %TEMP% on every single launch.
#   opencv_videoio_ffmpeg*  28.6MB  OpenCV's video-FILE i/o backend. We only use
#                                   VideoCapture(index, CAP_DSHOW) for live
#                                   capture plus resize/imencode/cvtColor/
#                                   GaussianBlur/absdiff. No file i/o anywhere.
#   _avif                    7.8MB  PIL AVIF codec. PIL draws a 64x64 tray circle
#                                   and one Tk thumbnail.
_EXCLUDED_BINARIES = ('opencv_videoio_ffmpeg', '_avif')

# Build the launcher (app.py), NOT the package module main.py. PyInstaller runs
# the entry as __main__, which has no parent package -- main.py's relative
# imports (`from .config import ...`) would then crash the exe at startup. app.py
# imports the package absolutely. pathex includes the package's PARENT dir so
# `import webcam_client` resolves and the whole package is collected (keeping
# every submodule's relative imports valid).
a = Analysis(
    ['app.py'],
    pathex=[str(Path(SPECPATH).parent)],
    binaries=_binaries,
    datas=[],
    hiddenimports=['webcam_client', 'cv2', 'numpy', 'httpx', 'pystray', 'PIL'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pandas', 'PIL._avif'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# `excludes` only reaches modules the analyser resolved as imports; these ship as
# plain DLL/pyd payload, so filter the binaries list directly. PyInstaller 6.x
# treats a.binaries as a plain list of (name, path, typecode) tuples.
_before = len(a.binaries)
a.binaries = [b for b in a.binaries
              if not any(p in b[0].lower() for p in _EXCLUDED_BINARIES)]
print(f'[build.spec] dropped {_before - len(a.binaries)} excluded binaries')
```

Also add `import os` to the imports at the top of `build.spec` (line 3 area), so it reads:

```python
import os
import shutil
import sys
from pathlib import Path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_packaging.py -q -p no:cacheprovider`
Expected: PASS — 10 passed

- [ ] **Step 5: Add the payload audit tool**

Create `webcam_client/tools/payload_audit.py`:

```python
"""Sum the on-disk size of everything PyInstaller put in the onefile payload,
grouped by component, so the <=200MB target stays verifiable instead of assumed.

    /c/Python314/python tools/payload_audit.py build/build/PKG-00.toc
"""
import ast
import collections
import os
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    raw = ast.literal_eval(f.read())
# A PKG TOC is an 11-tuple of build params; the (name, src, typecode) list is [2].
entries = raw[2] if isinstance(raw, tuple) else raw

groups, counts, biggest = collections.Counter(), collections.Counter(), []
for name, src, _typecode in entries:
    size = os.path.getsize(src) if src and os.path.isfile(src) else 0
    n = name.replace("\\", "/")
    if n.startswith("ffmpeg"):
        key = "ffmpeg.exe"
    elif n.startswith("cv2/"):
        key = "cv2"
    elif n.startswith("numpy"):
        key = "numpy"
    elif n.startswith("PIL"):
        key = "PIL/Pillow"
    elif n.startswith("tcl") or n.startswith("tk") or "_tkinter" in n:
        key = "tcl/tk (tkinter)"
    elif n.endswith(".pyz"):
        key = "PYZ (pure python)"
    elif n.startswith("python"):
        key = "CPython runtime"
    else:
        key = "other"
    groups[key] += size
    counts[key] += 1
    biggest.append((size, name))

total = sum(groups.values())
print(f"{'component':24s} {'MB':>9s} {'share':>7s} {'files':>7s}")
print("-" * 52)
for key, size in groups.most_common():
    print(f"{key:24s} {size/1e6:9.1f} {100*size/total:6.1f}% {counts[key]:7d}")
print("-" * 52)
print(f"{'TOTAL (uncompressed)':24s} {total/1e6:9.1f}")
print("\nTop 10 individual files:")
for size, name in sorted(biggest, reverse=True)[:10]:
    print(f"  {size/1e6:8.1f} MB  {name}")
```

- [ ] **Step 6: Commit**

```bash
cd sdprs
git add webcam_client/build.spec webcam_client/tools/payload_audit.py webcam_client/tests/test_packaging.py
git commit -m "perf(webcam): slim onefile payload (ffmpeg essentials, drop unused binaries)"
```

---

### Task 5: Splash screen and app icon

**Files:**
- Create: `webcam_client/assets/make_assets.py`, `webcam_client/assets/sdprs.ico`, `webcam_client/assets/splash.png`
- Modify: `webcam_client/build.spec` (add `Splash(...)`, wire into `EXE(...)`, set `icon=`)
- Test: `webcam_client/tests/test_packaging.py` (extend)

**Interfaces:**
- Consumes: `_close_splash()` from Task 3 (already wired — no `main.py` change needed here)
- Produces: `assets/splash.png` (420×240 PNG), `assets/sdprs.ico`

**Assets are generated from code**, not hand-drawn, so they are reproducible and reviewable. If real branding arrives later it simply replaces the two output files.

**CJK caveat:** PIL's default bitmap font cannot render Chinese — it draws boxes. `make_assets.py` looks for Microsoft JhengHei (`msjh.ttc`) and falls back to an ASCII-only splash if no CJK font is present, so the build never breaks on a machine without it.

- [ ] **Step 1: Write the failing test**

Append to `webcam_client/tests/test_packaging.py`:

```python
def test_assets_exist():
    assert (WEBCAM_DIR / "assets" / "sdprs.ico").is_file()
    assert (WEBCAM_DIR / "assets" / "splash.png").is_file()


def test_icon_16px_is_handtuned_not_a_downsample():
    """Naive downsampling of the 256px master closes the aperture into an
    unreadable dot and reduces the mount nub to a stray pixel -- at 16px, the
    size Windows uses in the taskbar. Pin that the .ico carries DISTINCT small
    artwork rather than a resample."""
    from PIL import Image

    ico = WEBCAM_DIR / "assets" / "sdprs.ico"
    with Image.open(ico) as im:
        im.size = (256, 256)          # ICO frame selection
        im.load()
        naive = im.convert("RGBA").resize((16, 16), Image.LANCZOS)
    with Image.open(ico) as im:
        im.size = (16, 16)
        im.load()
        actual = im.convert("RGBA")
    assert list(actual.getdata()) != list(naive.getdata()), \
        "16px frame is just a downsample -- hand-tuned artwork did not make it in"


def test_build_spec_declares_a_splash():
    """S4: console=False + ~20s onefile extraction = a completely blank screen.
    The bootloader paints the splash before Python starts."""
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "Splash(" in spec
    assert "splash.binaries" in spec, "onefile EXE must receive splash.binaries"


def test_build_spec_sets_an_icon():
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "icon=None" not in spec
    assert "sdprs.ico" in spec
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_packaging.py -q -p no:cacheprovider`
Expected: FAIL — 4 failed, 10 passed

- [ ] **Step 3: Write the asset generator and run it**

Create `webcam_client/assets/make_assets.py`:

```python
"""Regenerate the app icon and splash image.

    cd webcam_client && /c/Python314/python assets/make_assets.py

Generated from code so they are reproducible and diffable in review. Replace the
two output files directly if real branding becomes available.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
BG = (18, 32, 52)
ACCENT = (63, 138, 224)
TEXT = (238, 242, 248)
MUTED = (150, 168, 190)
# Small-size palette: the 256px navy body disappears against a dark taskbar once
# downsampled, and the thin ring closes up. Lift both for 16/24px.
BG_SMALL = (30, 52, 84)
ACCENT_SMALL = (99, 170, 246)

# PIL's default bitmap font renders CJK as boxes, so find a real CJK face.
# Absent one, the splash falls back to ASCII rather than shipping tofu.
_CJK_CANDIDATES = ["C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/msyh.ttc"]


def _font(size, prefer_cjk=True):
    if prefer_cjk:
        for path in _CJK_CANDIDATES:
            if Path(path).is_file():
                try:
                    return ImageFont.truetype(path, size), True
                except OSError:
                    pass
    try:
        return ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", size), False
    except OSError:
        return ImageFont.load_default(), False


def _icon_detailed():
    """256px master: full lens with mount nub. Reads well at 32px and up."""
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, 248, 248], radius=52, fill=BG)
    d.ellipse([76, 76, 180, 180], fill=ACCENT)          # lens
    d.ellipse([104, 104, 152, 152], fill=BG)            # aperture
    d.rounded_rectangle([112, 34, 144, 62], radius=8, fill=ACCENT)  # mount
    return img


def _icon_compact(size):
    """Hand-tuned 16/24px artwork.

    Naive downsampling of the master closes the aperture into a dot and reduces
    the mount nub to a stray pixel, so it reads as a blob rather than a camera.
    This drops the nub, thickens the ring, and lifts both colours for contrast on
    a dark taskbar. Drawn at 8x then downsampled -- drawing directly at 16px
    gives jagged, uneven strokes.
    """
    s = size * 8
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = s * 0.02
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=s * 0.22, fill=BG_SMALL)
    inset = s * 0.20
    d.ellipse([inset, inset, s - inset, s - inset],
              outline=ACCENT_SMALL, width=int(s * 0.15))
    return img.resize((size, size), Image.LANCZOS)


def make_icon():
    """Write a multi-resolution .ico with DIFFERENT artwork at small sizes.

    Pillow's ICO writer uses an entry from `append_images` verbatim when its size
    matches a requested size exactly, and otherwise resamples the master -- so
    16/24 come from _icon_compact and the rest from the 256px master.
    (Verified against Pillow 12.1.1 IcoImagePlugin._save.)
    """
    master = _icon_detailed()
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    master.save(HERE / "sdprs.ico", sizes=sizes,
                append_images=[_icon_compact(16), _icon_compact(24)])
    print("wrote sdprs.ico")


def make_splash():
    img = Image.new("RGB", (420, 240), BG)
    d = ImageDraw.Draw(img)
    d.ellipse([170, 40, 250, 120], outline=ACCENT, width=7)
    d.ellipse([196, 66, 224, 94], fill=ACCENT)
    title, _ = _font(30, prefer_cjk=False)
    d.text((210, 145), "SDPRS", font=title, fill=TEXT, anchor="mm")
    sub, is_cjk = _font(15)
    msg = "啟動中，請稍候…" if is_cjk else "Starting, please wait..."
    d.text((210, 178), msg, font=sub, fill=MUTED, anchor="mm")
    img.save(HERE / "splash.png")
    print(f"wrote splash.png (cjk={is_cjk})")


if __name__ == "__main__":
    make_icon()
    make_splash()
```

Run it:

```bash
cd webcam_client && /c/Python314/python assets/make_assets.py
```
Expected: `wrote sdprs.ico` / `wrote splash.png (cjk=True)`

- [ ] **Step 4: Wire the splash into `build.spec`**

Insert after the `pyz = PYZ(...)` line:

```python
# S4: console=False and a onefile payload that takes ~20s to extract means the
# operator sees NOTHING after double-clicking -- so they double-click again. The
# bootloader paints this before Python starts. text_pos lets the bootloader write
# progress over the image; keep it inside the 420x240 canvas.
splash = Splash(
    # SPECPATH-relative, NOT 'assets/splash.png': a bare relative path resolves
    # against the CWD pyinstaller was invoked from, so building from anywhere
    # other than webcam_client/ would fail to find it.
    str(Path(SPECPATH) / 'assets' / 'splash.png'),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(20, 215),
    text_size=10,
    text_color='white',
    always_on_top=False,
)
```

Then in `EXE(...)`, add `splash` and `splash.binaries` immediately after `a.scripts`, and set the icon:

```python
exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SDPRS_Webcam',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # skip UPX: its per-launch decompression slows onefile cold start
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(Path(SPECPATH) / 'assets' / 'sdprs.ico'),
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_packaging.py -q -p no:cacheprovider`
Expected: PASS — 13 passed

- [ ] **Step 6: Commit**

```bash
cd sdprs
git add webcam_client/assets webcam_client/build.spec webcam_client/tests/test_packaging.py
git commit -m "feat(webcam): splash screen during onefile extraction + app icon"
```

---

### Task 6: Rebuild and measure against the baseline

**Files:**
- Modify: `docs/superpowers/specs/2026-07-26-webcam-startup-and-guard-ux-design.md` (record measured results in §5.3)

**Interfaces:**
- Consumes: `tools/payload_audit.py` from Task 4
- Produces: measured before/after numbers

This task is where the plan's claims get **falsified or confirmed**. Do not skip it, and do not report Phase 1 complete without these numbers.

- [ ] **Step 1: Ensure the essentials ffmpeg is what the build will pick up**

```bash
winget install Gyan.FFmpeg.Essentials
```

Then find the installed binary and pin it explicitly (do not rely on PATH order — the full build is still installed):

```bash
find /c/Users/$USERNAME/AppData/Local/Microsoft/WinGet/Packages -iname "ffmpeg.exe" -path "*essentials*"
```

Export it for the build (Git Bash):

```bash
export SDPRS_FFMPEG="<path printed above>"
```

- [ ] **Step 2: Rebuild**

```bash
cd webcam_client && /c/Python314/python -m PyInstaller build.spec --noconfirm
```
Expected: the build log prints `[build.spec] ffmpeg 85 MB from ...` (a number ≤120) and a `[build.spec] dropped N excluded binaries` line. **If it prints the oversized-ffmpeg WARNING banner, stop and fix `SDPRS_FFMPEG` before continuing** — the measurement below will be meaningless otherwise.

> **Do not assert `N ≥ 2` (correction, 2026-07-30).** This draft assumed one dropped
> entry per name in `EXCLUDED_BINARIES`, but `N` counts entries PyInstaller actually
> *collected*, which varies by opencv/PIL wheel. On the 2026-07-30 rebuild it printed
> `dropped 1` and the payload was still correct: verified against
> `build/build/PKG-00.toc` that **both** `opencv_videoio_ffmpeg` and `_avif` are absent
> — only one of the two had been collected in the first place. Check the outcome (the
> names are not in the TOC), never the count.

- [ ] **Step 3: Measure the payload**

```bash
cd webcam_client && /c/Python314/python tools/payload_audit.py build/build/PKG-00.toc
```
Expected: `TOTAL (uncompressed)` ≤ **250.0** MB (baseline was 409.4 MB).

> **The 200 MB figure in this plan is superseded — use 250 MB (correction, 2026-07-30).**
> This plan's 200 MB was derived from an assumed ~85 MB ffmpeg essentials build; the real
> `Gyan.FFmpeg.Essentials` binary measures 101.5 MB, and the draft's own subtraction was
> wrong besides (even at 85 MB the arithmetic gives ~211 MB, not the 190 MB written here).
> `docs/superpowers/specs/2026-07-26-webcam-startup-and-guard-ux-design.md` §5.3 re-derived
> the target to **≤250 MB** during Phase 1 and recorded the component arithmetic.
> Measured 2026-07-30 (Phase 2 rebuild): **247.5 MB — meets the revised target**, with only
> ~2.5 MB (≈1%) of headroom. Treat any future growth in cv2/numpy as a gate breach.

- [ ] **Step 4: Measure startup, same harness as the baseline**

```powershell
$exe = "C:\D\WorkSpace\[Cloud]_Company_Sync\1Project(Single)\TyphoneCrackDetect_waterRemove\sdprs\webcam_client\dist\SDPRS_Webcam.exe"
foreach ($i in 1..3) {
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $p = Start-Process -FilePath $exe -ArgumentList "--check" -PassThru -Wait
  $sw.Stop()
  Write-Output ("run {0}: {1:N2} s  exit={2}" -f $i, $sw.Elapsed.TotalSeconds, $p.ExitCode)
}
```
Expected: run 3 (warm) ≤ **20 s**. Baseline was 60.08 / 48.33 / 39.42 s.

- [ ] **Step 5: Measure when the splash actually appears — the §3.1 risk**

Launch `dist\SDPRS_Webcam.exe` normally (no `--check`) and time by stopwatch or screen recording how long until the splash is visible.
Expected: ≤ **3 s**.

**If it exceeds 3 s:** the splash is being painted too late to solve S4. Do not silently accept it. Record the number, and report it — the fallback (a small separate pre-launcher) is a design change that needs a decision, not an improvised fix.

- [ ] **Step 6: Verify the runtime behaviours by hand**

- Double-click the exe twice → the second launch shows 「SDPRS 監控已在執行中。」 and exits; only one `SDPRS_Webcam` **child** process remains. (Remember onefile shows 2 PIDs per instance — a bootloader parent plus the real child. Compare `ParentProcessId` before concluding there is a duplicate.)
- Confirm `%APPDATA%\SDPRSWebcam\logs\webcam.log` exists and has content.
- **Confirm the API key does NOT appear in it.** Run from `sdprs/` — this prints only a verdict, never the key itself (echoing a live credential into the terminal scrollback is exactly what we are trying to prevent):
  ```bash
  /c/Python314/python -c "
  from pathlib import Path
  from webcam_client.config import load_config, get_config_dir
  key = load_config().get('api_key', '')
  log = get_config_dir() / 'logs' / 'webcam.log'
  body = log.read_text(encoding='utf-8', errors='replace')
  print('log bytes:', len(body))
  print('VERDICT:', 'LEAKED - FIX BEFORE SHIPPING' if key and key in body else 'clean')
  "
  ```
  Expected: `VERDICT: clean`.
- Confirm the exe shows the new icon in Explorer.

- [ ] **Step 7: Record the results in the spec**

Update the §5.3 table in `docs/superpowers/specs/2026-07-26-webcam-startup-and-guard-ux-design.md`, replacing the 目標 column entries with the measured values and adding a 實測 column. Note explicitly whether the splash met ≤3 s, since Phase 2/3 design depends on that answer.

- [ ] **Step 8: Run the full client suite once more**

```bash
cd webcam_client && for f in tests/test_*.py; do /c/Python314/python -m pytest "$f" -q -p no:cacheprovider; done
```
Expected: every file passes.

- [ ] **Step 9: Commit**

```bash
cd sdprs
git add docs/superpowers/specs/2026-07-26-webcam-startup-and-guard-ux-design.md
git commit -m "docs(webcam): record measured Phase 1 startup results"
```

---

### Task 7 — ATTEMPTED AND REJECTED 2026-07-30. Do not retry. ~~(OPTIONAL, may be abandoned): drop OpenBLAS~~

> **VERDICT: rejected on evidence. The steps below were executed in full; do not run them again.**
>
> Task 6 missed the ≤200 MB figure drafted here (measured 247.5 MB), which per Step
> "attempt only if…" made this task live, so it was carried out on 2026-07-30.
>
> - **Static:** `libscipy_openblas64_*.dll` is a **hard, non-delay-loaded import-table
>   entry** of `numpy/_core/_multiarray_umath.cp314-win_amd64.pyd`. The Windows loader
>   must resolve it before any Python code runs — there is no lazy path to exploit.
> - **Dynamic:** with the exclusion added, the build **succeeded**
>   (`dropped 2 excluded binaries`) and the payload fell to **227.1 MB**, matching the
>   spec's 226.7 MB prediction. The exe then died on launch with
>   `ImportError: DLL load failed while importing _multiarray_umath`, via
>   `app.py → webcam_client.main → gui/setup_wizard → camera_manager → import cv2 →
>   import numpy`. **cv2 hard-depends on numpy, so this is total startup failure**, not
>   a degraded feature.
> - **Reverted**, per Step 3: `EXCLUDED_BINARIES` is back to
>   `('opencv_videoio_ffmpeg', '_avif')`, with a `DO NOT ADD` comment and the reasoning
>   left in `buildconfig.py` itself. Restored exe: `--check` → `exit=0`, 7 consecutive runs.
> - **Gotcha for whoever touches this next:** the crashed onefile exe leaves its
>   bootloader process resident **holding a lock on `dist/SDPRS_Webcam.exe`** (overwriting
>   gives *Device or resource busy*; `tasklist` shows 2 PIDs). `taskkill /F /IM
>   SDPRS_Webcam.exe` before replacing the file.
>
> Consequence: **247.5 MB is the floor for this feature set.** The two largest remaining
> items are ffmpeg 101.5 MB (already the essentials build) and cv2 74.5 MB (OpenCV
> itself). Going lower is an architecture decision (replace cv2, or stop bundling ffmpeg),
> not a packaging tweak. Full write-up in spec §5.3.

**Files:**
- Modify: `webcam_client/build.spec` (`_EXCLUDED_BINARIES`)

`libscipy_openblas64_*.dll` is 20.4 MB and this client does no linear algebra. **But** numpy's `_multiarray_umath` links BLAS at load time on Windows, so removing it may break `import numpy` outright.

Attempt this **only if Task 6 missed the ≤200 MB target.** If Task 6 hit the target, skip — 20 MB is not worth risking the client's ability to start.

- [ ] **Step 1: Add the exclusion**

Add `'libscipy_openblas'` to `_EXCLUDED_BINARIES` in `build.spec`.

- [ ] **Step 2: Rebuild and probe**

```bash
cd webcam_client && /c/Python314/python -m PyInstaller build.spec --noconfirm
./dist/SDPRS_Webcam.exe --check; echo "exit=$?"
```

- [ ] **Step 3: Decide**

- `exit=0` → keep it. Re-run Task 6 Steps 3–4 and update the recorded numbers.
- Anything else (crash, non-zero, silent failure) → **revert immediately**: remove `'libscipy_openblas'` from `_EXCLUDED_BINARIES`, rebuild, confirm `--check` returns 0 again. Record in the spec that the exclusion was attempted and rejected, so nobody retries it blind.

- [ ] **Step 4: Commit (whichever way it went)**

```bash
cd sdprs
git add webcam_client/build.spec docs/superpowers/specs/2026-07-26-webcam-startup-and-guard-ux-design.md
git commit -m "perf(webcam): drop OpenBLAS from payload"   # or: "docs(webcam): record OpenBLAS exclusion as rejected"
```
