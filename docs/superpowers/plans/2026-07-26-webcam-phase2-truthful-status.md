# Webcam Client Phase 2 — Truthful Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the client tell the truth about its own health — replace a tray light that is painted green once at startup and never updated with a real health model, a Windows toast that pushes faults to the operator, and a status window that says what broke and what to do about it in plain 繁體中文.

**Architecture:** A pure `StatusHub` owns the single source of truth. Workers (`PushEngine`, `ControlChannel`) report a coarse fault code from their own threads; the hub aggregates by precedence, debounces degradations, and notifies listeners **only on transition**. Listeners only enqueue onto the existing `queue.Queue`; the main dispatch loop does every UI touch, so Tk and pystray are never driven from a worker thread. All operator-facing text lives in one `strings.py`, which lets a test assert that no status code or exception text ever reaches the guard.

**Tech Stack:** Python 3.14, pystray 0.19.5 (`Icon.notify` — verified `HAS_NOTIFICATION=True`, no new dependency), tkinter, pytest.

## Global Constraints

- **Base branch:** `main` @ `d7561ad` (Phase 1 shipped). Work on a new branch `feat/webcam-truthful-status`.
- **Python invocation is `/c/Python314/python`** — there is no `python3` alias.
- **pytest runs PER FILE from `webcam_client/`, with `-p no:cacheprovider`:**
  `cd webcam_client && /c/Python314/python -m pytest tests/test_x.py -q -p no:cacheprovider`
  A bare `pytest` from the repo root fails with *"path cannot contain [] parametrization"* because `[Cloud]` in the absolute path is parsed as a test id. **Never run pytest from the repo root.**
- **Git root is the repo/worktree root.** There is no `sdprs` subdirectory inside it.
- **Existing suite is 102 tests across 13 files, all passing.** Do not break them.
- **Test import convention:** `sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))` then `import webcam_client.X`.
- **No worker thread may touch Tk or pystray.** Workers enqueue; the main loop acts. This is the existing, proven pattern — violating it is the bug class Phase 1's predecessor spent a session fixing.
- **The API key must never appear in the log file.** Phase 1 added a redacting filter + formatter; do not add a second logging handler anywhere.
- **No status code, exception repr, or English error text may reach operator-facing UI.** Codes still go to the log for the technician.
- **Never hardcode credentials.** `Msc@2333` and `MSC-Person` must not appear anywhere; `broker.emqx.io` must not appear on a production path.
- **Do not add any downlink command interface to edge devices** beyond the existing `stream_start` / `stream_stop`.
- Packaging stays onefile; `upx=False` stays. Phase 2 touches no packaging.

## Relationship to the unmerged `feat/webcam-auth-error-tray` branch

That branch (2 commits, `33f5268`) already built a narrower version of this: per-worker transition-deduped auth reporting, aggregate state in `AppController`, notification emitted **inside** the lock so crossing transitions cannot latch the tray to the wrong colour, and `main.py` marshalling via the queue. **Its design is correct and this plan generalises it** from one boolean to a full health model.

**Do NOT `git merge` that branch.** Phase 1 rewrote `main.py` substantially (logging, single-instance, splash, reordering), so the merge conflicts there, and this plan restructures the auth boolean into `StatusHub` anyway. Port the *ideas and the tests*, then delete the branch once Phase 2 lands. The specific things worth preserving verbatim:

- Per-worker dedup: each worker holds its own last-reported value and calls back only on change.
- Aggregate notify emitted inside the lock that mutates the state.
- `stop_engines()` clears reported faults, so a settings edit never leaves a stale red light owned by no worker.
- Tray colour precedence with paused winning (uploads are intentionally stopped, so no fault can be occurring).

## File Structure

| File | Responsibility |
|---|---|
| `webcam_client/strings.py` | *new* — every operator-facing 繁中 string, and the state→(title, detail, action) mapping. No logic. |
| `webcam_client/status.py` | *new* — `Fault`, `Health`, `StatusHub`. Pure: no Tk, no pystray, injected clock. |
| `webcam_client/gui/notifier.py` | *new* — thin wrapper over `pystray.Icon.notify`, swallows backend failure. |
| `webcam_client/gui/status_window.py` | *new* — the Tk status window. Thin; renders a `Health` + context. |
| `webcam_client/push_engine.py` | *modify* — report faults; classify 401/403 vs connect-error vs camera-open failure. |
| `webcam_client/control_channel.py` | *modify* — same, for the command poll. |
| `webcam_client/app_controller.py` | *modify* — wire worker callbacks into the hub; clear on stop. |
| `webcam_client/gui/tray_app.py` | *modify* — colour and tooltip driven by `Health`; default (double-click) menu item opens the status window. |
| `webcam_client/main.py` | *modify* — construct the hub, tick it from the dispatch loop, handle new queue messages. |

---

### Task 1: Operator-facing strings

**Files:**
- Create: `webcam_client/strings.py`
- Test: `webcam_client/tests/test_strings.py`

**Interfaces:**
- Consumes: nothing
- Produces: `describe(state: "Health", **ctx) -> tuple[str, str, str]` returning `(title, detail, action)`; `TRAY_TOOLTIP_PREFIX: str`; `WINDOW_TITLE: str`; `BTN_OPEN_LOGS`, `BTN_RECONNECT`, `BTN_SETTINGS`, `MENU_STATUS`; `ALREADY_RUNNING: str`

Note the circular-import shape: `strings.py` must NOT import `status.py` (Task 2 imports `strings`). Key the mapping on the **string value** of the enum member, and have `describe()` accept anything with a `.value`.

- [ ] **Step 1: Write the failing test**

Create `webcam_client/tests/test_strings.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_strings.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'webcam_client.strings'`
(It also imports `webcam_client.status`, added in Task 2. Write `status.py`'s `Health` enum first if you prefer strict red-green; otherwise this test goes green at the end of Task 2. Either order is fine — say which you chose in your report.)

- [ ] **Step 3: Write minimal implementation**

Create `webcam_client/strings.py`:

```python
# sdprs/webcam_client/strings.py
"""Every operator-facing string, in one place.

The operator is a security guard, not a technician. Two rules follow, and
tests/test_strings.py enforces both:
  1. No status code, exception text, or English developer vocabulary.
  2. Every fault names an ACTION. "something went wrong" is unactionable.

The status code is NOT discarded -- it goes to the log file for whoever the
guard calls. This module is only what the guard reads.

Keyed on the Health enum's .value (a str) rather than on the enum itself, so
status.py can import strings without a circular import.
"""

WINDOW_TITLE = "SDPRS 監控狀態"
TRAY_TOOLTIP_PREFIX = "SDPRS 監控"
ALREADY_RUNNING = "SDPRS 監控已在執行中。"

MENU_STATUS = "監控狀態"
BTN_OPEN_LOGS = "開啟記錄"
BTN_RECONNECT = "重新連線"
BTN_SETTINGS = "設定"

# state value -> (title, detail template, action). Templates use str.format;
# describe() supplies defaults so a missing key can never raise.
_TEXT = {
    "starting": (
        "啟動中",
        "正在連線並開啟攝影機，請稍候。",
        "",
    ),
    "running": (
        "監控中",
        "{camera_count} 支攝影機運作正常。",
        "",
    ),
    "paused": (
        "已暫停上傳",
        "目前由操作員手動暫停，畫面不會上傳。",
        "在系統匣圖示按右鍵，選「恢復推送」即可繼續。",
    ),
    "no_server": (
        "無法連線到伺服器",
        "畫面目前無法上傳。",
        "請檢查網路連線是否正常；若網路正常仍無法連線，請通知管理員。",
    ),
    "bad_key": (
        "連線密碼已失效",
        "伺服器不接受這台電腦目前的連線密碼。",
        "請通知管理員重新設定連線密碼。",
    ),
    "camera_down": (
        "攝影機沒有畫面",
        "{camera_names} 目前沒有畫面。",
        "請檢查攝影機的 USB 線是否鬆脫；重新插好後仍沒有畫面，請通知管理員。",
    ),
}

_DEFAULTS = {"camera_count": 0, "camera_names": "攝影機"}


def describe(state, **ctx):
    """Return (title, detail, action) for a Health state.

    `state` is anything with a `.value` matching a key above. Missing context
    keys fall back to _DEFAULTS rather than raising -- an error path that has no
    context must still be able to render something.
    """
    key = getattr(state, "value", state)
    title, detail_tpl, action = _TEXT[key]
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in ctx.items() if v is not None})
    try:
        detail = detail_tpl.format(**merged)
    except (KeyError, IndexError):
        detail = detail_tpl
    return title, detail, action
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_strings.py -q -p no:cacheprovider`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add webcam_client/strings.py webcam_client/tests/test_strings.py
git commit -m "feat(webcam): centralise operator-facing strings with no-status-code guarantee"
```

---

### Task 2: `StatusHub` — the health model

**Files:**
- Create: `webcam_client/status.py`
- Test: `webcam_client/tests/test_status.py`

**Interfaces:**
- Consumes: nothing (must NOT import `strings`, Tk, or pystray)
- Produces:
  - `class Fault(Enum)`: `NONE="none"`, `NO_SERVER="no_server"`, `BAD_KEY="bad_key"`, `CAMERA_DOWN="camera_down"`
  - `class Health(Enum)`: `STARTING="starting"`, `RUNNING="running"`, `PAUSED="paused"`, `NO_SERVER="no_server"`, `BAD_KEY="bad_key"`, `CAMERA_DOWN="camera_down"`
  - `CONTROL_SOURCE = "__control__"`
  - `NOTIFY_DEBOUNCE_SECONDS = 30.0`
  - `class StatusHub:`
    - `__init__(self, *, on_change: Callable[[Health], None] = None, on_notify: Callable[[Health], None] = None, clock: Callable[[], float] = time.monotonic)`
    - `report(self, source: str, fault: Fault) -> None`
    - `set_paused(self, paused: bool) -> None`
    - `clear_all(self) -> None`
    - `tick(self) -> None`
    - `state` (property) -> `Health`
    - `faulty_sources(self) -> list[str]`

**Design rules, all test-enforced:**
- **Precedence** (worst first): `PAUSED > BAD_KEY > NO_SERVER > CAMERA_DOWN > RUNNING`. Paused wins because uploads are deliberately stopped, so no fault can be occurring. `BAD_KEY` outranks `NO_SERVER` because it is the more actionable instruction to the guard.
- `on_change` fires **immediately** on every aggregate change (drives the tray colour — the light must never lag).
- `on_notify` fires only from `tick()`, and only once a degraded state has been stable for `NOTIFY_DEBOUNCE_SECONDS`. **Recovery to `RUNNING`/`PAUSED` notifies immediately** — a guard should learn instantly that a problem cleared, and a transient blip must not toast them.
- Both callbacks are invoked **inside the lock**, together with the mutation that caused them. Two workers with crossing transitions could otherwise deliver notifications in the opposite order to the real state sequence and latch the UI to the wrong state permanently. **Callbacks must therefore only enqueue** — never re-enter the hub, never touch Tk/pystray.

- [ ] **Step 1: Write the failing test**

Create `webcam_client/tests/test_status.py`:

```python
# webcam_client/tests/test_status.py
"""StatusHub is the single source of truth for app health. It is deliberately
pure -- no Tk, no pystray, injected clock -- so all of this is unit-testable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.status import (Fault, Health, StatusHub, CONTROL_SOURCE,
                                  NOTIFY_DEBOUNCE_SECONDS)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, secs):
        self.now += secs


def make(**kw):
    changes, notifies = [], []
    clock = FakeClock()
    hub = StatusHub(on_change=changes.append, on_notify=notifies.append,
                    clock=clock, **kw)
    return hub, changes, notifies, clock


def test_starts_in_starting_state():
    hub, _, _, _ = make()
    assert hub.state is Health.STARTING


def test_first_healthy_report_becomes_running():
    hub, changes, _, _ = make()
    hub.report("cam1", Fault.NONE)
    assert hub.state is Health.RUNNING
    assert changes == [Health.RUNNING]


def test_fault_changes_state_immediately():
    """The tray light must never lag -- on_change is not debounced."""
    hub, changes, _, _ = make()
    hub.report("cam1", Fault.NONE)
    hub.report("cam1", Fault.NO_SERVER)
    assert hub.state is Health.NO_SERVER
    assert changes == [Health.RUNNING, Health.NO_SERVER]


def test_on_change_fires_only_on_transition():
    hub, changes, _, _ = make()
    hub.report("cam1", Fault.NO_SERVER)
    hub.report("cam1", Fault.NO_SERVER)
    hub.report("cam1", Fault.NO_SERVER)
    assert changes == [Health.NO_SERVER], "repeat reports must not re-fire"


def test_precedence_bad_key_beats_no_server():
    """BAD_KEY is the more actionable instruction, so it outranks NO_SERVER."""
    hub, _, _, _ = make()
    hub.report("cam1", Fault.NO_SERVER)
    hub.report(CONTROL_SOURCE, Fault.BAD_KEY)
    assert hub.state is Health.BAD_KEY


def test_precedence_no_server_beats_camera_down():
    hub, _, _, _ = make()
    hub.report("cam1", Fault.CAMERA_DOWN)
    hub.report("cam2", Fault.NO_SERVER)
    assert hub.state is Health.NO_SERVER


def test_paused_outranks_every_fault():
    """Uploads are intentionally stopped while paused, so no fault can be
    occurring -- showing a red 'no server' light then would be a lie."""
    hub, _, _, _ = make()
    hub.report("cam1", Fault.BAD_KEY)
    hub.set_paused(True)
    assert hub.state is Health.PAUSED
    hub.set_paused(False)
    assert hub.state is Health.BAD_KEY, "unpausing must reveal the real state again"


def test_recovery_when_last_faulty_source_clears():
    hub, _, _, _ = make()
    hub.report("cam1", Fault.NO_SERVER)
    hub.report("cam2", Fault.NO_SERVER)
    hub.report("cam1", Fault.NONE)
    assert hub.state is Health.NO_SERVER, "cam2 still failing"
    hub.report("cam2", Fault.NONE)
    assert hub.state is Health.RUNNING


def test_degradation_is_debounced():
    """A 2-second network blip must not toast the guard."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NONE)
    notifies.clear()
    hub.report("cam1", Fault.NO_SERVER)
    hub.tick()
    assert notifies == [], "must not notify immediately"
    clock.advance(NOTIFY_DEBOUNCE_SECONDS - 1)
    hub.tick()
    assert notifies == [], "still inside the debounce window"
    clock.advance(2)
    hub.tick()
    assert notifies == [Health.NO_SERVER]


def test_blip_shorter_than_debounce_never_notifies():
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NONE)
    notifies.clear()
    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(5)
    hub.tick()
    hub.report("cam1", Fault.NONE)   # recovered before the window elapsed
    clock.advance(NOTIFY_DEBOUNCE_SECONDS)
    hub.tick()
    assert Health.NO_SERVER not in notifies, "a transient blip must never toast"


def test_recovery_notifies_immediately_without_debounce():
    """The guard should learn at once that the problem cleared."""
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    notifies.clear()
    hub.report("cam1", Fault.NONE)
    assert notifies == [Health.RUNNING], "recovery must not wait for a tick"


def test_tick_does_not_renotify_a_stable_state():
    hub, _, notifies, clock = make()
    hub.report("cam1", Fault.NO_SERVER)
    clock.advance(NOTIFY_DEBOUNCE_SECONDS + 1)
    hub.tick()
    notifies.clear()
    for _ in range(5):
        clock.advance(60)
        hub.tick()
    assert notifies == [], "a stable fault must be announced once, not forever"


def test_clear_all_resets_to_starting_and_forgets_sources():
    """stop_engines() clears reported faults so a settings edit never leaves a
    red light owned by no live worker."""
    hub, _, _, _ = make()
    hub.report("cam1", Fault.BAD_KEY)
    hub.clear_all()
    assert hub.state is Health.STARTING
    assert hub.faulty_sources() == []


def test_clear_all_does_not_toast_starting():
    """stop_engines() runs on every settings edit. Toasting 啟動中 each time is
    exactly the notification fatigue the debounce exists to prevent."""
    hub, _, notifies, _ = make()
    hub.report("cam1", Fault.BAD_KEY)
    notifies.clear()
    hub.clear_all()
    assert notifies == [], "returning to STARTING must be silent"


def test_faulty_sources_lists_only_failing_ones():
    hub, _, _, _ = make()
    hub.report("cam1", Fault.NONE)
    hub.report("cam2", Fault.CAMERA_DOWN)
    assert hub.faulty_sources() == ["cam2"]


def test_callbacks_are_optional():
    hub = StatusHub()
    hub.report("cam1", Fault.NO_SERVER)   # must not raise
    hub.tick()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_status.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'webcam_client.status'`

- [ ] **Step 3: Write minimal implementation**

Create `webcam_client/status.py`:

```python
# sdprs/webcam_client/status.py
"""Single source of truth for the client's health.

Deliberately pure -- no Tk, no pystray, injected clock -- so every rule below is
unit-testable without hardware or a display.

THREADING: workers call report() from their own threads; the main thread calls
tick(). Both callbacks fire INSIDE the lock, together with the mutation that
caused them. Two workers with crossing transitions (one recovering as another
starts failing) would otherwise be able to deliver notifications in the opposite
order to the real state sequence and latch the UI to the wrong state
permanently. The price of that guarantee: a callback MUST only enqueue -- never
re-enter this hub, never touch Tk or pystray.
"""
import threading
import time
from enum import Enum
from typing import Callable, Optional


class Fault(Enum):
    """What a single worker is currently experiencing."""
    NONE = "none"
    NO_SERVER = "no_server"
    BAD_KEY = "bad_key"
    CAMERA_DOWN = "camera_down"


class Health(Enum):
    """The aggregate state the operator is shown."""
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    NO_SERVER = "no_server"
    BAD_KEY = "bad_key"
    CAMERA_DOWN = "camera_down"


CONTROL_SOURCE = "__control__"

# A fault must persist this long before it is announced. A failing uplink
# reports at ~1Hz and transient blips are routine; toasting every one of them
# trains the operator to ignore all notifications.
NOTIFY_DEBOUNCE_SECONDS = 30.0

# Worst first. PAUSED is handled separately (it outranks everything). BAD_KEY
# beats NO_SERVER because it is the more actionable instruction to the guard;
# CAMERA_DOWN is last because other cameras may still be working.
_PRECEDENCE = (Fault.BAD_KEY, Fault.NO_SERVER, Fault.CAMERA_DOWN)

_FAULT_TO_HEALTH = {
    Fault.BAD_KEY: Health.BAD_KEY,
    Fault.NO_SERVER: Health.NO_SERVER,
    Fault.CAMERA_DOWN: Health.CAMERA_DOWN,
}

_HEALTHY = (Health.RUNNING, Health.PAUSED, Health.STARTING)


class StatusHub:
    def __init__(self, *,
                 on_change: Optional[Callable[[Health], None]] = None,
                 on_notify: Optional[Callable[[Health], None]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self._on_change = on_change or (lambda state: None)
        self._on_notify = on_notify or (lambda state: None)
        self._clock = clock
        self._lock = threading.Lock()
        self._faults = {}            # source -> Fault (NONE entries dropped)
        self._paused = False
        self._state = Health.STARTING
        self._seen_any_report = False
        self._state_since = clock()
        self._notified = Health.STARTING

    # --- public API -------------------------------------------------------

    @property
    def state(self) -> Health:
        with self._lock:
            return self._state

    def faulty_sources(self) -> list:
        with self._lock:
            return sorted(self._faults)

    def report(self, source: str, fault: Fault) -> None:
        with self._lock:
            self._seen_any_report = True
            if fault is Fault.NONE:
                self._faults.pop(source, None)
            else:
                self._faults[source] = fault
            self._recompute_locked()

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused
            self._recompute_locked()

    def clear_all(self) -> None:
        """Engines are gone -> every reported fault is stale. Without this a
        settings edit or a quit can leave a red light that no live worker owns."""
        with self._lock:
            self._faults.clear()
            self._seen_any_report = False
            self._recompute_locked()

    def tick(self) -> None:
        """Called from the main dispatch loop. Announces a degraded state once
        it has been stable for the debounce window."""
        with self._lock:
            if self._state in _HEALTHY or self._state is self._notified:
                return
            if self._clock() - self._state_since >= NOTIFY_DEBOUNCE_SECONDS:
                self._notified = self._state
                self._on_notify(self._state)

    # --- internals (call with the lock held) ------------------------------

    def _compute_locked(self) -> Health:
        if self._paused:
            return Health.PAUSED
        for fault in _PRECEDENCE:
            if fault in self._faults.values():
                return _FAULT_TO_HEALTH[fault]
        return Health.RUNNING if self._seen_any_report else Health.STARTING

    def _recompute_locked(self) -> None:
        new = self._compute_locked()
        if new is self._state:
            return
        self._state = new
        self._state_since = self._clock()
        self._on_change(new)
        # Recovery is NOT debounced: the operator should learn at once that the
        # problem cleared. Degradation waits for tick() so a blip stays silent.
        #
        # STARTING is excluded from the recovery toast on purpose: clear_all()
        # (called by stop_engines on every settings edit) drops the state back to
        # STARTING, and toasting "啟動中" every time the operator opens settings
        # is exactly the notification-fatigue this debounce exists to avoid.
        if new in _HEALTHY:
            recovered = self._notified not in _HEALTHY
            self._notified = new
            if recovered and new is not Health.STARTING:
                self._on_notify(new)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_status.py -q -p no:cacheprovider`
Expected: PASS — 15 passed

Then confirm Task 1's strings test now passes too:
Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_strings.py -q -p no:cacheprovider`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add webcam_client/status.py webcam_client/tests/test_status.py
git commit -m "feat(webcam): StatusHub health model with precedence and debounce"
```

---

### Task 3: Workers report faults

**Files:**
- Modify: `webcam_client/push_engine.py`, `webcam_client/control_channel.py`, `webcam_client/app_controller.py`
- Test: `webcam_client/tests/test_push_engine.py`, `tests/test_control_channel.py`, `tests/test_app_controller.py` (all extend)

**Interfaces:**
- Consumes: `Fault`, `CONTROL_SOURCE` from `webcam_client.status`
- Produces:
  - `PushEngine(camera_config, server_url, api_key, on_fault: Optional[Callable[[Fault], None]] = None)`
  - `ControlChannel(server_url, api_key, node_ids, on_command, on_fault: Optional[Callable[[Fault], None]] = None)`
  - `AppController(config, *, engine_factory=None, control_factory=None, status_hub=None)`

**Fault classification — get this right, it is the whole point:**

| Situation | Fault |
|---|---|
| `cap is None` (camera would not open) | `CAMERA_DOWN` |
| snapshot POST / command poll → **401 or 403** | `BAD_KEY` |
| `httpx.ConnectError`, `ConnectTimeout`, any transport failure | `NO_SERVER` |
| any other non-2xx (5xx, 404) | `NO_SERVER` (the server is not usefully reachable) |
| success | `NONE` |

**Each worker dedups its own reports** — hold the last value, call back only on change. This is ported verbatim from `feat/webcam-auth-error-tray`; without it a 1 Hz failing uplink calls the hub every second.

- [ ] **Step 1: Write the failing tests**

Append to `webcam_client/tests/test_control_channel.py`:

```python
def test_control_channel_reports_bad_key_on_401(monkeypatch):
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append)

    class Resp:
        status_code = 401

    ch._client = type("C", (), {"get": lambda self, *a, **k: Resp()})()
    ch._poll_node("n1")
    assert seen == [Fault.BAD_KEY]


def test_control_channel_reports_bad_key_on_403(monkeypatch):
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append)

    class Resp:
        status_code = 403

    ch._client = type("C", (), {"get": lambda self, *a, **k: Resp()})()
    ch._poll_node("n1")
    assert seen == [Fault.BAD_KEY]


def test_control_channel_reports_none_on_clean_poll():
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append)

    class Resp:
        status_code = 200

        def json(self):
            return {"command": None}

    ch._client = type("C", (), {"get": lambda self, *a, **k: Resp()})()
    ch._poll_node("n1")
    assert seen == [Fault.NONE]


def test_control_channel_dedups_repeated_faults():
    """A failing poll runs continuously; the hub must not be called every time."""
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append)

    class Resp:
        status_code = 401

    ch._client = type("C", (), {"get": lambda self, *a, **k: Resp()})()
    for _ in range(5):
        ch._poll_node("n1")
    assert seen == [Fault.BAD_KEY], "repeat identical faults must report once"
```

Append to `webcam_client/tests/test_push_engine.py`:

```python
def test_classify_maps_401_and_403_to_bad_key():
    """These are the two the guard can act on: 'call the administrator'."""
    from webcam_client.push_engine import _classify
    from webcam_client.status import Fault

    for code in (401, 403):
        exc = Exception("rejected")
        exc.response = type("R", (), {"status_code": code})()
        assert _classify(exc) is Fault.BAD_KEY, f"{code} must be BAD_KEY"


def test_classify_maps_transport_failure_to_no_server():
    from webcam_client.push_engine import _classify
    from webcam_client.status import Fault
    import httpx

    assert _classify(httpx.ConnectError("refused")) is Fault.NO_SERVER


def test_classify_maps_5xx_to_no_server():
    """A 500 is not a key problem -- telling the guard to call the admin about
    their password would send them down the wrong path."""
    from webcam_client.push_engine import _classify
    from webcam_client.status import Fault

    exc = Exception("boom")
    exc.response = type("R", (), {"status_code": 500})()
    assert _classify(exc) is Fault.NO_SERVER
```

Append to `webcam_client/tests/test_app_controller.py`:

```python
def test_controller_forwards_worker_faults_to_the_hub():
    from webcam_client.app_controller import AppController
    from webcam_client.status import StatusHub, Fault, Health

    hub = StatusHub()
    captured = {}

    class FakeEngine:
        def __init__(self, cam, url, key, on_fault=None):
            captured["on_fault"] = on_fault
            self._node_id = cam.get("node_id")

        def start(self): pass
        def set_paused(self, v): pass
        def stop(self): pass

    ctrl = AppController(
        {"cameras": [{"device_index": 0, "node_id": "n1", "enabled": True}]},
        engine_factory=lambda cam, url, key, on_fault=None: FakeEngine(
            cam, url, key, on_fault),
        control_factory=lambda url, key, ids, cb, on_fault=None: type(
            "C", (), {"start": lambda s: None, "stop": lambda s: None})(),
        status_hub=hub,
    )
    ctrl.start_engines()
    captured["on_fault"](Fault.BAD_KEY)
    assert hub.state is Health.BAD_KEY


def test_stop_engines_clears_stale_faults_from_the_hub():
    """A red light must never outlive the worker that reported it."""
    from webcam_client.app_controller import AppController
    from webcam_client.status import StatusHub, Fault, Health

    hub = StatusHub()
    hub.report("n1", Fault.BAD_KEY)
    ctrl = AppController(
        {"cameras": []},
        engine_factory=lambda *a, **k: None,
        control_factory=lambda *a, **k: type(
            "C", (), {"start": lambda s: None, "stop": lambda s: None})(),
        status_hub=hub,
    )
    ctrl.stop_engines()
    assert hub.state is Health.STARTING
    assert hub.faulty_sources() == []


def test_pause_and_resume_reach_the_hub():
    from webcam_client.app_controller import AppController
    from webcam_client.status import StatusHub, Health

    hub = StatusHub()
    ctrl = AppController({"cameras": []}, engine_factory=lambda *a, **k: None,
                         control_factory=lambda *a, **k: None, status_hub=hub)
    ctrl.pause_all()
    assert hub.state is Health.PAUSED
    ctrl.resume_all()
    assert hub.state is not Health.PAUSED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_control_channel.py tests/test_app_controller.py -q -p no:cacheprovider`
(Run them as two separate per-file invocations if the combined form trips the `[]` path issue.)
Expected: FAIL — `TypeError: ... unexpected keyword argument 'on_fault'` / `'status_hub'`

- [ ] **Step 3: Write the implementation**

In `control_channel.py`, add the parameter and a deduping reporter, and classify:

```python
    def __init__(self, server_url: str, api_key: str, node_ids: list,
                 on_command: Callable[[str, str, Optional[dict]], None],
                 on_fault: Optional[Callable] = None):
        ...
        # Dedup locally: a failing poll runs continuously, and calling the hub
        # every cycle would swamp it. Report only on change.
        self._on_fault = on_fault
        self._last_fault = None

    def _report(self, fault) -> None:
        if fault is self._last_fault:
            return
        self._last_fault = fault
        if self._on_fault is not None:
            self._on_fault(fault)
```

In `_poll_node`, add `self._report(Fault.NONE)` on the clean-200 arm, `self._report(Fault.BAD_KEY)` on 401, a new `elif resp.status_code == 403:` arm that reports `BAD_KEY` and backs off (the key may be valid but not own this camera — unlike 401, do NOT stop the channel), and `self._report(Fault.NO_SERVER)` on the other-status arm. In `run()`, report `NO_SERVER` in the `httpx.ConnectError` handler. Import `from .status import Fault`.

In `push_engine.py`, add the same `on_fault` parameter and `_report` helper, plus `from .status import Fault`. Report `Fault.CAMERA_DOWN` where `open_camera` returns `None`. `_push_snapshot` currently funnels every failure through one `try/except Exception`, so classification happens by inspecting the exception:

```python
    def _push_snapshot(self, frame) -> None:
        try:
            small = cv2.resize(frame, self._resolution)
            _, jpeg = cv2.imencode(".jpg", small,
                                   [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
            # Webcam ingest route (spec §303). NOT /api/edge/... -- that path is
            # gated by the global EDGE_API_KEY and would 401 the webcam key.
            url = f"{self._server_url}/api/webcam/{self._node_id}/snapshot"
            resp = self._client.post(url, content=jpeg.tobytes(),
                                     headers={"Content-Type": "image/jpeg"})
            # httpx does NOT raise on 4xx without this.
            resp.raise_for_status()
        except Exception as e:
            # The technician still gets the real status code, in the log file.
            # The OPERATOR only ever sees the plain-language string this Fault
            # maps to -- see strings.py.
            self._report(_classify(e))
            logger.warning(f"Snapshot push to {self._node_id} failed: {e}")
        else:
            self._report(Fault.NONE)
```

with this module-level helper:

```python
def _classify(exc) -> Fault:
    """Map a failed upload to the fault the operator needs to act on.

    401/403 mean the key is rejected -> the guard must call the administrator.
    Everything else (transport failure, 5xx, 404) means the server is not
    usefully reachable -> the guard should check the network first.
    """
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) in (401, 403):
        return Fault.BAD_KEY
    return Fault.NO_SERVER
```

`httpx.HTTPStatusError` carries `.response`; `httpx.ConnectError` and friends do not, so they fall through to `NO_SERVER`, which is correct.

In `app_controller.py`, take `status_hub=None`, keep the default factories wiring each worker's `on_fault` to `self._hub.report(<source>, fault)` (node_id for engines, `CONTROL_SOURCE` for the channel), call `self._hub.clear_all()` in `stop_engines()`'s `finally`, and call `self._hub.set_paused(True/False)` from `pause_all`/`resume_all`. When `status_hub` is None use a no-op stand-in so existing tests that construct `AppController` without one keep working.

- [ ] **Step 4: Run tests to verify they pass**

Run each of `tests/test_control_channel.py`, `tests/test_app_controller.py`, `tests/test_push_engine.py` per-file, then the full sweep:
`cd webcam_client && for f in tests/test_*.py; do /c/Python314/python -m pytest "$f" -q -p no:cacheprovider; done`
Expected: every file passes.

- [ ] **Step 5: Commit**

```bash
git add webcam_client/push_engine.py webcam_client/control_channel.py webcam_client/app_controller.py webcam_client/tests/
git commit -m "feat(webcam): workers classify and report faults to StatusHub"
```

---

### Task 4: Truthful tray icon

**Files:**
- Modify: `webcam_client/gui/tray_app.py`
- Test: `webcam_client/tests/test_tray_app.py` (extend)

**Interfaces:**
- Consumes: `Health` from `webcam_client.status`; `describe`, `TRAY_TOOLTIP_PREFIX`, `MENU_STATUS` from `webcam_client.strings`
- Produces: `TrayApp(..., on_open_status: Callable)`; `TrayApp.set_health(state: Health) -> None`; `_health_color(state: Health) -> str`

**Removes the lie:** `main.py`'s `tray.set_status(True)` disappears in Task 6. Delete `set_status` and `_icon_color`'s `connected` parameter — leaving a dead "always true" path invites someone to call it again.

Colours: `PAUSED`→amber, `BAD_KEY`/`NO_SERVER`/`CAMERA_DOWN`→red, `RUNNING`→green, `STARTING`→grey.

- [ ] **Step 1: Write the failing test**

Append to `webcam_client/tests/test_tray_app.py`:

```python
def test_health_colors_cover_every_state():
    from webcam_client.gui.tray_app import _health_color
    from webcam_client.status import Health
    for state in Health:
        assert _health_color(state) in {"green", "red", "amber", "grey"}


def test_faults_are_red_and_paused_is_amber():
    from webcam_client.gui.tray_app import _health_color
    from webcam_client.status import Health
    assert _health_color(Health.RUNNING) == "green"
    assert _health_color(Health.PAUSED) == "amber"
    for bad in (Health.NO_SERVER, Health.BAD_KEY, Health.CAMERA_DOWN):
        assert _health_color(bad) == "red", f"{bad} must be red"


def test_starting_is_not_green():
    """Green before anything has reported is the original lie."""
    from webcam_client.gui.tray_app import _health_color
    from webcam_client.status import Health
    assert _health_color(Health.STARTING) != "green"


def test_set_status_is_gone():
    """The always-true connected flag was the U6 defect; it must not survive."""
    from webcam_client.gui.tray_app import TrayApp
    assert not hasattr(TrayApp, "set_status")


def test_tooltip_names_the_state_in_plain_language():
    from webcam_client.gui.tray_app import _tooltip
    from webcam_client.status import Health
    text = _tooltip(Health.BAD_KEY)
    assert "連線密碼" in text
    assert "401" not in text and "403" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_tray_app.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name '_health_color'`

- [ ] **Step 3: Write the implementation**

Replace `_icon_color` with:

```python
_HEALTH_COLORS = {
    Health.STARTING: "grey",
    Health.RUNNING: "green",
    Health.PAUSED: "amber",       # deliberate operator action, not a fault
    Health.NO_SERVER: "red",
    Health.BAD_KEY: "red",
    Health.CAMERA_DOWN: "red",
}


def _health_color(state) -> str:
    return _HEALTH_COLORS.get(state, "grey")


def _tooltip(state) -> str:
    title, detail, _ = describe(state)
    return f"{TRAY_TOOLTIP_PREFIX}\n{title}\n{detail}"
```

Add `"grey": (140, 140, 140, 255)` to `_create_icon`'s palette. Replace `set_status(connected)` with `set_health(state)` which stores the state, repaints the icon and sets `self._icon.title = _tooltip(state)`. Add an `on_open_status` constructor argument and, as the FIRST menu entry, `pystray.MenuItem(MENU_STATUS, lambda: self._on_open_status(), default=True)` — `default=True` makes it the double-click action (verified against pystray 0.19.5's `MenuItem.__init__`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_tray_app.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webcam_client/gui/tray_app.py webcam_client/tests/test_tray_app.py
git commit -m "feat(webcam): tray icon reflects real health, drop the always-green lie"
```

---

### Task 5: Toast notifier

**Files:**
- Create: `webcam_client/gui/notifier.py`
- Test: `webcam_client/tests/test_notifier.py`

**Interfaces:**
- Consumes: `describe` from `webcam_client.strings`
- Produces: `notify_state(icon, state) -> bool` — returns True if a toast was attempted, False if unavailable.

The toast is what actually reaches a guard who never looks at the tray. It must never raise: a notification backend failure cannot be allowed to kill the dispatch loop.

- [ ] **Step 1: Write the failing test**

Create `webcam_client/tests/test_notifier.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_notifier.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'webcam_client.gui.notifier'`

- [ ] **Step 3: Write the implementation**

Create `webcam_client/gui/notifier.py`:

```python
# sdprs/webcam_client/gui/notifier.py
"""Windows toast, via pystray's own notification support (no new dependency --
pystray._win32.Icon has HAS_NOTIFICATION = True).

This is the only channel that reaches an operator who never looks at the tray,
so the message always carries the ACTION, not just the fault. It must never
raise: a notification backend failure cannot be allowed to kill the dispatch
loop that keeps the cameras running.
"""
import logging

from ..strings import describe

logger = logging.getLogger("webcam_client.gui.notifier")


def notify_state(icon, state) -> bool:
    """Toast the given health state. Returns True if a toast was attempted."""
    if icon is None or not hasattr(icon, "notify"):
        return False
    title, detail, action = describe(state)
    message = detail if not action else f"{detail}\n{action}"
    try:
        icon.notify(message, title)
        return True
    except Exception:
        logger.warning("Toast notification failed", exc_info=True)
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webcam_client && /c/Python314/python -m pytest tests/test_notifier.py -q -p no:cacheprovider`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add webcam_client/gui/notifier.py webcam_client/tests/test_notifier.py
git commit -m "feat(webcam): Windows toast notifier for health transitions"
```

---

### Task 6: Status window and integration

**Files:**
- Create: `webcam_client/gui/status_window.py`
- Modify: `webcam_client/main.py`
- Test: `webcam_client/tests/test_status_window.py`, `tests/test_main_dispatch.py` (extend)

**Interfaces:**
- Consumes: `Health`, `StatusHub`, `describe`, `notify_state`, `TrayApp.set_health`
- Produces:
  - `build_status_lines(state, camera_count, faulty_names) -> tuple[str, str, str]` — pure, testable without Tk
  - `open_status_window(state, *, camera_count, faulty_names, on_open_logs, on_reconnect, on_settings) -> None` — Tk; must run on the main thread
  - `open_log_folder() -> bool`

**Wiring decisions (routine calls, stated so the implementer does not have to guess):**
- **重新連線** = `controller.apply(controller.config)` — stops and rebuilds engines in-process, no restart. Same call the OPEN_SETTINGS error path already uses.
- **開啟記錄** = `os.startfile(get_log_dir())` from `logging_setup`, guarded — opens Explorer at the log folder for the technician.
- The hub's `on_change` and `on_notify` callbacks **only enqueue** (`q.put("HEALTH")` / `q.put("NOTIFY")`); the dispatch loop reads `hub.state` and does the UI work. This preserves the existing no-Tk-from-worker-threads rule.
- `hub.tick()` is called on the dispatch loop's existing 1-second `queue.Empty` timeout — no new thread, no new timer.

- [ ] **Step 1: Write the failing tests**

Create `webcam_client/tests/test_status_window.py`:

```python
# webcam_client/tests/test_status_window.py
"""The window's TEXT is pure and testable; only the Tk rendering needs a display,
so the logic worth protecting lives in build_status_lines()."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.gui.status_window import build_status_lines
from webcam_client.status import Health


def test_running_line_reports_camera_count():
    title, detail, action = build_status_lines(Health.RUNNING, 2, [])
    assert "監控中" in title
    assert "2" in detail
    assert action == ""


def test_camera_down_names_the_failing_cameras():
    title, detail, action = build_status_lines(
        Health.CAMERA_DOWN, 2, ["前門攝影機"])
    assert "前門攝影機" in detail
    assert "USB" in action, "the guard needs the physical action"


def test_bad_key_tells_the_guard_to_call_the_admin():
    _, _, action = build_status_lines(Health.BAD_KEY, 2, [])
    assert "管理員" in action


def test_no_status_codes_anywhere():
    for state in Health:
        joined = " ".join(build_status_lines(state, 2, ["前門攝影機"]))
        assert "401" not in joined and "403" not in joined and "500" not in joined
```

Append to `webcam_client/tests/test_main_dispatch.py`:

```python
def test_health_message_repaints_the_tray(monkeypatch):
    """Workers only enqueue; the tray is repainted on the MAIN thread."""
    import webcam_client.main as m
    from webcam_client.status import StatusHub, Fault, Health

    hub = StatusHub()
    painted = []

    class FakeTray:
        def set_health(self, state):
            painted.append(state)

    hub.report("n1", Fault.BAD_KEY)
    m._handle_health(hub, FakeTray())
    assert painted == [Health.BAD_KEY]


def test_notify_message_toasts_current_state(monkeypatch):
    import webcam_client.main as m
    from webcam_client.status import StatusHub, Fault, Health

    sent = []
    monkeypatch.setattr(m, "notify_state",
                        lambda icon, state: sent.append(state) or True)
    hub = StatusHub()
    hub.report("n1", Fault.NO_SERVER)
    m._handle_notify(hub, object())
    assert sent == [Health.NO_SERVER]
```

- [ ] **Step 2: Run tests to verify they fail**

Run each per-file.
Expected: FAIL — `ModuleNotFoundError: ... status_window` and `AttributeError: ... has no attribute '_handle_health'`

- [ ] **Step 3: Write the implementation**

Create `webcam_client/gui/status_window.py` with `build_status_lines(state, camera_count, faulty_names)` delegating to `strings.describe(state, camera_count=camera_count, camera_names="、".join(faulty_names) or None)` and returning its triple; `open_log_folder()` doing a guarded `os.startfile(get_log_dir())`; and `open_status_window(...)` building a `tk.Tk()` with a coloured status banner (title bold, detail, action), a per-camera list, and three buttons wired to the callbacks — mirroring `setup_wizard.py`'s existing Tk idioms (`ttk`, `_safe_after` style guarding).

In `main.py`, add these module-level handlers (they are separate functions so
they are unit-testable without running `main()`):

```python
def _handle_health(hub, tray) -> None:
    """Repaint the tray from the hub's CURRENT state.

    Deliberately re-reads hub.state rather than trusting a state carried on the
    queue: several transitions can be enqueued before the loop drains them, and
    painting a stale one would leave the light showing a state that has already
    passed. The queue message is only a wake-up.
    """
    tray.set_health(hub.state)


def _handle_notify(hub, icon) -> None:
    notify_state(icon, hub.state)
```

Then rewrite the construction block and the dispatch loop:

```python
    # The queue must exist BEFORE the hub, because the hub's callbacks enqueue
    # onto it -- and they fire from worker threads. Workers never touch Tk or
    # pystray; every UI action happens on this thread, below.
    q: "queue.Queue[str]" = queue.Queue()
    hub = StatusHub(on_change=lambda state: q.put("HEALTH"),
                    on_notify=lambda state: q.put("NOTIFY"))

    controller = AppController(config, status_hub=hub)

    tray = TrayApp(
        on_open_settings=lambda: q.put("OPEN_SETTINGS"),
        on_open_status=lambda: q.put("OPEN_STATUS"),
        on_quit=lambda: q.put("QUIT"),
        on_pause=controller.pause_all,
        on_resume=controller.resume_all,
    )
    # S6: the tray icon is the ONLY sign of life, and start_engines() opens each
    # camera (0.5-2s apiece). Show the icon first, then do the slow work.
    tray.start()
    tray.set_health(hub.state)      # STARTING/grey -- NOT the old always-green lie
    _close_splash()

    controller.start_engines()
    logger.info(f"SDPRS Webcam Client running ({len(enabled)} cameras)")

    running = True
    while running and _running:
        try:
            req = q.get(timeout=1.0)
        except queue.Empty:
            # The 1s idle tick is what promotes a sustained fault into a toast.
            # No extra thread or timer is needed -- this loop already wakes here.
            hub.tick()
            continue
        if req == "HEALTH":
            _handle_health(hub, tray)
            continue
        if req == "NOTIFY":
            _handle_notify(hub, tray.icon)
            continue
        if req == "OPEN_STATUS":
            open_status_window(
                hub.state,
                camera_count=len(enabled),
                faulty_names=hub.faulty_sources(),
                on_open_logs=open_log_folder,
                on_reconnect=lambda: controller.apply(controller.config),
                on_settings=lambda: q.put("OPEN_SETTINGS"),
            )
            continue
        running = _handle_request(
            req, controller, lambda cfg: run_setup_wizard(cfg, mode="edit"))
```

Add the imports at the top of `main.py`:

```python
from .status import StatusHub
from .gui.notifier import notify_state
from .gui.status_window import open_status_window, open_log_folder
```

`TrayApp` must expose the underlying pystray icon for the notifier — add a
plain `icon` property returning `self._icon` (it is `None` until `start()`
runs, and `notify_state` already handles `None`).

- [ ] **Step 4: Run tests, then the full sweep**

Run: `cd webcam_client && for f in tests/test_*.py; do /c/Python314/python -m pytest "$f" -q -p no:cacheprovider; done`
Expected: every file passes (102 pre-existing + the new tests).

- [ ] **Step 5: Commit**

```bash
git add webcam_client/gui/status_window.py webcam_client/main.py webcam_client/tests/
git commit -m "feat(webcam): status window + wire StatusHub into the dispatch loop"
```

---

### Task 7: Retire the superseded branch and verify by hand

**Files:**
- Modify: `docs/superpowers/specs/2026-07-26-webcam-startup-and-guard-ux-design.md` (record Phase 2 as shipped)

- [ ] **Step 1: Confirm the auth-error-tray branch is fully superseded**

Check each behaviour of `feat/webcam-auth-error-tray` has an equivalent here: 401 → red, 403 → red, recovery → green, aggregate across workers, cleared on `stop_engines()`, tray touched only from the main thread. List them in your report with the Phase 2 file:line that provides each.

```bash
git log --oneline main..feat/webcam-auth-error-tray
git diff main...feat/webcam-auth-error-tray --stat
```

- [ ] **Step 2: Delete it once superseded**

```bash
git branch -D feat/webcam-auth-error-tray
```
Only after Step 1 shows full coverage. If anything is NOT covered, stop and report it instead of deleting.

- [ ] **Step 3: Hand-verify against a real server**

With the client configured and running:
- Stop the server (or pull the network) → within ~30 s the tray goes red and a toast says 「無法連線到伺服器」 with the action line. Restore → returns green promptly, with a recovery toast.
- Corrupt the API key via tray → 設定 → the tray goes red and the toast says 「連線密碼已失效」. **The log must contain the 401; the toast must not.**
- Unplug a camera → 「攝影機沒有畫面」 naming that camera.
- Pause from the tray → amber, and no fault toast fires while paused.
- Double-click the tray icon → the status window opens; 開啟記錄 opens the log folder; 重新連線 rebuilds engines without restarting the app.

Record the actual observed behaviour. **If a toast fires for a blip shorter than 30 s, the debounce is not working — report it rather than adjusting the threshold to hide it.**

- [ ] **Step 4: Record Phase 2 in the spec**

Update §6 to 已實作, noting the observed debounce behaviour and anything that differed from the design.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-07-26-webcam-startup-and-guard-ux-design.md
git commit -m "docs(webcam): record Phase 2 as shipped"
```
