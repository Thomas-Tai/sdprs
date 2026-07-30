# sdprs/webcam_client/gui/wizard/window.py
"""The Tk rendering of the setup page — and nothing else.

One window, three numbered sections that unlock in order:

    1 連線                   -- 連線位址 / 連線密碼 (+ 顯示) + 測試連線
    2 選擇要監控的攝影機     -- locked until section 1's test passes
    3 開始監控               -- 開機時自動啟動 + the confirm button

Every rule about *which* section is unlocked lives in ``flow.py``; every
network call lives in ``connection.py``; every string the guard reads lives in
``strings.py``. What is left here is rendering and wiring, which is the point:
the parts worth testing ended up in modules that need no display, and this
module is the thin part that a bench pass covers.

Two rules hold throughout.

**No worker thread touches Tk.** Workers hand their result to ``_safe_after``,
which marshals onto the Tk thread with ``root.after``; the main loop acts. That
includes thumbnails: the worker returns a plain PIL Image and the Tk thread
wraps it (``preview.to_photo_image``).

**``root.update()`` appears nowhere.** It used to pump the loop once before a
synchronous POST, which left the window unrepaintable and undraggable for the
whole connect timeout — "not responding", which a guard reads as a crash. The
network is asynchronous now, and the way this window stays honest while a call
is in flight is by disabling the buttons, not by freezing.
"""
import logging
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

from ... import autostart as autostart_mod
from ... import strings
from ..preview import to_photo_image
from .connection import (normalize_server_url, register_cameras,
                         register_cameras_async, test_connection_async,
                         _client_identity_changed, _build_cameras_for_registration)
from .flow import MODE_EDIT, MODE_FIRST_RUN, WizardFlow
from .scanning import (_load_thumbnail_async, _prepare_thumbnail_async,
                       _scan_cameras_async)

logger = logging.getLogger("webcam_client.gui.wizard")

_SCAN_MAX_INDEX = 10


def _camera_rows_from_config(config: dict) -> list:
    """Edit-mode prefill: the rows to show from a saved config, each keeping its
    node_id so a re-save re-registers nothing (see register_cameras)."""
    rows = []
    for c in config.get("cameras", []):
        rows.append({
            "device_index": c.get("device_index"),
            "name": c.get("name", strings.WIZ_CAMERA_LABEL.format(
                index=c.get("device_index"))),
            "node_id": c.get("node_id"),
            "enabled": c.get("enabled", True),
        })
    return rows


def _strip_frame(camera: dict) -> dict:
    """Drop the scanned ndarray before a camera dict can reach save_config.

    scan_cameras attaches the frame it grabbed so the thumbnail needs no second
    device open. That frame must not survive into the config: json cannot
    encode an ndarray, and save_config serialises before it truncates precisely
    so this cannot destroy the guard's settings — but relying on that as the
    only defence means every save is one refactor away from failing. Strip it
    here, at the boundary, where the reason is visible.
    """
    return {k: v for k, v in camera.items() if k != "frame"}


def _set_enabled(widget, enabled: bool) -> None:
    """Enable/disable a widget and everything under it.

    Containers (Frame, Canvas, LabelFrame) have no ``state`` option, so setting
    it raises — walk past those and act on the leaves.
    """
    try:
        widget.configure(state=("normal" if enabled else "disabled"))
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        _set_enabled(child, enabled)


def run_setup_wizard(existing_config: Optional[dict] = None,
                     mode: str = MODE_FIRST_RUN) -> Optional[dict]:
    """Show the setup page; return the new config, or None if cancelled.

    The signature is load-bearing: main.py calls this positionally with
    ``mode=`` as a keyword in both places, and test_main_dispatch monkeypatches
    it as ``lambda cfg, **kw``. Positional-first-plus-keyword must survive.
    """
    config = existing_config or {}
    result = {"config": None}
    # Mutable cells, because the nested callbacks below close over them and Python
    # has no other way to rebind a name from an inner function without `nonlocal`
    # scattered through every handler.
    busy = {"v": False}          # a network call is in flight
    rows_state = {"rows": []}    # the camera rows currently rendered

    flow = WizardFlow(mode)

    root = tk.Tk()
    # 監控, not "Webcam": this title bar is the FIRST thing the guard ever reads
    # from this app, and every other operator-facing surface already says 監控.
    title = strings.WIZ_TITLE if mode != MODE_EDIT else strings.WIZ_TITLE_EDIT
    root.title(title)
    root.geometry("560x660")
    # Height is adjustable, width is not: the camera list scrolls (U5), but the
    # copy below is wrapped to a fixed width and reflowing it buys nothing.
    root.resizable(False, True)

    def _safe_after(fn):
        # Worker threads marshal every UI update through here. If the guard
        # closed the window while work was in flight, root is destroyed and a
        # bare root.after(...) raises on the daemon thread. Some Tcl builds
        # raise RuntimeError("main thread is not in main loop") rather than
        # TclError for the same race, so catch both.
        try:
            if root.winfo_exists():
                root.after(0, fn)
        except (tk.TclError, RuntimeError):
            pass

    # ======================================================================
    # Section 1 -- 連線
    # ======================================================================
    sec1 = ttk.LabelFrame(root, text=strings.WIZ_SECTION_CONNECT, padding=10)
    sec1.pack(fill="x", padx=10, pady=(10, 5))

    # The expanded form (the two fields) and the collapsed form (a ✓ summary)
    # are two frames in the same slot; verifying swaps them.
    sec1_body = ttk.Frame(sec1)
    sec1_body.pack(fill="x")

    ttk.Label(sec1_body, text=f"{strings.LBL_SERVER_URL}：").grid(
        row=0, column=0, sticky="w")
    url_var = tk.StringVar(value=config.get("server_url", ""))
    url_entry = ttk.Entry(sec1_body, textvariable=url_var, width=40)
    url_entry.grid(row=0, column=1, columnspan=2, padx=5, sticky="w")

    ttk.Label(sec1_body, text=f"{strings.LBL_API_KEY}：").grid(
        row=1, column=0, sticky="w", pady=5)
    key_var = tk.StringVar(value=config.get("api_key", ""))
    key_entry = ttk.Entry(sec1_body, textvariable=key_var, width=40, show="*")
    key_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")

    # U12: the guard may be asked to type the connection password, so they need
    # a way to see what they typed. Default masked; the toggle is theirs.
    reveal_var = tk.BooleanVar(value=False)

    def _toggle_reveal():
        key_entry.configure(show="" if reveal_var.get() else "*")

    ttk.Checkbutton(sec1_body, text=strings.BTN_REVEAL_KEY,
                    variable=reveal_var, command=_toggle_reveal).grid(
        row=1, column=2, sticky="w")

    ttk.Label(sec1_body, text=strings.WIZ_REVEAL_KEY_HINT,
              wraplength=480, justify="left", foreground="gray").grid(
        row=2, column=0, columnspan=3, sticky="w", pady=(0, 6))

    test_btn = ttk.Button(sec1_body, text=strings.BTN_WIZARD_TEST)
    test_btn.grid(row=3, column=1, sticky="w", padx=5)

    conn_status = tk.StringVar(value="")
    ttk.Label(sec1_body, textvariable=conn_status, wraplength=480,
              justify="left").grid(row=4, column=0, columnspan=3,
                                   sticky="w", pady=(6, 0))

    # Collapsed form: shown once the connection is verified, so the guard's eye
    # goes to section 2 instead of re-reading fields that are already right.
    sec1_summary = ttk.Frame(sec1)
    summary_text = tk.StringVar(value="")
    ttk.Label(sec1_summary, text="✓", foreground="green").pack(side="left")
    ttk.Label(sec1_summary, textvariable=summary_text).pack(side="left", padx=6)

    def _reopen_section1():
        sec1_summary.pack_forget()
        sec1_body.pack(fill="x")
        url_entry.focus_set()

    ttk.Button(sec1_summary, text=strings.BTN_SETTINGS,
               command=_reopen_section1).pack(side="right")

    # ======================================================================
    # Section 2 -- 選擇要監控的攝影機
    # ======================================================================
    sec2 = ttk.LabelFrame(root, text=strings.WIZ_SECTION_CAMERAS, padding=10)
    sec2.pack(fill="both", expand=True, padx=10, pady=5)

    # U5: the list used to render straight into a fixed, non-resizable window
    # with no scroll region, so with six cameras the later rows -- and the
    # button below them -- were simply off the bottom of the screen with no way
    # to reach them. Canvas + Scrollbar, and the rows go inside.
    cam_canvas = tk.Canvas(sec2, height=200, highlightthickness=0)
    cam_scroll = ttk.Scrollbar(sec2, orient="vertical", command=cam_canvas.yview)
    cam_list = ttk.Frame(cam_canvas)
    cam_window = cam_canvas.create_window((0, 0), window=cam_list, anchor="nw")
    cam_canvas.configure(yscrollcommand=cam_scroll.set)
    cam_canvas.pack(side="left", fill="both", expand=True)
    cam_scroll.pack(side="right", fill="y")

    def _on_list_configure(_event=None):
        cam_canvas.configure(scrollregion=cam_canvas.bbox("all"))

    def _on_canvas_configure(event):
        cam_canvas.itemconfigure(cam_window, width=event.width)

    cam_list.bind("<Configure>", _on_list_configure)
    cam_canvas.bind("<Configure>", _on_canvas_configure)

    def _on_mousewheel(event):
        cam_canvas.yview_scroll(-1 * (event.delta // 120), "units")

    cam_canvas.bind("<Enter>",
                    lambda e: cam_canvas.bind_all("<MouseWheel>", _on_mousewheel))
    cam_canvas.bind("<Leave>", lambda e: cam_canvas.unbind_all("<MouseWheel>"))

    sec2_footer = ttk.Frame(sec2)
    sec2_footer.pack(side="bottom", fill="x")
    ttk.Label(sec2_footer, text=strings.WIZ_RESCAN_HINT, wraplength=400,
              justify="left", foreground="gray").pack(side="left")
    rescan_btn = ttk.Button(sec2_footer, text=strings.BTN_WIZARD_RESCAN)
    rescan_btn.pack(side="right", anchor="ne")

    scan_status = tk.StringVar(value="")
    ttk.Label(sec2, textvariable=scan_status).pack(side="bottom", anchor="w")

    cam_vars = []   # (row_dict, enabled_var, name_var)

    # ======================================================================
    # Section 3 -- 開始監控
    # ======================================================================
    sec3 = ttk.LabelFrame(root, text=strings.WIZ_SECTION_START, padding=10)
    sec3.pack(fill="x", padx=10, pady=(5, 10))

    # The registry is the truth, not the config file: the guard may have turned
    # this off in Task Manager since the last save, and is_enabled() reads both
    # keys precisely so this checkbox cannot claim otherwise.
    autostart_var = tk.BooleanVar(value=autostart_mod.is_enabled())
    ttk.Checkbutton(sec3, text=strings.CHK_AUTOSTART,
                    variable=autostart_var).pack(anchor="w")
    ttk.Label(sec3, text=strings.WIZ_AUTOSTART_HINT, wraplength=500,
              justify="left", foreground="gray").pack(anchor="w", pady=(0, 8))

    btn_row = ttk.Frame(sec3)
    btn_row.pack(fill="x")
    # 儲存 in edit mode: the bad-key message sends the guard here to save a key
    # the administrator just issued, and 開始 reads like "start something new"
    # at precisely that moment -- a guard told to save who sees only 開始 and
    # 取消 presses 取消 and loses the key they just typed.
    confirm_label = (strings.BTN_WIZARD_SAVE if mode == MODE_EDIT
                     else strings.BTN_WIZARD_START)
    confirm_btn = ttk.Button(btn_row, text=confirm_label)
    confirm_btn.pack(side="right")
    cancel_btn = ttk.Button(btn_row, text=strings.BTN_WIZARD_CANCEL)
    cancel_btn.pack(side="right", padx=5)

    # ======================================================================
    # Rendering the flow state
    # ======================================================================
    def _refresh():
        """Push the flow's state onto the widgets. The single place that
        decides what is enabled, so no handler can leave the window in a state
        the flow does not describe."""
        unlocked = flow.section2_unlocked
        idle = not busy["v"]

        if unlocked and sec1_body.winfo_ismapped():
            sec1_body.pack_forget()
            summary_text.set(url_var.get().strip())
            sec1_summary.pack(fill="x")
        elif not unlocked and not sec1_body.winfo_ismapped():
            sec1_summary.pack_forget()
            sec1_body.pack(fill="x")

        _set_enabled(sec2, unlocked and idle)
        test_btn.configure(state="normal" if idle else "disabled")
        cancel_btn.configure(state="normal" if idle else "disabled")
        confirm_btn.configure(
            state="normal" if (flow.can_confirm and idle) else "disabled")

    def _selection_changed():
        flow.on_cameras_selected(sum(1 for _, v, _ in cam_vars if v.get()))
        _refresh()

    def _credentials_edited(*_args):
        flow.on_credentials_edited()
        conn_status.set("")
        _refresh()

    # ======================================================================
    # Camera rows
    # ======================================================================
    def _add_row(row):
        device_index = row.get("device_index")
        enabled_var = tk.BooleanVar(value=row.get("enabled", True))
        name_var = tk.StringVar(value=row.get("name") or
                                strings.WIZ_CAMERA_LABEL.format(index=device_index))
        cam_vars.append((row, enabled_var, name_var))

        if row.get("width") and row.get("height"):
            label = strings.WIZ_CAMERA_LABEL_WITH_SIZE.format(
                index=device_index, width=row["width"], height=row["height"])
        else:
            label = strings.WIZ_CAMERA_LABEL.format(index=device_index)

        line = ttk.Frame(cam_list)
        line.pack(fill="x", anchor="w", pady=2)
        ttk.Checkbutton(line, text=label, variable=enabled_var,
                        command=_selection_changed).pack(side="left")
        ttk.Label(line, text=f"{strings.LBL_CAMERA_NAME}：").pack(
            side="left", padx=(8, 2))
        ttk.Entry(line, textvariable=name_var, width=16).pack(side="left")

        def _attach(image):
            # Tk thread. to_photo_image is the ONLY Tk call in the thumbnail
            # path; everything expensive already happened on the worker.
            photo = to_photo_image(image)
            if photo is None:
                return
            try:
                if not line.winfo_exists():
                    return
                lbl = ttk.Label(line, image=photo)
                lbl.image = photo   # keep a ref or Tk GCs it and renders blank
                lbl.pack(side="right")
            except tk.TclError:
                pass    # the row was destroyed (a re-scan) while this loaded

        _prepare_thumbnail_async(device_index, row.get("frame"),
                                 lambda image: _safe_after(lambda: _attach(image)))

    def _render(rows):
        for w in cam_list.winfo_children():
            w.destroy()
        cam_vars.clear()
        rows_state["rows"] = rows
        if not rows:
            # An unplugged camera is the single most likely state of a FIRST
            # run, so this is the common path, not an edge case. The copy names
            # the physical action and the button that retries.
            ttk.Label(cam_list, text=strings.WIZ_NO_CAMERA_FOUND,
                      wraplength=440, justify="left").pack(anchor="w")
        for row in rows:
            _add_row(row)
        _on_list_configure()
        _selection_changed()

    def _on_scan_done(cams):
        # Worker thread -> marshal back.
        def apply_ui():
            _render(cams)
            # Suppress the count when there is nothing to count: 找到 0 支攝影機
            # next to 找不到攝影機 is two messages about the same nothing, and
            # the number is not something the guard can act on.
            scan_status.set(strings.WIZ_SCAN_FOUND.format(count=len(cams))
                            if cams else "")
            _refresh()
        _safe_after(apply_ui)

    def _do_scan(full_sweep: bool = False):
        scan_status.set(strings.WIZ_SCANNING)
        _scan_cameras_async(
            _on_scan_done,
            max_index=_SCAN_MAX_INDEX,
            # 重新掃描 forces a full sweep: it is the documented escape hatch
            # for a camera sitting past a gap in the device indices, which the
            # fast early-stop scan would never reach.
            stop_after_misses=_SCAN_MAX_INDEX if full_sweep else 3,
        )

    rescan_btn.configure(command=lambda: _do_scan(full_sweep=True))

    # ======================================================================
    # 測試連線 (U4)
    # ======================================================================
    def _on_test():
        server_url = normalize_server_url(url_var.get())
        api_key = key_var.get().strip()
        if not server_url or not api_key:
            conn_status.set(strings.WIZ_NEED_URL_AND_KEY)
            return
        # Token minted BEFORE the call: the reply lands on a worker thread and
        # can arrive after the guard has gone back and changed the address. The
        # flow drops a success that answers a question about credentials which
        # no longer exist -- without this, section 2 unlocks for a pair the app
        # has never tried, which is the exact failure this branch is about.
        token = flow.begin_connection_test()
        busy["v"] = True
        conn_status.set(strings.WIZ_TEST_IN_PROGRESS)
        _refresh()

        def done(ok, err):
            def apply_ui():
                busy["v"] = False
                if ok:
                    flow.on_connection_verified(token)
                    conn_status.set(strings.WIZ_TEST_OK)
                    if mode != MODE_EDIT and not rows_state["rows"]:
                        _do_scan()
                else:
                    conn_status.set(err)
                _refresh()
            _safe_after(apply_ui)

        test_connection_async(server_url, api_key, done)

    test_btn.configure(command=_on_test)

    # ======================================================================
    # Confirm / cancel
    # ======================================================================
    def _on_confirm():
        if not flow.can_confirm or busy["v"]:
            return
        server_url = normalize_server_url(url_var.get())
        api_key = key_var.get().strip()
        identity_changed = _client_identity_changed(config, server_url, api_key)
        selected_rows = [
            {"device_index": row["device_index"],
             "name": nv.get().strip() or strings.WIZ_CAMERA_LABEL.format(
                 index=row["device_index"]),
             "node_id": row.get("node_id")}
            for row, v, nv in cam_vars if v.get()
        ]
        selected = _build_cameras_for_registration(selected_rows, identity_changed)
        if not selected:
            conn_status.set(strings.WIZ_NEED_A_CAMERA)
            return

        busy["v"] = True
        conn_status.set(strings.WIZ_CONNECTING)
        _refresh()

        def done(cams, err):
            def apply_ui():
                busy["v"] = False
                if err:
                    # Re-enabling on the FAILURE path matters as much as on the
                    # success path: a window left with every button disabled is
                    # the freeze this change removed, wearing a different hat.
                    conn_status.set(err)
                    _refresh()
                    return
                # A failed registry write must never fail the config save --
                # the same rule main.py applies to setup_logging(). set_enabled
                # reports the REAL resulting state, so what is stored is what
                # will actually happen at the next logon.
                wanted = autostart_var.get()
                actual = wanted if autostart_mod.set_enabled(wanted) else False
                if actual != wanted:
                    logger.warning("Autostart could not be set to %s", wanted)
                result["config"] = {
                    "server_url": server_url,
                    "api_key": api_key,
                    "cameras": [_strip_frame(c) for c in cams],
                    "motion_threshold": config.get("motion_threshold", 25),
                    "heartbeat_interval": config.get("heartbeat_interval", 30),
                    "autostart": actual,
                }
                root.destroy()
            _safe_after(apply_ui)

        register_cameras_async(server_url, api_key, selected, done)

    def _on_cancel():
        if busy["v"]:
            return
        # U11: cancel used to be a bare root.destroy() -- the window vanished
        # and the guard was left with no idea whether anything had happened.
        #
        # The message differs by mode because ONE message would be false in one
        # of them. On a first run, main() returns as soon as this returns None,
        # BEFORE the tray icon exists, so "reopen it from the tray" points at
        # something that is not there. In edit mode the tray IS up and
        # cancelling stops nothing, so 監控不會啟動 would be the lie instead.
        message = (strings.WIZ_CONFIRM_CANCEL_EDIT if mode == MODE_EDIT
                   else strings.WIZ_CONFIRM_CANCEL)
        if messagebox.askokcancel(title, message):
            root.destroy()

    confirm_btn.configure(command=_on_confirm)
    cancel_btn.configure(command=_on_cancel)
    root.protocol("WM_DELETE_WINDOW", _on_cancel)

    # U8: not one keyboard binding existed here before. Enter confirms, Esc
    # cancels, and the first field has focus at open so the guard can simply
    # start typing.
    root.bind("<Return>", lambda e: _on_confirm())
    root.bind("<Escape>", lambda e: _on_cancel())

    # Traces go on AFTER the initial values are set, or edit mode would re-lock
    # itself the instant it prefilled the fields it had just been told to trust.
    url_var.trace_add("write", _credentials_edited)
    key_var.trace_add("write", _credentials_edited)

    if mode == MODE_EDIT:
        # Edit mode starts verified -- the client is running and uploading, so
        # the saved credentials are being proven continuously and making the
        # guard re-test them to rename a camera would be theatre. But the flow
        # cannot know the saved config already has cameras ticked, so without
        # this render (which ends in _selection_changed) can_confirm stays
        # False and 儲存 is dead on arrival.
        _render(_camera_rows_from_config(config))
    else:
        root.after(100, _do_scan)

    _refresh()
    url_entry.focus_set()
    root.mainloop()
    return result["config"]
