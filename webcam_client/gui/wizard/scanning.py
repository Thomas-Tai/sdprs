# sdprs/webcam_client/gui/wizard/scanning.py
"""Off-the-Tk-thread camera discovery and thumbnail grabbing.

Moved verbatim out of ``gui/setup_wizard.py``. Both helpers hand their result
to a callback that runs on the WORKER thread; a Tk caller marshals back with
``root.after``.

Note for tests: ``scan_cameras``/``grab_preview_frame``/``make_thumbnail`` are
resolved as globals of THIS module, so a test faking them must patch
``webcam_client.gui.wizard.scanning``, not the ``setup_wizard`` façade.
"""
import logging
import threading

from ...camera_manager import scan_cameras
from ..preview import make_thumbnail, grab_preview_frame, prepare_thumbnail

logger = logging.getLogger("webcam_client.gui.wizard")


def _scan_cameras_async(on_done, max_index: int = 10,
                        stop_after_misses: int = 3) -> None:
    """Run the slow, blocking DSHOW probe on a worker thread so the settings
    window paints immediately and never freezes on 掃描中…. on_done(cams) is
    invoked from the worker thread; a Tk caller marshals back with root.after.

    ``stop_after_misses`` is passed straight through: the first-run scan takes
    the default early stop (fast), and 重新掃描 passes ``max_index`` to force a
    full sweep for the case where a USB hub leaves a gap in the device indices.

    The blanket ``except`` below is now a backstop, not the main defence:
    ``scan_cameras`` handles a bad device per-index and keeps the good ones, so
    reaching here means the sweep itself failed rather than one camera.
    """
    def worker():
        try:
            cams = scan_cameras(max_index, stop_after_misses=stop_after_misses)
        except Exception as e:
            logger.warning(f"camera scan failed: {e}")
            cams = []
        on_done(cams)
    threading.Thread(target=worker, daemon=True).start()


def _prepare_thumbnail_async(device_index, frame, on_ready) -> None:
    """Build a thumbnail off the Tk thread, reusing the frame the scan already
    grabbed when there is one.

    ``on_ready(pil_image_or_None)`` runs on the WORKER thread and receives a
    plain PIL Image -- NOT a Tk PhotoImage. The Tk caller marshals back with
    root.after and finishes with ``preview.to_photo_image``. That split is what
    keeps this worker off Tk entirely.

    ``frame`` is the ndarray from ``scan_cameras``; when it is None (edit mode
    prefills from the saved config and has no frames) the device is opened once
    to grab one. Passing the scanned frame is what makes the second DSHOW
    negotiation per camera disappear -- the entire point of U2.
    """
    def worker():
        try:
            src = frame if frame is not None else grab_preview_frame(device_index)
            image = prepare_thumbnail(src)
        except Exception as e:
            logger.warning(f"thumbnail for device {device_index} failed: {e}")
            image = None
        on_ready(image)
    threading.Thread(target=worker, daemon=True).start()


def _load_thumbnail_async(device_index, on_ready) -> None:
    """Grab a preview frame + build its thumbnail OFF the Tk thread so the
    settings window paints immediately. on_ready(thumb_or_None) runs on the
    worker thread; a Tk caller marshals back with root.after."""
    def worker():
        try:
            thumb = make_thumbnail(grab_preview_frame(device_index))
        except Exception as e:
            logger.warning(f"thumbnail grab for device {device_index} failed: {e}")
            thumb = None
        on_ready(thumb)
    threading.Thread(target=worker, daemon=True).start()
