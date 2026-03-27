"""
MP4 編碼器單元測試

測試 encode_mp4、detect_encoder、cleanup_local_events 函式。
"""

import os
import shutil
import subprocess
import time
from unittest import mock

import numpy as np
import pytest

from utils.mp4_encoder import (
    cleanup_local_events,
    detect_encoder,
    encode_mp4,
)

# 檢查 ffmpeg 是否可用
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


@pytest.fixture(autouse=True)
def reset_encoder_cache():
    """每個測試前重置編碼器快取。"""
    import utils.mp4_encoder as mp4_module
    mp4_module._cached_encoder = None
    yield


class TestEncodeMp4:
    """測試 encode_mp4 函式。"""

    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg not installed")
    def test_encode_mp4_creates_file(self, tmp_path):
        """傳入 30 幀假幀，驗證 MP4 檔案存在且 >0 bytes。"""
        # 建立假幀
        frames = []
        fake_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        for i in range(30):
            frames.append((time.time() + i * 0.1, fake_frame.copy()))

        # 編碼
        mp4_path = encode_mp4(
            frames,
            node_id="test_node",
            timestamp=time.time(),
            output_dir=str(tmp_path),
            encoder="libx264",
        )

        assert os.path.exists(mp4_path)
        assert os.path.getsize(mp4_path) > 0

    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg not installed")
    def test_encode_mp4_filename_format(self, tmp_path):
        """驗證輸出檔名格式符合 YYYY-MM-DD_HH-MM-SS_{node_id}.mp4。"""
        frames = [(time.time(), np.zeros((720, 1280, 3), dtype=np.uint8))]
        node_id = "glass_node_01"

        mp4_path = encode_mp4(
            frames,
            node_id=node_id,
            timestamp=time.time(),
            output_dir=str(tmp_path),
            encoder="libx264",
        )

        filename = os.path.basename(mp4_path)
        # 檢查格式
        assert node_id in filename
        assert filename.endswith(".mp4")
        # 檢查日期時間格式（簡化檢查）
        parts = filename.replace(".mp4", "").split("_")
        assert len(parts) >= 3  # YYYY-MM-DD, HH-MM-SS, node_id

    def test_encode_mp4_empty_frames_raises(self, tmp_path):
        """傳入空列表 → ValueError。"""
        with pytest.raises(ValueError, match="frames list cannot be empty"):
            encode_mp4(
                [],
                node_id="test_node",
                timestamp=time.time(),
                output_dir=str(tmp_path),
                encoder="libx264",
            )

    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg not installed")
    def test_encode_mp4_creates_output_dir(self, tmp_path):
        """傳入不存在的目錄 → 自動建立。"""
        output_dir = tmp_path / "new_dir" / "events"
        frames = [(time.time(), np.zeros((720, 1280, 3), dtype=np.uint8))]

        mp4_path = encode_mp4(
            frames,
            node_id="test_node",
            timestamp=time.time(),
            output_dir=str(output_dir),
            encoder="libx264",
        )

        assert os.path.exists(output_dir)
        assert os.path.exists(mp4_path)

    def test_encode_mp4_ffmpeg_not_found(self, tmp_path):
        """ffmpeg 不存在時拋出 RuntimeError。"""
        frames = [(time.time(), np.zeros((720, 1280, 3), dtype=np.uint8))]

        with mock.patch("subprocess.Popen", side_effect=FileNotFoundError("ffmpeg not found")):
            with pytest.raises(RuntimeError, match="ffmpeg not found"):
                encode_mp4(
                    frames,
                    node_id="test_node",
                    timestamp=time.time(),
                    output_dir=str(tmp_path),
                    encoder="libx264",
                )

    def test_encode_mp4_runtime_error_on_failure(self, tmp_path):
        """ffmpeg 返回非零退出碼時拋出 RuntimeError。"""
        frames = [(time.time(), np.zeros((720, 1280, 3), dtype=np.uint8))]

        # Mock subprocess.Popen to return a process with non-zero return code
        mock_process = mock.Mock()
        mock_process.stdin = mock.Mock()
        mock_process.communicate.return_value = (b"", b"error message")
        mock_process.returncode = 1

        with mock.patch("subprocess.Popen", return_value=mock_process):
            with pytest.raises(RuntimeError, match="ffmpeg encoding failed"):
                encode_mp4(
                    frames,
                    node_id="test_node",
                    timestamp=time.time(),
                    output_dir=str(tmp_path),
                    encoder="libx264",
                )


class TestDetectEncoder:
    """測試 detect_encoder 函式。"""

    def test_detect_encoder_with_v4l2m2m(self):
        """mock 返回包含 h264_v4l2m2m → 返回 'h264_v4l2m2m'。"""
        mock_result = mock.Mock()
        mock_result.stdout = "... h264_v4l2m2m ..."

        with mock.patch("subprocess.run", return_value=mock_result):
            encoder = detect_encoder()
            assert encoder == "h264_v4l2m2m"

    def test_detect_encoder_without_v4l2m2m(self):
        """mock 返回不包含 h264_v4l2m2m → 返回 'libx264'。"""
        mock_result = mock.Mock()
        mock_result.stdout = "... libx264 ..."

        with mock.patch("subprocess.run", return_value=mock_result):
            encoder = detect_encoder()
            assert encoder == "libx264"

    def test_detect_encoder_ffmpeg_missing(self):
        """mock 拋出 FileNotFoundError → 返回 'libx264'。"""
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            encoder = detect_encoder()
            assert encoder == "libx264"

    def test_detect_encoder_caches_result(self):
        """呼叫兩次，subprocess.run 只被呼叫一次。"""
        import utils.mp4_encoder as mp4_module
        mp4_module._cached_encoder = None

        mock_result = mock.Mock()
        mock_result.stdout = "libx264"

        with mock.patch("subprocess.run", return_value=mock_result) as mock_run:
            encoder1 = detect_encoder()
            encoder2 = detect_encoder()

            assert encoder1 == encoder2
            assert mock_run.call_count == 1

    def test_detect_encoder_timeout_fallback(self):
        """ffmpeg 超時時降級到 libx264。"""
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10)):
            encoder = detect_encoder()
            assert encoder == "libx264"


class TestCleanupLocalEvents:
    """測試 cleanup_local_events 函式。"""

    def test_cleanup_deletes_oldest(self, tmp_path):
        """建立 25 個 MP4 檔案，呼叫後剩 20 個，最舊 5 個被刪除。"""
        # 建立 25 個 MP4 檔案，設定不同的 mtime
        files = []
        for i in range(25):
            filepath = tmp_path / f"event_{i:03d}.mp4"
            filepath.write_bytes(b"fake mp4 content")
            # 設定 mtime
            os.utime(filepath, (i, i))
            files.append(filepath)

        # 呼叫 cleanup
        deleted = cleanup_local_events(str(tmp_path), max_files=20)

        assert deleted == 5

        # 驗證剩餘檔案
        remaining = list(tmp_path.glob("*.mp4"))
        assert len(remaining) == 20

        # 驗證最舊的 5 個被刪除
        for i in range(5):
            assert not (tmp_path / f"event_{i:03d}.mp4").exists()

    def test_cleanup_under_limit(self, tmp_path):
        """建立 10 個檔案，呼叫後仍 10 個，返回 0。"""
        for i in range(10):
            filepath = tmp_path / f"event_{i:03d}.mp4"
            filepath.write_bytes(b"fake mp4")

        deleted = cleanup_local_events(str(tmp_path), max_files=20)

        assert deleted == 0
        remaining = list(tmp_path.glob("*.mp4"))
        assert len(remaining) == 10

    def test_cleanup_empty_dir(self, tmp_path):
        """空目錄呼叫不報錯，返回 0。"""
        deleted = cleanup_local_events(str(tmp_path), max_files=20)
        assert deleted == 0

    def test_cleanup_nonexistent_dir(self):
        """不存在的目錄不報錯，返回 0。"""
        deleted = cleanup_local_events("/nonexistent/path", max_files=20)
        assert deleted == 0

    def test_cleanup_ignores_non_mp4(self, tmp_path):
        """目錄中有 .txt 檔案，不被刪除。"""
        # 建立 25 個 MP4
        for i in range(25):
            filepath = tmp_path / f"event_{i:03d}.mp4"
            filepath.write_bytes(b"fake mp4")
            os.utime(filepath, (i, i))

        # 建立 5 個 TXT
        for i in range(5):
            filepath = tmp_path / f"log_{i:03d}.txt"
            filepath.write_bytes(b"log content")

        deleted = cleanup_local_events(str(tmp_path), max_files=20)

        assert deleted == 5  # 只刪除 MP4

        # TXT 仍存在
        txt_files = list(tmp_path.glob("*.txt"))
        assert len(txt_files) == 5