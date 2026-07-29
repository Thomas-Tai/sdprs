# sdprs/webcam_client/gui/setup_wizard.py
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

import httpx

from .. import strings
from ..camera_manager import scan_cameras
from .preview import make_thumbnail, grab_preview_frame

logger = logging.getLogger("webcam_client.gui.wizard")


def normalize_server_url(raw: str) -> str:
    """Make a user-typed server URL usable before it is joined with a path.

    - Default to ``http://`` when the scheme is omitted. A schemeless URL made
      ``httpx`` raise ``UnsupportedProtocol``, which is NOT an ``httpx.ConnectError``
      and so escaped ``on_start`` as an unhandled Tk-callback exception — in a
      windowed (console=False) exe that is swallowed and the button "does nothing".
    - Strip a trailing slash so ``f"{url}/api/..."`` never becomes ``//api/...``,
      which the server routes as 404 (``push_engine`` rstrips; the wizard did not).
    """
    url = (raw or "").strip()
    if url and "://" not in url:
        url = "http://" + url
    return url.rstrip("/")


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
        # The exception goes to the LOG, never to the guard: its text is English
        # ("All connection attempts failed", "[Errno 11001] getaddrinfo failed")
        # and it blames the server for what is most often a mistyped address.
        logger.warning("camera registration transport failure: %s", e, exc_info=True)
        return None, strings.WIZ_CANNOT_REACH_SERVER
    if resp.status_code == 401:
        logger.warning("camera registration rejected the key (401)")
        return None, strings.WIZ_KEY_REJECTED
    if resp.status_code != 201:
        # Same rule as everywhere else: the technician gets the code, the guard
        # gets the action. This line used to hand the guard "伺服器回應：500".
        logger.warning("camera registration unexpected status %s", resp.status_code)
        return None, strings.WIZ_SERVER_REFUSED
    try:
        registered = resp.json()
    except Exception as e:
        logger.warning("camera registration body was not JSON: %s", e, exc_info=True)
        return None, strings.WIZ_BAD_RESPONSE
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


def _load_thumbnail_async(device_index, on_ready) -> None:
    """Grab a preview frame + build its thumbnail OFF the Tk thread so the
    settings window paints immediately. on_ready(thumb_or_None) runs on the
    worker thread; a Tk caller marshals back with root.after."""
    def worker():
        try:
            thumb = make_thumbnail(grab_preview_frame(device_index))
        except Exception as e:
            logger.warning(f"thumbnail grab for device {device_index} failed: {e}")
            thumb = None
        on_ready(thumb)
    threading.Thread(target=worker, daemon=True).start()


def _camera_rows_from_config(config: dict) -> list:
    """Edit-mode prefill: the rows to show from a saved config, each keeping its
    node_id so a re-save re-registers nothing (see register_cameras)."""
    rows = []
    for c in config.get("cameras", []):
        rows.append({
            "device_index": c.get("device_index"),
            "name": c.get("name", f"攝影機 {c.get('device_index')}"),
            "node_id": c.get("node_id"),
            "enabled": c.get("enabled", True),
        })
    return rows


def _client_identity_changed(existing_config: Optional[dict], server_url: str,
                             api_key: str) -> bool:
    """True when the entered server_url or api_key differs from the config the
    cameras were last registered under.

    A camera's node_id is owned by the webcam CLIENT whose key registered it.
    If the operator changes the API Key (or Server URL) in the settings window,
    the key now authenticates as a different client, and reusing the old
    node_ids makes the server reject every snapshot/command with
    403 "Camera not owned by this client". When the identity changes the old
    node_ids must be dropped so the cameras re-register under the new client.
    """
    old = existing_config or {}
    return (api_key != old.get("api_key", "")
            or server_url != normalize_server_url(old.get("server_url", "")))


def _build_cameras_for_registration(selected_rows: list, identity_changed: bool) -> list:
    """Build the camera dicts to hand register_cameras. Preserve a row's node_id
    ONLY when the client identity is unchanged (idempotent — no duplicate tile);
    drop it when the identity changed so the camera re-registers under, and is
    owned by, the new client. A row that never had a node_id never gains one."""
    cameras = []
    for r in selected_rows:
        cam = {"device_index": r["device_index"],
               "name": r["name"],
               "resolution": [640, 480], "jpeg_quality": 40, "target_fps": 8}
        if r.get("node_id") and not identity_changed:
            cam["node_id"] = r["node_id"]
        cameras.append(cam)
    return cameras


def run_setup_wizard(existing_config: Optional[dict] = None, mode: str = "first-run") -> Optional[dict]:
    result = {"config": None}
    root = tk.Tk()
    # 監控, not "Webcam": this title bar is the FIRST thing the guard ever reads
    # from this app, and every other operator-facing surface already says 監控.
    # Two names for one program reads as two programs. Now in strings.py with
    # the rest of this window's copy, as the note that used to sit here asked.
    title = strings.WIZ_TITLE if mode == "first-run" else strings.WIZ_TITLE_EDIT
    root.title(title)
    root.geometry("500x480")
    root.resizable(False, False)

    config = existing_config or {}

    # --- Frame: Server connection ---
    frame_conn = ttk.LabelFrame(root, text="伺服器連線", padding=10)
    frame_conn.pack(fill="x", padx=10, pady=5)
    ttk.Label(frame_conn, text=f"{strings.LBL_SERVER_URL}：").grid(row=0, column=0, sticky="w")
    url_var = tk.StringVar(value=config.get("server_url", ""))
    ttk.Entry(frame_conn, textvariable=url_var, width=40).grid(row=0, column=1, padx=5)
    ttk.Label(frame_conn, text=f"{strings.LBL_API_KEY}：").grid(row=1, column=0, sticky="w", pady=5)
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

    def _safe_after(fn):
        # Worker threads (camera scan, thumbnail grab) marshal UI updates
        # through here. If the operator closed the window while work was in
        # flight, root is destroyed and a bare root.after(...) would raise an
        # unhandled exception on the daemon thread -> guard + swallow the race.
        # Some Tcl builds raise RuntimeError("main thread is not in main
        # loop") instead of TclError for this same race, so catch both.
        try:
            if root.winfo_exists():
                root.after(0, fn)
        except (tk.TclError, RuntimeError):
            pass

    def _add_row(device_index, name, node_id, enabled, subtitle):
        var = tk.BooleanVar(value=enabled)
        name_var = tk.StringVar(value=name)
        cam_vars.append(({"device_index": device_index, "node_id": node_id},
                         var, name_var))
        row = ttk.Frame(cam_frame_inner)
        row.pack(fill="x", anchor="w", pady=2)
        ttk.Checkbutton(row, text=subtitle, variable=var).pack(side="left")
        ttk.Label(row, text="名稱：").pack(side="left", padx=(8, 2))
        ttk.Entry(row, textvariable=name_var, width=16).pack(side="left")
        # Render the row NOW; grab_preview_frame opens the camera and can
        # take 0.5-2s per device, so the thumbnail loads on a worker thread
        # and attaches when ready instead of blocking the window from
        # painting. Best-effort: a busy device yields None -> omitted.
        def _attach(thumb):
            if thumb is None:
                return
            try:
                if not row.winfo_exists():
                    return
                lbl = ttk.Label(row, image=thumb)
                lbl.image = thumb  # keep a ref so Tk doesn't GC the PhotoImage
                lbl.pack(side="right")
            except tk.TclError:
                pass  # row was destroyed (e.g. re-scan) while thumbnail loaded

        def on_ready(thumb):
            # Called from the worker thread -> marshal back to the Tk thread.
            _safe_after(lambda: _attach(thumb))

        _load_thumbnail_async(device_index, on_ready)

    def _render(rows):
        for w in cam_frame_inner.winfo_children():
            w.destroy()
        cam_vars.clear()
        if not rows:
            # An unplugged camera is the single most likely state of a FIRST
            # run, so this is not an edge case -- it is the common path. It used
            # to say 未偵測到攝影機 and stop, three characters of diagnosis and
            # no action, while the button that resolves it sat unnamed in the
            # corner. wraplength so the action does not run off the frame.
            ttk.Label(cam_frame_inner, text=strings.WIZ_NO_CAMERA_FOUND,
                      wraplength=440, justify="left").pack(anchor="w")
        for r in rows:
            di = r["device_index"]
            subtitle = f"攝影機 {di}"
            if r.get("width") and r.get("height"):
                subtitle = f"攝影機 {di} ({r['width']}x{r['height']})"
            _add_row(di, r.get("name", f"攝影機 {di}"),
                     r.get("node_id"), r.get("enabled", True), subtitle)

    def _on_scan_done(cams):
        # Called from the worker thread -> marshal back to the Tk thread.
        def apply_ui():
            _render(cams)
            status_var.set(f"找到 {len(cams)} 支攝影機")
        _safe_after(apply_ui)

    def do_scan():
        status_var.set("掃描中...")
        _scan_cameras_async(_on_scan_done)

    ttk.Button(frame_cam, text=strings.BTN_WIZARD_RESCAN,
               command=do_scan).pack(anchor="e", pady=5)

    # --- Buttons ---
    frame_btn = ttk.Frame(root, padding=10)
    frame_btn.pack(fill="x")

    def on_start():
        server_url = normalize_server_url(url_var.get())
        api_key = key_var.get().strip()
        if not server_url or not api_key:
            messagebox.showerror(title, strings.WIZ_NEED_URL_AND_KEY)
            return
        # Re-home cameras when the client identity changed: reusing node_ids
        # owned by the OLD client makes the new key 403 "Camera not owned".
        identity_changed = _client_identity_changed(config, server_url, api_key)
        selected_rows = [
            {"device_index": row["device_index"],
             "name": nv.get().strip() or f"攝影機 {row['device_index']}",
             "node_id": row.get("node_id")}
            for row, v, nv in cam_vars if v.get()
        ]
        selected = _build_cameras_for_registration(selected_rows, identity_changed)
        if not selected:
            messagebox.showerror(title, strings.WIZ_NEED_A_CAMERA)
            return
        status_var.set("連線中...")
        root.update()
        cams, err = register_cameras(server_url, api_key, selected)
        status_var.set("")
        if err:
            messagebox.showerror(title, err)
            return
        result["config"] = {
            "server_url": server_url, "api_key": api_key, "cameras": cams,
            "motion_threshold": config.get("motion_threshold", 25),
            "heartbeat_interval": config.get("heartbeat_interval", 30),
        }
        root.destroy()

    # 儲存 in edit mode: bad_key's action line sends the guard here to save a key
    # the administrator just issued, and 「開始」 reads like "start something new"
    # at precisely that moment -- a guard told to save who sees only 開始 and 取消
    # presses 取消 and loses the key they just typed. strings.py names this label
    # rather than retyping it, so the two cannot drift again.
    confirm = strings.BTN_WIZARD_SAVE if mode == "edit" else strings.BTN_WIZARD_START
    ttk.Button(frame_btn, text=confirm, command=on_start).pack(side="right")
    ttk.Button(frame_btn, text=strings.BTN_WIZARD_CANCEL,
               command=root.destroy).pack(side="right", padx=5)

    if mode == "edit":
        _render(_camera_rows_from_config(config))  # show saved cameras, no rescan
    else:
        root.after(100, do_scan)                    # first run: async auto-scan

    root.mainloop()
    return result["config"]
