"""
Camera abstraction that survives the Pi 5 libcamera transition.

The old edge code used ``cv2.VideoCapture`` directly. On a Raspberry Pi 5 the
CSI camera hangs off the rp1-cfe driver stack which V4L2 alone cannot fully
drive — ``cv2.VideoCapture(0)`` opens ``/dev/video0`` but fails to start
streaming (``Failed to start streaming: Remote I/O error``). The proper
Pi 5 API is picamera2 (a Python binding on top of libcamera).

To avoid making the rest of the code Pi-5-aware, this module returns objects
that quack like ``cv2.VideoCapture`` — same ``read()`` / ``set()`` / ``get()``
/ ``release()`` / ``isOpened()`` surface — while routing to whichever backend
actually works on the host:

* Raspberry Pi 5 → picamera2 (system dist-packages, added to ``sys.path``
  on first use so the isolated venv doesn't need its own picamera2 install)
* Everything else (USB webcam, Pi 4, dev laptop) → ``cv2.VideoCapture``
"""

import logging
import sys
from typing import Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# picamera2 lives in the OS dist-packages (installed via ``apt install
# python3-picamera2``). It cannot be pip-installed into a venv because it
# depends on ``libcamera`` Python bindings that ship as compiled modules
# alongside the system Python. We add the dist-packages dir to sys.path on
# demand so the venv stays otherwise isolated.
_SYSTEM_SITE = "/usr/lib/python3/dist-packages"


def _is_pi5() -> bool:
    """True iff /proc/device-tree/model reports Raspberry Pi 5."""
    try:
        with open("/proc/device-tree/model", "rb") as f:
            model = f.read().decode("utf-8", errors="ignore").strip("\x00").strip()
        return "Raspberry Pi 5" in model
    except OSError:
        return False


class _Picamera2Capture:
    """cv2.VideoCapture-compatible facade over picamera2."""

    def __init__(self, source: int, width: int, height: int, fps: int):
        if _SYSTEM_SITE not in sys.path:
            sys.path.insert(0, _SYSTEM_SITE)
        from picamera2 import Picamera2  # noqa: E402  (delayed import by design)

        self._picam = Picamera2(source)
        config = self._picam.create_video_configuration(
            main={"size": (width, height), "format": "RGB888"},
            controls={"FrameRate": float(fps)},
        )
        self._picam.configure(config)
        self._picam.start()
        self._opened = True
        self._width = width
        self._height = height
        self._fps = fps
        logger.info(
            f"Camera: picamera2 backend open (cam{source} {width}x{height}@{fps}fps)"
        )

    def isOpened(self) -> bool:  # noqa: N802 — cv2 API name
        return self._opened

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self._opened:
            return False, None
        try:
            frame_rgb = self._picam.capture_array("main")
            # Downstream OpenCV code assumes BGR; picamera2 gives RGB.
            return True, frame_rgb[..., ::-1]
        except Exception as e:  # noqa: BLE001 — picamera2 raises many types
            logger.error(f"picamera2 capture_array failed: {e}")
            return False, None

    def set(self, prop: int, value: float) -> bool:
        # picamera2 fixes format at configure() time; runtime prop sets are
        # a no-op. Return True so downstream code doesn't treat this as an
        # error (matches cv2 behaviour where set() often returns True on
        # unsupported props too).
        return True

    def get(self, prop: int) -> float:
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._width)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._height)
        if prop == cv2.CAP_PROP_FPS:
            return float(self._fps)
        return 0.0

    def release(self) -> None:
        if not self._opened:
            return
        try:
            self._picam.stop()
            self._picam.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"picamera2 release error: {e}")
        self._opened = False


def open_camera(cam_config: dict, prefer: str = "auto"):
    """
    Open a camera and return a cv2.VideoCapture-compatible object.

    Args:
        cam_config: keys ``source``, ``resolution`` [w,h], ``fps``.
        prefer:
            ``"auto"`` (default) — picamera2 on Pi 5, else cv2.
            ``"cv2"`` — force cv2 (useful for USB webcams on Pi 5).
            ``"picamera2"`` — force picamera2 (raises on non-Pi 5).

    Returns:
        Object with ``read()``, ``set()``, ``get()``, ``release()``,
        ``isOpened()`` — either ``cv2.VideoCapture`` or ``_Picamera2Capture``.
    """
    source = cam_config["source"]
    width, height = cam_config["resolution"]
    fps = int(cam_config["fps"])

    use_picamera2 = prefer == "picamera2" or (prefer == "auto" and _is_pi5())

    if use_picamera2:
        try:
            return _Picamera2Capture(int(source), width, height, fps)
        except Exception as e:  # noqa: BLE001
            if prefer == "picamera2":
                raise
            logger.warning(
                f"picamera2 backend failed ({e}); falling back to cv2.VideoCapture"
            )

    cap = cv2.VideoCapture(source)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    logger.info(f"Camera: cv2.VideoCapture backend open (source={source})")
    return cap
