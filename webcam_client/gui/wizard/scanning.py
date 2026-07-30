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
from ..preview import make_thumbnail, grab_preview_frame

logger = logging.getLogger("webcam_client.gui.wizard")


def _scan_cameras_async(on_done, max_index: int = 10) -> None:
    """Run the slow, blocking DSHOW probe on a worker thread so the settings
    window paints immediately and never freezes on 掃描中…. on_done(cams) is
    invoked from the worker thread; a Tk caller marshals back with root.after."""
    def worker():
        try:
            cams = scan_cameras(max_index)
        except Exception as e:
            logger.warning(f"camera scan failed: {e}")
            cams = []
        on_done(cams)
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
