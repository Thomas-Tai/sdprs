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
        try:
            controller.stop_engines()          # free the cameras for the wizard
            new_cfg = settings_fn(controller.config)  # runs on the main thread
            if new_cfg:
                save_config(new_cfg)
                controller.apply(new_cfg)      # rebuild in-process, no restart
            else:
                controller.start_engines()     # cancelled -> resume old config
        except Exception:
            # A bad settings interaction (e.g. Tk blowing up mid-rebuild) must
            # not kill the tray app -- log it, best-effort resume the previous
            # engines, and keep the dispatch loop alive.
            logger.exception("Unexpected error handling OPEN_SETTINGS")
            try:
                controller.start_engines()
            except Exception:
                logger.exception("Failed to resume engines after OPEN_SETTINGS error")
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
