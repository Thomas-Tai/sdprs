# sdprs/webcam_client/tests/test_camera_manager.py
import cv2
import numpy as np
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.camera_manager import compute_motion, adaptive_fps, scan_cameras


class _FakeCapture:
    """Stand-in for cv2.VideoCapture, injected via scan_cameras(capture_factory=...).

    No test may touch real hardware: a DSHOW probe of a missing index costs
    0.5-2 s, which is exactly the defect these tests pin down.
    """

    def __init__(self, ok=True, read_ok=True, width=640, height=480, raise_on_read=False):
        self._ok = ok
        self._read_ok = read_ok
        self._width = width
        self._height = height
        self._raise_on_read = raise_on_read
        self.release_calls = 0
        self.last_frame = None

    def isOpened(self):
        return self._ok

    def get(self, prop):
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._width)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._height)
        return 0.0

    def read(self):
        if self._raise_on_read:
            raise RuntimeError("driver blew up")
        if not self._read_ok:
            return False, None
        self.last_frame = np.zeros((self._height, self._width, 3), dtype=np.uint8)
        return True, self.last_frame

    def release(self):
        self.release_calls += 1


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


# --- scan_cameras: early stop (U1) + one open per camera (U2) -----------------


def test_scan_stops_after_three_consecutive_misses():
    opened = []

    def factory(index):
        opened.append(index)
        return _FakeCapture(ok=index in (0, 1))

    cams = scan_cameras(max_index=10, stop_after_misses=3, capture_factory=factory)
    assert [c["device_index"] for c in cams] == [0, 1]
    # 0,1 hit; 2,3,4 miss -> stop. Index 5 must never be opened.
    assert opened == [0, 1, 2, 3, 4], opened


def test_full_sweep_finds_a_camera_past_the_gap():
    def factory(index):
        return _FakeCapture(ok=index in (0, 4))

    cams = scan_cameras(max_index=10, stop_after_misses=10, capture_factory=factory)
    assert [c["device_index"] for c in cams] == [0, 4]


def test_scan_respects_max_index_as_the_hard_ceiling():
    opened = []

    def factory(index):
        opened.append(index)
        return _FakeCapture(ok=True)

    cams = scan_cameras(max_index=3, stop_after_misses=10, capture_factory=factory)
    assert opened == [0, 1, 2]
    assert [c["device_index"] for c in cams] == [0, 1, 2]


def test_scan_returns_the_frame_it_already_grabbed():
    cams = scan_cameras(max_index=1, capture_factory=lambda i: _FakeCapture(ok=True))
    assert cams[0]["frame"] is not None, "the thumbnail must not need a second open"
    assert cams[0]["width"] == 640
    assert cams[0]["height"] == 480


def test_scan_returns_the_exact_frame_object_the_capture_handed_over():
    cap = _FakeCapture(ok=True)
    cams = scan_cameras(max_index=1, capture_factory=lambda i: cap)
    assert cams[0]["frame"] is cap.last_frame


def test_device_that_opens_but_cannot_read_counts_as_a_hit():
    # All five open; none yields a frame. If a failed read were treated as a
    # miss, the scan would stop after index 2 and return nothing.
    def factory(index):
        return _FakeCapture(ok=True, read_ok=False)

    cams = scan_cameras(max_index=5, stop_after_misses=3, capture_factory=factory)
    assert [c["device_index"] for c in cams] == [0, 1, 2, 3, 4]
    assert all(c["frame"] is None for c in cams), cams


def test_scan_releases_every_capture_it_opened_including_the_early_break():
    caps = []

    def factory(index):
        cap = _FakeCapture(ok=index == 0)
        caps.append(cap)
        return cap

    scan_cameras(max_index=10, stop_after_misses=3, capture_factory=factory)
    assert len(caps) == 4, [c for c in caps]  # 0 hit; 1,2,3 miss -> stop
    assert all(c.release_calls == 1 for c in caps), [c.release_calls for c in caps]


def test_scan_releases_the_capture_when_the_device_raises():
    cap = _FakeCapture(ok=True, raise_on_read=True)
    with pytest.raises(RuntimeError):
        scan_cameras(max_index=1, capture_factory=lambda i: cap)
    assert cap.release_calls == 1
