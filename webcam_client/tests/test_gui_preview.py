# sdprs/webcam_client/tests/test_gui_preview.py
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.gui.preview import resize_keep_aspect


def test_resize_fits_within_bounds_and_keeps_aspect():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)  # 4:3 landscape
    out = resize_keep_aspect(frame, (160, 120))
    h, w = out.shape[:2]
    assert w <= 160 and h <= 120
    assert abs((w / h) - (640 / 480)) < 0.05  # aspect preserved


def test_resize_never_upscales():
    frame = np.zeros((60, 80, 3), dtype=np.uint8)  # already smaller
    out = resize_keep_aspect(frame, (160, 120))
    assert out.shape[:2] == (60, 80)


def test_resize_tall_frame_bounded():
    frame = np.zeros((640, 480, 3), dtype=np.uint8)  # portrait 3:4
    out = resize_keep_aspect(frame, (160, 120))
    h, w = out.shape[:2]
    assert w <= 160 and h <= 120
