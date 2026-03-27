"""
循環緩衝區類別

儲存帶時間戳的影像幀，自動淘汰最舊的幀。
觸發時可凍結（淺拷貝）當前緩衝區內容。

記憶體預算：
- 單幀 720p BGR = 1280 × 720 × 3 bytes ≈ 2.64 MB
- 10 秒 × 15 fps = 150 幀
- 總計 ≈ 396 MB（適用於 Pi 4/5 4GB+）

使用範例：
    from buffer.circular_buffer import CircularBuffer

    buffer = CircularBuffer(fps=15, duration_seconds=10)

    # 寫入幀
    buffer.append(time.time(), frame)

    # 凍結緩衝區
    frozen = buffer.freeze()
"""

import collections
from typing import List, Optional, Tuple

import numpy as np


class CircularBuffer:
    """
    基於 collections.deque 的幀循環緩衝區。

    儲存帶時間戳的影像幀，自動淘汰最舊的幀。
    觸發時可凍結（淺拷貝）當前緩衝區內容。
    """

    def __init__(self, fps: int, duration_seconds: int):
        """
        初始化循環緩衝區。

        Args:
            fps: 每秒幀數（如 15）
            duration_seconds: 緩衝區持續時間（如 10 秒）
        """
        self._maxlen = fps * duration_seconds
        self._buffer: collections.deque = collections.deque(maxlen=self._maxlen)

    def append(self, timestamp: float, frame: np.ndarray) -> None:
        """
        寫入一幀。超過 maxlen 時自動淘汰最舊幀。

        Args:
            timestamp: time.time() 時間戳
            frame: numpy ndarray（BGR 影像）
        """
        self._buffer.append((timestamp, frame))

    def freeze(self) -> List[Tuple[float, np.ndarray]]:
        """
        凍結緩衝區：返回當前所有幀的淺拷貝列表。

        淺拷貝意味著拷貝引用而非深拷貝影像數據。
        主迴圈繼續寫入 deque 不影響凍結的列表。
        凍結的列表持有引用，確保 frame 不被 GC 回收。

        Returns:
            [(timestamp, frame), ...] 的列表（按時間排序）
        """
        return list(self._buffer)

    def __len__(self) -> int:
        """返回當前緩衝區中的幀數。"""
        return len(self._buffer)

    @property
    def is_full(self) -> bool:
        """緩衝區是否已滿（達到 maxlen）。"""
        return len(self._buffer) == self._maxlen

    @property
    def maxlen(self) -> int:
        """返回最大容量。"""
        return self._maxlen

    def clear(self) -> None:
        """清空緩衝區。"""
        self._buffer.clear()


if __name__ == "__main__":
    import time

    # 測試循環緩衝區
    buffer = CircularBuffer(fps=15, duration_seconds=10)

    print(f"Max length: {buffer.maxlen}")
    print(f"Initial length: {len(buffer)}")
    print(f"Is full: {buffer.is_full}")

    # 建立假幀
    fake_frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    # 寫入 160 幀
    for i in range(160):
        buffer.append(time.time() + i, fake_frame.copy())

    print(f"\nAfter 160 appends:")
    print(f"Length: {len(buffer)}")
    print(f"Is full: {buffer.is_full}")

    # 測試凍結
    frozen = buffer.freeze()
    print(f"\nFrozen list length: {len(frozen)}")

    # 繼續寫入
    buffer.append(time.time() + 200, fake_frame.copy())
    print(f"Buffer length after additional append: {len(buffer)}")
    print(f"Frozen list length (unchanged): {len(frozen)}")

    # 測試清空
    buffer.clear()
    print(f"\nAfter clear:")
    print(f"Length: {len(buffer)}")
    print(f"Is full: {buffer.is_full}")