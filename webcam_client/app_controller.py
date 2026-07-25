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
