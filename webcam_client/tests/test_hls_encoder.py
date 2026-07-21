# sdprs/webcam_client/tests/test_hls_encoder.py
"""ffmpeg resolution for the packaged (frozen) exe.

The onefile PyInstaller build can bundle ffmpeg.exe so the drop is fully
standalone (live view included). At runtime the encoder must PREFER that
bundled binary — unpacked into sys._MEIPASS — and otherwise fall back to the
bare `ffmpeg` command on PATH (dev runs, or a build made without ffmpeg).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.hls_encoder import _resolve_ffmpeg


def test_not_frozen_falls_back_to_path(monkeypatch):
    # No _MEIPASS => not frozen => use the PATH command name.
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert _resolve_ffmpeg() == "ffmpeg"


def test_frozen_prefers_bundled_ffmpeg(tmp_path, monkeypatch):
    # Frozen with a bundled ffmpeg.exe next to the unpacked app => use it,
    # by absolute path, so the target PC needs nothing on PATH.
    bundled = tmp_path / "ffmpeg.exe"
    bundled.write_bytes(b"stub")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert _resolve_ffmpeg() == str(bundled)
    assert os.path.isabs(_resolve_ffmpeg())


def test_frozen_without_bundled_ffmpeg_falls_back(tmp_path, monkeypatch):
    # Frozen but the build did NOT bundle ffmpeg => must still fall back to
    # PATH rather than returning a path to a file that does not exist.
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)  # empty dir
    assert _resolve_ffmpeg() == "ffmpeg"
