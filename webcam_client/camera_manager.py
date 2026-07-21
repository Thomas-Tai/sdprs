# sdprs/webcam_client/camera_manager.py
import logging
import os
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("webcam_client.camera")


def scan_cameras(max_index: int = 10) -> List[dict]:
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW if os.name == "nt" else 0)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            found.append({"device_index": i, "width": w, "height": h})
            cap.release()
        else:
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
