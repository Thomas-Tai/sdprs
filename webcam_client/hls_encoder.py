# sdprs/webcam_client/hls_encoder.py
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("webcam_client.hls_encoder")


def _resolve_ffmpeg() -> str:
    """Return the ffmpeg executable to invoke.

    A onefile PyInstaller build may bundle ffmpeg.exe so the packaged app is
    fully standalone (live view works with no separate ffmpeg install). At
    runtime the bootloader unpacks bundled binaries into ``sys._MEIPASS`` —
    prefer an ffmpeg there, by ABSOLUTE path, so the target PC needs nothing on
    PATH. Fall back to the bare command name for dev runs, or a build made
    without ffmpeg, letting the OS resolve it from PATH as before.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = os.path.join(base, "ffmpeg.exe")
        if os.path.exists(candidate):
            return candidate
    return "ffmpeg"


class HlsEncoder:
    def __init__(self, width: int = 640, height: int = 480, fps: int = 8, output_dir: Optional[Path] = None):
        self._width = width
        self._height = height
        self._fps = fps
        self._output_dir = output_dir or Path("./hls_out")
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._segment_count = 0
        # Non-blocking frame feed. write_frame() only hands the LATEST frame to
        # _pending (overwriting any not-yet-written one -> drop, never block) and
        # wakes _writer, which does the actual BLOCKING stdin.write to ffmpeg. A
        # ~900KB raw frame into a full pipe on a CPU-starved PC blocks for tens of
        # seconds; doing that on the caller's run loop stalled the 1Hz snapshot
        # too (push_engine.run pushes both), so the dashboard tile went grey.
        # Latest-frame-wins: there is never a growing backlog.
        self._pending: Optional[bytes] = None
        self._frame_cond = threading.Condition()
        self._writer: Optional[threading.Thread] = None
        self._writer_stop = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                return True
            cmd = [
                _resolve_ffmpeg(), "-y",
                "-f", "rawvideo",
                "-vcodec", "rawvideo",
                "-s", f"{self._width}x{self._height}",
                "-pix_fmt", "bgr24",
                "-r", str(self._fps),
                "-i", "-",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-g", str(self._fps * 2),
                "-hls_time", "2",
                "-hls_list_size", "5",
                "-hls_segment_filename", str(self._output_dir / "seg_%06d.ts"),
                "-f", "hls",
                str(self._output_dir / "playlist.m3u8"),
            ]
            try:
                # mkdir is inside the try so an unwritable output dir (OSError) is
                # caught here and returns False, rather than propagating up through
                # _start_encoder -> set_streaming -> on_command and killing the
                # control-channel thread (completes Fix 4's intent).
                self._output_dir.mkdir(parents=True, exist_ok=True)
                self._process = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError as e:
                logger.error(f"Failed to start ffmpeg HLS encoder: {e}")
                return False
        # Spawn the writer OUTSIDE self._lock: it owns the blocking stdin.write so
        # write_frame() never blocks the caller's run loop.
        self._writer_stop.clear()
        self._pending = None
        self._writer = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer.start()
        logger.info("FFmpeg HLS encoder started")
        return True

    def write_frame(self, frame_bytes: bytes) -> bool:
        """Hand the latest frame to the writer thread WITHOUT blocking. Any frame
        the writer has not yet written is dropped in favour of this newer one, so
        a slow ffmpeg never backs up or stalls the caller. Returns False only when
        the encoder is not running."""
        if not self.is_running:
            return False
        with self._frame_cond:
            self._pending = frame_bytes
            self._frame_cond.notify()
        return True

    def _writer_loop(self) -> None:
        """Drain the single-slot latest-frame buffer, doing the BLOCKING write to
        ffmpeg's stdin here so the caller's run loop stays responsive even when
        ffmpeg cannot keep up."""
        while not self._writer_stop.is_set():
            with self._frame_cond:
                while self._pending is None and not self._writer_stop.is_set():
                    self._frame_cond.wait(timeout=0.5)
                frame = self._pending
                self._pending = None
            if frame is None:
                continue
            proc = self._process
            if proc is None or proc.stdin is None:
                break
            try:
                proc.stdin.write(frame)
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                break

    def stop(self) -> None:
        # Stop the writer FIRST so it is never mid-write when we close stdin /
        # terminate ffmpeg. If it is blocked in a real stdin.write, the join
        # times out and the terminate below breaks the pipe, which unblocks it.
        self._writer_stop.set()
        with self._frame_cond:
            self._frame_cond.notify_all()
        writer = self._writer
        if writer is not None and writer.is_alive():
            writer.join(timeout=3)
        self._writer = None
        with self._lock:
            if self._process is not None:
                try:
                    if self._process.stdin:
                        self._process.stdin.close()
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception:
                    self._process.kill()
                    self._process.wait()
                self._process = None
                logger.info("FFmpeg HLS encoder stopped")

    def get_new_segments(self) -> list:
        ts_files = sorted(self._output_dir.glob("seg_*.ts"))
        new = ts_files[self._segment_count:]
        self._segment_count = len(ts_files)
        playlist = self._output_dir / "playlist.m3u8"
        result = [(f.name, f.read_bytes()) for f in new]
        if playlist.exists():
            result.append(("playlist.m3u8", playlist.read_bytes()))
        return result
