# sdprs/webcam_client/tests/test_hls_encoder.py
"""ffmpeg resolution for the packaged (frozen) exe.

The onefile PyInstaller build can bundle ffmpeg.exe so the drop is fully
standalone (live view included). At runtime the encoder must PREFER that
bundled binary — unpacked into sys._MEIPASS — and otherwise fall back to the
bare `ffmpeg` command on PATH (dev runs, or a build made without ffmpeg).
"""
import os
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.hls_encoder import _resolve_ffmpeg, HlsEncoder


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


def test_write_frame_does_not_block_caller(tmp_path):
    # ROBUSTNESS (grey-tile fix): write_frame must return promptly even when
    # ffmpeg cannot drain its stdin (a ~900KB raw frame into a full pipe on a
    # CPU-starved PC). The old blocking stdin.write stalled the run loop for tens
    # of seconds, which also stopped the 1Hz snapshot -> the tile went grey. The
    # blocking write now lives on a dedicated writer thread; write_frame only
    # hands over the latest frame and returns.
    release = threading.Event()
    written = []

    def blocking_write(data):
        written.append(bytes(data))
        release.wait(5)          # simulate ffmpeg stalled: the WRITE blocks

    fake_stdin = MagicMock()
    fake_stdin.write.side_effect = blocking_write
    fake_proc = MagicMock()
    fake_proc.stdin = fake_stdin
    fake_proc.poll.return_value = None       # "running"

    enc = HlsEncoder(width=8, height=8, fps=8, output_dir=tmp_path)
    try:
        with patch("webcam_client.hls_encoder.subprocess.Popen", return_value=fake_proc):
            assert enc.start() is True
            done = threading.Event()

            def feed():
                enc.write_frame(b"frame-1")   # a writer thread picks this up and blocks
                enc.write_frame(b"frame-2")   # must NOT wait on the stalled writer
                done.set()

            threading.Thread(target=feed, daemon=True).start()
            # Pre-fix write_frame blocked in stdin.write, so done is never set in
            # time; post-fix it returns immediately and done fires well under 1s.
            assert done.wait(1.0), "write_frame blocked the caller (must be non-blocking)"
    finally:
        release.set()
        enc.stop()
