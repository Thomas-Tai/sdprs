# sdprs/webcam_client/main.py
import logging
import queue
import signal

from .config import load_config, save_config, is_first_run
from .app_controller import AppController, enabled_cameras
from .gui.notifier import notify_state
from .gui.setup_wizard import run_setup_wizard
from .gui.status_window import open_status_window, open_log_folder
from .gui.tray_app import TrayApp
from .logging_setup import setup_logging, add_secret
from .single_instance import SingleInstance
from .status import StatusHub, CONTROL_SOURCE, Fault
from .strings import ALREADY_RUNNING, TRAY_TOOLTIP_PREFIX

logger = logging.getLogger("webcam_client.main")

_running = True

# How many queue tokens one status-window pump may service before yielding back
# to Tk. Bounded so a fast-flapping fault cannot hold the Tk thread and freeze
# the window that this pump exists to keep live; the rest waits 250ms.
_PUMP_DRAIN_LIMIT = 32
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


def _signal_handler(sig, frame):
    global _running
    _running = False


def _handle_health(hub, tray) -> None:
    """Repaint the tray from the hub's CURRENT state.

    Deliberately re-reads hub.state rather than trusting a state carried on the
    queue: several transitions can be enqueued before the loop drains them, and
    painting a stale one would leave the light showing a state that has already
    passed. The queue message is only a wake-up.
    """
    tray.set_health(hub.state)


def _handle_notify(icon, state, camera_names=None) -> None:
    """Toast the state this token was HANDED -- never hub.state.

    The opposite of _handle_health, and deliberately so. tick() decides which
    state matured past the 30s debounce and latches it at FIRE time; if the
    toast re-read hub.state at DRAIN time it would announce whatever happened to
    be true by then. Three things break at once when it does, and the window is
    wide open in practice because the status window and the settings wizard both
    block this loop, so every transition that occurs while one is open piles up
    and drains at once on close:

      * the matured notification is LOST -- the server was down for 30+ seconds,
        the guard is told 監控中 and never learns there was an outage at all;
      * the debounce is BYPASSED -- a fault that started 0 seconds ago gets
        toasted because it is what hub.state says now, which is exactly the
        "toast for a 2-second blip" this design exists to prevent;
      * the toast DUPLICATES -- _notified was latched to the old state, so the
        new one matures and toasts again 30 seconds later.

    Coalescing is right for the tray light (one repaint, newest state) and wrong
    for notifications (each one is a distinct event the guard must see once).

    camera_names does NOT ride the token the way state does, and that is a
    deliberate, separate call, not an oversight (F-1). Threading names through
    the token the same way would need StatusHub's on_notify to hand back the
    fault-source set as well as the state -- on_notify fires from tick()/
    _recompute_locked() while the hub still holds its own non-reentrant lock,
    so the ONLY safe thing a callback may do is enqueue (see main()'s q
    comment); calling hub.faulty_sources() from inside that callback would
    try to re-take the same lock and deadlock the thread that is trying to
    announce the fault. That callback shape belongs to status.py, not this
    caller. So names are resolved by the CALLER of this function, at drain
    time, from hub.faulty_sources() -- safe because by then the lock that
    decided to notify has long since been released. Unlike state, this is not
    a one-shot fact: hub.faulty_sources() stays truthfully queryable at any
    time, and answering "which camera needs attention right NOW" is more
    useful to the guard than freezing the answer to "whichever camera the
    debounce math happened to be timing 30s ago" -- especially the one case
    where the two would actually differ, a status window or settings wizard
    blocking this loop for a while.
    """
    if state is None:
        # A bare "NOTIFY" string reaches _split_token's default payload of
        # None. Nothing enqueues that today, but the alternative to this guard
        # is notify_state() rendering a toast for a state that does not exist.
        logger.warning("NOTIFY token carried no state; dropping it")
        return
    notify_state(icon, state, camera_names=camera_names)


def _split_token(req):
    """(name, payload) for any dispatch-queue token.

    Most tokens are a bare string -- they are pure wake-ups with nothing to
    carry. NOTIFY is the exception: it carries the Health state that matured,
    because that state cannot be recovered later (see _handle_notify). Rather
    than make the loop test types inline, every token is normalised here, so the
    loop reads the same way for both shapes.
    """
    if isinstance(req, tuple):
        name, payload = req
        return name, payload
    return req, None


def _camera_display_names(sources, cameras) -> list:
    """Translate the hub's INTERNAL fault source keys into names a guard knows.

    hub.faulty_sources() returns raw keys -- node_ids, plus the literal
    CONTROL_SOURCE ("__control__") when the control channel is what is failing
    -- while the status window joins the names it is given into a Chinese
    sentence. Passing the keys through unchanged would show a security guard
    "__control__", or a server-side UUID, in the middle of that sentence.

    Two kinds of source are therefore dropped rather than rendered:

    * CONTROL_SOURCE. It is not a camera, so listing it among camera names
      would be a lie. Nothing is lost by dropping it: control_channel.py only
      ever reports NO_SERVER or BAD_KEY, both of which outrank CAMERA_DOWN in
      the hub's precedence, and CAMERA_DOWN's text is the ONLY one that
      interpolates camera names at all. Whenever the control channel is at
      fault the guard is reading "無法連線到伺服器" or "連線密碼已失效", whose
      wording never mentions a camera -- so there is no sentence for it to be
      missing from.
    * A node_id with no camera in the config (a settings edit racing an
      in-flight worker). The id itself is meaningless to the guard, so it is
      dropped and logged for the technician instead.
    """
    by_id = {}
    for cam in cameras:
        node_id = cam.get("node_id")
        if node_id:
            by_id[node_id] = cam.get("name") or f"Webcam {cam.get('device_index')}"
    names = []
    for source in sources:
        if source == CONTROL_SOURCE:
            continue
        name = by_id.get(source)
        if name is None:
            # INFO, not DEBUG: the root logger is configured at INFO, so a DEBUG
            # line here is dropped and logged NOWHERE -- while the docstring
            # above promises the technician gets it. This is the only trace that
            # a fault was reported for a camera the config no longer knows.
            logger.info("Fault reported by a source with no camera in config: %s",
                        source)
            continue
        if name not in names:
            names.append(name)
    return names


def _report_rebuild_failure(controller, hub) -> None:
    """Tell the hub what is TRUE after a rebuild that failed twice.

    start_engines() can fail PARTWAY. It publishes each engine the moment it
    starts (deliberately -- so a later failure cannot leak an open camera) and it
    builds the ControlChannel LAST. So a camera that refuses to start leaves the
    cameras before it alive and uploading, the cameras after it never attempted,
    and no control channel at all.

    This used to report CONTROL_SOURCE/NO_SERVER unconditionally, and that was
    wrong in the worst way available (finding #18): there is no control channel
    alive to ever report Fault.NONE for that key, so the fault was PERMANENT, and
    NO_SERVER outranks CAMERA_DOWN, so it masked everything under it. The guard
    read 「無法連線到伺服器 / 請檢查電腦後方的網路線是否鬆脫」 for the rest of the
    process while camera 1's snapshots landed on the server perfectly well, and
    only another clear_all() -- a settings save, or pressing 重新連線 again --
    could shift it.

    What is actually true is per camera, so that is what is reported:

    * A camera with NO worker has no picture. Nothing will upload for it and
      nothing will ever discover that it is fine again, so CAMERA_DOWN is not
      merely allowed to outlive the report -- it is CORRECT that it does, and
      camera_down's action line says exactly that: re-seat the USB cable, then
      press 重新連線. CAMERA_DOWN is also the LOWEST fault in _PRECEDENCE, so a
      stale one masks nothing; that is the whole difference from the NO_SERVER it
      replaces, and it is why leaving working cameras running is safe here.
    * A camera that IS running is left alone. The alternative -- stop_engines()
      first, so the old 「畫面目前無法上傳」 becomes literally true -- would make
      the tray honest by taking a camera that was recording perfectly well off
      the air, and NO_SERVER's own action line promises 「網路恢復後會自動繼續
      上傳」, which nothing would then be alive to do. Trading a working camera
      for a message that is still half false is the wrong way round.
    * Nothing running and no camera to name (an empty camera list) is the one
      case where NO_SERVER is the honest report: nothing is uploading and there
      is no camera to blame. Kept, because the hub must never be left on
      STARTING -- a grey tray reading 啟動中 forever, with no toast, is the
      silent lie this whole phase exists to remove.
    * Every camera running, and the ControlChannel is what raised: every camera
      has a picture and is uploading, so ANY fault here would be a lie, and the
      engines report the truth by themselves. The loss is live view, which has no
      word in the frozen Fault vocabulary -- so it goes to the technician's log
      and nothing is said to the guard. ERROR, not DEBUG: the root logger sits at
      INFO and a DEBUG line would be discarded, and this log entry is the only
      trace this failure will ever leave.
    """
    running = set(controller.running_node_ids())
    enabled = enabled_cameras(controller.config)
    # Cameras with no node_id are skipped: the hub keys on node_id and
    # _camera_display_names() maps by it, so a report for one could neither be
    # cleared nor named at the guard.
    silent = [nid for nid in (c.get("node_id") for c in enabled)
              if nid and nid not in running]
    if silent:
        logger.error("Rebuild failed; these cameras have no worker: %s",
                     ", ".join(silent))
        for node_id in silent:
            hub.report(node_id, Fault.CAMERA_DOWN)
    elif not running:
        hub.report(CONTROL_SOURCE, Fault.NO_SERVER)
    else:
        logger.error(
            "Rebuild failed after every camera started: remote commands "
            "(live view) are unavailable until the next 重新連線")


def _rebuild_engines(controller, hub) -> bool:
    """Rebuild every engine in-process from the controller's CURRENT config.
    Returns True when the rebuild succeeded. Never raises.

    hub has no default on purpose (NIT): a `hub=None` default let a call site
    that simply forgot the argument keep running with the STARTING-forever
    backstop silently switched off -- no error, no log line, just a tray that
    can sit on 啟動中 forever after a failed rebuild, which is the exact bug
    this whole phase exists to remove. The one production call site (main()'s
    dispatch loop and its two start_engines() guards) always has a real hub to
    give; a caller with none must say so explicitly (hub=None), not get there
    by omission. `if hub is not None:` below still exists to serve that
    explicit case -- it is no longer reachable by accident.

    apply() is stop_engines() + start_engines(), and stop_engines() ends in
    hub.clear_all() -- which resets _seen_any_report and drops the hub back to
    STARTING. If start_engines() then raises (a malformed config the engine
    factory cannot use, a thread that will not start -- and the control channel
    is built LAST, so it may never get built at all), the hub can be left with
    nothing alive to report a fault: it sits on STARTING for the rest of the
    process, a grey tray reading 啟動中 / 正在連線並開啟攝影機，請稍候。 forever,
    with no toast either, because tick() never announces a healthy state.

    That is precisely the silent lie this phase exists to remove -- and the
    guard reaches it by pressing 重新連線, the button they press to FIX things.
    So: retry once (the same best-effort recovery the OPEN_SETTINGS error path
    has always used -- apply() stops first, so a retry cleans up any partial
    engine set instead of stacking a second one on top of it), and if that
    fails too, tell the hub what is true (see _report_rebuild_failure).
    """
    for attempt in (1, 2):
        try:
            controller.apply(controller.config)
            return True
        except Exception:
            logger.exception("Engine rebuild failed (attempt %d)", attempt)
    if hub is not None:
        try:
            _report_rebuild_failure(controller, hub)
        except Exception:
            # "Never raises" is load-bearing: startup calls this, and an
            # exception leaving main() there sends the traceback to a sys.stderr
            # that is None in the windowed build -- the guard watches the tray
            # icon appear and vanish with no log line and no dialog.
            logger.exception("Could not report the failed rebuild to the hub")
    return False


def _status_pump(hub, tray, controller, q) -> dict:
    """Service the dispatch queue while the status window owns the loop.

    The status window used to block this loop outright and render a snapshot
    taken at open time, which made the screen built to tell the guard the truth
    the one screen that could not. This is called from the window's Tk timer --
    on THIS thread -- and does the loop's job for it: drain the queue, repaint
    the tray, fire matured toasts, tick the hub, and hand back the current state
    for the window to re-render.

    Two tokens are not serviced here. QUIT and OPEN_SETTINGS both need the
    window gone first, and each already has exactly one implementation in
    _handle_request; duplicating either here is how the two copies drift. They
    go BACK on the queue and this returns close=True, so the window closes and
    the main loop -- resumed and none the wiser -- runs the real handler.

    Draining is bounded. A camera flapping fast enough to enqueue faster than
    this drains would otherwise hold the Tk thread here indefinitely and freeze
    the very window this exists to keep live; the remainder is simply serviced
    on the next tick, 250ms later.
    """
    for _ in range(_PUMP_DRAIN_LIMIT):
        try:
            req = q.get_nowait()
        except queue.Empty:
            break
        name, payload = _split_token(req)
        if name == "HEALTH":
            _handle_health(hub, tray)
        elif name == "NOTIFY":
            # See _handle_notify's docstring: names are resolved HERE, at drain
            # time, deliberately -- not carried on the token the way state is.
            _handle_notify(tray.icon, payload, _camera_display_names(
                hub.faulty_sources(), enabled_cameras(controller.config)))
        elif name == "OPEN_STATUS":
            continue                       # already open; nothing to do
        else:
            q.put(req)
            return {"close": True}
    hub.tick()
    cameras = enabled_cameras(controller.config)
    return {
        "close": False,
        "state": hub.state,
        "camera_count": len(cameras),
        "faulty_names": _camera_display_names(hub.faulty_sources(), cameras),
    }


def _handle_open_status(hub, controller, q, tray=None) -> None:
    """Open the guard-facing status window on the MAIN thread.

    Guarded like OPEN_SETTINGS: a Tk failure here must cost the guard a window,
    never the dispatch loop that keeps the cameras uploading.
    """
    cameras = enabled_cameras(controller.config)
    try:
        open_status_window(
            hub.state,
            camera_count=len(cameras),
            faulty_names=_camera_display_names(hub.faulty_sources(), cameras),
            on_open_logs=open_log_folder,
            on_reconnect=lambda: _rebuild_engines(controller, hub),
            on_settings=lambda: q.put("OPEN_SETTINGS"),
            # No tray means no queue to service and nothing to repaint, so the
            # window falls back to the one-shot snapshot rather than pumping
            # against half a system.
            pump=(None if tray is None
                  else lambda: _status_pump(hub, tray, controller, q)),
        )
    except Exception:
        logger.exception("Unexpected error opening the status window")


def _handle_request(req, controller, settings_fn, hub) -> bool:
    """Service one queued request on the MAIN thread. Returns False to quit.

    The tray (daemon thread) only enqueues; opening the settings window and
    rebuilding engines therefore happen here, on the main thread, which is what
    lets Tk run correctly and the cameras be released before the window scans.

    hub has no default, same reasoning as _rebuild_engines() (NIT): this
    function's only use of it is forwarding to _rebuild_engines() on the
    OPEN_SETTINGS error path, so a `hub=None` default here would silently
    disable that same backstop one call further up. A caller that has no hub
    to give must write hub=None, not rely on a default to get there."""
    if req == "QUIT":
        controller.shutdown()
        return False
    if req == "OPEN_SETTINGS":
        try:
            controller.stop_engines()          # free the cameras for the wizard
            new_cfg = settings_fn(controller.config)  # runs on the main thread
            if new_cfg:
                save_config(new_cfg)
                # load_config() and the first-run wizard both register the key
                # right after they change it; this OPEN_SETTINGS path (tray ->
                # settings, mid-process key rotation) is the third path that
                # changes api_key and must do the same, or the redactor keeps
                # scrubbing only the ORIGINAL key for the rest of the process.
                add_secret(new_cfg.get("api_key", ""))
                controller.apply(new_cfg)      # rebuild in-process, no restart
            else:
                controller.start_engines()     # cancelled -> resume old config
        except Exception:
            # A bad settings interaction (e.g. Tk blowing up mid-rebuild) must
            # not kill the tray app -- log it, best-effort recover, and keep
            # the dispatch loop alive. Recovery uses apply(controller.config)
            # rather than start_engines(): if the failure happened mid-apply()
            # (config already swapped, engines partially started), calling
            # start_engines() again would stack a SECOND engine set on top of
            # the partial one. apply() stops first -- cleaning up any partial
            # set and releasing cameras -- then rebuilds from the current
            # config, so there is never a duplicate set.
            #
            # _rebuild_engines() is that same recovery, shared with the status
            # window's 重新連線 button: this path ends in stop_engines() ->
            # hub.clear_all() too, so if the recovery ALSO fails it strands the
            # hub on STARTING in exactly the same way, and needs exactly the
            # same "tell the hub" backstop.
            logger.exception("Unexpected error handling OPEN_SETTINGS")
            _rebuild_engines(controller, hub)
    return True


def main():
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # setup_logging() MUST run before _acquire_single_instance(): three log
    # statements sit in that window (single_instance.py's "Global unavailable,
    # using session-local" INFO -- the COMMON path on a non-admin account --
    # its "mutex creation failed, allowing launch" WARNING, and the "another
    # instance already running" INFO just below). With no handler attached
    # yet those vanish entirely (INFO) or reach only sys.stderr, which is None
    # in a console=False build. No secret is loaded at this point either way,
    # so moving logging first introduces no key-timing risk.
    #
    # CAVEAT: a refused second instance now briefly opens the same
    # RotatingFileHandler target before exiting, and Windows cannot rename a
    # file that is open in another process -- so a rollover racing a refused
    # launch can fail. The refused instance writes one line and exits
    # immediately, so the window is ~milliseconds and logging's handleError()
    # degrades gracefully (the write is just dropped), but it is a real,
    # accepted trade-off, not an oversight.
    try:
        setup_logging()
    except Exception:
        pass  # a diagnostic aid must never become a startup dependency (FIX 4)

    if not _acquire_single_instance():
        _close_splash()
        logger.info("Another instance is already running; exiting")
        try:
            from tkinter import messagebox
            messagebox.showinfo(TRAY_TOOLTIP_PREFIX, ALREADY_RUNNING)
        except Exception:
            pass
        return

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

    enabled = enabled_cameras(config)
    if not enabled:
        _close_splash()
        logger.error("No cameras configured")
        return

    # The queue must exist BEFORE the hub, because the hub's callbacks enqueue
    # onto it -- and they fire from worker threads, inside the hub's lock.
    # Workers never touch Tk or pystray; every UI action happens on this
    # thread, in the dispatch loop below.
    #
    # Token shape: a bare string is a pure wake-up; ("NOTIFY", state) carries
    # the state that matured, because unlike the tray repaint it CANNOT be
    # re-derived at drain time. _split_token() normalises both.
    q: "queue.Queue" = queue.Queue()
    hub = StatusHub(on_change=lambda state: q.put("HEALTH"),
                    on_notify=lambda state: q.put(("NOTIFY", state)))

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
    # AppController.__init__ touches no hardware, so building it above is free.
    tray.start()
    # Nothing has reported yet at this point in startup, so the hub is still
    # STARTING (grey) -- the honest state, NOT the old hardcoded
    # "connected=True" (always green) lie.
    tray.set_health(hub.state)
    _close_splash()

    # Guarded for the same reason both rebuild paths are (see _rebuild_engines):
    # if this raises, the exception leaves main(), the traceback goes to a
    # sys.stderr that is None in the windowed (console=False) build, and the
    # tray icon the guard just watched appear vanishes again with no log line
    # and no dialog. Unguarded, this was the ONE start_engines() call site
    # without the backstop that stops the light sitting on grey forever.
    try:
        controller.start_engines()
    except Exception:
        logger.exception("Engine startup failed")
        _rebuild_engines(controller, hub)
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
        name, payload = _split_token(req)
        if name == "HEALTH":
            _handle_health(hub, tray)
            continue
        if name == "NOTIFY":
            # See _handle_notify's docstring: names are resolved HERE, at drain
            # time, deliberately -- not carried on the token the way state is.
            _handle_notify(tray.icon, payload, _camera_display_names(
                hub.faulty_sources(), enabled_cameras(controller.config)))
            continue
        if name == "OPEN_STATUS":
            _handle_open_status(hub, controller, q, tray)
            continue
        running = _handle_request(
            name, controller, lambda cfg: run_setup_wizard(cfg, mode="edit"), hub)

    # Every exit path converges here: an ordinary QUIT from the tray menu
    # (running went False, below) and SIGINT/SIGTERM (_signal_handler only
    # flips _running, above -- it must stay fast and reentrant-safe, so it
    # touches neither the tray nor the controller).
    #
    # tray.stop() NIM_DELETEs the icon. Before this fix nothing on the signal
    # path ever called it -- TrayApp.stop() existed but main() never reached
    # it -- so a guard who closed the app with Ctrl+C or a service stop still
    # saw its icon sitting in the notification area, live-looking, until they
    # happened to hover over it and Windows finally noticed the process was
    # gone. Calling it here on EVERY path, including tray-quit (which already
    # stopped the icon itself in TrayApp._quit()), is a deliberate no-op on
    # that path: pystray's stop() is documented idempotent -- "if the icon is
    # not running, calling this method has no effect."
    tray.stop()

    if running:
        # `running` only becomes False inside _handle_request's QUIT branch,
        # and that branch already called controller.shutdown() before
        # returning it -- so reaching here with `running` still True means the
        # loop instead exited because the signal handler flipped `_running`,
        # and shutdown() has not run yet. Calling it unconditionally used to
        # repeat it on every ordinary tray-quit: shutdown() -> stop_engines()
        # -> hub.clear_all() pushes a "HEALTH" token onto a queue nobody is
        # left to drain -- harmless today, but two calls for one quit.
        controller.shutdown()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
