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
            logger.exception("Unexpected error handling OPEN_SETTINGS")
            try:
                controller.apply(controller.config)
            except Exception:
                logger.exception("Failed to resume engines after OPEN_SETTINGS error")
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
            messagebox.showinfo("SDPRS 監控", "SDPRS 監控已在執行中。")
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

    enabled = [c for c in config.get("cameras", []) if c.get("enabled", True)]
    if not enabled:
        _close_splash()
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
