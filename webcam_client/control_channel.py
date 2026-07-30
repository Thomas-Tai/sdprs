# sdprs/webcam_client/control_channel.py
import logging
import threading
import time
from typing import Callable, Dict, Optional

import httpx

# worse_fault() is the hub's own worst-first fault comparison. It is imported
# READ-ONLY and deliberately not re-declared here: this module and
# push_engine.py used to each carry a byte-identical private copy of it, which
# is exactly the drift risk a single shared rule is supposed to prevent -- a
# private copy could go stale the moment status.py's _PRECEDENCE changed, and
# this channel and the hub would then silently disagree about which of two
# live faults matters more to the guard. Promoted to status.py so there is
# only one rule for both modules to keep in sync with.
from .status import Fault, worse_fault

logger = logging.getLogger("webcam_client.control")

# One node's penalty schedule: its first failure costs a second, each further
# consecutive failure doubles, and it stops at half a minute. PER NODE -- see
# ControlChannel._back_off for what a shared counter did to the healthy cameras.
_BACKOFF_START = 1.0
_BACKOFF_CEILING = 30.0


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
        # Set ONLY by stop(), i.e. only when the MAIN thread is tearing this
        # channel down. _stop_event cannot answer "who stopped us", and the two
        # cases need OPPOSITE reporting behaviour: a 401 stops the channel
        # itself and its verdict must still reach the guard, while an owner
        # teardown must go quiet. See _report and stop().
        self._stopped_by_owner = threading.Event()
        self._client: Optional[httpx.Client] = None
        # node -> ITS OWN current penalty, absent until that node fails. A single
        # channel-wide float made the nodes fine each other by list position --
        # see _back_off.
        self._backoff: Dict[str, float] = {}
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

    def _backoff_for(self, node_id: str) -> float:
        """This node's own current penalty. A node with no failure on record --
        never failed, or recovered since -- starts again from the 1s floor."""
        return self._backoff.get(node_id, _BACKOFF_START)

    def _back_off(self, node_id: str) -> None:
        """Penalise ONE node, then grow THAT NODE's own penalty.

        Order matters: this node waits its CURRENT penalty and its next failure
        waits longer. Nothing sleeps -- that is the whole point (see run()).

        The penalty is per node. It used to be a single channel-wide float, and
        because it is SPENT as a per-node deadline the nodes fined each other by
        list position: with one permanently-403 camera driving the shared value
        to the ceiling, the FIRST transient failure on a perfectly healthy
        camera was deferred 30 seconds instead of 1 -- a sentence it had not
        earned, for a fault that had already cleared. The guard pressing "live
        view" on the WORKING camera then waited half a minute for stream_start,
        decided by which camera happened to sit next to it in the list.
        """
        penalty = self._backoff_for(node_id)
        self._defer(node_id, penalty)
        self._backoff[node_id] = min(penalty * 2, _BACKOFF_CEILING)

    def _clear_backoff(self, node_id: str) -> None:
        """This node answered cleanly: its NEXT failure starts from 1s again.

        Called from the 200 arm only, and deliberately after the body has been
        parsed -- a 200 the client cannot read is not a clean answer, and a
        captive portal returning one on every poll must still decay.

        This used to be a whole-cycle decision in run(), gated on every node's
        latest verdict being good, because with ONE shared counter a single
        healthy node resetting it held the whole channel at 1.0 while another
        node failed every cycle -- so a permanently broken camera re-hammered
        the server ~1x/sec and the cadence never slowed down. With each node
        owning its penalty that cannot happen: this pops one node's entry and
        touches no other node's, so a broken neighbour keeps decaying to the
        ceiling on its own schedule.
        """
        self._backoff.pop(node_id, None)

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
        if self._stopped_by_owner.is_set():
            # The main thread is tearing this channel down.
            # AppController.stop_engines() calls stop() and then
            # hub.clear_all(), which drops every fault no live worker owns any
            # more -- and it does NOT join this thread, so a cycle already in
            # flight used to land AFTER that clear_all() and put a fault back.
            # That leaves the guard a light owned by a channel that no longer
            # exists: red with nothing retrying behind it, or green over a
            # camera whose engine is already gone. On a settings edit it then
            # stands until the NEW channel's first cycle overwrites it.
            #
            # Keyed on _stopped_by_owner and NOT on _stop_event on purpose: the
            # 401 arm sets _stop_event itself and its BAD_KEY MUST still be
            # announced, or the channel dies with the tray green
            # (test_control_channel_401_still_reaches_the_hub_before_stopping).
            # "We stopped ourselves over something the guard must act on" and
            # "the main thread told us to shut down" are opposite cases.
            return
        if fault is self._last_fault:
            return
        self._last_fault = fault
        if self._on_fault is not None:
            self._on_fault(fault)

    def stop(self) -> None:
        # _stopped_by_owner first: it must already be visible to a cycle that
        # wakes up on the _stop_event being set, or the very last cycle can
        # still slip a fault past _report's guard.
        self._stopped_by_owner.set()
        self._stop_event.set()

    def run(self) -> None:
        # EVERYTHING is inside the try, client construction included, and the
        # client is closed in the finally. Before this, close() sat below the
        # loop with no finally over it, and _report() -> on_fault ->
        # StatusHub.report -> the hub's own on_change is a callback path INTO
        # the caller: anything raising there took the whole thread out. In the
        # windowed console=False onefile build sys.stderr is None, so
        # threading.excepthook wrote that traceback nowhere -- the channel died
        # leaving ZERO evidence, the connection pool leaked, and remote commands
        # were dead for the life of the process. push_engine.run() already
        # wraps its loop this way for exactly this reason.
        try:
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
                # the then-shared backoff, that wait climbed to the 30s ceiling
                # and a FULLY WORKING camera's command poll degraded from ~1.1s to
                # ~30.1s. A guard pressing "live view" then waited up to half a
                # minute for stream_start. Each node now carries its own deadline
                # and the cycle SKIPS what is not due; nothing here sleeps per node.
                # The penalty itself is per node too (_back_off), so a node cannot
                # be charged for its neighbour's failures either.
                #
                # The cycle's verdict still spans EVERY node, not merely the ones
                # polled this time: a node is skipped precisely BECAUSE it is
                # broken, so dropping it from the fold would flap the hub
                # RUNNING/BAD_KEY exactly as per-node reporting once did.
                worst = Fault.NONE
                polled_any = False
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
                except httpx.TransportError:
                    # httpx.TransportError is the shared base of ConnectError,
                    # ConnectTimeout, ReadTimeout, ReadError, etc. -- catching only
                    # ConnectError left the client's own connect=3.0 timeout
                    # (below) falling into the bare `except Exception` arm, which
                    # never reports to the hub (Task 3 review finding 1).
                    worst = worse_fault(worst, Fault.NO_SERVER)
                    polled_any = True
                    # NAME THE NODE. On a multi-camera site this line used to say
                    # only "transport failure", so the technician reading the log
                    # could not tell WHICH camera's uplink had dropped -- and with
                    # per-node penalties a channel-wide "retry in" figure would
                    # not even be true of the node that failed. The retry figure
                    # is read before _back_off spends it, so it is the delay this
                    # node is actually about to serve.
                    logger.warning("Control channel transport failure for %s, "
                                   "retry in %ss", current,
                                   self._backoff_for(current))
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
                    # not outrank one they can. _back_off below still decays THIS
                    # node's cadence -- an app bug must not be rewarded with a
                    # full-speed poll, and the 200 arm's reset is out of reach
                    # from here because the body never parsed.
                    logger.warning("Control channel error on %s: %s", current, e,
                                   exc_info=True)
                    if current is not None:
                        self._node_faults[current] = Fault.NONE
                        polled_any = True
                        self._back_off(current)

                if polled_any:
                    for node_id in self._node_ids:
                        worst = worse_fault(worst, self._node_faults.get(node_id, Fault.NONE))
                    self._report(worst)
                    # No backoff reset here any more. It used to need whole-cycle
                    # knowledge (every node's latest verdict good) because ONE
                    # shared counter meant a single healthy node's reset also
                    # freed the failing ones, holding a permanently broken camera
                    # at a ~1x/sec re-hammer of the server. Each node now owns its
                    # penalty, so recovery is a per-node fact and belongs where
                    # that fact is known: the 200 arm, via _clear_backoff.
                if not self._stop_event.is_set():
                    # The ONLY sleep in the loop. Nothing due yet (every node is
                    # either freshly polled or backed off), so wait until the
                    # earliest deadline. An empty camera list still starts a
                    # channel; _idle_wait's 1s floor keeps that from spinning a
                    # core on the guard's PC.
                    self._stop_event.wait(self._idle_wait())
        except Exception:
            # The channel is DEAD from here on: nothing below re-enters the loop.
            # The log line is therefore the only artifact this failure will ever
            # leave, which is why it is logger.exception (ERROR + traceback) and
            # not logger.debug -- the root logger sits at INFO, so debug never
            # reaches the file the technician reads (logging_setup.py).
            #
            # DELIBERATELY NO _report() HERE. Two reasons, in order of weight:
            #
            #  1. The most likely way to arrive here is _report() itself: it
            #     calls on_fault, which is the hub, which runs the hub's
            #     on_change callback. Reporting from this arm would re-enter the
            #     code that just raised, and a second exception would escape
            #     run() anyway -- replacing the real traceback with a derived
            #     one and undoing the whole point of this handler. Guarding that
            #     with its own try/except would add a failure mode for zero
            #     guard benefit.
            #  2. Every fault value this module may use states something FALSE
            #     about a crashed channel, and each one sends the guard
            #     somewhere it cannot help. NO_SERVER claims uploads have
            #     stopped and promises they resume by themselves -- but the push
            #     engines are separate threads still uploading pictures fine,
            #     and nothing here will ever resume; it also outranks their
            #     healthy reports, so a site whose recording works would show a
            #     red "check the network cable" light. BAD_KEY sends the guard
            #     to the administrator for a replacement key that was never the
            #     problem. NONE turns the tray GREEN over a channel that can no
            #     longer deliver a single command. The camera-down fault, whose
            #     action (press 重新連線, which is what rebuilds this thread) is
            #     the only one that would actually be right, is exactly the
            #     fault this module may never emit: main.py strips __control__
            #     from the camera-name text, so the guard would read a sentence
            #     naming no camera at all (see
            #     test_control_channel_never_reports_camera_down).
            #
            # So the hub keeps this channel's last TRUTHFUL verdict and the
            # crash goes to the log. Residual, accepted knowingly: if that last
            # verdict was NONE the tray stays green while commands are dead. The
            # alternative is a red light that lies about a working site, which
            # is worse -- it teaches the guard to ignore the light.
            logger.exception("Control channel stopped unexpectedly")
        finally:
            # Not conditional on a clean exit: the leak this closes was worst
            # exactly when the loop died early, and 重新連線 -- which strings.py
            # tells the guard to press again and again -- rebuilds the channel
            # every time, so a leaked pool per press accumulates.
            if self._client is not None:
                self._client.close()
                self._client = None

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
            # response from tight-looping the uplink.
            self._defer(node_id, 0.1)
            # THIS node answered cleanly, so THIS node's penalty is spent: its
            # next failure starts from 1s again. Reached only after resp.json()
            # succeeded above -- a 200 carrying a captive portal's login page is
            # not a clean answer and must keep decaying (see run()'s bare
            # `except Exception` arm and its two regression tests).
            self._clear_backoff(node_id)
            return Fault.NONE
        if resp.status_code == 401:
            # The one arm that sets no deadline: there is no next poll to
            # schedule, because the channel is stopping. run() still reports
            # this cycle's verdict before the while-loop sees the stop (only
            # stop() from the main thread silences that -- see _report).
            #
            # The node id is in the message because a rejected key is per
            # camera: on a multi-camera site the technician's first question is
            # which camera the server refused.
            logger.error("API key rejected for %s — stopping control channel",
                         node_id)
            self._stop_event.set()
            return Fault.BAD_KEY
        if resp.status_code == 403:
            # Unlike 401, the key itself may be valid -- it just doesn't own
            # this camera. A settings change can fix that without a restart,
            # so back off and keep polling rather than stopping the channel.
            logger.warning(
                f"Control channel forbidden (403) for {node_id}, "
                f"retry in {self._backoff_for(node_id)}s"
            )
            self._back_off(node_id)
            return Fault.BAD_KEY
        # httpx does NOT raise on 5xx; without this arm a persistent non-200/
        # non-401 status re-polls with no delay (Task 9 [Important] busy-loop).
        # Apply the SAME exponential backoff the transport-error arm uses.
        logger.warning(
            f"Control channel unexpected status {resp.status_code} for {node_id}, "
            f"retry in {self._backoff_for(node_id)}s"
        )
        self._back_off(node_id)
        return Fault.NO_SERVER
