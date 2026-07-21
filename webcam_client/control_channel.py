# sdprs/webcam_client/control_channel.py
import logging
import threading
import time
from typing import Callable, Dict, Optional

import httpx

logger = logging.getLogger("webcam_client.control")


class ControlChannel(threading.Thread):
    def __init__(self, server_url: str, api_key: str, node_ids: list,
                 on_command: Callable[[str, str, Optional[dict]], None]):
        super().__init__(daemon=True)
        self._server_url = server_url.rstrip("/")
        self._api_key = api_key
        self._node_ids = node_ids
        self._on_command = on_command
        self._stop_event = threading.Event()
        self._client: Optional[httpx.Client] = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        self._client = httpx.Client(
            timeout=httpx.Timeout(10.0, connect=3.0),
            headers={"X-API-Key": self._api_key},
        )
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                for node_id in self._node_ids:
                    if self._stop_event.is_set():
                        break
                    self._poll_node(node_id)
                backoff = 1.0
            except httpx.ConnectError:
                logger.warning(f"Control channel connection failed, retry in {backoff}s")
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            except Exception as e:
                logger.debug(f"Control channel error: {e}")
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, 30.0)
        if self._client:
            self._client.close()

    def _poll_node(self, node_id: str) -> None:
        url = f"{self._server_url}/api/webcam/{node_id}/commands"
        resp = self._client.get(url, params={"timeout": 5})
        if resp.status_code == 200:
            data = resp.json()
            cmd = data.get("command")
            if cmd:
                params = data.get("params")
                logger.info(f"Received command: {cmd} for {node_id}")
                self._on_command(node_id, cmd, params)
        elif resp.status_code == 401:
            logger.error("API key rejected — stopping control channel")
            self._stop_event.set()
