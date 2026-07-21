# sdprs/webcam_client/push_engine.py
import logging
import threading
import time
from typing import Optional

import cv2
import httpx

from .camera_manager import open_camera, compute_motion, adaptive_fps
from .hls_encoder import HlsEncoder

logger = logging.getLogger("webcam_client.push_engine")


class PushEngine(threading.Thread):
    def __init__(self, camera_config: dict, server_url: str, api_key: str):
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
        self._streaming = False
        self._stream_lock = threading.Lock()
        self._encoder: Optional[HlsEncoder] = None
        self._client: Optional[httpx.Client] = None

    def set_streaming(self, enabled: bool) -> None:
        with self._stream_lock:
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
            return

        prev_frame = None
        last_snapshot_time = 0.0
        last_hls_upload = 0.0

        try:
            while not self._stop_event.is_set():
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue

                motion = compute_motion(frame, prev_frame, self._motion_threshold)
                prev_frame = frame
                now = time.time()

                with self._stream_lock:
                    streaming = self._streaming

                if streaming and self._encoder:
                    fps = adaptive_fps(motion, self._target_fps)
                    interval = 1.0 / fps
                    if now - last_snapshot_time >= interval:
                        self._encoder.write_frame(frame.tobytes())
                        last_snapshot_time = now
                        if now - last_hls_upload >= 2.0:
                            self._upload_segments()
                            last_hls_upload = now
                else:
                    if motion < 0.01 and now - last_snapshot_time < 2.0:
                        time.sleep(0.05)
                        continue
                    if now - last_snapshot_time >= 1.0:
                        self._push_snapshot(frame)
                        last_snapshot_time = now

                time.sleep(0.01)
        finally:
            cap.release()
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
            logger.warning(f"Snapshot push to {self._node_id} failed: {e}")

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
