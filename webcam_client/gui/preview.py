# sdprs/webcam_client/gui/preview.py
import logging
import os
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("webcam_client.gui.preview")


def resize_keep_aspect(frame: np.ndarray, max_size=(160, 120)) -> np.ndarray:
    """Resize a frame to fit within max_size (w, h), preserving aspect ratio and
    never upscaling. Pure + headless — the unit-tested core of the preview."""
    h, w = frame.shape[:2]
    max_w, max_h = max_size
    scale = min(max_w / w, max_h / h, 1.0)
    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    return cv2.resize(frame, (new_w, new_h))


def make_thumbnail(frame: Optional[np.ndarray], max_size=(160, 120)):
    """Downscale a BGR frame to a Tk PhotoImage thumbnail. Returns None if the
    frame is None or Tk/Pillow is unavailable — the wizard then simply omits the
    preview and never blocks setup on a bad device."""
    if frame is None:
        return None
    resized = resize_keep_aspect(frame, max_size)
    try:
        from PIL import Image, ImageTk
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        return ImageTk.PhotoImage(Image.fromarray(rgb))
    except Exception as e:
        logger.debug(f"thumbnail render skipped: {e}")
        return None


# --- the two-step thumbnail, split across the thread boundary ----------------
# make_thumbnail above builds an ImageTk.PhotoImage, which is a Tk object, and
# the wizard called it from a worker thread -- against this codebase's standing
# rule that no worker may touch Tk. It happened to survive because the wizard
# is the only Tk in the process and the object is handed straight back, but it
# is a real latent crash and the rule exists for a reason.
#
# So the work splits at the thread boundary: prepare_thumbnail() does the
# expensive part (resize + colour convert) on the worker and returns a plain
# PIL Image, which touches no Tk; to_photo_image() does the one Tk call, on the
# Tk thread. make_thumbnail is kept unchanged for callers that are already on
# the Tk thread.

def prepare_thumbnail(frame: Optional[np.ndarray], max_size=(160, 120)):
    """Worker-safe half: resize + BGR->RGB, returning a PIL Image or None.

    Touches no Tk, so it is safe on any thread. Returns None -- rather than
    raising -- for a missing frame or a missing Pillow, because a preview is a
    nicety and setup must never block on a bad device.
    """
    if frame is None:
        return None
    try:
        from PIL import Image
        rgb = cv2.cvtColor(resize_keep_aspect(frame, max_size), cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)
    except Exception as e:
        logger.debug(f"thumbnail prepare skipped: {e}")
        return None


def to_photo_image(image):
    """Tk-thread half: wrap a prepared PIL Image as a PhotoImage, or None.

    MUST be called on the Tk thread. The caller keeps a reference to the result
    for as long as the widget lives, or Tk garbage-collects the image and the
    label renders blank.
    """
    if image is None:
        return None
    try:
        from PIL import ImageTk
        return ImageTk.PhotoImage(image)
    except Exception as e:
        logger.debug(f"thumbnail render skipped: {e}")
        return None


def grab_preview_frame(device_index: int) -> Optional[np.ndarray]:
    """Open the camera, grab ONE frame, release. Returns None on any failure.
    Requires hardware — not exercised by unit tests."""
    backend = cv2.CAP_DSHOW if os.name == "nt" else 0
    cap = cv2.VideoCapture(device_index, backend)
    try:
        if not cap.isOpened():
            return None
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()
