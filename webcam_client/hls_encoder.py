# sdprs/webcam_client/hls_encoder.py
import logging
import subprocess
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("webcam_client.hls_encoder")


class HlsEncoder:
    def __init__(self, width: int = 640, height: int = 480, fps: int = 8, output_dir: Optional[Path] = None):
        self._width = width
        self._height = height
        self._fps = fps
        self._output_dir = output_dir or Path("./hls_out")
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._segment_count = 0

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                return True
            self._output_dir.mkdir(parents=True, exist_ok=True)
            cmd = [
                "ffmpeg", "-y",
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
                self._process = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                logger.info("FFmpeg HLS encoder started")
                return True
            except FileNotFoundError:
                logger.error("ffmpeg not found in PATH")
                return False

    def write_frame(self, frame_bytes: bytes) -> bool:
        with self._lock:
            if not self.is_running or self._process.stdin is None:
                return False
            try:
                self._process.stdin.write(frame_bytes)
                self._process.stdin.flush()
                return True
            except (BrokenPipeError, OSError):
                return False

    def stop(self) -> None:
        with self._lock:
            if self._process is not None:
                try:
                    if self._process.stdin:
                        self._process.stdin.close()
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception:
                    self._process.kill()
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
