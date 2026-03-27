"""
CircularBuffer 單元測試

測試循環緩衝區的基本功能：
- 初始化
- append 與自動淘汰
- freeze 淺拷貝
- clear
"""

import time

import numpy as np
import pytest

from buffer.circular_buffer import CircularBuffer


class TestCircularBufferInit:
    """測試初始化。"""

    def test_init(self):
        """初始化後 len==0, is_full==False, maxlen==fps*duration。"""
        fps = 15
        duration = 10
        buffer = CircularBuffer(fps=fps, duration_seconds=duration)

        assert len(buffer) == 0
        assert buffer.is_full is False
        assert buffer.maxlen == fps * duration

    def test_init_different_sizes(self):
        """測試不同大小的初始化。"""
        buffer = CircularBuffer(fps=30, duration_seconds=5)
        assert buffer.maxlen == 150

        buffer = CircularBuffer(fps=10, duration_seconds=30)
        assert buffer.maxlen == 300


class TestCircularBufferAppend:
    """測試 append 操作。"""

    def test_append_single(self):
        """append 一幀後 len==1。"""
        buffer = CircularBuffer(fps=15, duration_seconds=10)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        buffer.append(time.time(), frame)

        assert len(buffer) == 1

    def test_append_fills_buffer(self):
        """append 150 幀後 len==150, is_full==True。"""
        fps = 15
        duration = 10
        buffer = CircularBuffer(fps=fps, duration_seconds=duration)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        for i in range(fps * duration):
            buffer.append(time.time() + i, frame)

        assert len(buffer) == 150
        assert buffer.is_full is True

    def test_maxlen_auto_eviction(self):
        """append 160 幀後 len==150，最舊的 10 幀被淘汰。"""
        fps = 15
        duration = 10
        buffer = CircularBuffer(fps=fps, duration_seconds=duration)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        # 寫入 160 幀，每幀有不同的 timestamp
        for i in range(160):
            buffer.append(float(i), frame)

        assert len(buffer) == 150
        assert buffer.is_full is True

        # 驗證最舊幀的 timestamp 是第 11 幀（index 10）
        frozen = buffer.freeze()
        assert frozen[0][0] == 10.0  # 第 11 幀的 timestamp

    def test_append_continues_after_full(self):
        """緩衝區滿後繼續 append 仍正常運作。"""
        buffer = CircularBuffer(fps=15, duration_seconds=10)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        # 填滿
        for i in range(150):
            buffer.append(float(i), frame)

        # 繼續 append
        for i in range(150, 200):
            buffer.append(float(i), frame)

        assert len(buffer) == 150
        frozen = buffer.freeze()
        assert frozen[-1][0] == 199.0  # 最新的 timestamp


class TestCircularBufferFreeze:
    """測試 freeze 操作。"""

    def test_freeze_returns_list(self):
        """freeze() 返回 list 類型。"""
        buffer = CircularBuffer(fps=15, duration_seconds=10)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        buffer.append(time.time(), frame)
        frozen = buffer.freeze()

        assert isinstance(frozen, list)

    def test_freeze_shallow_copy(self):
        """freeze() 後繼續 append，凍結列表不受影響。"""
        buffer = CircularBuffer(fps=15, duration_seconds=10)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        # 填入一些幀
        for i in range(50):
            buffer.append(float(i), frame)

        # 凍結
        frozen = buffer.freeze()
        original_len = len(frozen)

        # 繼續 append
        for i in range(50, 100):
            buffer.append(float(i), frame)

        # 凍結列表長度不變
        assert len(frozen) == original_len

    def test_freeze_empty_buffer(self):
        """空緩衝區 freeze() 返回 []。"""
        buffer = CircularBuffer(fps=15, duration_seconds=10)
        frozen = buffer.freeze()

        assert frozen == []
        assert isinstance(frozen, list)

    def test_freeze_preserves_order(self):
        """凍結列表按 timestamp 升序排列。"""
        buffer = CircularBuffer(fps=15, duration_seconds=10)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        # 按順序寫入
        for i in range(100):
            buffer.append(float(i), frame)

        frozen = buffer.freeze()

        # 驗證順序
        timestamps = [ts for ts, _ in frozen]
        assert timestamps == sorted(timestamps)

    def test_freeze_frame_reference(self):
        """凍結列表中的 frame 與原始 numpy array 是同一物件（淺拷貝驗證）。"""
        buffer = CircularBuffer(fps=15, duration_seconds=10)

        # 建立唯一的 frame
        frame1 = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame1[0, 0, 0] = 1  # 標記
        frame2 = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame2[0, 0, 0] = 2  # 不同標記

        buffer.append(1.0, frame1)
        buffer.append(2.0, frame2)

        frozen = buffer.freeze()

        # 驗證是同一物件（淺拷貝）
        assert frozen[0][1] is frame1
        assert frozen[1][1] is frame2


class TestCircularBufferClear:
    """測試 clear 操作。"""

    def test_clear(self):
        """clear() 後 len==0。"""
        buffer = CircularBuffer(fps=15, duration_seconds=10)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        # 填入一些幀
        for i in range(50):
            buffer.append(float(i), frame)

        assert len(buffer) == 50

        # 清空
        buffer.clear()

        assert len(buffer) == 0
        assert buffer.is_full is False

    def test_clear_empty_buffer(self):
        """清空空緩衝區不報錯。"""
        buffer = CircularBuffer(fps=15, duration_seconds=10)
        buffer.clear()  # 不應拋出異常

        assert len(buffer) == 0


class TestCircularBufferProperties:
    """測試屬性。"""

    def test_is_full_property(self):
        """is_full 屬性正確反映緩衝區狀態。"""
        buffer = CircularBuffer(fps=15, duration_seconds=10)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        assert buffer.is_full is False

        # 接近滿
        for i in range(149):
            buffer.append(float(i), frame)

        assert buffer.is_full is False

        # 剛好滿
        buffer.append(149.0, frame)

        assert buffer.is_full is True

    def test_maxlen_property(self):
        """maxlen 屬性返回正確值。"""
        buffer = CircularBuffer(fps=20, duration_seconds=15)
        assert buffer.maxlen == 300