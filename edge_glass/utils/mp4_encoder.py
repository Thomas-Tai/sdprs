"""
MP4 編碼器模組

將影像幀列表編碼為 MP4 檔案，支援硬體加速和本地備份清理。

使用範例：
    from utils.mp4_encoder import encode_mp4, detect_encoder, cleanup_local_events

    # 偵測可用編碼器
    encoder = detect_encoder()

    # 編碼 MP4
    mp4_path = encode_mp4(frames, node_id="glass_node_01", timestamp=time.time())

    # 清理舊檔案
    cleanup_local_events("./events", max_files=20)
"""

import glob
import logging
import os
import subprocess
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 模組級快取變數
_cached_encoder: Optional[str] = None


def detect_encoder() -> str:
    """
    偵測可用的 H.264 編碼器。

    優先使用 Pi 硬體加速 h264_v4l2m2m，不可用時降級到 libx264。
    結果快取到模組變數，只執行一次 ffmpeg 子進程。

    Returns:
        "h264_v4l2m2m" 或 "libx264"
    """
    global _cached_encoder

    if _cached_encoder is not None:
        return _cached_encoder

    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if "h264_v4l2m2m" in result.stdout:
            _cached_encoder = "h264_v4l2m2m"
            logger.info("Selected video encoder: h264_v4l2m2m (hardware accelerated)")
        else:
            _cached_encoder = "libx264"
            logger.info("Selected video encoder: libx264 (software)")

    except FileNotFoundError:
        _cached_encoder = "libx264"
        logger.warning("ffmpeg not found, defaulting to libx264")
    except subprocess.TimeoutExpired:
        _cached_encoder = "libx264"
        logger.warning("ffmpeg check timed out, defaulting to libx264")
    except Exception as e:
        _cached_encoder = "libx264"
        logger.warning(f"ffmpeg check failed: {e}, defaulting to libx264")

    return _cached_encoder


def encode_mp4(
    frames: List[Tuple[float, np.ndarray]],
    node_id: str,
    timestamp: float,
    output_dir: str = "./events",
    encoder: Optional[str] = None,
) -> str:
    """
    將幀列表編碼為 MP4 檔案。

    Args:
        frames: [(timestamp, frame_ndarray), ...] 幀列表
        node_id: 節點 ID（用於檔名）
        timestamp: 事件觸發時間戳（用於檔名）
        output_dir: MP4 輸出目錄
        encoder: 編碼器名稱（None 則自動偵測）

    Returns:
        生成的 MP4 檔案完整路徑

    Raises:
        RuntimeError: ffmpeg 編碼失敗
        ValueError: 幀列表為空
    """
    if not frames:
        raise ValueError("frames list cannot be empty")

    # 確定編碼器
    if encoder is None:
        encoder = detect_encoder()

    # 按時間戳排序
    sorted_frames = sorted(frames, key=lambda x: x[0])

    # 計算實際幀率
    if len(sorted_frames) >= 2:
        first_ts = sorted_frames[0][0]
        last_ts = sorted_frames[-1][0]
        duration = last_ts - first_ts
        if duration > 0:
            actual_fps = len(sorted_frames) / duration
        else:
            actual_fps = 15.0
    else:
        actual_fps = 15.0

    # 取得影像尺寸
    height, width = sorted_frames[0][1].shape[:2]

    # 生成輸出路徑
    dt = datetime.fromtimestamp(timestamp)
    filename = dt.strftime(f"%Y-%m-%d_%H-%M-%S_{node_id}.mp4")
    output_path = os.path.join(output_dir, filename)

    # 確保輸出目錄存在
    os.makedirs(output_dir, exist_ok=True)

    logger.info(f"Encoding MP4: {output_path} ({len(sorted_frames)} frames, {actual_fps:.2f} fps, {encoder})")

    # 建立 ffmpeg 命令
    cmd = [
        "ffmpeg",
        "-y",  # 覆蓋現有檔案
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}",
        "-r", str(actual_fps),
        "-i", "pipe:0",
        "-c:v", encoder,
        "-b:v", "2M",
        "-movflags", "+faststart",
    ]

    # libx264 加上 preset
    if encoder == "libx264":
        cmd.extend(["-preset", "ultrafast"])

    cmd.append(output_path)

    # 執行 ffmpeg
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # 逐幀寫入 stdin
        for _, frame in sorted_frames:
            process.stdin.write(frame.tobytes())

        process.stdin.close()
        _, stderr = process.communicate(timeout=300)

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="ignore")
            raise RuntimeError(f"ffmpeg encoding failed with code {process.returncode}: {error_msg}")

    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Please install ffmpeg.")
    except subprocess.TimeoutExpired:
        process.kill()
        raise RuntimeError("ffmpeg encoding timed out")
    except Exception as e:
        raise RuntimeError(f"ffmpeg encoding error: {e}")

    # 驗證輸出
    if not os.path.exists(output_path):
        raise RuntimeError(f"MP4 file not created: {output_path}")

    file_size = os.path.getsize(output_path)
    if file_size == 0:
        raise RuntimeError(f"MP4 file is empty: {output_path}")

    logger.info(f"MP4 encoded successfully: {output_path} ({file_size} bytes)")

    # 清理本地備份
    cleanup_local_events(output_dir, max_files=20)

    return output_path


def cleanup_local_events(event_dir: str = "./events", max_files: int = 20) -> int:
    """
    清理本地事件目錄，保留最新的 max_files 個 MP4 檔案。

    若目錄中 MP4 檔案數超過 max_files，按修改時間（mtime）排序，
    刪除最舊的檔案直到剩餘 max_files 個。

    Args:
        event_dir: 事件目錄路徑
        max_files: 最大保留檔案數

    Returns:
        實際刪除的檔案數量
    """
    if not os.path.exists(event_dir):
        return 0

    # 列出所有 MP4 檔案
    mp4_files = glob.glob(os.path.join(event_dir, "*.mp4"))

    if len(mp4_files) <= max_files:
        return 0

    # 按修改時間排序（升序，最舊在前）
    mp4_files.sort(key=lambda f: os.path.getmtime(f))

    # 計算需要刪除的數量
    delete_count = len(mp4_files) - max_files
    deleted = 0

    for filepath in mp4_files[:delete_count]:
        try:
            os.remove(filepath)
            logger.info(f"Cleaned up old event: {os.path.basename(filepath)}")
            deleted += 1
        except OSError as e:
            logger.warning(f"Failed to delete {filepath}: {e}")

    return deleted


if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO)

    # 測試編碼器偵測
    print(f"Detected encoder: {detect_encoder()}")

    # 建立假幀進行測試
    frames = []
    fake_frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    for i in range(30):
        frames.append((time.time() + i * 0.1, fake_frame.copy()))

    # 編碼測試
    try:
        mp4_path = encode_mp4(
            frames,
            node_id="test_node",
            timestamp=time.time(),
            output_dir="./test_events",
            encoder="libx264",
        )
        print(f"Test MP4 created: {mp4_path}")
    except RuntimeError as e:
        print(f"Encoding failed (ffmpeg may not be installed): {e}")