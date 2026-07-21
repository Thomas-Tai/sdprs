# sdprs/webcam_client/tests/test_camera_manager.py
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.camera_manager import compute_motion, adaptive_fps


def test_compute_motion_no_prev():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert compute_motion(frame, None) == 1.0


def test_compute_motion_identical():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    ratio = compute_motion(frame, frame.copy())
    assert ratio < 0.01


def test_compute_motion_different():
    frame1 = np.zeros((480, 640, 3), dtype=np.uint8)
    frame2 = np.ones((480, 640, 3), dtype=np.uint8) * 255
    ratio = compute_motion(frame2, frame1)
    assert ratio > 0.5


def test_adaptive_fps():
    assert adaptive_fps(0.005) == 1
    assert adaptive_fps(0.03) == 3
    assert adaptive_fps(0.1, target_fps=10) == 10
