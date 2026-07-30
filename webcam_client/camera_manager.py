# sdprs/webcam_client/camera_manager.py
import logging
import os
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("webcam_client.camera")


def scan_cameras(
    max_index: int = 10,
    stop_after_misses: int = 3,
    capture_factory=None,
) -> List[dict]:
    """Probe device indices 0..max_index-1 and return the cameras that answered.

    Each hit is {"device_index", "width", "height", "frame"}.

    Stops early after `stop_after_misses` CONSECUTIVE failures to open a device.
    On Windows every miss still pays a full DSHOW negotiation (~0.5-2 s), so an
    unconditional 0..9 sweep freezes the setup window for 10-20 s and a guard
    reads that as "broken". Device indices are not guaranteed contiguous though
    -- a USB hub can legitimately put cameras at 0 and 4 -- so the threshold is a
    parameter, not a constant: the 重新掃描 button passes
    `stop_after_misses=max_index` to force a full sweep, and that button is the
    escape hatch for the gap case. `max_index` remains the hard ceiling.

    `frame` is the frame this probe already grabbed, so the thumbnail loader
    never has to reopen the device -- one DSHOW negotiation per camera instead of
    two. It is None when the device opened but handed over no frame; that still
    counts as a HIT, not a miss, because the device plainly exists.

    `capture_factory` takes a device index and returns a capture object; when
    None the real `cv2.VideoCapture` is used. It exists so tests never have to
    touch real hardware.
    """
    if capture_factory is None:
        backend = cv2.CAP_DSHOW if os.name == "nt" else 0

        def _open(index):
            return cv2.VideoCapture(index, backend)
    else:
        _open = capture_factory

    found: List[dict] = []
    consecutive_misses = 0
    for i in range(max_index):
        # One bad device must not cost the guard the good ones. A flaky virtual
        # camera driver raising mid-sweep used to unwind the whole call, and the
        # wizard's `except Exception -> cams = []` turned that into 找不到攝影機
        # while two working cameras sat plugged in. Per-device now: log it,
        # count it as a miss, keep going.
        try:
            cap = _open(i)
        except Exception as e:
            logger.warning(f"camera scan: device {i} could not be opened: {e}")
            consecutive_misses += 1
            if consecutive_misses >= stop_after_misses:
                break
            continue
        try:
            if not cap.isOpened():
                consecutive_misses += 1
                if consecutive_misses >= stop_after_misses:
                    logger.debug(
                        f"camera scan stopped at index {i} after "
                        f"{consecutive_misses} consecutive misses"
                    )
                    break
                continue
            consecutive_misses = 0
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            ok, frame = cap.read()
            found.append(
                {
                    "device_index": i,
                    "width": w,
                    "height": h,
                    "frame": frame if ok else None,
                }
            )
        except Exception as e:
            logger.warning(f"camera scan: device {i} failed mid-probe: {e}")
            consecutive_misses += 1
            if consecutive_misses >= stop_after_misses:
                break
        finally:
            cap.release()
    return found


def compute_motion(frame: np.ndarray, prev_frame: Optional[np.ndarray], threshold: int = 25) -> float:
    if prev_frame is None:
        return 1.0
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (21, 21), 0)
    prev_gray = cv2.GaussianBlur(prev_gray, (21, 21), 0)
    diff = cv2.absdiff(gray, prev_gray)
    motion_ratio = float((diff > threshold).sum()) / diff.size
    return motion_ratio


def adaptive_fps(motion_ratio: float, target_fps: int = 8) -> int:
    if motion_ratio < 0.01:
        return 1
    elif motion_ratio < 0.05:
        return 3
    else:
        return target_fps


def open_camera(device_index: int, width: int = 640, height: int = 480) -> Optional[cv2.VideoCapture]:
    backend = cv2.CAP_DSHOW if os.name == "nt" else 0
    cap = cv2.VideoCapture(device_index, backend)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap
