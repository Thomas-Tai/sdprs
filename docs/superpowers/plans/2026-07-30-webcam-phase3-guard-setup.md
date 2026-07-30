# Webcam Client Phase 3 — Guard-Facing Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the setup window from a developer form into a single guided page a non-technical security guard can complete alone — three numbered sections that unlock in order, plain-language 繁中 labels, and no operation that freezes the UI. Closes the nine open interface findings U1, U2, U3, U4, U5, U8, U10, U11, U12.

**Architecture:** Four separable concerns, deliberately split so the parts worth testing are testable without a display. (1) *Scanning* — `camera_manager.scan_cameras` gains an early-stop and returns the frame it already grabbed, killing both the 10–20 s probe and the second camera open. (2) *Flow* — a pure state machine (`wizard/flow.py`) owning which section is unlocked; no Tk, fully unit-tested. (3) *Connection* — every network call moves off the Tk thread behind the `_safe_after` marshalling idiom this codebase already proved in Phase 1. (4) *Window* — `wizard/window.py` renders the three sections against the flow state and owns nothing else.

**Tech Stack:** Python 3.14, Tkinter/ttk, httpx, `winreg` (stdlib), threading, pytest. No new runtime dependency — this is a hard constraint, see below.

## Global Constraints

- **No new runtime dependency.** The onefile payload sits at 247.5 MB against a 250 MB budget (spec §5.3 驗收標準). Anything that adds payload breaches the gate. This is why U10 uses stdlib `winreg` and not `pywin32`/COM.
- **Packaging stays onefile**; `upx=False` stays. Standing user requirement, pinned by `test_packaging.py`.
- **No status code, exception repr, or English error text may reach operator-facing UI.** Codes go to the log for the technician. `test_strings.py` enforces this automatically over every `UPPER_SNAKE` str in `strings.py`.
- **The API key must never appear in the log file.** Do not add a second logging handler anywhere.
- **No worker thread may touch Tk.** Workers enqueue or marshal via `_safe_after`; the main loop acts.
- **Never hardcode credentials.** The banned literals must not appear anywhere; `broker.emqx.io` must not appear on a production path.
- **Do not add any downlink command interface to edge devices** beyond the existing `stream_start` / `stream_stop`.
- **pytest must be run per-file from `webcam_client/`, with `-p no:cacheprovider`.** A bare `pytest` from the repo root fails on `[Cloud]` in the absolute path being parsed as a test id:
  `cd webcam_client && /c/Python314/python -m pytest tests/test_x.py -q -p no:cacheprovider`
- **Python invocation is `/c/Python314/python`** — there is no `python3` alias.
- **Locale is 台灣 zh-TW.** All operator copy lives in `strings.py`, never inline in a widget.
- **The site has a wired uplink and NO guard-accessible router.** Never instruct the guard to check router lights. This is a locked human decision.

## Locked human decisions carried in

1. The operator is a non-technical security guard who does **both** setup and daily operation.
2. The guard **may** be asked to type the connection password — hence U12's reveal toggle.
3. Copy names a **physical action** wherever the guard can act, and escalates to 管理員 only where they genuinely cannot.
4. `Server URL` → **連線位址**, `API Key` → **連線密碼**. Already in `strings.py:68-69`.

## File Structure

| File | Responsibility |
|---|---|
| `webcam_client/gui/wizard/__init__.py` | *new* — package façade, re-exports the public surface |
| `webcam_client/gui/wizard/flow.py` | *new* — pure section-unlock state machine |
| `webcam_client/gui/wizard/connection.py` | *new* — off-thread network: test + register |
| `webcam_client/gui/wizard/scanning.py` | *new* — `_scan_cameras_async`, `_load_thumbnail_async` |
| `webcam_client/gui/wizard/window.py` | *new* — Tk rendering of the three sections |
| `webcam_client/autostart.py` | *new* — HKCU Run decisions (pure) + thin `winreg` shim |
| `webcam_client/camera_manager.py` | *modify* — early stop + return frame |
| `webcam_client/config.py` | *modify* — serialise-before-truncate; `autostart` default |
| `webcam_client/strings.py` | *modify* — new guard copy |
| `webcam_client/main.py` | *modify* — import path only |
| `webcam_client/gui/setup_wizard.py` | *transitional façade, deleted in Task 9* |
| `webcam_client/tests/test_wizard_flow.py` | *new* |
| `webcam_client/tests/test_wizard_connection.py` | *new* |
| `webcam_client/tests/test_autostart.py` | *new* |
| `webcam_client/tests/test_camera_manager.py` | *modify* |
| `webcam_client/tests/test_setup_wizard.py` | *rewritten in Task 9* |

---

## Task 0 — DECIDED 2026-07-30: option (A). Spec §9 is amended by this decision.

**The user chose (A): add `GET /api/webcam/ping`.** Spec §9's 「不新增任何 server 端 API」
no longer holds unqualified — it is amended to permit this single side-effect-free
authentication probe, and nothing further. Tasks 3/4 are unblocked. Update spec §9 to
record the amendment before implementing, so the spec stops contradicting the plan.

The analysis that produced the decision is kept below as the record.

**U4 as specified could not be built without violating spec §9.**

Spec §7.2 requires 「測試連線」to verify *identity* (`只驗證身分，不註冊攝影機`). Spec §9 YAGNI locks 「**不新增任何 server 端 API**」.

Verified 2026-07-30 against the source, not inferred: `verify_webcam_api_key` is depended on by **exactly four** routes, all in `central_server/api/webcam.py` (`:45`, `:56`, `:109`, `:186`) — confirmed by grep over all of `central_server/`. Each fails at least one requirement:

| Route | Why it cannot serve as a connection test |
|---|---|
| `POST /webcam/cameras` `:45` | Registers cameras (`:47`) — the exact side effect U4 forbids |
| `PUT /webcam/{node_id}/hls/{filename}` `:56` | 403 without a registered camera (`:67`); writes on success |
| `POST /webcam/{node_id}/snapshot` `:109` | Writes the snapshot buffer + `touch_webcam_upload` (`:126-127`) |
| `GET /webcam/{node_id}/commands` `:186` | Dequeues from a single-consumer queue (`:198`) — would steal the client's own `stream_start` |

At first run the client has **zero** registered cameras, so every ownership-checked route 403s regardless. `GET /api/health` (`main.py:303`) is public and proves the URL only — a wrong password still returns 200.

**Three ways forward. This is the user's call, not the implementer's:**

- **(A) Add `GET /api/webcam/ping`** — `Depends(verify_webcam_api_key)`, returns `{"ok": True}`, touches nothing. ~6 lines in `webcam.py` + 2 tests in `test_webcam_api.py`; `auth.py`, `database.py`, `main.py` all unchanged. Delivers U4 exactly as specified. **Requires the user to amend §9.** *Recommended* — the constraint was written to prevent scope creep, and this is six lines that make a locked UX requirement buildable.
- **(B) Sentinel-id probe** — `GET /webcam/<sentinel>/commands`; 401 = bad key, 403 = key good. Honours §9 and is genuinely side-effect-free today (the ownership guard at `:195` runs *before* the dequeue at `:198`). But success is signalled by an HTTP error, and if that guard is ever moved below the dequeue the test button silently steals a command off the client's control channel. Rejected unless (A) is refused.
- **(C) Narrow U4 to reachability** — use public `GET /api/health`, relabel the button so it does not promise password verification. Honours §9, but the guard learns their password is wrong only at 開始監控, which is most of what U4 was for.

- [ ] **Step 0: Obtain the decision and record it here before writing any connection code.**

If (A): add the route above the `/{node_id}/...` routes in `webcam.py`, add it to the module docstring inventory (`:9-16`, maintained as complete), and add two tests to `central_server/tests/test_webcam_api.py` following its existing fixture pattern (`tmp_db` autouse + `app` + `authed_client`; a valid key is minted through `POST /api/nodes/webcam`, never inserted directly into the DB). The route must **not** take a `node_id`, must **not** touch `webcam_clients.status`, and must **not** log the submitted key.

---

### Task 1: `scan_cameras` — early stop and return the frame (U1, U2)

**Files:**
- Modify: `webcam_client/camera_manager.py`
- Test: `webcam_client/tests/test_camera_manager.py`

**Interfaces:**
- Produces: `scan_cameras(max_index: int = 10, stop_after_misses: int = 3, capture_factory=None) -> list[dict]`; each dict gains a `frame` key (a `numpy.ndarray`, or `None` if the grab failed)
- Consumed by: `wizard/scanning.py` only

**Why the miss threshold is a parameter, not a constant:** device indices are not guaranteed contiguous. A guard with a USB hub can legitimately have cameras at 0 and 4, and an unconditional early stop would lose the second one. So: the **first-run** scan stops after `stop_after_misses` consecutive misses (fast — the common case is indices 0..1), and the **重新掃描** button passes `stop_after_misses=max_index` to force a full sweep. The button is therefore the documented escape hatch for the gap case, and the copy in `WIZ_NO_CAMERA_FOUND` already points at it.

**Why the frame comes back:** today `scan_cameras` opens each device, reads its resolution, closes it — then `_load_thumbnail_async` reopens the same device to grab a preview. Two opens per camera, each costing a DSHOW negotiation. Returning the frame from the first open removes the second.

**Correction, verified 2026-07-30 against the source:** an earlier draft of this line claimed the scan "already has a frame in hand at the moment it reads `width`/`height`". It does not. The old `scan_cameras` never called `cap.read()` — only `cap.get(CAP_PROP_FRAME_WIDTH/HEIGHT)`, which are driver property queries, not frame grabs. So this is **a trade, not a free lunch**: the scan now pays one frame grab per *hit* device that it never paid before (a first DSHOW frame can cost a few hundred ms), and buys back an entire open+negotiate+read. One negotiation beats two, so it is still clearly positive — but smaller than "deletes the second open entirely" implied.

**This makes Task 5 load-bearing for U2.** Until the thumbnail path consumes `cams[i]["frame"]` instead of calling `grab_preview_frame(device_index)` (`gui/wizard/scanning.py:41`), Task 1 is **strictly slower** per hit device: extra read, no offsetting saving. The `frame` semantics are a drop-in for `grab_preview_frame`'s contract (`frame if ok else None`, `gui/preview.py:46-47`).

- [ ] **Step 1: Write the failing tests**

Add to `webcam_client/tests/test_camera_manager.py`. Use a fake capture factory — no test may touch real hardware:

```python
def test_scan_stops_after_three_consecutive_misses():
    opened = []

    def factory(index):
        opened.append(index)
        return _FakeCapture(ok=index in (0, 1))

    cams = scan_cameras(max_index=10, stop_after_misses=3, capture_factory=factory)
    assert [c["device_index"] for c in cams] == [0, 1]
    # 0,1 hit; 2,3,4 miss -> stop. Index 5 must never be opened.
    assert opened == [0, 1, 2, 3, 4], opened


def test_full_sweep_finds_a_camera_past_the_gap():
    def factory(index):
        return _FakeCapture(ok=index in (0, 4))

    cams = scan_cameras(max_index=10, stop_after_misses=10, capture_factory=factory)
    assert [c["device_index"] for c in cams] == [0, 4]


def test_scan_returns_the_frame_it_already_grabbed():
    cams = scan_cameras(max_index=1, capture_factory=lambda i: _FakeCapture(ok=True))
    assert cams[0]["frame"] is not None, "the thumbnail must not need a second open"
```

- [ ] **Step 2: Verify RED** — run the file, confirm failures are about the new signature/keys, not import errors.
- [ ] **Step 3: Implement** — track `consecutive_misses`, `break` at the threshold, keep the retrieved frame on the dict. Preserve the existing `max_index` upper bound.
- [ ] **Step 4: Verify GREEN** — this file plus `test_setup_wizard.py` (its `fake_scan` returns dicts without `frame`; confirm nothing crashes on the missing key).

---

### Task 2: Move the wizard into `gui/wizard/` behind a façade (structural, no behaviour change)

**Files:**
- Create: `webcam_client/gui/wizard/{__init__,flow,connection,scanning,window}.py`
- Modify: `webcam_client/gui/setup_wizard.py` → thin re-export

**Interfaces:**
- `gui/setup_wizard.py` keeps exporting `run_setup_wizard`, `normalize_server_url`, `register_cameras`, `_client_identity_changed`, `_build_cameras_for_registration`, `_camera_rows_from_config`, `_scan_cameras_async`, `_load_thumbnail_async`, and a module-level `httpx`
- `main.py:9` (`from .gui.setup_wizard import run_setup_wizard`) is **not** touched in this task

**Why a façade first, when spec §10 says delete `setup_wizard.py`:** the end state is the spec's. The sequencing is not. Moving ~330 lines and rewriting 15 tests in one step means nothing proves the move preserved behaviour. With a façade, the **existing 15 tests pass unchanged**, and that is the proof. The façade is deleted in Task 9 once the new structure has its own tests.

**The one real hazard, verified at `test_setup_wizard.py:179` and `:207-208`:** two tests do `monkeypatch.setattr(sw, "scan_cameras", ...)` / `"grab_preview_frame"` / `"make_thumbnail"` — they rebind globals **in the `setup_wizard` module namespace**. A re-exported `_scan_cameras_async` living in `scanning.py` reads `scanning.scan_cameras`, which the monkeypatch never touches.

This fails **loudly**, not silently: the fake never runs, so `seen["thread"]` raises `KeyError` and the test errors — but only after really probing DSHOW hardware. Therefore:

- [ ] **Step 1:** Move bodies to the new modules; `gui/setup_wizard.py` becomes `from .wizard import *` plus explicit re-exports and `import httpx` (needed — seven tests patch `webcam_client.gui.setup_wizard.httpx.post`).
- [ ] **Step 2:** Update **only** the two patch targets in `test_setup_wizard.py:179, 207-208` to `webcam_client.gui.wizard.scanning`. Change nothing else in that file. Record in the commit message that exactly two lines changed and why.
- [ ] **Step 3: Verify GREEN** — all 15 `test_setup_wizard.py` tests pass, and `test_main_dispatch.py` (54 tests, monkeypatches `main.run_setup_wizard` at `:1574`) still passes. If any of the other 13 needed a change, the move was not behaviour-preserving — stop and find out why.

---

### Task 3: `wizard/flow.py` — the section-unlock state machine (pure)

**Files:**
- Create: `webcam_client/gui/wizard/flow.py`, `webcam_client/tests/test_wizard_flow.py`

**Interfaces:**
- Produces: `WizardFlow` with `.section2_unlocked -> bool`, `.can_confirm -> bool`, `.on_connection_verified()`, `.on_credentials_edited()`, `.on_cameras_selected(n: int)`
- Consumes: nothing. No Tk, no httpx, no I/O.

**Why edit mode starts pre-verified but re-locks:** a guard opening 設定 to rename a camera should not be forced to re-test a connection that is demonstrably working. But the moment they touch 連線位址 or 連線密碼, the verified state is a lie — so any edit to either field re-locks section 2. This is the same "the light must never lie" thesis Phase 2 was built on.

- [ ] **Step 1: Write the failing tests** — first-run starts locked; `on_connection_verified()` unlocks; `on_credentials_edited()` re-locks even after verification; edit mode starts unlocked; `can_confirm` is False with zero cameras selected.
- [ ] **Step 2: Verify RED.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify GREEN.**

---

### Task 4: `wizard/connection.py` — network off the Tk thread (U3, U4)

> Depends on Task 0's decision for the `test_connection` endpoint. `register_cameras` can be done first regardless.

**Files:**
- Create: `webcam_client/gui/wizard/connection.py`, `webcam_client/tests/test_wizard_connection.py`

**Interfaces:**
- Produces: `test_connection_async(url, key, on_done)`, `register_cameras_async(url, key, cams, on_done)`; both invoke `on_done(result, error_message_or_None)` **from the worker thread**
- Keeps: `normalize_server_url`, `register_cameras` (sync, already tested)

**The bug being fixed, verbatim at `gui/setup_wizard.py:298-300`:**

```python
        status_var.set("連線中...")
        root.update()
        cams, err = register_cameras(server_url, api_key, selected)
```

`root.update()` pumps the event loop once, then the synchronous POST blocks the Tk thread for the full connect timeout. The window is dead — unrepaintable, undraggable, "not responding" — for as long as the network takes. Phase 2's `54e5303` fixed this window's *copy*; it did not touch its *threading*.

**Two pre-existing bugs in `register_cameras`, found during the Task 2 move and deliberately left for this task** (a pure move was not the place to fix them). Both are in the result-zip loop at `gui/wizard/connection.py:74-83`:

1. **It can raise, despite its docstring promising it never does.** `r.get("node_id")` at `:81` sits **outside** the `try`, which guards only `resp.json()`. If the server returns 201 with a JSON *object* rather than a list, iterating it yields string keys, `r` is a truthy `str`, and `r.get` raises `AttributeError` straight out into the Tk callback — the exact "windowed exe swallows it, the 開始 button appears dead" failure this function exists to prevent.
2. **Silent under-registration.** The loop never checks `len(registered) == len(new_cams)`. If the server returns fewer entries than were posted, `next(reg, None)` yields `None`, the trailing cameras keep no `node_id`, and the function still returns `err is None` — so the wizard saves a config containing cameras that were never registered, with nothing said to the guard and nothing in the log.

Both must be closed here, with tests.

- [ ] **Step 1: Write the failing tests** — follow the proven idiom at `test_setup_wizard.py:170-190`: `threading.Event()`, `assert done.wait(5), "on_done was never called"`, and `assert seen["thread"] is not _t.current_thread()`. Assert every failure maps to a `strings.*` constant and that no status code or exception repr appears in the message.
- [ ] **Step 2: Verify RED.**
- [ ] **Step 3: Implement** — `threading.Thread(..., daemon=True)`; all exceptions become `(None, message)`; the status code and exception go to `logger`, never to the return value.
- [ ] **Step 4: Verify GREEN.**

---

### Task 5: `wizard/window.py` — the three-section relayout (U5, U8, U11, U12)

**Files:**
- Create: `webcam_client/gui/wizard/window.py`

**Interfaces:**
- Produces: `run_setup_wizard(config: dict, mode: str = "first-run") -> dict | None`
- Signature is load-bearing: `main.py:456` calls `run_setup_wizard(config, mode="first-run")`, `main.py:537` calls it with `mode="edit"`, and `test_main_dispatch.py:1574` monkeypatches it as `lambda cfg, **kw:`. Positional-first-plus-keyword must survive.

**Layout:** one window, three numbered sections, unlocked in order by `WizardFlow`.
1. **連線** — 連線位址 / 連線密碼 (+ 顯示 reveal toggle, U12) + 測試連線
2. **選擇要監控的攝影機** — locked until section 1 verifies; on success section 1 collapses to a green ✓ summary row
3. **開始監控** — plus the 開機時自動啟動 checkbox (Task 6)

**Per-item requirements:**
- **U5** — the camera list goes in a `Canvas` + `Scrollbar`. `gui/setup_wizard.py:171` currently sets `resizable(False, False)` with no scroll region, so six cameras render off the bottom of the screen with no way to reach 開始.
- **U8** — `<Return>` confirms, `<Escape>` cancels, `focus_set()` on 連線位址 at open. Zero such bindings exist today.
- **U11** — cancel is currently a bare `command=root.destroy` (`:320`). It must explain the consequence and say how to get back here.
  **Corrected 2026-07-30 — the single message this plan originally specified would have been false in BOTH modes**, verified against `main.py:456-459` and `:536`:
  - *First run:* cancel makes the wizard return `None` and `main()` **returns before `TrayApp` is ever constructed**. There is no tray icon. Copy that names the tray 設定 menu item sends the guard hunting for something that does not exist. → `WIZ_CONFIRM_CANCEL` says the program closes and the way back is to open it again.
  - *Edit mode:* the tray **is** up and cancelling stops nothing, so 「監控不會啟動」 is a lie. → `WIZ_CONFIRM_CANCEL_EDIT` says monitoring continues on the previous settings, and names the tray 設定 item, which there genuinely exists.
  Both constants exist and are tested. The window must pick by `mode`. (The alternative — change `main.py` so a first-run cancel does not exit — is a product decision, not an implementation one; not taken.)
- **U12** — a 顯示 toggle flipping `show="*"` (`:183`) to `show=""`. Default masked.
- **Confirm button label stays mode-dependent** (`:317`: `BTN_WIZARD_SAVE` in edit mode, else `BTN_WIZARD_START`). This is deliberate and is a bench item — do not collapse it to one label.
- **The confirm button must be disabled while a network call is in flight.** No widget in this codebase is ever disabled today — this is a **new** convention, not one to copy. Re-enable it from the `_safe_after` callback, including on the failure path.
- **`root.update()` must not appear anywhere in the new module.**
- **Suppress the count line when the list is empty.** Today the status line says 「找到 0 支攝影機」 while `_render` simultaneously shows `WIZ_NO_CAMERA_FOUND` — two messages about the same nothing, one of which is a number the guard cannot act on.
- **The scan is all-or-nothing today, and that is a guard-facing bug.** `gui/wizard/scanning.py:28-30` wraps the *whole* sweep in `except Exception: cams = []`, so a single flaky virtual-camera driver failing mid-sweep discards every camera already found — the guard has two working cameras and is told none exist. Found 2026-07-30 while implementing Task 1; deliberately not fixed there because per-device exception swallowing inside `scan_cameras` would have been untested new behaviour outside that task's brief. Fix it here or in a follow-up: catch per device, keep the hits, and let the copy distinguish "found nothing" from "some devices failed".

- [ ] **Step 1:** Build the window against `WizardFlow`; keep all logic in `flow.py`/`connection.py` so `window.py` holds only rendering.
- [ ] **Step 2:** Strip `frame` off every camera dict before `save_config` — see Task 7.
- [ ] **Step 3:** Manual smoke on a real display; automated coverage is Tasks 3/4. **No test may construct a `tk.Tk()`** — `webcam_client/tests/` has no display guard and must pass on a headless build machine.

---

### Task 6: `autostart.py` — 開機時自動啟動 (U10)

**Files:**
- Create: `webcam_client/autostart.py`, `webcam_client/tests/test_autostart.py`

**Interfaces:**
- Produces: `build_run_command(executable: str, frozen: bool) -> str | None` (pure), `is_enabled() -> bool`, `set_enabled(on: bool) -> bool`

**Why HKCU `Run` and not a Startup-folder `.lnk`:** a real `.lnk` needs COM `IShellLink`, i.e. `pywin32` — which is not in `requirements.txt` and would add payload against a 2.5 MB margin. `winreg` is stdlib and costs nothing. **The tradeoff being accepted:** the Startup folder could have been tested against real artifacts using the `monkeypatch.setenv("APPDATA", tmp_path)` pattern already proven at `test_config.py:12`, whereas `winreg` has no injectable root and can only ever be tested against a monkeypatched seam. Mitigate exactly as `buildconfig.py` mitigates the untestable `build.spec`: put the *decisions* in a pure function with real tests and keep the `winreg` shim to three trivial calls.

**Three traps, all mandatory:**
1. **Never write `sys._MEIPASS`.** Under onefile it is a per-launch `%TEMP%\_MEIxxxxxx` directory deleted at process exit; a Run value pointing there silently fails at the next boot and logs nothing. `sys.argv[0]` is also unreliable (may be relative). **`sys.executable` is the correct value** — PyInstaller sets it to the real exe path.
2. **Guard on `getattr(sys, "frozen", False)`.** In a source run `sys.executable` is `python.exe`; writing that produces a Run entry that launches a bare interpreter at every logon. No `sys.frozen` check exists anywhere in this repo today — this is new code.
3. **Quote the path.** A `REG_SZ` Run value is parsed as a command line and the path will contain spaces.

**Two further facts to design around, not discover at bench time:**
- **The checkbox can lie.** Disabling an entry via Task Manager writes to `HKCU\...\Explorer\StartupApproved\Run` and **leaves the `Run` value in place**. A checkbox reading only `Run` renders ON while autostart is OFF. On a branch whose entire thesis is that the status must never lie, either read `StartupApproved` too or do not claim to report state.
- **A failed registry write must never fail the config save.** Same rule `main.py:437-440` applies to `setup_logging()`: a nicety must not become a startup dependency.

- [ ] **Step 1: Write the failing tests** — `build_run_command` returns `None` when `frozen=False`; quotes a path containing spaces; never returns a string containing `_MEI`. Monkeypatch the `winreg` seam for `is_enabled`/`set_enabled`; assert a raising write returns `False` rather than propagating.
- [ ] **Step 2: Verify RED.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Verify GREEN.**

---

### Task 7: `config.py` — stop `save_config` corrupting the config

**Files:** Modify `webcam_client/config.py`; add a test to `webcam_client/tests/test_config.py`

**This is not defensive tidiness.** `config.py:111-112` opens the file in `"w"` — truncating it — and *then* runs `json.dump`. A non-serialisable value (a `numpy.ndarray` frame, a `PhotoImage`) raises `TypeError` **after the file is already empty**. Next launch, `load_config` hits `JSONDecodeError`, returns defaults (`:88-90`), and `main.py:454` fires the first-run wizard: the guard's address, password and cameras are gone. Task 1 puts an ndarray on every camera dict, so this becomes reachable in exactly this phase.

- [ ] **Step 1:** Test that `save_config` with a non-serialisable value leaves the **existing file intact**.
- [ ] **Step 2: Verify RED** (it will truncate).
- [ ] **Step 3:** Serialise to a string first, then write — `json.dumps(...)` before `open(...)`, or write-temp-then-`os.replace`. The latter also fixes power-loss-mid-write.
- [ ] **Step 4:** Add `"autostart": False` to `DEFAULT_CONFIG` (`:16-22`). `load_config` merges over defaults (`:91-92`), so this round-trips with no migration.
- [ ] **Step 5: Verify GREEN.**

---

### Task 8: `strings.py` — the new guard copy

**Files:** Modify `webcam_client/strings.py`; extend `webcam_client/tests/test_strings.py`

Move every string still hardcoded in the wizard into `strings.py`, where the automated scan covers it: `"伺服器連線"` (`:176`), `"攝影機"` (`:190`), `f"攝影機 {di}"` / `f"攝影機 {di} ({w}x{h})"` (`:255`, `:257`), `"名稱："` (`:217`), `"掃描中..."` (`:269`), `f"找到 {len(cams)} 支攝影機"` (`:265`), `"連線中..."` (`:298`). Add copy for the three section headings, 測試連線 and its outcomes, the 顯示 toggle, the cancel confirmation, and the autostart checkbox.

**Rules every new constant must satisfy** (`test_strings.py` discovers them automatically via `vars(strings)`):
- `UPPER_SNAKE`, module-level `str`.
- Must not match `(?<!\d)[1-5]\d\d(?!\d)`. **Trap:** this bans any bare 3-digit number in 100–599 — so `"640x480"` **fails** on the `480`. Resolution text needs a different form.
- Must not contain `Error`, `error`, `Exception`, `Traceback`, `None`, `null`, `HTTP`, `http`, `API`, `timeout`, `socket`. **Trap:** `http` bans quoting an example URL in operator copy.
- If it names a control, interpolate that control's constant by f-string — never retype the label. Add a drift test asserting `strings.BTN_X in <message>`.
- If it is a new stuck-state message, add it to the hardcoded `stuck` tuple in `test_the_setup_window_speaks_the_same_language_as_everything_else` (it does **not** auto-grow), and it must contain `請`. Escalate to `管理員` only if the guard genuinely cannot fix it alone.

- [ ] **Step 1:** Add constants. — [ ] **Step 2:** Extend the `stuck` tuple and add drift tests. — [ ] **Step 3: Verify GREEN.**

**Known gap worth closing here:** `test_the_destination_has_exactly_one_name` (「監控中心」must never appear) is scoped to `describe()` output only. The dynamic constant scan does not check it, so a new `WIZ_*` constant could slip it through. Extend the scan.

---

### Task 9: Retire the façade

**Files:** Delete `webcam_client/gui/setup_wizard.py`; rewrite `webcam_client/tests/test_setup_wizard.py`; modify `main.py:9`

Only after Tasks 3–5 have their own passing tests. This reaches the end state spec §10 specifies.

- [ ] **Step 1:** Point `main.py:9` at `from .gui.wizard import run_setup_wizard`. Confirm `run_setup_wizard` remains a module-level attribute of `webcam_client.main` (`test_main_dispatch.py:1574` monkeypatches it there).
- [ ] **Step 2:** Redistribute `test_setup_wizard.py`'s 15 tests to `test_wizard_flow.py` / `test_wizard_connection.py` / `test_camera_manager.py`. **Every assertion must survive somewhere** — if one no longer has a home, say so explicitly rather than dropping it.
- [ ] **Step 3:** Delete the façade.
- [ ] **Step 4: Verify GREEN across all 17+ test files.**

---

### Task 10: Bench verification (requires the human at hardware)

- [ ] Rebuild with `SDPRS_FFMPEG` exported. Confirm payload ≤250 MB — the gate is **250 MB**, per the spec's 驗收標準 table, not the 200 MB an earlier draft of the Phase 1 plan quoted.
- [ ] First-run wizard on a machine with no config: three sections, section 2 locked until 測試連線 passes.
- [ ] Camera scan completes in ~2 s with one camera attached (was 10–20 s).
- [ ] Each camera thumbnail appears **without** a second camera open (watch the activity LED).
- [ ] 測試連線 with a deliberately wrong password → `WIZ_KEY_REJECTED`, no status code on screen, code present in the log.
- [ ] 測試連線 with the network cable pulled → `WIZ_CANNOT_REACH_SERVER`; window stays draggable throughout (U3).
- [ ] Six cameras → list scrolls, 開始 reachable (U5).
- [ ] Enter confirms, Esc cancels, 連線位址 focused at open (U8).
- [ ] Cancel explains the consequence and names the way back (U11).
- [ ] 顯示 reveals the password, default masked (U12).
- [ ] Edit mode: confirm button reads 儲存, not 開始. Editing the password re-locks section 2.
- [ ] Autostart ticked → reboot → client starts. Untick → reboot → it does not. Then disable via Task Manager and confirm the checkbox does not lie.
- [ ] **The one thing `test_autostart.py` cannot prove: that deleting the `StartupApproved` value actually re-enables the entry at the next boot.** The blob semantics were confirmed by reading the live registry (first byte `0x02` enabled / `0x03` disabled), but the *write-side* effect is inferred from "absent = enabled", which is Windows' documented default, not something observed. Sequence to run: tick autostart → disable via Task Manager → re-tick in the wizard → **reboot** → confirm the client actually starts.
- [ ] **Accepted cost to confirm, not discover:** autostart means the ~17 s onefile extraction and its splash happen at **every logon**. The splash is painted by the bootloader before Python starts, so no CLI flag can suppress it. Confirm this is acceptable to the user.
- [ ] Guard the broken-config path: `main.py:454` pops a modal wizard at every logon if `server_url` is empty, and `main.py:464-468` exits **silently with no UI** when no cameras are enabled.
