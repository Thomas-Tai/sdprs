# sdprs/webcam_client/tests/test_wizard_scanning.py
"""Camera discovery and thumbnail preparation, off the Tk thread.

Both helpers here exist for the same reason: opening a DSHOW device costs
0.5-2 s, and doing that on the Tk thread is what made the setup window freeze
on 掃描中… with Windows painting "not responding" over it. The "runs off the
calling thread" assertions are the only thing keeping them there -- a refactor
that inlined either call back onto the caller would pass every other assertion
in this file.

The second thing this file defends is U2. `scan_cameras` hands back the frame it
already grabbed precisely so the thumbnail does not need a second open of the
same device. That saving is invisible from the outside: a thumbnail built by
reopening the camera looks identical to one built from the scanned frame. Only
`grab_preview_frame.assert_not_called()` can tell them apart, which is why it
is here -- without it, U2 silently regresses into a pure cost, since the scan
now pays a `cap.read()` it never used to.

Migrated from test_setup_wizard.py when the transitional façade was deleted.
No test here touches real hardware.
"""
import sys
import threading as _t
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.gui.wizard import scanning as sc


def test_scan_cameras_async_runs_off_the_calling_thread(monkeypatch):
    seen = {}

    # **kwargs, not a fixed signature: this fake once declared
    # `fake_scan(max_index=10)` while the real scan_cameras had grown
    # stop_after_misses, so the call raised TypeError -- which the worker's
    # blanket `except Exception -> cams = []` turned into a silent "no cameras
    # found". A fake narrower than the real thing does not fail as a mismatch,
    # it fails as the product's own worst behaviour.
    def fake_scan(max_index=10, **kwargs):
        seen["thread"] = _t.current_thread()
        seen["kwargs"] = kwargs
        return [{"device_index": 0, "width": 640, "height": 480, "frame": None}]

    monkeypatch.setattr(sc, "scan_cameras", fake_scan)
    done = _t.Event()
    result = {}

    def on_done(cams):
        result["cams"] = cams
        done.set()

    sc._scan_cameras_async(on_done)
    assert done.wait(5), "on_done was never called"
    assert seen["thread"] is not _t.current_thread(), "scan must not run on the caller thread"
    assert result["cams"][0]["device_index"] == 0


def test_rescan_forces_a_full_sweep(monkeypatch):
    """重新掃描 is the documented escape hatch for a camera sitting past a gap in
    the device indices -- a USB hub can legitimately put cameras at 0 and 4, and
    the fast early-stop scan would never reach the second one. If the button
    does not raise the miss threshold it is just a slower duplicate of the
    automatic scan, and the guard has no way to find that camera at all."""
    seen = {}

    def fake_scan(max_index=10, **kwargs):
        seen["max_index"] = max_index
        seen["stop_after_misses"] = kwargs.get("stop_after_misses")
        return []

    monkeypatch.setattr(sc, "scan_cameras", fake_scan)
    done = _t.Event()
    sc._scan_cameras_async(lambda cams: done.set(), max_index=10,
                           stop_after_misses=10)
    assert done.wait(5)
    assert seen["stop_after_misses"] == seen["max_index"] == 10


def test_a_failed_sweep_reports_no_cameras_rather_than_killing_the_worker(monkeypatch):
    """The backstop still has to hold: on_done must fire even when the sweep
    itself blows up, or the window sits on 掃描中… forever."""
    def boom(max_index=10, **kwargs):
        raise RuntimeError("the whole sweep failed")

    monkeypatch.setattr(sc, "scan_cameras", boom)
    done = _t.Event()
    result = {}
    sc._scan_cameras_async(lambda cams: (result.setdefault("cams", cams), done.set()))
    assert done.wait(5), "on_done was never called"
    assert result["cams"] == []


def test_prepare_thumbnail_async_runs_off_the_calling_thread(monkeypatch):
    seen = {}

    def fake_prepare(frame, *a, **kw):
        seen["thread"] = _t.current_thread()
        seen["frame"] = frame
        return "IMAGE"

    monkeypatch.setattr(sc, "prepare_thumbnail", fake_prepare)
    done = _t.Event()
    result = {}

    def on_ready(image):
        result["image"] = image
        done.set()

    sc._prepare_thumbnail_async(3, "SCANNED_FRAME", on_ready)
    assert done.wait(5), "on_ready was never called"
    assert seen["thread"] is not _t.current_thread(), \
        "thumbnail prep must not run on the caller thread"
    assert result["image"] == "IMAGE"


def test_a_scanned_frame_is_reused_instead_of_reopening_the_camera(monkeypatch):
    """THE U2 assertion. Opening the device twice per camera -- once to scan,
    once for the preview -- is two DSHOW negotiations for information the scan
    already had in hand. Since the scan now pays a cap.read() it never used to,
    skipping this second open is not an optimisation on top of U2, it is the
    whole of it: without it the change is a net LOSS."""
    grab = patch.object(sc, "grab_preview_frame")
    monkeypatch.setattr(sc, "prepare_thumbnail", lambda frame, *a, **kw: frame)
    done = _t.Event()
    result = {}
    with grab as fake_grab:
        sc._prepare_thumbnail_async(0, "SCANNED_FRAME",
                                    lambda img: (result.setdefault("img", img), done.set()))
        assert done.wait(5)
    fake_grab.assert_not_called()
    assert result["img"] == "SCANNED_FRAME"


def test_the_device_is_opened_when_there_is_no_scanned_frame(monkeypatch):
    """Edit mode prefills its rows from the saved config, which carries no
    frames -- so the fallback must still work, or the guard editing settings
    sees no previews at all."""
    monkeypatch.setattr(sc, "grab_preview_frame", lambda idx: f"GRABBED_{idx}")
    monkeypatch.setattr(sc, "prepare_thumbnail", lambda frame, *a, **kw: frame)
    done = _t.Event()
    result = {}
    sc._prepare_thumbnail_async(4, None,
                                lambda img: (result.setdefault("img", img), done.set()))
    assert done.wait(5)
    assert result["img"] == "GRABBED_4"


def test_a_failing_thumbnail_reports_none_rather_than_killing_the_worker(monkeypatch):
    """A busy or broken device must cost the guard a preview, never the row --
    and on_ready must still fire, or the row waits for a callback that is
    never coming."""
    def boom(idx):
        raise RuntimeError("device is being used by another application")

    monkeypatch.setattr(sc, "grab_preview_frame", boom)
    done = _t.Event()
    result = {}
    sc._prepare_thumbnail_async(1, None,
                                lambda img: (result.setdefault("img", img), done.set()))
    assert done.wait(5), "on_ready was never called"
    assert result["img"] is None
