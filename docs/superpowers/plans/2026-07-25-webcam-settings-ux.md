# Webcam Settings-Editable + Launch-Speed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the SDPRS webcam client's settings editable after first setup (from the system tray), apply changes in-process without an app restart, speed up cold launch, and land four targeted UX fixes.

**Architecture:** Invert the thread model so the GUI (Tkinter) owns the main thread. A new `AppController` owns the running `PushEngine`s + `ControlChannel` and exposes `start_engines()` / `stop_engines()` (releases cameras) / `apply(new_config)` / `pause_all()` / `resume_all()`. The tray runs in its pystray daemon thread and its callbacks only **enqueue** requests onto a `queue.Queue`; the main thread services the queue, opening the settings window on the main thread and rebuilding engines in place on save.

**Tech Stack:** Python 3.14 / tkinter / pystray / OpenCV (cv2, DirectShow) / httpx / PyInstaller (onefile).

## Global Constraints

- Python interpreter: `/c/Python314/python`. Run pytest **ONE FILE PER INVOCATION** with `-p no:cacheprovider` (the `[Cloud]` bracket path breaks whole-dir collection).
- Write/Edit require **absolute Windows paths**.
- Packaging stays **single-file (onefile)** — no onedir, no `COLLECT`. (User decision.)
- Settings apply **in-process, no app restart**. (User decision.)
- All UI strings are **Traditional Chinese (zh-TW)**.
- Security (verbatim, must hold): **no hardcoded credentials of any kind**; the literal strings `Msc@***` and `MSC-***` must **NEVER** appear anywhere; no `broker.emqx.io` in any production path; **do NOT add any new command/downlink surface to edge devices beyond the existing `stream_start` / `stream_stop`**. This work is client-side and touches none of these — keep it that way.
- Working dir for all commands: `sdprs/`. Branch: `feat/webcam-settings-ux` (spec already committed there).

---

## File Structure

- **Create** `webcam_client/app_controller.py` — engine/control lifecycle owner (`AppController`). Single responsibility: own and rebuild the worker threads.
- **Create** `webcam_client/tests/test_app_controller.py` — unit tests with injected fake engine/control factories (no real cameras/network).
- **Modify** `webcam_client/gui/setup_wizard.py` — idempotent `register_cameras`; `mode` param on the window; async (off-UI-thread) camera scan; two new pure/threaded helpers.
- **Modify** `webcam_client/main.py` — queue dispatch loop; GUI on the main thread; wire the controller. New testable seam `_handle_request`.
- **Modify** `webcam_client/gui/tray_app.py` — pause label reflects state; amber paused icon; two pure helpers.
- **Modify** `webcam_client/build.spec` — `upx=False` for faster cold start (still onefile).
- **Modify** tests: `webcam_client/tests/test_setup_wizard.py`, `test_tray_app.py`, `test_packaging.py`.

---

## Task 1: AppController — engine lifecycle owner

**Files:**
- Create: `webcam_client/app_controller.py`
- Test: `webcam_client/tests/test_app_controller.py`

**Interfaces:**
- Consumes: `webcam_client.push_engine.PushEngine(camera_config: dict, server_url: str, api_key: str)` with `.start()/.stop()/.join(timeout)/.set_paused(bool)/.set_streaming(bool)/._node_id`; `webcam_client.control_channel.ControlChannel(server_url, api_key, node_ids: list, on_command)` with `.start()/.stop()`.
- Produces: `AppController(config: dict, *, engine_factory=None, control_factory=None)` with `.config` (property), `.start_engines()`, `.stop_engines()`, `.apply(new_config: dict)`, `.pause_all()`, `.resume_all()`, `.shutdown()`. Factories have signatures `engine_factory(cam: dict, server_url: str, api_key: str)` and `control_factory(server_url: str, api_key: str, node_ids: list, on_command: Callable)`.

- [ ] **Step 1: Write the failing test**

Create `webcam_client/tests/test_app_controller.py`:

```python
# webcam_client/tests/test_app_controller.py
"""AppController owns the worker threads so the MAIN thread can stop them
(freeing cameras), rebuild them from a new config in-process, and fan out
pause/resume. Factories are injected so this is testable without real cameras
or network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.app_controller import AppController


class FakeEngine:
    def __init__(self, cam, server_url, api_key):
        self.cam, self.server_url, self.api_key = cam, server_url, api_key
        self._node_id = cam.get("node_id", "")
        self.started = self.stopped = self.joined = False
        self.paused = None
        self.streaming = None

    def start(self): self.started = True
    def stop(self): self.stopped = True
    def join(self, timeout=None): self.joined = True
    def set_paused(self, v): self.paused = v
    def set_streaming(self, v): self.streaming = v


class FakeControl:
    def __init__(self, server_url, api_key, node_ids, on_command):
        self.node_ids, self.on_command = node_ids, on_command
        self.started = self.stopped = False

    def start(self): self.started = True
    def stop(self): self.stopped = True


def _controller(config):
    made = {"engines": [], "controls": []}
    def ef(cam, s, k):
        e = FakeEngine(cam, s, k); made["engines"].append(e); return e
    def cf(s, k, ids, cb):
        c = FakeControl(s, k, ids, cb); made["controls"].append(c); return c
    return AppController(config, engine_factory=ef, control_factory=cf), made


CONFIG = {
    "server_url": "http://x", "api_key": "k", "motion_threshold": 30,
    "cameras": [
        {"device_index": 0, "node_id": "webcam_a", "enabled": True},
        {"device_index": 1, "node_id": "webcam_b", "enabled": True},
    ],
}


def test_start_engines_builds_one_per_enabled_camera_and_starts_them():
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    assert len(made["engines"]) == 2
    assert all(e.started for e in made["engines"])
    assert made["engines"][0].cam["motion_threshold"] == 30
    assert made["controls"][0].node_ids == ["webcam_a", "webcam_b"]
    assert made["controls"][0].started


def test_stop_engines_stops_joins_and_clears():
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    first = list(made["engines"])
    ctrl.stop_engines()
    assert all(e.stopped and e.joined for e in first)
    assert made["controls"][0].stopped
    ctrl.start_engines()
    assert len(made["engines"]) == 4  # fresh engines, old ones not reused


def test_apply_stops_old_then_starts_new_and_updates_config():
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    old = list(made["engines"])
    new_cfg = {"server_url": "http://y", "api_key": "k2",
               "cameras": [{"device_index": 0, "node_id": "webcam_c", "enabled": True}]}
    ctrl.apply(new_cfg)
    assert all(e.stopped for e in old)
    assert ctrl.config["server_url"] == "http://y"
    assert made["engines"][-1].server_url == "http://y"
    assert len(made["engines"]) == 3  # 2 old + 1 new built


def test_pause_and_resume_fan_out_to_current_engines():
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    ctrl.pause_all()
    assert all(e.paused is True for e in made["engines"])
    ctrl.resume_all()
    assert all(e.paused is False for e in made["engines"])


def test_disabled_cameras_are_skipped():
    cfg = {"server_url": "http://x", "api_key": "k", "cameras": [
        {"device_index": 0, "node_id": "a", "enabled": True},
        {"device_index": 1, "node_id": "b", "enabled": False},
    ]}
    ctrl, made = _controller(cfg)
    ctrl.start_engines()
    assert len(made["engines"]) == 1
    assert made["controls"][0].node_ids == ["a"]


def test_on_command_routes_stream_toggles_to_matching_engine():
    ctrl, made = _controller(CONFIG)
    ctrl.start_engines()
    cb = made["controls"][0].on_command
    cb("webcam_b", "stream_start", None)
    assert made["engines"][1].streaming is True
    cb("webcam_b", "stream_stop", None)
    assert made["engines"][1].streaming is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest webcam_client/tests/test_app_controller.py -p no:cacheprovider -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'webcam_client.app_controller'`.

- [ ] **Step 3: Write minimal implementation**

Create `webcam_client/app_controller.py`:

```python
# webcam_client/app_controller.py
import logging
from typing import Callable, List, Optional

logger = logging.getLogger("webcam_client.app_controller")


def _default_engine_factory(cam: dict, server_url: str, api_key: str):
    from .push_engine import PushEngine
    return PushEngine(cam, server_url, api_key)


def _default_control_factory(server_url: str, api_key: str, node_ids: list,
                             on_command: Callable):
    from .control_channel import ControlChannel
    return ControlChannel(server_url, api_key, node_ids, on_command)


class AppController:
    """Owns the running PushEngines + ControlChannel. Lets the MAIN thread stop
    them (releasing cameras), rebuild them from a new config in-process, and fan
    out pause/resume. Factories are injectable for unit testing without real
    cameras or network."""

    def __init__(self, config: dict, *, engine_factory: Optional[Callable] = None,
                 control_factory: Optional[Callable] = None):
        self._config = dict(config)
        self._engine_factory = engine_factory or _default_engine_factory
        self._control_factory = control_factory or _default_control_factory
        self._engines: List = []
        self._control = None

    @property
    def config(self) -> dict:
        return self._config

    def _enabled_cameras(self) -> List[dict]:
        return [c for c in self._config.get("cameras", []) if c.get("enabled", True)]

    def start_engines(self) -> None:
        server_url = self._config.get("server_url", "")
        api_key = self._config.get("api_key", "")
        motion = self._config.get("motion_threshold", 25)
        for cam in self._enabled_cameras():
            cam = dict(cam)
            cam["motion_threshold"] = motion
            engine = self._engine_factory(cam, server_url, api_key)
            engine.start()
            self._engines.append(engine)
        node_ids = [c["node_id"] for c in self._enabled_cameras() if c.get("node_id")]
        self._control = self._control_factory(server_url, api_key, node_ids,
                                              self._on_command)
        self._control.start()

    def stop_engines(self) -> None:
        if self._control is not None:
            self._control.stop()
            self._control = None
        for e in self._engines:
            e.stop()
        for e in self._engines:
            join = getattr(e, "join", None)
            if callable(join):
                join(timeout=5)
        self._engines = []

    def apply(self, new_config: dict) -> None:
        self.stop_engines()
        self._config = dict(new_config)
        self.start_engines()

    def pause_all(self) -> None:
        for e in self._engines:
            e.set_paused(True)

    def resume_all(self) -> None:
        for e in self._engines:
            e.set_paused(False)

    def shutdown(self) -> None:
        self.stop_engines()

    def _on_command(self, node_id: str, command: str,
                    params: Optional[dict] = None) -> None:
        for e in self._engines:
            if getattr(e, "_node_id", None) == node_id:
                if command == "stream_start":
                    e.set_streaming(True)
                elif command == "stream_stop":
                    e.set_streaming(False)
                break
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Python314/python -m pytest webcam_client/tests/test_app_controller.py -p no:cacheprovider -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add webcam_client/app_controller.py webcam_client/tests/test_app_controller.py
git commit -m "feat(webcam): AppController owns engine lifecycle (in-process apply)"
```

---

## Task 2: Idempotent camera registration

**Files:**
- Modify: `webcam_client/gui/setup_wizard.py` (`register_cameras`)
- Test: `webcam_client/tests/test_setup_wizard.py`

**Interfaces:**
- Produces: `register_cameras(server_url, api_key, selected: list) -> (list | None, str | None)` — now POSTs **only** cameras whose dict lacks `node_id`; cameras that already have one are returned unchanged and never re-POSTed. Order preserved.

- [ ] **Step 1: Write the failing test** — append to `webcam_client/tests/test_setup_wizard.py`:

```python
def test_register_skips_post_when_all_cameras_already_registered():
    # Editing settings must NOT re-register already-known cameras (that minted a
    # fresh node_id each edit -> duplicate dashboard tiles).
    selected = [{"device_index": 0, "node_id": "webcam_a", "name": "A"}]
    with patch("webcam_client.gui.setup_wizard.httpx.post") as post:
        cams, err = register_cameras("http://x", "k", selected)
    post.assert_not_called()
    assert err is None
    assert cams[0]["node_id"] == "webcam_a"


def test_register_posts_only_new_cameras_and_preserves_existing_ids():
    selected = [
        {"device_index": 0, "node_id": "webcam_a", "name": "A"},  # existing
        {"device_index": 1, "name": "B"},                          # new
    ]
    resp = MagicMock(status_code=201)
    resp.json.return_value = [{"node_id": "webcam_new"}]
    with patch("webcam_client.gui.setup_wizard.httpx.post", return_value=resp) as post:
        cams, err = register_cameras("http://x", "k", selected)
    body = post.call_args.kwargs["json"]
    assert body["cameras"] == [{"device_index": 1, "name": "B"}]  # only the new one
    assert err is None
    assert cams[0]["node_id"] == "webcam_a"    # preserved
    assert cams[1]["node_id"] == "webcam_new"  # assigned to the new one
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest webcam_client/tests/test_setup_wizard.py -p no:cacheprovider -q`
Expected: FAIL — `test_register_skips_post_when_all_cameras_already_registered` fails (old code always POSTs) and the body assertion fails (old code sends all `selected`).

- [ ] **Step 3: Write minimal implementation** — replace the body of `register_cameras` in `webcam_client/gui/setup_wizard.py` with:

```python
def register_cameras(server_url: str, api_key: str, selected: list):
    """POST only cameras that lack a node_id; keep already-registered ones as-is.

    Editing settings must be idempotent: re-registering every camera on each
    edit minted a fresh node_id per camera, so the dashboard grew a duplicate
    webcam tile every time settings were opened. Cameras that already carry a
    node_id are left untouched; only new ones are sent.

    Returns ``(cameras, None)`` on success or ``(None, message)`` on ANY failure.
    Never raises: the caller runs inside a Tk callback where an unhandled
    exception is swallowed in a windowed exe and the Start button appears dead.
    """
    new_cams = [dict(c) for c in selected if not c.get("node_id")]
    if not new_cams:
        return [dict(c) for c in selected], None
    try:
        resp = httpx.post(
            f"{server_url}/api/webcam/cameras",
            json={"cameras": new_cams},
            headers={"X-API-Key": api_key},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        return None, f"無法連線到伺服器：{e}"
    if resp.status_code == 401:
        return None, "API Key 無效"
    if resp.status_code != 201:
        return None, f"伺服器回應：{resp.status_code}"
    try:
        registered = resp.json()
    except Exception:
        return None, "伺服器回應格式錯誤（非 JSON）"
    reg = iter(registered)
    result = []
    for c in selected:
        c = dict(c)
        if not c.get("node_id"):
            r = next(reg, None)
            if r:
                c["node_id"] = r.get("node_id")
        result.append(c)
    return result, None
```

- [ ] **Step 4: Run test to verify it passes** (and existing wizard tests stay green)

Run: `/c/Python314/python -m pytest webcam_client/tests/test_setup_wizard.py -p no:cacheprovider -q`
Expected: PASS (all — the 6 original + 2 new).

- [ ] **Step 5: Commit**

```bash
git add webcam_client/gui/setup_wizard.py webcam_client/tests/test_setup_wizard.py
git commit -m "fix(webcam): register cameras idempotently (no duplicate node_ids on edit)"
```

---

## Task 3: Settings window — mode split + non-freezing scan

**Files:**
- Modify: `webcam_client/gui/setup_wizard.py` (`run_setup_wizard`; add `_scan_cameras_async`, `_camera_rows_from_config`)
- Test: `webcam_client/tests/test_setup_wizard.py`

**Interfaces:**
- Produces: `_scan_cameras_async(on_done: Callable[[list], None], max_index: int = 10) -> None` (runs `scan_cameras` on a daemon thread, then calls `on_done(cams)`); `_camera_rows_from_config(config: dict) -> list[dict]` (edit-mode prefill rows, each keeping `node_id`); `run_setup_wizard(existing_config=None, mode: str = "first-run") -> dict | None` (mode is `"first-run"` or `"edit"`).
- Consumes: `scan_cameras` (Task's module already imports it), `register_cameras` (Task 2).

- [ ] **Step 1: Write the failing test** — append to `webcam_client/tests/test_setup_wizard.py`:

```python
def test_scan_cameras_async_runs_off_the_calling_thread(monkeypatch):
    import threading as _t
    from webcam_client.gui import setup_wizard as sw
    seen = {}

    def fake_scan(max_index=10):
        seen["thread"] = _t.current_thread()
        return [{"device_index": 0, "width": 640, "height": 480}]

    monkeypatch.setattr(sw, "scan_cameras", fake_scan)
    done = _t.Event()
    result = {}

    def on_done(cams):
        result["cams"] = cams
        done.set()

    sw._scan_cameras_async(on_done)
    assert done.wait(5), "on_done was never called"
    assert seen["thread"] is not _t.current_thread(), "scan must not run on the caller thread"
    assert result["cams"][0]["device_index"] == 0


def test_camera_rows_from_config_preserves_node_id_and_name():
    from webcam_client.gui.setup_wizard import _camera_rows_from_config
    cfg = {"cameras": [
        {"device_index": 2, "name": "前門", "node_id": "webcam_a", "enabled": True},
        {"device_index": 5, "name": "後門", "node_id": "webcam_b", "enabled": False},
    ]}
    assert _camera_rows_from_config(cfg) == [
        {"device_index": 2, "name": "前門", "node_id": "webcam_a", "enabled": True},
        {"device_index": 5, "name": "後門", "node_id": "webcam_b", "enabled": False},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest webcam_client/tests/test_setup_wizard.py -p no:cacheprovider -q`
Expected: FAIL — `AttributeError` / `ImportError` for `_scan_cameras_async` and `_camera_rows_from_config`.

- [ ] **Step 3: Write minimal implementation** — in `webcam_client/gui/setup_wizard.py`, add the two helpers (near the top, after the imports/`logger`):

```python
def _scan_cameras_async(on_done, max_index: int = 10) -> None:
    """Run the slow, blocking DSHOW probe on a worker thread so the settings
    window paints immediately and never freezes on 掃描中…. on_done(cams) is
    invoked from the worker thread; a Tk caller marshals back with root.after."""
    def worker():
        try:
            cams = scan_cameras(max_index)
        except Exception as e:
            logger.warning(f"camera scan failed: {e}")
            cams = []
        on_done(cams)
    threading.Thread(target=worker, daemon=True).start()


def _camera_rows_from_config(config: dict) -> list:
    """Edit-mode prefill: the rows to show from a saved config, each keeping its
    node_id so a re-save re-registers nothing (see register_cameras)."""
    rows = []
    for c in config.get("cameras", []):
        rows.append({
            "device_index": c.get("device_index"),
            "name": c.get("name", f"Webcam {c.get('device_index')}"),
            "node_id": c.get("node_id"),
            "enabled": c.get("enabled", True),
        })
    return rows
```

Then replace `run_setup_wizard` with the mode-aware version below. `cam_vars` now holds `(row_dict, enabled_var, name_var)` where `row_dict` carries `device_index` and optional `node_id`, so `on_start` preserves `node_id`. First-run auto-scans **asynchronously**; edit mode prefills from config and rescans only on a button.

```python
def run_setup_wizard(existing_config=None, mode: str = "first-run"):
    result = {"config": None}
    root = tk.Tk()
    title = "SDPRS Webcam 設定" if mode == "first-run" else "SDPRS Webcam 設定（編輯）"
    root.title(title)
    root.geometry("500x480")
    root.resizable(False, False)

    config = existing_config or {}

    # --- Frame: Server connection ---
    frame_conn = ttk.LabelFrame(root, text="伺服器連線", padding=10)
    frame_conn.pack(fill="x", padx=10, pady=5)
    ttk.Label(frame_conn, text="Server URL:").grid(row=0, column=0, sticky="w")
    url_var = tk.StringVar(value=config.get("server_url", ""))
    ttk.Entry(frame_conn, textvariable=url_var, width=40).grid(row=0, column=1, padx=5)
    ttk.Label(frame_conn, text="API Key:").grid(row=1, column=0, sticky="w", pady=5)
    key_var = tk.StringVar(value=config.get("api_key", ""))
    ttk.Entry(frame_conn, textvariable=key_var, width=40, show="*").grid(
        row=1, column=1, padx=5, pady=5)
    status_var = tk.StringVar(value="")
    ttk.Label(frame_conn, textvariable=status_var, foreground="gray").grid(
        row=2, column=0, columnspan=2)

    # --- Frame: Camera selection ---
    frame_cam = ttk.LabelFrame(root, text="攝影機", padding=10)
    frame_cam.pack(fill="both", expand=True, padx=10, pady=5)
    cam_vars = []
    cam_frame_inner = ttk.Frame(frame_cam)
    cam_frame_inner.pack(fill="both", expand=True)

    def _add_row(device_index, name, node_id, enabled, subtitle):
        var = tk.BooleanVar(value=enabled)
        name_var = tk.StringVar(value=name)
        cam_vars.append(({"device_index": device_index, "node_id": node_id},
                         var, name_var))
        row = ttk.Frame(cam_frame_inner)
        row.pack(fill="x", anchor="w", pady=2)
        ttk.Checkbutton(row, text=subtitle, variable=var).pack(side="left")
        ttk.Label(row, text="名稱:").pack(side="left", padx=(8, 2))
        ttk.Entry(row, textvariable=name_var, width=16).pack(side="left")
        # Live thumbnail is best-effort; a busy device yields None -> omitted.
        thumb = make_thumbnail(grab_preview_frame(device_index))
        if thumb is not None:
            lbl = ttk.Label(row, image=thumb)
            lbl.image = thumb  # keep a ref so Tk doesn't GC the PhotoImage
            lbl.pack(side="right")

    def _render(rows):
        for w in cam_frame_inner.winfo_children():
            w.destroy()
        cam_vars.clear()
        if not rows:
            ttk.Label(cam_frame_inner, text="未偵測到攝影機").pack()
        for r in rows:
            di = r["device_index"]
            subtitle = f"Camera {di}"
            if r.get("width") and r.get("height"):
                subtitle = f"Camera {di} ({r['width']}x{r['height']})"
            _add_row(di, r.get("name", f"Webcam {di}"),
                     r.get("node_id"), r.get("enabled", True), subtitle)

    def _on_scan_done(cams):
        # Called from the worker thread -> marshal back to the Tk thread.
        def apply_ui():
            _render(cams)
            status_var.set(f"找到 {len(cams)} 支攝影機")
        root.after(0, apply_ui)

    def do_scan():
        status_var.set("掃描中...")
        _scan_cameras_async(_on_scan_done)

    ttk.Button(frame_cam, text="重新掃描", command=do_scan).pack(anchor="e", pady=5)

    # --- Buttons ---
    frame_btn = ttk.Frame(root, padding=10)
    frame_btn.pack(fill="x")

    def on_start():
        server_url = normalize_server_url(url_var.get())
        api_key = key_var.get().strip()
        if not server_url or not api_key:
            messagebox.showerror("錯誤", "請填入 Server URL 和 API Key")
            return
        selected = []
        for row, v, nv in cam_vars:
            if not v.get():
                continue
            cam = {"device_index": row["device_index"],
                   "name": nv.get().strip() or f"Webcam {row['device_index']}",
                   "resolution": [640, 480], "jpeg_quality": 40, "target_fps": 8}
            if row.get("node_id"):
                cam["node_id"] = row["node_id"]  # preserve -> idempotent register
            selected.append(cam)
        if not selected:
            messagebox.showerror("錯誤", "請至少選擇一支攝影機")
            return
        status_var.set("連線中...")
        root.update()
        cams, err = register_cameras(server_url, api_key, selected)
        status_var.set("")
        if err:
            messagebox.showerror("錯誤", err)
            return
        result["config"] = {
            "server_url": server_url, "api_key": api_key, "cameras": cams,
            "motion_threshold": config.get("motion_threshold", 25),
            "heartbeat_interval": config.get("heartbeat_interval", 30),
        }
        root.destroy()

    ttk.Button(frame_btn, text="開始", command=on_start).pack(side="right")
    ttk.Button(frame_btn, text="取消", command=root.destroy).pack(side="right", padx=5)

    if mode == "edit":
        _render(_camera_rows_from_config(config))  # show saved cameras, no rescan
    else:
        root.after(100, do_scan)                    # first run: async auto-scan

    root.mainloop()
    return result["config"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Python314/python -m pytest webcam_client/tests/test_setup_wizard.py -p no:cacheprovider -q`
Expected: PASS (all — original + Task 2's + these 2).

- [ ] **Step 5: Commit**

```bash
git add webcam_client/gui/setup_wizard.py webcam_client/tests/test_setup_wizard.py
git commit -m "feat(webcam): settings window mode split + non-freezing async scan"
```

---

## Task 4: Main-thread dispatch loop

**Files:**
- Modify: `webcam_client/main.py`
- Test: `webcam_client/tests/test_main_dispatch.py` (create)

**Interfaces:**
- Consumes: `AppController` (Task 1), `run_setup_wizard(cfg, mode=...)` (Task 3), `TrayApp` (Task 5 keeps its 4-callback constructor), `save_config`.
- Produces: `_handle_request(req: str, controller, settings_fn: Callable[[dict], dict | None]) -> bool` — returns `False` only for `"QUIT"`.

- [ ] **Step 1: Write the failing test** — create `webcam_client/tests/test_main_dispatch.py`:

```python
# webcam_client/tests/test_main_dispatch.py
"""The tray runs in a daemon thread; its callbacks must only ENQUEUE, so the
GUI opens on the MAIN thread. _handle_request is that main-thread servicer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class FakeController:
    def __init__(self, config):
        self._config = config
        self.calls = []
        self.applied = None

    @property
    def config(self):
        return self._config

    def stop_engines(self): self.calls.append("stop_engines")
    def start_engines(self): self.calls.append("start_engines")
    def apply(self, cfg): self.calls.append("apply"); self.applied = cfg
    def shutdown(self): self.calls.append("shutdown")


def test_open_settings_applies_new_config_on_save(monkeypatch):
    import webcam_client.main as m
    saved = {}
    monkeypatch.setattr(m, "save_config", lambda c: saved.update(c))
    ctrl = FakeController({"server_url": "old"})
    keep = m._handle_request("OPEN_SETTINGS", ctrl, lambda cfg: {"server_url": "new"})
    assert keep is True
    assert ctrl.calls == ["stop_engines", "apply"]
    assert ctrl.applied == {"server_url": "new"}
    assert saved == {"server_url": "new"}


def test_open_settings_resumes_old_config_on_cancel(monkeypatch):
    import webcam_client.main as m
    monkeypatch.setattr(m, "save_config", lambda c: None)
    ctrl = FakeController({"server_url": "old"})
    keep = m._handle_request("OPEN_SETTINGS", ctrl, lambda cfg: None)
    assert keep is True
    assert ctrl.calls == ["stop_engines", "start_engines"]
    assert ctrl.applied is None


def test_quit_shuts_down_and_stops_loop():
    import webcam_client.main as m
    ctrl = FakeController({})
    keep = m._handle_request("QUIT", ctrl, lambda cfg: None)
    assert keep is False
    assert ctrl.calls == ["shutdown"]


def test_tray_open_settings_callback_only_enqueues():
    import queue
    q = queue.Queue()
    on_open = lambda: q.put("OPEN_SETTINGS")
    on_open()
    assert q.get_nowait() == "OPEN_SETTINGS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest webcam_client/tests/test_main_dispatch.py -p no:cacheprovider -q`
Expected: FAIL — `AttributeError: module 'webcam_client.main' has no attribute '_handle_request'`.

- [ ] **Step 3: Write minimal implementation** — rewrite `webcam_client/main.py` as:

```python
# sdprs/webcam_client/main.py
import logging
import queue
import signal

from .config import load_config, save_config, is_first_run
from .app_controller import AppController
from .gui.setup_wizard import run_setup_wizard
from .gui.tray_app import TrayApp

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("webcam_client.main")

_running = True


def _signal_handler(sig, frame):
    global _running
    _running = False


def _handle_request(req, controller, settings_fn) -> bool:
    """Service one queued request on the MAIN thread. Returns False to quit.

    The tray (daemon thread) only enqueues; opening the settings window and
    rebuilding engines therefore happen here, on the main thread, which is what
    lets Tk run correctly and the cameras be released before the window scans."""
    if req == "QUIT":
        controller.shutdown()
        return False
    if req == "OPEN_SETTINGS":
        controller.stop_engines()          # free the cameras for the wizard
        new_cfg = settings_fn(controller.config)  # runs on the main thread
        if new_cfg:
            save_config(new_cfg)
            controller.apply(new_cfg)      # rebuild in-process, no restart
        else:
            controller.start_engines()     # cancelled -> resume old config
    return True


def main():
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    config = load_config()
    if is_first_run() or not config.get("server_url"):
        new_config = run_setup_wizard(config, mode="first-run")
        if new_config is None:
            logger.info("Setup cancelled, exiting")
            return
        config = new_config
        save_config(config)

    enabled = [c for c in config.get("cameras", []) if c.get("enabled", True)]
    if not enabled:
        logger.error("No cameras configured")
        return

    controller = AppController(config)
    controller.start_engines()

    q: "queue.Queue[str]" = queue.Queue()
    tray = TrayApp(
        on_open_settings=lambda: q.put("OPEN_SETTINGS"),
        on_quit=lambda: q.put("QUIT"),
        on_pause=controller.pause_all,
        on_resume=controller.resume_all,
    )
    tray.start()
    tray.set_status(True)
    logger.info(f"SDPRS Webcam Client running ({len(enabled)} cameras)")

    running = True
    while running and _running:
        try:
            req = q.get(timeout=1.0)
        except queue.Empty:
            continue
        running = _handle_request(
            req, controller, lambda cfg: run_setup_wizard(cfg, mode="edit"))
    controller.shutdown()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Python314/python -m pytest webcam_client/tests/test_main_dispatch.py -p no:cacheprovider -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add webcam_client/main.py webcam_client/tests/test_main_dispatch.py
git commit -m "feat(webcam): main-thread queue dispatch; GUI off the tray thread"
```

---

## Task 5: Tray pause reflects state (label + amber icon)

**Files:**
- Modify: `webcam_client/gui/tray_app.py`
- Test: `webcam_client/tests/test_tray_app.py`

**Interfaces:**
- Produces: `_pause_label(paused: bool) -> str`; `_icon_color(paused: bool, connected: bool) -> str`; `_create_icon(color)` now also accepts `"amber"`. `TrayApp` keeps its 4-callback constructor and `set_status(connected)`.

- [ ] **Step 1: Write the failing test** — append to `webcam_client/tests/test_tray_app.py`:

```python
def test_pause_label_reflects_state():
    from webcam_client.gui.tray_app import _pause_label
    assert _pause_label(False) == "暫停推送"
    assert _pause_label(True) == "恢復推送"


def test_icon_color_paused_beats_connection():
    from webcam_client.gui.tray_app import _icon_color
    assert _icon_color(paused=False, connected=True) == "green"
    assert _icon_color(paused=False, connected=False) == "red"
    assert _icon_color(paused=True, connected=True) == "amber"
    assert _icon_color(paused=True, connected=False) == "amber"


@pytest.mark.skipif(not TRAY_AVAILABLE, reason="PIL/pystray not installed")
def test_create_icon_amber():
    assert _create_icon("amber").getpixel((32, 32))[:3] == (230, 160, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest webcam_client/tests/test_tray_app.py -p no:cacheprovider -q`
Expected: FAIL — `ImportError` for `_pause_label` / `_icon_color`.

- [ ] **Step 3: Write minimal implementation** — in `webcam_client/gui/tray_app.py`, replace `_create_icon` and add the two helpers:

```python
def _create_icon(color: str = "green") -> "Image.Image":
    # Transparent background needs RGBA + (0,0,0,0); "transparent" is not a valid
    # PIL color and raises ValueError (that crashed startup once).
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    palette = {"green": (0, 200, 0, 255), "red": (220, 50, 50, 255),
               "amber": (230, 160, 0, 255)}
    c = palette.get(color, palette["green"])
    draw.ellipse([8, 8, 56, 56], fill=c)
    return img


def _pause_label(paused: bool) -> str:
    return "恢復推送" if paused else "暫停推送"


def _icon_color(paused: bool, connected: bool) -> str:
    # Paused is the operator's most important state, so it wins over connection.
    if paused:
        return "amber"
    return "green" if connected else "red"
```

Then update `TrayApp` to track `_connected`, drive the icon through `_icon_color`, and make the pause menu label dynamic:

```python
class TrayApp:
    def __init__(self, on_open_settings: Callable, on_quit: Callable,
                 on_pause: Callable, on_resume: Callable):
        self._on_open_settings = on_open_settings
        self._on_quit = on_quit
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._icon: Optional["pystray.Icon"] = None
        self._paused = False
        self._connected = False

    def _refresh_icon(self) -> None:
        if self._icon and TRAY_AVAILABLE:
            self._icon.icon = _create_icon(_icon_color(self._paused, self._connected))

    def set_status(self, connected: bool) -> None:
        self._connected = connected
        self._refresh_icon()

    def start(self) -> None:
        if not TRAY_AVAILABLE:
            logger.warning("pystray not available, running without tray icon")
            return
        menu = pystray.Menu(
            pystray.MenuItem("開啟設定", lambda: self._on_open_settings()),
            pystray.MenuItem(lambda item: _pause_label(self._paused),
                             lambda: self._toggle_pause()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("離開", lambda: self._quit()),
        )
        self._icon = pystray.Icon("SDPRS Webcam", _create_icon("green"),
                                  "SDPRS Webcam", menu)
        threading.Thread(target=self._icon.run, daemon=True).start()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self._on_pause()
        else:
            self._on_resume()
        self._refresh_icon()
        if self._icon and TRAY_AVAILABLE:
            self._icon.update_menu()

    def _quit(self) -> None:
        if self._icon:
            self._icon.stop()
        self._on_quit()

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()
```

- [ ] **Step 4: Run test to verify it passes** (existing green/red/RGBA tests stay green)

Run: `/c/Python314/python -m pytest webcam_client/tests/test_tray_app.py -p no:cacheprovider -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add webcam_client/gui/tray_app.py webcam_client/tests/test_tray_app.py
git commit -m "feat(webcam): tray pause reflects state (label toggle + amber icon)"
```

---

## Task 6: Faster cold start (upx=False, stay onefile)

**Files:**
- Modify: `webcam_client/build.spec`
- Test: `webcam_client/tests/test_packaging.py`

**Interfaces:** none (build config).

- [ ] **Step 1: Write the failing test** — append to `webcam_client/tests/test_packaging.py`:

```python
def test_build_spec_disables_upx_for_faster_launch():
    # UPX decompression runs on every onefile launch; turning it off trades a
    # slightly larger exe for a faster cold start.
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "upx=False" in spec, "UPX must be off — decompression slows cold start"


def test_build_spec_stays_onefile():
    # Single-file drop is a hard product requirement: no onedir COLLECT.
    spec = (WEBCAM_DIR / "build.spec").read_text(encoding="utf-8")
    assert "COLLECT(" not in spec, "must remain a one-file build (no onedir COLLECT)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/c/Python314/python -m pytest webcam_client/tests/test_packaging.py -p no:cacheprovider -q`
Expected: FAIL — `test_build_spec_disables_upx_for_faster_launch` fails (spec has `upx=True`).

- [ ] **Step 3: Write minimal implementation** — in `webcam_client/build.spec`, change the `EXE(...)` `upx` line:

```python
    strip=False,
    upx=False,  # skip UPX: its per-launch decompression slows onefile cold start
    upx_exclude=[],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/c/Python314/python -m pytest webcam_client/tests/test_packaging.py -p no:cacheprovider -q`
Expected: PASS (all — original 4 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add webcam_client/build.spec webcam_client/tests/test_packaging.py
git commit -m "perf(webcam): disable UPX for faster onefile cold start"
```

---

## Task 7: Rebuild + manual bench verification (no automated test)

**Files:** none (verification only). This task has no unit test — it is the human/hardware gate that the automated suite cannot cover (real cameras, real tray clicks, real launch timing). Consistent with `docs/webcam-client-bench-test-checklist.md`.

- [ ] **Step 1: Full client suite green** — run each file (per-file, the CI trap):

```bash
for f in test_app_controller test_setup_wizard test_main_dispatch test_tray_app test_packaging test_hls_encoder test_camera_manager test_config test_control_channel test_push_engine test_gui_preview; do
  /c/Python314/python -m pytest webcam_client/tests/$f.py -p no:cacheprovider -q || echo "FAILED: $f"
done
```
Expected: every file passes; no "FAILED:" line.

- [ ] **Step 2: Rebuild the exe**

```bash
rm -f dist/SDPRS_Webcam.exe
/c/Python314/python -m PyInstaller webcam_client/build.spec --distpath webcam_client/dist --workpath webcam_client/build
```
Expected: `webcam_client/dist/SDPRS_Webcam.exe` produced; `--check` exits 0:
```bash
webcam_client/dist/SDPRS_Webcam.exe --check
```

- [ ] **Step 3: Bench click-path (operator, on a real PC with a webcam)** — verify by hand and record in the bench checklist:
  1. Launch → time to tray icon appears (compare against the pre-change build; expect faster).
  2. First run: wizard appears **immediately**, camera list fills in a moment later (no freeze on 掃描中…).
  3. Configure + 開始 → tray goes green.
  4. Tray → `開啟設定` **while running** → window opens (no freeze), cameras editable.
  5. Rename a camera, 開始 → change takes effect **without an app restart**; dashboard shows **no duplicate** webcam tile.
  6. Tray → `暫停推送` → label flips to `恢復推送`, icon turns amber, uploads stop; click again → resumes, icon green.
  7. Tray → `離開` → clean exit.

- [ ] **Step 4: No commit** unless the bench run requires a fix (then loop back through the relevant task's RED→GREEN).

---

## Self-Review (completed by plan author)

- **Spec coverage:** Root causes A (camera contention) → Tasks 1+3+4 (engines stopped before the window scans); B (nested loops) → Task 4 (GUI on main thread); C (restart required) → Tasks 1+4 (`apply`); D (duplicate nodes) → Task 2. Open-speed: packaging → Task 6, scan-off-critical-path → Task 3. UX pause → Task 5. First-run vs edit split → Task 3. All spec sections map to a task.
- **Type consistency:** `AppController` method names (`start_engines`/`stop_engines`/`apply`/`pause_all`/`resume_all`/`shutdown`/`config`) are identical across Tasks 1, 4, and the tests. `run_setup_wizard(cfg, mode=...)` signature identical in Tasks 3 and 4. `register_cameras` return contract `(list|None, str|None)` identical in Tasks 2 and 3. Tray callback set (`on_open_settings/on_quit/on_pause/on_resume`) identical in Tasks 4 and 5.
- **Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step shows the command and expected result.
