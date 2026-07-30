# sdprs/webcam_client/push_engine.py
import logging
import threading
import time
from typing import Callable, Optional

import cv2
import httpx

from .camera_manager import open_camera, compute_motion, adaptive_fps
from .hls_encoder import HlsEncoder
# _PRECEDENCE is the hub's own worst-first fault ordering, imported READ-ONLY.
# _publish() below merges this engine's two fault domains with it, so the engine
# and the hub can never disagree about which of two live problems the guard
# should be told about. A private copy of the ordering would drift the first time
# a fault was added. control_channel.py imports it for the same reason.
from .status import Fault, _PRECEDENCE

logger = logging.getLogger("webcam_client.push_engine")

_RANK = {fault: i for i, fault in enumerate(_PRECEDENCE)}
_RANK_NONE = len(_PRECEDENCE)          # Fault.NONE is not in _PRECEDENCE


def _worse(a: Fault, b: Fault) -> Fault:
    """Return whichever of the two faults the operator should hear about."""
    return a if _RANK.get(a, _RANK_NONE) <= _RANK.get(b, _RANK_NONE) else b

# How long a camera may deliver nothing before the operator is told.
#
# cap.read() returning False is routine for a frame or two (USB re-enumeration,
# a driver hiccup, a device waking up), so a single bad read must not flap the
# tray. But a pulled USB cable ALSO shows up as nothing but failed reads, and
# the old loop just slept and retried forever without ever reporting -- the tray
# stayed green on a camera that had been dead for hours.
#
# BAD_READ_LIMIT consecutive failures x BAD_READ_SLEEP each = ~3 seconds of no
# picture before we say so. Keep these two together: the wall-clock figure is
# what the comment and the operator-facing behaviour are argued from.
BAD_READ_SLEEP = 0.1
BAD_READ_LIMIT = 30


def _classify(exc) -> Fault:
    """Map a failed upload to the fault the operator needs to act on.

    401/403 mean the key is rejected -> the guard must call the administrator.
    Everything else (transport failure, 5xx, 404) means the server is not
    usefully reachable -> the guard should check the network first.
    """
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) in (401, 403):
        return Fault.BAD_KEY
    return Fault.NO_SERVER


class PushEngine(threading.Thread):
    def __init__(self, camera_config: dict, server_url: str, api_key: str,
                 on_fault: Optional[Callable[[Fault], None]] = None):
        super().__init__(daemon=True)
        self._cam_config = camera_config
        self._server_url = server_url.rstrip("/")
        self._api_key = api_key
        self._node_id = camera_config.get("node_id", "")
        self._resolution = tuple(camera_config.get("resolution", [640, 480]))
        self._jpeg_quality = camera_config.get("jpeg_quality", 40)
        self._target_fps = camera_config.get("target_fps", 8)
        self._motion_threshold = camera_config.get("motion_threshold", 25)

        self._stop_event = threading.Event()
        self._paused = threading.Event()
        self._streaming = False
        self._stream_lock = threading.Lock()
        self._encoder: Optional[HlsEncoder] = None
        self._client: Optional[httpx.Client] = None

        # TWO fault domains, deliberately separate -- see _publish().
        #
        # This engine watches two independent things: is there a PICTURE (the
        # camera opens, cap.read() delivers frames, the loop is alive) and do
        # snapshots REACH THE SERVER. They fail independently, and they used to
        # share one slot, so whichever spoke last erased the other. The reachable
        # consequence, with a pulled USB cable AND a down server: the read path
        # `continue`s before _push_snapshot, freezing the uplink verdict; the
        # guard re-seats the cable; the recovery arm fired Fault.NONE; the hub
        # dropped this source and went RUNNING; and because recovery toasts are
        # deliberately NOT debounced the guard was told 監控中 while nothing was
        # uploading at all. The next failed push then re-reported NO_SERVER,
        # resetting the hub's _state_since, so the REAL fault had to sit through
        # another full 30s debounce before it could be announced.
        self._camera_fault = Fault.NONE
        self._uplink_fault = Fault.NONE

        # Dedup locally: a failing push runs continuously, and calling the hub
        # every cycle would swamp it. Report only on change.
        self._on_fault = on_fault
        self._last_fault = None

    # Both domains are written from THIS engine's run() thread only -- the read
    # loop, its except arm, and _push_snapshot are all on it -- so the pair needs
    # no lock. set_streaming() is the only method another thread calls, and it
    # touches neither slot.

    def _report_camera(self, fault: Fault) -> None:
        """Is there a picture? CAMERA_DOWN, or NONE once frames return."""
        self._camera_fault = fault
        self._publish()

    def _report_uplink(self, fault: Fault) -> None:
        """Do snapshots reach the server? NO_SERVER / BAD_KEY, or NONE."""
        self._uplink_fault = fault
        self._publish()

    def _publish(self) -> None:
        """Send the hub the worse of the two domains, deduped.

        The hub holds ONE fault per source and this engine is one source, so a
        merge is required rather than optional: reporting the camera domain while
        an uplink fault is live would DOWNGRADE what the guard is told -- sending
        them to check a USB cable when the server is unreachable -- and the
        uplink verdict would be lost entirely.
        """
        fault = _worse(self._camera_fault, self._uplink_fault)
        if fault is self._last_fault:
            return
        self._last_fault = fault
        if self._on_fault is not None:
            self._on_fault(fault)

    def set_paused(self, enabled: bool) -> None:
        """Local, tray-driven pause. When paused the run loop keeps the camera
        warm and motion state fresh but uploads nothing (no snapshot, no encoder
        write, no HLS upload). An active encoder / httpx client stay intact so
        resume returns to normal push without re-initialising anything."""
        if enabled:
            self._paused.set()
        else:
            self._paused.clear()

    def set_streaming(self, enabled: bool) -> None:
        with self._stream_lock:
            if enabled and self._stop_event.is_set():
                # A stream_start landing after this engine has been told to stop.
                # run()'s teardown has already passed here (or is about to), so an
                # encoder started now has no owner left to stop it: the ffmpeg
                # child outlives the engine and keeps running on the guard's PC
                # until the machine is rebooted. Refusing means the guard's live
                # view simply does not open for a camera that is shutting down --
                # which is the truthful outcome.
                logger.info("Ignoring stream_start for %s: the engine is stopping",
                            self._node_id)
                return
            if enabled == self._streaming:
                return
            self._streaming = enabled
            if enabled:
                self._start_encoder()
            else:
                self._stop_encoder()

    def _start_encoder(self) -> None:
        self._encoder = HlsEncoder(
            width=self._resolution[0], height=self._resolution[1], fps=self._target_fps
        )
        if not self._encoder.start():
            self._encoder = None
            self._streaming = False

    def _stop_encoder(self) -> None:
        if self._encoder:
            self._encoder.stop()
            self._encoder = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        self._client = httpx.Client(
            timeout=httpx.Timeout(5.0, connect=3.0),
            headers={"X-API-Key": self._api_key},
        )
        cap = open_camera(self._cam_config.get("device_index", 0), *self._resolution)
        if cap is None:
            logger.error(f"Cannot open camera {self._cam_config.get('device_index')}")
            self._report_camera(Fault.CAMERA_DOWN)
            # Close the client on the way out. This early return sits BETWEEN
            # the client's creation and the try/finally below, so it used to
            # leak the connection pool every time. Dormant until this phase --
            # then 重新連線 made it guard-repeatable, and the text at
            # strings.py's camera_down invites exactly that: a guard whose
            # camera will not open is TOLD to re-seat the cable and press it,
            # twice per press (start_engines retries), for as long as it takes.
            self._client.close()
            self._client = None
            return

        prev_frame = None
        last_snapshot_time = 0.0
        last_encode_time = 0.0
        last_hls_upload = 0.0
        bad_reads = 0

        try:
            while not self._stop_event.is_set():
                ret, frame = cap.read()
                if not ret:
                    # A camera that stopped delivering frames is the ONLY signal
                    # we get for a pulled USB cable: _push_snapshot is never
                    # reached from here, so without this the camera domain's last
                    # value (Fault.NONE) would stand forever. _publish dedups,
                    # so re-asserting it every iteration is a pointer compare.
                    bad_reads += 1
                    if bad_reads == BAD_READ_LIMIT:
                        logger.error(
                            "Camera %s delivered no frame for %.1fs",
                            self._node_id, BAD_READ_LIMIT * BAD_READ_SLEEP)
                    if bad_reads >= BAD_READ_LIMIT:
                        self._report_camera(Fault.CAMERA_DOWN)
                    time.sleep(BAD_READ_SLEEP)
                    continue

                if bad_reads >= BAD_READ_LIMIT:
                    # Cable plugged back in: clear the CAMERA domain only. There
                    # IS a picture again; whether it reaches the server is not
                    # this arm's business, and claiming otherwise is what toasted
                    # the guard 監控中 while the uplink was still down.
                    logger.info("Camera %s is delivering frames again", self._node_id)
                    self._report_camera(Fault.NONE)
                bad_reads = 0

                motion = compute_motion(frame, prev_frame, self._motion_threshold)
                prev_frame = frame
                now = time.time()

                # Paused (tray "暫停推送"): keep reading frames so prev_frame /
                # motion state stay current — resume must not see a stale delta —
                # but UPLOAD NOTHING. Skip snapshot, encoder write and HLS upload.
                # The encoder and httpx client are deliberately left untouched.
                if self._paused.is_set():
                    time.sleep(0.1)
                    continue

                # 1) The tile's lifeline: push a ~1Hz snapshot EVERY iteration,
                #    REGARDLESS of live-view streaming. This used to live in an
                #    `else` branch, so opening live view stopped snapshots and the
                #    dashboard tile went grey once it passed the server staleness
                #    threshold. Motion-gated so a static scene does not hammer the
                #    uplink (a fresh frame still goes out at least every ~2s).
                static_recent = motion < 0.01 and now - last_snapshot_time < 2.0
                if not static_recent and now - last_snapshot_time >= 1.0:
                    self._push_snapshot(frame)
                    last_snapshot_time = now

                # 2) When a viewer is watching, ALSO feed the encoder — via the
                #    now non-blocking write_frame (it drops frames rather than
                #    blocking on a slow ffmpeg), so live view can never stall the
                #    snapshot heartbeat above. Its own cadence (last_encode_time)
                #    is kept separate from the snapshot cadence.
                with self._stream_lock:
                    streaming = self._streaming
                    encoder = self._encoder
                if streaming and encoder:
                    fps = adaptive_fps(motion, self._target_fps)
                    interval = 1.0 / fps
                    if now - last_encode_time >= interval:
                        encoder.write_frame(cv2.resize(frame, self._resolution).tobytes())
                        last_encode_time = now
                        if now - last_hls_upload >= 2.0:
                            self._upload_segments()
                            last_hls_upload = now

                # Idle throttle: ease CPU on a static scene with no live viewer
                # (the old snapshot path slept 0.05 here). While streaming, keep
                # the loop tight so the encoder still gets frames at target fps.
                time.sleep(0.05 if (static_recent and not streaming) else 0.01)
        except Exception:
            # Without this the exception leaves run() and threading.excepthook
            # writes it to sys.stderr -- which is None in the console=False
            # onefile build. A dead engine thread left ZERO evidence and its
            # last report (usually Fault.NONE) stood forever.
            #
            # CAMERA_DOWN is not literally true of a crashed worker, but it is
            # true of what the operator can observe and act on: this camera has
            # no picture, and strings.py's action for it -- check the USB cable,
            # then press 重新連線, then call the administrator -- is exactly the
            # right instruction, since 重新連線 is what rebuilds this dead thread.
            # NO_SERVER would send the guard to a network that is fine.
            #
            # It belongs to the CAMERA domain for the same reason: what the guard
            # can observe is "this camera has no picture". A live uplink fault is
            # not cleared by this -- _publish keeps the worse of the two, so a
            # crash during a server outage still reads 無法連線到伺服器.
            logger.exception("Push engine for %s stopped unexpectedly", self._node_id)
            self._report_camera(Fault.CAMERA_DOWN)
        finally:
            cap.release()
            # Under _stream_lock: set_streaming() runs on the ControlChannel
            # thread (app_controller._on_command dispatches stream_start /
            # stream_stop from there) and mutates these same two attributes under
            # this lock. Unguarded, a stream_start landing during shutdown could
            # interleave with this teardown -- freeing an encoder mid-construction,
            # or installing one AFTER this line, which then has no owner left to
            # stop it and leaves an orphaned ffmpeg running on the guard's PC
            # until the machine is rebooted. _streaming is cleared too so the
            # engine's state matches the fact that nothing is being encoded.
            with self._stream_lock:
                self._streaming = False
                self._stop_encoder()
            if self._client:
                self._client.close()

    def _push_snapshot(self, frame) -> None:
        try:
            small = cv2.resize(frame, self._resolution)
            _, jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
            # Webcam ingest route (spec §303). NOT /api/edge/... — that path is gated
            # by the global EDGE_API_KEY and would 401 the per-client webcam key.
            url = f"{self._server_url}/api/webcam/{self._node_id}/snapshot"
            resp = self._client.post(url, content=jpeg.tobytes(),
                                     headers={"Content-Type": "image/jpeg"})
            # httpx does NOT raise on 4xx without this. Spec §322: a silent 401 leaves
            # the tray green and the dashboard tile permanently blank — surface it.
            resp.raise_for_status()
        except Exception as e:
            # The technician still gets the real status code, in the log file.
            # The OPERATOR only ever sees the plain-language string this Fault
            # maps to -- see strings.py.
            self._report_uplink(_classify(e))
            logger.warning(f"Snapshot push to {self._node_id} failed: {e}")
        else:
            # Clears the UPLINK domain only: a successful push proves the server
            # is reachable and the key is accepted, and says nothing about
            # whether this camera still has a picture.
            self._report_uplink(Fault.NONE)

    def _upload_segments(self) -> None:
        if not self._encoder:
            return
        try:
            segments = self._encoder.get_new_segments()
            for filename, data in segments:
                url = f"{self._server_url}/api/webcam/{self._node_id}/hls/{filename}"
                resp = self._client.put(url, content=data)
                resp.raise_for_status()
        except Exception as e:
            logger.warning(f"HLS upload for {self._node_id} failed: {e}")
