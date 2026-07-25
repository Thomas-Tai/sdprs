# webcam_client/app_controller.py
import logging
import threading
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
        self._paused = False
        # pause_all/resume_all run on the pystray daemon thread; start/stop/apply
        # run on the main thread. Guards _engines mutations + snapshots so the
        # two threads never observe/mutate the list mid-iteration.
        self._lock = threading.Lock()

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
            # Re-assert the remembered pause state on freshly built engines --
            # otherwise a settings save silently un-pauses uploads while the
            # tray still shows amber/"resume" (Finding 1).
            engine.set_paused(self._paused)
            # Track the engine THE MOMENT it is started (Finding A regression
            # fix): if a later camera in this loop fails to build/start,
            # engines started so far are already tracked, so stop_engines()
            # can still release them instead of leaking an open camera.
            with self._lock:
                self._engines.append(engine)
        node_ids = [c["node_id"] for c in self._enabled_cameras() if c.get("node_id")]
        self._control = self._control_factory(server_url, api_key, node_ids,
                                              self._on_command)
        self._control.start()

    def stop_engines(self) -> None:
        with self._lock:
            engines = list(self._engines)
        try:
            if self._control is not None:
                try:
                    self._control.stop()
                except Exception:
                    logger.exception("Error stopping control channel")
                self._control = None
            for e in engines:
                try:
                    e.stop()
                except Exception:
                    logger.exception("Error stopping engine %s",
                                     getattr(e, "_node_id", e))
            for e in engines:
                join = getattr(e, "join", None)
                if callable(join):
                    try:
                        join(timeout=5)
                    except Exception:
                        logger.exception("Error joining engine %s",
                                         getattr(e, "_node_id", e))
                is_alive = getattr(e, "is_alive", None)
                if callable(is_alive) and is_alive():
                    logger.warning(
                        "Engine %s did not stop within timeout; "
                        "camera device may still be open",
                        getattr(e, "_node_id", e))
        finally:
            with self._lock:
                self._engines = []

    def apply(self, new_config: dict) -> None:
        self.stop_engines()
        self._config = dict(new_config)
        self.start_engines()

    def pause_all(self) -> None:
        self._paused = True
        with self._lock:
            engines = list(self._engines)
        for e in engines:
            e.set_paused(True)

    def resume_all(self) -> None:
        self._paused = False
        with self._lock:
            engines = list(self._engines)
        for e in engines:
            e.set_paused(False)

    def shutdown(self) -> None:
        self.stop_engines()

    def _on_command(self, node_id: str, command: str,
                    params: Optional[dict] = None) -> None:
        # _on_command runs on the ControlChannel thread -- snapshot under the
        # lock first (Finding C) so it never iterates _engines concurrently
        # with a start/stop/apply mutation on the main thread.
        with self._lock:
            engines = list(self._engines)
        for e in engines:
            if getattr(e, "_node_id", None) == node_id:
                if command == "stream_start":
                    e.set_streaming(True)
                elif command == "stream_stop":
                    e.set_streaming(False)
                break
