# sdprs/webcam_client/gui/status_window.py
"""The window a security guard opens from the tray to find out what is wrong.

Two halves, deliberately separated:

  * build_status_lines() is PURE -- it turns a Health state into the three
    lines the guard reads. It needs no display, so it is the part the test
    suite can actually protect (tests/test_status_window.py).
  * open_status_window() is the Tk rendering. It MUST run on the main thread
    (it is called from main.py's dispatch loop). It owns the loop while it is
    open, so it is handed a `pump` and calls it on a timer: the dispatch queue
    keeps draining, the tray keeps repainting, hub.tick() keeps maturing
    notifications, and this window re-renders itself from the result.

    That used to be a plain mainloop() with the state passed in BY VALUE, and
    it made the one screen built to tell the guard the truth the only screen
    structurally guaranteed to lie. A guard who opened it at 09:00 and left it
    up while phoning a technician was still reading 監控中 / 2 支攝影機運作正常
    at 09:35 with the tray light still green, half an hour after the switch
    died -- and no toast could fire either, because tick() never ran. Worse,
    picking 離開 from the tray removed the icon (pystray's own thread) while
    the QUIT token sat undrained behind this loop, so the app kept uploading
    with no icon, ALREADY_RUNNING pointed the guard at an icon that no longer
    existed, and only Task Manager could end it.

Every word the guard sees comes from webcam_client.strings; nothing in here
formats a status code, an exception, or an internal identifier.
"""
import logging
import os
import tkinter as tk
from tkinter import ttk

from ..logging_setup import get_log_dir
from ..strings import (describe, WINDOW_TITLE, BTN_OPEN_LOGS, BTN_RECONNECT,
                       BTN_SETTINGS, LOG_FOLDER_FAILED)
from .tray_app import _health_color

logger = logging.getLogger("webcam_client.gui.status_window")

# The tray light and this banner must never disagree about severity, so both
# read their colour from tray_app._health_color(); only the rendering differs
# (PIL needs an RGBA tuple, Tk needs a hex string). A second, independent
# Health->colour table here would drift the first time a state was added.
_HEX = {
    "green": "#2e9e4f",
    "red": "#c0392b",
    "amber": "#d68910",
    "grey": "#7f8c8d",
}


def _banner_color(state) -> str:
    return _HEX.get(_health_color(state), _HEX["grey"])


def build_status_lines(state, camera_count, faulty_names):
    """(title, detail, action) for the window. Pure -- no Tk, no I/O.

    `faulty_names` are DISPLAY names (the ones the operator typed in the
    settings window), never node_ids and never CONTROL_SOURCE -- main.py's
    _camera_display_names() is what translates the hub's internal source keys
    into these. An empty list collapses to None so strings.py's own generic
    fallback applies; passing "" instead would override that default with
    nothing and render a sentence with no subject.
    """
    names = "、".join(str(n) for n in faulty_names if n)
    # `camera_count or None` for the same reason as `names or None`: describe()
    # only swaps in its "no count to hand" wording when camera_count IS None, so
    # a literal 0 renders "0 支攝影機運作正常。" -- the self-contradiction
    # strings.py documents (claiming zero cameras work while saying all is
    # well). Reachable whenever the live config has no enabled camera left.
    return describe(state, camera_count=camera_count or None,
                    camera_names=names or None)


def _warn_log_folder_failed() -> None:
    """Tell the guard the 開啟記錄 button could not do its job.

    Without this the button is simply inert: nothing opens, nothing is said,
    and the guard -- who is pressing it because a technician on the phone asked
    them to -- has no idea whether they mis-clicked, whether it is still
    loading, or whether the app is broken. The log line this failure already
    wrote goes to the very folder that would not open, so it reaches nobody.

    Itself best-effort: if the dialog cannot be shown either, there is nothing
    further to try and the caller must still return normally.
    """
    try:
        from tkinter import messagebox
        messagebox.showwarning(WINDOW_TITLE, LOG_FOLDER_FAILED)
    except Exception:
        logger.warning("Could not show the log-folder failure dialog",
                       exc_info=True)


def open_log_folder() -> bool:
    """Open the log directory in Explorer for whoever the guard phoned.

    Best effort: os.startfile does not exist off Windows and raises when the
    shell has no handler. A technician convenience bolted onto a guard-facing
    button must never take the window (or the dispatch loop) down with it --
    but it must not fail SILENTLY either, hence the dialog.
    """
    try:
        path = get_log_dir()
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))
        return True
    except Exception:
        logger.warning("Could not open the log folder", exc_info=True)
        _warn_log_folder_failed()
        return False


def _guarded(fn) -> None:
    """Run a button's action without letting a failure escape into Tk, where a
    windowed (console=False) build swallows it and the button looks dead."""
    if fn is None:
        return
    try:
        fn()
    except Exception:
        logger.exception("Status window action failed")


# 250ms: fast enough that the guard never catches the window disagreeing with
# the tray, slow enough to be free. The old design's effective rate was "never".
_PUMP_INTERVAL_MS = 250


def open_status_window(state, *, camera_count, faulty_names, on_open_logs,
                       on_reconnect, on_settings, pump=None) -> None:
    """MAIN THREAD ONLY. Runs until the guard closes the window.

    `pump` is main.py's dispatch-queue service call, invoked every
    _PUMP_INTERVAL_MS on Tk's own timer -- i.e. on this same thread, so the
    no-Tk-from-workers rule is untouched. It returns the current state to
    re-render, or asks this window to close so the main loop can service a
    token that needs it gone (QUIT, OPEN_SETTINGS).

    Omitting `pump` keeps the old one-shot snapshot behaviour, which is what
    the pure-rendering tests use.
    """
    title, detail, action = build_status_lines(state, camera_count, faulty_names)
    color = _banner_color(state)

    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.geometry("440x270")
    root.resizable(False, False)

    # Opened from a tray click, so the window can land behind whatever the
    # guard was looking at. Lift it, then drop topmost again so it does not
    # sit over everything else for the rest of its life.
    root.lift()
    root.attributes("-topmost", True)

    def _drop_topmost():
        # Same race as setup_wizard._safe_after: the guard may close the window
        # inside the delay, and some Tcl builds raise RuntimeError rather than
        # TclError for a call on a destroyed root.
        try:
            if root.winfo_exists():
                root.attributes("-topmost", False)
        except (tk.TclError, RuntimeError):
            pass

    root.after(500, _drop_topmost)

    banner = tk.Frame(root, background=color, padx=16, pady=14)
    banner.pack(fill="x")
    title_lbl = tk.Label(banner, text=title, background=color,
                         foreground="#ffffff", font=("", 14, "bold"))
    title_lbl.pack(anchor="w")

    body = ttk.Frame(root, padding=16)
    body.pack(fill="both", expand=True)
    detail_lbl = ttk.Label(body, text=detail, wraplength=400, justify="left")
    detail_lbl.pack(anchor="w")
    # Always built, shown only when there IS an action: a healthy state carries
    # an empty one, and a permanently-packed empty label would leave the 10px
    # gap sitting under 監控中 for no reason. Rebuilding the widget on every
    # transition instead would make the window flicker under the pump.
    action_lbl = ttk.Label(body, text=action, wraplength=400, justify="left",
                           foreground="#555555")
    if action:
        action_lbl.pack(anchor="w", pady=(10, 0))

    def _render(new_state, new_count, new_faulty):
        """Repaint in place from the hub's CURRENT state."""
        t, d, a = build_status_lines(new_state, new_count, new_faulty)
        c = _banner_color(new_state)
        banner.configure(background=c)
        title_lbl.configure(text=t, background=c)
        detail_lbl.configure(text=d)
        action_lbl.configure(text=a)
        if a and not action_lbl.winfo_ismapped():
            action_lbl.pack(anchor="w", pady=(10, 0))
        elif not a and action_lbl.winfo_ismapped():
            action_lbl.pack_forget()

    # 重新連線 rebuilds every engine (0.5-2s per camera) and 設定 opens another
    # window; running either underneath this mainloop would freeze the window
    # mid-click. Close first, then run the action once mainloop() returns.
    pending = {"action": None}

    def _close_then(fn):
        pending["action"] = fn
        root.destroy()

    buttons = ttk.Frame(root, padding=(16, 0, 16, 16))
    buttons.pack(fill="x")
    # 開啟記錄 only hands the folder to Explorer and returns at once, so it runs
    # in place -- closing the window under the guard would be a worse surprise.
    ttk.Button(buttons, text=BTN_OPEN_LOGS,
               command=lambda: _guarded(on_open_logs)).pack(side="left")
    ttk.Button(buttons, text=BTN_SETTINGS,
               command=lambda: _close_then(on_settings)).pack(side="right")
    ttk.Button(buttons, text=BTN_RECONNECT,
               command=lambda: _close_then(on_reconnect)).pack(side="right", padx=6)

    def _pump_once():
        # Runs on Tk's timer, i.e. on THIS (main) thread -- the same thread the
        # dispatch loop runs on when this window is closed. Nothing here is
        # reachable from a worker.
        if not root.winfo_exists():
            return
        try:
            result = pump()
        except Exception:
            # The window must survive a pump failure. Losing it would drop the
            # guard back to a tray icon with no way to read what is wrong, and
            # the dispatch loop resumes normally the moment this window closes.
            logger.exception("Status window pump failed")
            result = None
        if result is not None:
            if result.get("close"):
                # A token the main loop must handle with this window gone
                # (QUIT, OPEN_SETTINGS). The token itself was put back on the
                # queue by the pump -- there is exactly ONE implementation of
                # each, and it is not in here.
                root.destroy()
                return
            _render(result["state"], result["camera_count"],
                    result["faulty_names"])
        try:
            root.after(_PUMP_INTERVAL_MS, _pump_once)
        except (tk.TclError, RuntimeError):
            pass          # window went away between the guard and the reschedule

    if pump is not None:
        root.after(_PUMP_INTERVAL_MS, _pump_once)

    root.mainloop()
    _guarded(pending["action"])
