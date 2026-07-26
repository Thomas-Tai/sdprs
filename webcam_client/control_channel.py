# sdprs/webcam_client/control_channel.py
import logging
import threading
import time
from typing import Callable, Dict, Optional

import httpx

# _PRECEDENCE is the hub's own worst-first fault ordering. It is imported
# READ-ONLY and deliberately not re-declared here: a private copy could drift,
# and then the channel and the hub would disagree about which of two faults
# matters more to the guard.
from .status import Fault, _PRECEDENCE

logger = logging.getLogger("webcam_client.control")

_RANK = {fault: i for i, fault in enumerate(_PRECEDENCE)}
_RANK_NONE = len(_PRECEDENCE)          # Fault.NONE is not in _PRECEDENCE


def _worse(a: Fault, b: Fault) -> Fault:
    """Return whichever of the two faults the operator should hear about."""
    return a if _RANK.get(a, _RANK_NONE) <= _RANK.get(b, _RANK_NONE) else b


class ControlChannel(threading.Thread):
    def __init__(self, server_url: str, api_key: str, node_ids: list,
                 on_command: Callable[[str, str, Optional[dict]], None],
                 on_fault: Optional[Callable] = None):
        super().__init__(daemon=True)
        self._server_url = server_url.rstrip("/")
        self._api_key = api_key
        self._node_ids = node_ids
        self._on_command = on_command
        self._stop_event = threading.Event()
        self._client: Optional[httpx.Client] = None
        self._backoff = 1.0

        # Dedup locally: a failing poll runs continuously, and calling the hub
        # every cycle would swamp it. Report only on change.
        self._on_fault = on_fault
        self._last_fault = None

    def _report(self, fault) -> None:
        if fault is self._last_fault:
            return
        self._last_fault = fault
        if self._on_fault is not None:
            self._on_fault(fault)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        self._client = httpx.Client(
            timeout=httpx.Timeout(10.0, connect=3.0),
            headers={"X-API-Key": self._api_key},
        )
        while not self._stop_event.is_set():
            # ONE report per cycle, of the cycle's WORST outcome.
            #
            # Every node writes the hub's single CONTROL_SOURCE slot, so
            # reporting per node made divergent outcomes overwrite each other:
            # with n1 -> 200 and n2 -> 403 the hub flapped RUNNING/BAD_KEY twice
            # a second forever. _last_fault cannot help -- the value genuinely
            # alternates. Worse, every transition reset the hub's _state_since,
            # so the 30s notify debounce never matured and a permanent,
            # actionable fault was NEVER announced to the guard.
            worst = Fault.NONE
            polled_any = False
            clean_cycle = False
            handled_error = False
            try:
                for node_id in self._node_ids:
                    if self._stop_event.is_set():
                        break
                    worst = _worse(worst, self._poll_node(node_id))
                    polled_any = True
                else:
                    clean_cycle = True     # every node answered, no exception
            except httpx.TransportError:
                # httpx.TransportError is the shared base of ConnectError,
                # ConnectTimeout, ReadTimeout, ReadError, etc. -- catching only
                # ConnectError left the client's own connect=3.0 timeout
                # (below) falling into the bare `except Exception` arm, which
                # never reports to the hub (Task 3 review finding 1).
                worst = _worse(worst, Fault.NO_SERVER)
                polled_any = True
                handled_error = True
                logger.warning(f"Control channel transport failure, retry in {self._backoff}s")
                self._stop_event.wait(self._backoff)
                self._backoff = min(self._backoff * 2, 30.0)
            except Exception as e:
                # An application bug (a broken _on_command, a malformed body) --
                # NOT a network fault. Reporting it would tell the guard to go
                # check a cable that is fine, so this arm stays silent to the
                # hub. But the code still owes the technician a log line: this
                # was logger.debug and the root logger sits at INFO, so it went
                # nowhere at all while the channel backed off to 30s and failed
                # silently for the life of the process.
                handled_error = True
                logger.warning("Control channel error: %s", e, exc_info=True)
                self._stop_event.wait(self._backoff)
                self._backoff = min(self._backoff * 2, 30.0)

            if polled_any:
                self._report(worst)
                if clean_cycle and worst is Fault.NONE:
                    # Only a cycle in which EVERY node answered cleanly earns
                    # the reset. Resetting per node let one healthy camera hold
                    # the backoff at 1.0 while another failed every cycle, so a
                    # permanently broken node re-hammered the server ~1x/sec and
                    # the cadence never slowed down.
                    self._backoff = 1.0
            elif not handled_error and not self._stop_event.is_set():
                # Nothing was polled and nothing failed: an empty camera list
                # still starts a channel. Report nothing (no evidence), but do
                # not let this while-loop spin a core on the guard's PC.
                self._stop_event.wait(1.0)
        if self._client:
            self._client.close()

    def _poll_node(self, node_id: str) -> Fault:
        """Poll one node and RETURN its verdict; run() aggregates and reports.

        This must not call _report: see the once-per-cycle comment in run().
        """
        url = f"{self._server_url}/api/webcam/{node_id}/commands"
        resp = self._client.get(url, params={"timeout": 5})
        if resp.status_code == 200:
            data = resp.json()
            cmd = data.get("command")
            if cmd:
                params = data.get("params")
                logger.info(f"Received command: {cmd} for {node_id}")
                self._on_command(node_id, cmd, params)
            # The small per-node floor keeps a 200 + null (empty long-poll)
            # response from tight-looping the uplink. The backoff RESET lives in
            # run(), where the whole cycle's outcome is known.
            self._stop_event.wait(0.1)
            return Fault.NONE
        if resp.status_code == 401:
            logger.error("API key rejected — stopping control channel")
            self._stop_event.set()
            return Fault.BAD_KEY
        if resp.status_code == 403:
            # Unlike 401, the key itself may be valid -- it just doesn't own
            # this camera. A settings change can fix that without a restart,
            # so back off and keep polling rather than stopping the channel.
            logger.warning(
                f"Control channel forbidden (403) for {node_id}, "
                f"retry in {self._backoff}s"
            )
            self._stop_event.wait(self._backoff)
            self._backoff = min(self._backoff * 2, 30.0)
            return Fault.BAD_KEY
        # httpx does NOT raise on 5xx; without this arm a persistent non-200/
        # non-401 status re-polls with no delay (Task 9 [Important] busy-loop).
        # Apply the SAME exponential backoff the transport-error arm uses.
        logger.warning(
            f"Control channel unexpected status {resp.status_code} for {node_id}, "
            f"retry in {self._backoff}s"
        )
        self._stop_event.wait(self._backoff)
        self._backoff = min(self._backoff * 2, 30.0)
        return Fault.NO_SERVER
