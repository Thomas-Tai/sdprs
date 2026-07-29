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
                 on_fault: Optional[Callable] = None,
                 clock: Callable[[], float] = time.monotonic):
        super().__init__(daemon=True)
        self._server_url = server_url.rstrip("/")
        self._api_key = api_key
        self._node_ids = node_ids
        self._on_command = on_command
        self._stop_event = threading.Event()
        self._client: Optional[httpx.Client] = None
        self._backoff = 1.0
        # Injected so the per-node schedule below can be tested without a test
        # that really sleeps for tens of seconds. Production passes nothing.
        self._clock = clock
        # node -> the time it may next be polled, and node -> its latest
        # verdict. Together these let run() skip a backed-off node without
        # forgetting that it is broken. See run() and _defer.
        self._next_poll_at: Dict[str, float] = {}
        self._node_faults: Dict[str, Fault] = {}

        # Dedup locally: a failing poll runs continuously, and calling the hub
        # every cycle would swamp it. Report only on change.
        self._on_fault = on_fault
        self._last_fault = None

    def _defer(self, node_id: str, seconds: float) -> None:
        """Push ONE node's next poll out by `seconds`, without sleeping.

        Every arm of _poll_node that RETURNS must call this (directly or via
        _back_off): run() derives its own sleep from these deadlines, so a node
        that returns without setting one is polled again immediately. The 401
        arm is the sole exception and says why at its own site -- it stops the
        channel, so there is no next poll to schedule.
        """
        self._next_poll_at[node_id] = self._clock() + seconds

    def _back_off(self, node_id: str) -> None:
        """Penalise one failing node, then grow the penalty for the next one.

        Order matters: this node waits the CURRENT backoff and the next failure
        waits longer. Nothing sleeps -- that is the whole point (see run()).
        """
        self._defer(node_id, self._backoff)
        self._backoff = min(self._backoff * 2, 30.0)

    def _idle_wait(self) -> float:
        """Seconds until the earliest node comes due -- the loop's only sleep.

        With no cameras configured there are no deadlines at all: return the
        plain 1s anti-spin floor, because an empty camera list still starts a
        channel and must not spin a core on the guard's PC.

        The 0.05 floor is NOT a rare safety net -- it is the ROUTINE result
        whenever a node is already overdue, which happens as soon as polling
        outlasts its own deadline. The long-poll runs up to 5s, so with two or
        more cameras the earliest deadline is usually already in the past by
        the time we get here. All the floor does is bound how fast the cycle
        may spin. Do not mistake it for insurance against an arm that forgets
        to defer: it would turn a loud 100%-CPU spin into a quiet
        20-requests-per-second flood of the uplink, which is harder to notice,
        not safer.
        """
        now = self._clock()
        due_in = [self._next_poll_at.get(n, now) - now for n in self._node_ids]
        if not due_in:
            return 1.0
        return max(0.05, min(due_in))

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
            #
            # A node is polled only when it is DUE. The backoff used to be SLEPT
            # inside this single-threaded loop, so a failing node stalled every
            # healthy one behind it -- and once a healthy 200 stopped resetting
            # the shared backoff (above), that wait climbed to the 30s ceiling
            # and a FULLY WORKING camera's command poll degraded from ~1.1s to
            # ~30.1s. A guard pressing "live view" then waited up to half a
            # minute for stream_start. Each node now carries its own deadline
            # and the cycle SKIPS what is not due; nothing here sleeps per node.
            #
            # The cycle's verdict still spans EVERY node, not merely the ones
            # polled this time: a node is skipped precisely BECAUSE it is
            # broken, so dropping it from the fold would flap the hub
            # RUNNING/BAD_KEY exactly as per-node reporting once did.
            worst = Fault.NONE
            polled_any = False
            clean_cycle = False
            current = None

            now = self._clock()
            due = [n for n in self._node_ids
                   if self._next_poll_at.get(n, 0.0) <= now]
            try:
                for node_id in due:
                    if self._stop_event.is_set():
                        break
                    current = node_id
                    self._node_faults[node_id] = self._poll_node(node_id)
                    polled_any = True
                else:
                    clean_cycle = True     # every due node answered, no exception
            except httpx.TransportError:
                # httpx.TransportError is the shared base of ConnectError,
                # ConnectTimeout, ReadTimeout, ReadError, etc. -- catching only
                # ConnectError left the client's own connect=3.0 timeout
                # (below) falling into the bare `except Exception` arm, which
                # never reports to the hub (Task 3 review finding 1).
                worst = _worse(worst, Fault.NO_SERVER)
                polled_any = True
                logger.warning(f"Control channel transport failure, retry in {self._backoff}s")
                if current is not None:
                    # The request that raised belongs to ONE node; penalise that
                    # node. Without this it stays due and the loop re-polls it
                    # with no delay at all.
                    self._node_faults[current] = Fault.NO_SERVER
                    self._back_off(current)
            except Exception as e:
                # An application bug (a malformed body, a decoding error) -- NOT
                # a network fault. Reporting NO_SERVER would tell the guard to
                # go check a cable that is fine, so this arm asserts no fault of
                # its own. But it MUST still record a verdict.
                #
                # It used to record nothing, and that was the same stale-latch
                # bug the _on_command guard below fixes for its own path -- this
                # arm is simply the other door into it. Two shapes, both real on
                # a guard's site network:
                #   - Single camera behind a captive portal: every URL answers
                #     200 with an HTML login page, resp.json() raises here, and
                #     polled_any is never set (it is assigned AFTER _poll_node
                #     returns). The whole `if polled_any:` block is skipped, so
                #     the hub is never told anything and the tray sits on 啟動中
                #     for the life of the process. No toast, ever.
                #   - Two cameras, one healthy: the healthy one sets polled_any,
                #     so the all-nodes fold runs and re-reports THIS node's last
                #     verdict every cycle. A 403 the administrator already fixed
                #     shows 連線密碼已失效 forever while the server answers 200.
                #
                # NONE is the honest value: this node produced no operator-
                # actionable verdict, and a fault the guard cannot act on must
                # not outrank one they can. clean_cycle stays False, so the
                # backoff still decays -- an app bug must not be rewarded with
                # a full-speed poll.
                logger.warning("Control channel error on %s: %s", current, e,
                               exc_info=True)
                if current is not None:
                    self._node_faults[current] = Fault.NONE
                    polled_any = True
                    self._back_off(current)

            if polled_any:
                for node_id in self._node_ids:
                    worst = _worse(worst, self._node_faults.get(node_id, Fault.NONE))
                self._report(worst)
                if clean_cycle and worst is Fault.NONE:
                    # Only a cycle in which every node's LATEST VERDICT is good
                    # -- polled just now, or skipped while already healthy --
                    # earns the reset. Resetting on one healthy node let it hold
                    # the backoff at 1.0 while another failed every cycle, so a
                    # permanently broken node re-hammered the server ~1x/sec and
                    # the cadence never slowed down.
                    #
                    # A node with NO verdict yet folds as NONE, which would be
                    # scored as healthy. That is unreachable today -- a missing
                    # deadline defaults to 0.0, so every node is due and polled
                    # on cycle 1 -- but it is why this says "latest verdict"
                    # rather than "known good".
                    self._backoff = 1.0
            if not self._stop_event.is_set():
                # The ONLY sleep in the loop. Nothing due yet (every node is
                # either freshly polled or backed off), so wait until the
                # earliest deadline. An empty camera list still starts a
                # channel; _idle_wait's 1s floor keeps that from spinning a
                # core on the guard's PC.
                self._stop_event.wait(self._idle_wait())
        if self._client:
            self._client.close()

    def _poll_node(self, node_id: str) -> Fault:
        """Poll one node and RETURN its verdict; run() aggregates and reports.

        This must not call _report: see the once-per-cycle comment in run().
        It must not sleep either -- every arm sets the node's next-poll
        deadline instead, so one slow node cannot hold up the others.
        """
        url = f"{self._server_url}/api/webcam/{node_id}/commands"
        resp = self._client.get(url, params={"timeout": 5})
        if resp.status_code == 200:
            data = resp.json()
            cmd = data.get("command")
            if cmd:
                params = data.get("params")
                logger.info(f"Received command: {cmd} for {node_id}")
                try:
                    self._on_command(node_id, cmd, params)
                except Exception:
                    # A broken handler is an APP BUG, not the server's verdict.
                    # The 200 above already told us what this poll needed to
                    # know: the key is accepted and the uplink works.
                    #
                    # Letting it escape aborted the cycle before this node's
                    # verdict was recorded, so _node_faults kept whatever fault
                    # it last held -- and run()'s all-nodes fold re-reports that
                    # stale value every cycle. A 403 the administrator had
                    # already fixed would show 連線密碼已失效 forever while the
                    # server sat there answering 200.
                    logger.exception("Command handler failed for %s", node_id)
            # The small per-node floor keeps a 200 + null (empty long-poll)
            # response from tight-looping the uplink. The backoff RESET lives in
            # run(), where the whole cycle's outcome is known.
            self._defer(node_id, 0.1)
            return Fault.NONE
        if resp.status_code == 401:
            # The one arm that sets no deadline: there is no next poll to
            # schedule, because the channel is stopping. run() still reports
            # this cycle's verdict before the while-loop sees the stop.
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
            self._back_off(node_id)
            return Fault.BAD_KEY
        # httpx does NOT raise on 5xx; without this arm a persistent non-200/
        # non-401 status re-polls with no delay (Task 9 [Important] busy-loop).
        # Apply the SAME exponential backoff the transport-error arm uses.
        logger.warning(
            f"Control channel unexpected status {resp.status_code} for {node_id}, "
            f"retry in {self._backoff}s"
        )
        self._back_off(node_id)
        return Fault.NO_SERVER
