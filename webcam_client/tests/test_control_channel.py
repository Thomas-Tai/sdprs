# sdprs/webcam_client/tests/test_control_channel.py
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.control_channel import ControlChannel


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = {"command": None} if payload is None else payload

    def json(self):
        return self._payload


class _FakeClient:
    """Answers each node id with a fixed status code and records poll order.

    run() builds its own httpx.Client, so tests that drive run() must install
    this via patch("webcam_client.control_channel.httpx.Client", ...).
    """

    def __init__(self, codes, payloads=None, on_get=None):
        self._codes = codes
        self._payloads = payloads or {}
        self._on_get = on_get
        self.calls = []

    def get(self, url, **kwargs):
        node = url.rstrip("/").split("/")[-2]     # .../api/webcam/<node>/commands
        self.calls.append(node)
        if self._on_get is not None:
            self._on_get(node, self.calls.count(node))
        return _FakeResp(self._codes[node], self._payloads.get(node))

    def close(self):
        pass


class _FakeClock:
    """Simulated monotonic time.

    Paired with _advancing_wait below, this lets a test watch a node decay to
    the 30s backoff ceiling in microseconds. Nothing here ever really sleeps.
    """

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, secs):
        self.now += secs


def _advancing_wait(clock, ch):
    """A stand-in for _stop_event.wait() that moves the fake clock instead of
    blocking. Returns the real wait()'s contract (True once stopped)."""

    def fake_wait(timeout=None):
        if timeout:
            clock.advance(timeout)
        return ch._stop_event.is_set()

    return fake_wait


def test_control_channel_init():
    cb = MagicMock()
    ch = ControlChannel("https://example.com", "sk-test", ["webcam_01"], cb)
    assert ch._node_ids == ["webcam_01"]
    assert not ch._stop_event.is_set()


def test_stop():
    cb = MagicMock()
    ch = ControlChannel("https://example.com", "sk-test", ["webcam_01"], cb)
    ch.stop()
    assert ch._stop_event.is_set()


def test_poll_node_5xx_triggers_backoff():
    # httpx does NOT raise on 5xx, so neither the 200-dispatch, the 401-stop, nor
    # the ConnectError backoff fires. A persistent non-200/non-401 must back off
    # (positive delay), not immediately re-poll (Task 9 [Important] busy-loop).
    #
    # D-1: the penalty is now a DEADLINE, not a sleep -- _poll_node must not
    # block, or one failing node stalls every healthy one. Assert the node was
    # pushed into the future rather than counting wait() calls.
    cb = MagicMock()
    clock = _FakeClock()
    ch = ControlChannel("https://example.com", "sk-test", ["webcam_01"], cb,
                        clock=clock)
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_client = MagicMock()
    mock_client.get.return_value = mock_resp
    ch._client = mock_client
    with patch.object(ch._stop_event, "wait") as mock_wait:
        ch._poll_node("webcam_01")
    cb.assert_not_called()                 # 5xx must NOT dispatch a command
    assert mock_wait.call_count == 0, "_poll_node must never block the cycle"
    delay = ch._next_poll_at["webcam_01"] - clock.now
    assert delay > 0                       # backed off, not an immediate re-poll
    assert ch._backoff > 1.0               # backoff grew for the next cycle


def test_control_channel_reports_bad_key_on_401():
    """401 -> BAD_KEY, AND the channel stops (Row B).

    _poll_node now RETURNS its verdict instead of reporting it (I-3): with every
    node writing the hub's single CONTROL_SOURCE slot, per-node reporting made
    divergent outcomes overwrite each other every cycle. run() aggregates and
    reports once -- see test_control_channel_reports_worst_fault_once_per_cycle.
    """
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append)

    class Resp:
        status_code = 401

    ch._client = type("C", (), {"get": lambda self, *a, **k: Resp()})()
    assert ch._poll_node("n1") is Fault.BAD_KEY
    # Row B: a rejected key cannot be fixed by retrying -- the channel stops.
    assert ch._stop_event.is_set() is True
    assert seen == [], "_poll_node must not report; run() aggregates the cycle"


def test_control_channel_reports_bad_key_on_403():
    """403 -> BAD_KEY, and the channel KEEPS POLLING (Row B).

    Unlike 401 the key itself may be valid and simply not own this camera; a
    settings change fixes that without a restart, so the channel must back off
    and stay alive. This distinction is the whole reason 401 and 403 are
    separate arms -- pin it, don't leave it to code reading.
    """
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    clock = _FakeClock()
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append, clock=clock)

    class Resp:
        status_code = 403

    ch._client = type("C", (), {"get": lambda self, *a, **k: Resp()})()
    with patch.object(ch._stop_event, "wait") as mock_wait:
        assert ch._poll_node("n1") is Fault.BAD_KEY
    # Row B: NOT stopped -- it backed off and will poll again.
    assert ch._stop_event.is_set() is False
    # D-1: backing off means deferring this node, never blocking the cycle.
    assert mock_wait.call_count == 0, "_poll_node must never block the cycle"
    assert ch._next_poll_at["n1"] - clock.now > 0
    assert seen == [], "_poll_node must not report; run() aggregates the cycle"


def test_control_channel_reports_none_on_clean_poll():
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    clock = _FakeClock()
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append, clock=clock)

    class Resp:
        status_code = 200

        def json(self):
            return {"command": None}

    ch._client = type("C", (), {"get": lambda self, *a, **k: Resp()})()
    with patch.object(ch._stop_event, "wait") as mock_wait:
        assert ch._poll_node("n1") is Fault.NONE
    assert seen == [], "_poll_node must not report; run() aggregates the cycle"
    # The healthy per-node floor is a deadline too (D-1): a 200 + null long-poll
    # must not tight-loop the uplink, but it must not block the cycle either.
    assert mock_wait.call_count == 0, "_poll_node must never block the cycle"
    assert ch._next_poll_at["n1"] - clock.now == 0.1


def test_control_channel_reports_worst_fault_once_per_cycle():
    """I-3: two nodes, n1 -> 200 and n2 -> 403, six cycles.

    Every node writes the hub's SINGLE CONTROL_SOURCE slot, so per-node
    reporting made each node overwrite the previous one's verdict. The
    reviewer's probe recorded 12 hub transitions in 6 cycles
    (RUNNING, BAD_KEY, RUNNING, BAD_KEY, ...) and ZERO toasts: every transition
    reset the hub's _state_since, so the 30s debounce never matured and a
    permanent, genuinely actionable fault was never announced. _last_fault
    cannot help -- the value genuinely alternates.

    A cycle must report its WORST outcome exactly once.

    The two nodes no longer poll in lockstep: since D-1 a backed-off node is
    SKIPPED rather than slept on, so n1 keeps its own ~0.1s cadence while n2
    decays. That is the fix, not a regression -- what this test pins is that
    the hub still hears ONE stable verdict across every one of those cycles.
    """
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    clock = _FakeClock()
    ch = ControlChannel("http://x", "k", ["n1", "n2"], lambda *a: None,
                        on_fault=seen.append, clock=clock)

    def on_get(node, nth):
        if node == "n2" and nth >= 6:      # stop once n2 has failed six times
            ch._stop_event.set()

    client = _FakeClient({"n1": 200, "n2": 403}, on_get=on_get)
    with patch("webcam_client.control_channel.httpx.Client", return_value=client), \
         patch.object(ch._stop_event, "wait", _advancing_wait(clock, ch)):
        ch.run()

    assert client.calls.count("n2") == 6
    assert client.calls.count("n1") > 6, (
        "the healthy node must not be held to the failing node's cadence")
    assert seen == [Fault.BAD_KEY], f"one stable fault per cycle, got {seen}"
    # n1's 200 used to reset _backoff to 1.0 every cycle, so n2's backoff never
    # grew and the flapping cycle stayed at ~1.1s forever.
    assert ch._backoff >= 4.0, f"a persistently failing node must back off, got {ch._backoff}"


def test_a_backed_off_node_never_delays_a_healthy_one():
    """D-1: a failing node must not stall the healthy nodes' command polling.

    The backoff used to be SLEPT inside the single-threaded per-node loop, so a
    permanently-403 camera's wait() blocked the whole cycle -- including every
    healthy camera's next poll. Once the worst-of-cycle change stopped a healthy
    200 from resetting the shared backoff, that wait climbed to the 30s ceiling
    and stayed there, so a FULLY WORKING camera's command poll degraded from
    ~1.1s to ~30.1s. The reviewer's probe, with n1 healthy and n2 permanently
    403:

        healthy-node poll gaps: [1.1, 2.1, 4.1, 8.1, 16.1, 30.1, 30.1]

    A guard pressing "live view" on the working camera then waits up to half a
    minute for stream_start to be picked up -- and multi-camera installs with
    one dead node are exactly the deployment worst-of-cycle was written for.

    The fix is to SKIP a backed-off node rather than sleep on it: each node
    carries its own next-poll time, the cycle polls only what is due, and the
    cycle floor sets the cadence. The failing node still gets its 30s decay.
    """
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    clock = _FakeClock()
    seen = []
    ch = ControlChannel("http://x", "k", ["n1", "n2"], lambda *a: None,
                        on_fault=seen.append, clock=clock)
    n1_polls = []

    def on_get(node, nth):
        if node == "n1":
            n1_polls.append(clock.now)
        elif node == "n2" and nth >= 8:
            ch._stop_event.set()          # n2 has reached the ceiling and held it
        if len(n1_polls) > 20000:         # a broken schedule must fail, not hang
            ch._stop_event.set()

    client = _FakeClient({"n1": 200, "n2": 403}, on_get=on_get)
    with patch("webcam_client.control_channel.httpx.Client", return_value=client), \
         patch.object(ch._stop_event, "wait", _advancing_wait(clock, ch)):
        ch.run()

    assert client.calls.count("n2") == 8, "precondition: n2 failed eight times"
    assert ch._backoff == 30.0, (
        f"precondition: the failing node must still decay to the ceiling, "
        f"got {ch._backoff}")
    assert seen == [Fault.BAD_KEY], "the cycle verdict must not flap while n2 is skipped"

    gaps = [b - a for a, b in zip(n1_polls, n1_polls[1:])]
    assert gaps, "n1 was never polled twice"
    assert max(gaps) <= 0.5, (
        f"a healthy camera's command poll was delayed by its failing neighbour: "
        f"worst gap {max(gaps):.1f}s, all gaps {[round(g, 1) for g in gaps]}")
    assert len(n1_polls) > 5 * client.calls.count("n2"), (
        "the healthy node must poll far more often than the backed-off one, "
        f"got n1={len(n1_polls)} n2={client.calls.count('n2')}")


def test_control_channel_cycle_reports_the_worse_of_divergent_faults():
    """Precedence within one cycle: BAD_KEY beats NO_SERVER because it is the
    more actionable instruction to the guard (status._PRECEDENCE)."""
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    ch = ControlChannel("http://x", "k", ["n1", "n2"], lambda *a: None,
                        on_fault=seen.append)

    def on_get(node, nth):
        if node == "n2":
            ch._stop_event.set()

    client = _FakeClient({"n1": 500, "n2": 403}, on_get=on_get)
    with patch("webcam_client.control_channel.httpx.Client", return_value=client), \
         patch.object(ch._stop_event, "wait"):
        ch.run()

    assert seen == [Fault.BAD_KEY]


def test_control_channel_401_still_reaches_the_hub_before_stopping():
    """A 401 stops the channel -- but the guard must still be told the key was
    rejected, or the channel dies silently with the tray still green."""
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append)
    client = _FakeClient({"n1": 401})
    with patch("webcam_client.control_channel.httpx.Client", return_value=client), \
         patch.object(ch._stop_event, "wait"):
        ch.run()

    assert seen == [Fault.BAD_KEY]
    assert ch._stop_event.is_set() is True


def test_control_channel_recovery_reports_none_and_resets_backoff():
    """A cycle in which EVERY node answered cleanly is what earns the reset:
    the fault clears and the poll cadence returns to normal."""
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    ch = ControlChannel("http://x", "k", ["n1", "n2"], lambda *a: None,
                        on_fault=seen.append)
    ch._backoff = 16.0                     # as if it had been failing for a while

    def on_get(node, nth):
        if node == "n2":
            ch._stop_event.set()

    client = _FakeClient({"n1": 200, "n2": 200}, on_get=on_get)
    with patch("webcam_client.control_channel.httpx.Client", return_value=client), \
         patch.object(ch._stop_event, "wait"):
        ch.run()

    assert seen == [Fault.NONE]
    assert ch._backoff == 1.0


def test_control_channel_reports_no_server_on_connect_timeout():
    """Review finding 1: ConnectTimeout is NOT a subclass of ConnectError
    (verified against the installed httpx 0.28.1 -- ConnectTimeout's MRO is
    TimeoutException -> TransportError -> RequestError -> HTTPError, with no
    ConnectError in it). It IS the client's own configured connect=3.0 timeout
    (control_channel.py run()), a real and likely failure mode. Before the fix
    it fell into the bare `except Exception` arm, which only logs -- the
    operator's status went silently stale. It must reach the hub as NO_SERVER,
    same as ConnectError."""
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault
    import httpx

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append)

    def fake_poll_node(node_id):
        ch._stop_event.set()  # let run()'s while-loop exit after this cycle
        raise httpx.ConnectTimeout("timed out")

    ch._poll_node = fake_poll_node
    with patch.object(ch._stop_event, "wait"):
        ch.run()
    assert seen == [Fault.NO_SERVER]


def test_control_channel_dedups_repeated_faults():
    """A failing poll runs continuously; the hub must not be called every time.

    Ported from the _poll_node level to the run() level with I-3: reporting now
    happens once per CYCLE in run(), so the dedup invariant is only observable
    there. 403 rather than 401 so the channel survives all five cycles.
    """
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault

    seen = []
    clock = _FakeClock()
    ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                        on_fault=seen.append, clock=clock)

    def on_get(node, nth):
        if nth >= 5:
            ch._stop_event.set()

    client = _FakeClient({"n1": 403}, on_get=on_get)
    with patch("webcam_client.control_channel.httpx.Client", return_value=client), \
         patch.object(ch._stop_event, "wait", _advancing_wait(clock, ch)):
        ch.run()

    assert client.calls.count("n1") == 5
    assert seen == [Fault.BAD_KEY], "repeat identical faults must report once"


def test_control_channel_never_reports_camera_down():
    """Row 17 / m-9: main.py drops the internal id `__control__` from the
    operator-facing camera-name text. That drop is lossless ONLY because this
    module never reports CAMERA_DOWN -- the one fault whose string interpolates
    {camera_names}. If the control channel ever did report it, the guard would
    read "攝影機 目前沒有畫面" naming no camera at all. Pin the premise.
    """
    from webcam_client import control_channel as cc
    from webcam_client.control_channel import ControlChannel
    from webcam_client.status import Fault
    import httpx

    seen = []

    # 1) Behavioural: every status-code arm _poll_node has.
    for code in (200, 401, 403, 404, 418, 500, 503):
        ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                            on_fault=seen.append)
        ch._client = type("C", (), {
            "get": lambda self, *a, **k: _FakeResp(code)})()
        with patch.object(ch._stop_event, "wait"):
            seen.append(ch._poll_node("n1"))

    # 2) Behavioural: both of run()'s except arms.
    for exc in (httpx.ConnectTimeout("timed out"), ValueError("app bug")):
        ch = ControlChannel("http://x", "k", ["n1"], lambda *a: None,
                            on_fault=seen.append)

        def boom(node_id, _exc=exc, _ch=ch):
            _ch._stop_event.set()
            raise _exc

        ch._poll_node = boom
        with patch("webcam_client.control_channel.httpx.Client",
                   return_value=_FakeClient({})), \
             patch.object(ch._stop_event, "wait"):
            ch.run()

    assert Fault.CAMERA_DOWN not in seen, f"control channel produced CAMERA_DOWN: {seen}"

    # 3) Structural: nothing in the module can produce it in the first place, so
    #    a future arm cannot quietly break main.py's `__control__` filter.
    src = Path(cc.__file__).read_text(encoding="utf-8")
    assert "CAMERA_DOWN" not in src, "control_channel.py must never name CAMERA_DOWN"


def test_control_channel_logs_application_errors_at_warning(caplog):
    """I-4: the bare `except Exception` arm deliberately reports NOTHING to the
    hub -- a broken _on_command callback or a malformed body must not tell the
    guard to check a network cable that is fine. The accepted trade was that
    those land in the log. They did not: the record was logger.debug and the
    root logger sits at INFO (logging_setup.py), so it was dropped before the
    file handler. The channel then backed off to 30s and failed silently for
    the life of the process, with no log line for the technician.
    """
    import logging
    from webcam_client.control_channel import ControlChannel

    seen = []
    ch = ControlChannel("http://x", "k", ["n1"], None, on_fault=seen.append)

    def bad_handler(node_id, cmd, params):
        ch._stop_event.set()
        raise ValueError("broken command handler")

    ch._on_command = bad_handler
    client = _FakeClient({"n1": 200}, payloads={"n1": {"command": "start_stream"}})

    with caplog.at_level(logging.WARNING, logger="webcam_client.control"), \
         patch("webcam_client.control_channel.httpx.Client", return_value=client), \
         patch.object(ch._stop_event, "wait"):
        ch.run()

    records = [r for r in caplog.records if r.name == "webcam_client.control"]
    assert records, "an application error must survive the root logger's INFO level"
    assert any(r.levelno >= logging.WARNING and r.exc_info for r in records), \
        "the technician needs the traceback, not just a one-line message"
    assert seen == [], "this arm must not report to the hub (I-4)"


def test_control_channel_with_no_nodes_does_not_busy_loop():
    """AppController starts the channel unconditionally, so node_ids is empty
    whenever no camera is configured/enabled. A cycle that polls nothing must
    still wait, or run() spins a core on the guard's PC, and it must not report
    a health verdict it has no evidence for."""
    import threading
    from webcam_client.control_channel import ControlChannel

    seen = []
    ch = ControlChannel("http://x", "k", [], lambda *a: None, on_fault=seen.append)
    waits = []

    def fake_wait(timeout=None):
        waits.append(timeout)
        ch._stop_event.set()
        return True

    # Driven on a worker thread: the pre-fix behaviour is an INFINITE loop that
    # never calls wait(), which would hang the suite rather than fail it.
    def drive():
        with patch("webcam_client.control_channel.httpx.Client",
                   return_value=_FakeClient({})), \
             patch.object(ch._stop_event, "wait", fake_wait):
            ch.run()

    t = threading.Thread(target=drive, daemon=True)
    t.start()
    t.join(timeout=5.0)
    finished = not t.is_alive()
    ch._stop_event.set()          # let a spinning thread die either way

    assert finished, "run() with no nodes spun without ever sleeping"
    assert waits and waits[0] and waits[0] > 0, "an empty cycle must still sleep"
    assert seen == [], "nothing was polled -- report nothing"
