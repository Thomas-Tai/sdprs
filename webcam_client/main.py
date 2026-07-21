# sdprs/webcam_client/main.py
import logging
import signal
import sys
import threading
import time

from .config import load_config, save_config, is_first_run
from .push_engine import PushEngine
from .control_channel import ControlChannel
from .gui.setup_wizard import run_setup_wizard
from .gui.tray_app import TrayApp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("webcam_client.main")

_running = True


def _signal_handler(sig, frame):
    global _running
    _running = False


def main():
    global _running
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    config = load_config()

    if is_first_run() or not config.get("server_url"):
        new_config = run_setup_wizard(config)
        if new_config is None:
            logger.info("Setup cancelled, exiting")
            return
        config = new_config
        save_config(config)

    server_url = config["server_url"]
    api_key = config["api_key"]
    cameras = [c for c in config.get("cameras", []) if c.get("enabled", True)]

    if not cameras:
        logger.error("No cameras configured")
        return

    # Start push engines
    engines = []
    for cam in cameras:
        cam["motion_threshold"] = config.get("motion_threshold", 25)
        engine = PushEngine(cam, server_url, api_key)
        engine.start()
        engines.append(engine)

    # Start control channel
    node_ids = [c["node_id"] for c in cameras if c.get("node_id")]

    def on_command(node_id: str, command: str, params: dict = None):
        for engine in engines:
            if engine._node_id == node_id:
                if command == "stream_start":
                    engine.set_streaming(True)
                elif command == "stream_stop":
                    engine.set_streaming(False)
                break

    control = ControlChannel(server_url, api_key, node_ids, on_command)
    control.start()

    # Tray app
    paused = threading.Event()

    tray = TrayApp(
        on_open_settings=lambda: _open_settings(config),
        on_quit=lambda: _shutdown(engines, control),
        on_pause=lambda: paused.set(),
        on_resume=lambda: paused.clear(),
    )
    tray.start()
    tray.set_status(True)

    logger.info(f"SDPRS Webcam Client running ({len(cameras)} cameras)")

    # Main loop — heartbeat
    heartbeat_interval = config.get("heartbeat_interval", 30)
    last_heartbeat = 0.0
    while _running:
        time.sleep(1)
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            last_heartbeat = now
            # Heartbeat is implicit via snapshot push; server detects offline at 90s

    _shutdown(engines, control)


def _open_settings(config):
    new_config = run_setup_wizard(config)
    if new_config:
        save_config(new_config)
        logger.info("Settings updated — restart required")


def _shutdown(engines, control):
    global _running
    _running = False
    control.stop()
    for engine in engines:
        engine.stop()
    for engine in engines:
        engine.join(timeout=5)
    logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
