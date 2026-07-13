"""
event_capture 單元測試

涵蓋非阻塞事件捕獲的四個構件：
- PendingEventTracker：註冊觸發，post-roll 到期後才釋放（順序保留）
- slice_window：從凍結的 (timestamp, frame) 快照切出 [t_start, t_end] 的裸 ndarray
- clamp_capture_window：強制 pre+post+margin <= duration 的緩衝區不變式
- EncodeWorker：以背景執行緒 + 有界佇列（drop-newest）執行編碼與入列

執行緒相關測試一律使用 threading.Event 交握或有界輪詢，避免 sleep 競態。

備註：從 edge_glass 目錄執行 pytest 時，`from utils.event_capture import ...`
可正常運作（utils 為既有套件）。
"""

import logging
import threading
import time
from datetime import datetime

import numpy as np
import pytest

from utils.event_capture import (
    EncodeWorker,
    PendingEventTracker,
    clamp_capture_window,
    slice_window,
)


# ---------------------------------------------------------------------------
# 測試輔助
# ---------------------------------------------------------------------------


def _wait_for(predicate, timeout=5.0, interval=0.005):
    """在 timeout 內輪詢 predicate，成立回傳 True，逾時回傳 False（不 sleep-race）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class FakeEncoder:
    """假編碼器：記錄呼叫參數，回傳固定路徑。"""

    def __init__(self, path="/fake/event.mp4"):
        self.path = path
        self.calls = []

    def __call__(self, frames, node_id, timestamp, output_dir):
        self.calls.append((frames, node_id, timestamp, output_dir))
        return self.path


class FakeEventQueue:
    """假上傳佇列：記錄 enqueue 呼叫，並在每次 enqueue 後設定事件旗標。"""

    def __init__(self):
        self.calls = []
        self.enqueued = threading.Event()

    def enqueue(self, node_id, timestamp, mp4_path, metadata):
        self.calls.append(
            dict(
                node_id=node_id,
                timestamp=timestamp,
                mp4_path=mp4_path,
                metadata=metadata,
            )
        )
        self.enqueued.set()
        return len(self.calls)


def _frame(value=0, shape=(2, 2, 3)):
    return np.full(shape, value, dtype=np.uint8)


# ---------------------------------------------------------------------------
# PendingEventTracker
# ---------------------------------------------------------------------------


class TestPendingEventTracker:
    def test_add_increases_len(self):
        t = PendingEventTracker(post_roll_seconds=2.0)
        assert len(t) == 0
        t.add(100.0, {"a": 1})
        assert len(t) == 1

    def test_due_before_window_returns_empty_and_keeps_event(self):
        t = PendingEventTracker(2.0)
        t.add(100.0, {})
        # 100 + 2 = 102 > 101 -> 尚未到期
        assert t.due(101.0) == []
        assert len(t) == 1

    def test_due_exact_boundary_releases_and_empties(self):
        t = PendingEventTracker(2.0)
        t.add(100.0, {"k": "v"})
        ready = t.due(102.0)  # 100 + 2 <= 102（含邊界）
        assert len(ready) == 1
        assert ready[0].trigger_ts == 100.0
        assert ready[0].metadata == {"k": "v"}
        assert len(t) == 0  # 已被移除

    def test_due_past_due_releases(self):
        t = PendingEventTracker(2.0)
        t.add(100.0, {})
        ready = t.due(500.0)
        assert len(ready) == 1
        assert len(t) == 0

    def test_partial_due_preserves_registration_order(self):
        t = PendingEventTracker(2.0)
        t.add(100.0, {"i": 0})
        t.add(105.0, {"i": 1})
        t.add(101.0, {"i": 2})
        # now = 103 -> 到期者：ts=100(102<=103) 與 ts=101(103<=103)；ts=105(107) 未到期
        ready = t.due(103.0)
        assert [e.metadata["i"] for e in ready] == [0, 2]  # 保留註冊順序
        assert len(t) == 1
        # 剩下的是 ts=105 那筆
        remaining = t.due(200.0)
        assert [e.metadata["i"] for e in remaining] == [1]
        assert len(t) == 0


# ---------------------------------------------------------------------------
# slice_window
# ---------------------------------------------------------------------------


class TestSliceWindow:
    def _frames_0_to_10(self):
        # ts 0..10，frame 的每個元素等於其 ts（方便驗證內容）
        return [(float(i), _frame(i)) for i in range(11)]

    def test_inclusive_window(self):
        out = slice_window(self._frames_0_to_10(), 3.0, 7.0)
        # 3,4,5,6,7 -> 5 幀
        assert len(out) == 5
        assert [int(a[0, 0, 0]) for a in out] == [3, 4, 5, 6, 7]

    def test_returns_bare_ndarrays_not_tuples(self):
        out = slice_window(self._frames_0_to_10(), 0.0, 4.0)
        assert len(out) == 5
        assert all(isinstance(a, np.ndarray) for a in out)
        assert not any(isinstance(a, tuple) for a in out)

    def test_empty_window_returns_empty_list(self):
        out = slice_window(self._frames_0_to_10(), 100.0, 200.0)
        assert out == []

    def test_order_preserved(self):
        out = slice_window(self._frames_0_to_10(), 0.0, 10.0)
        assert [int(a[0, 0, 0]) for a in out] == list(range(11))


# ---------------------------------------------------------------------------
# clamp_capture_window
# ---------------------------------------------------------------------------


class TestClampCaptureWindow:
    def test_fits_returns_unchanged(self):
        assert clamp_capture_window(10, 4, 5, margin=1.0) == (4.0, 5.0)

    def test_too_big_shrinks_pre_first(self, caplog):
        with caplog.at_level(logging.WARNING, logger="event_capture"):
            pre, post = clamp_capture_window(10, 8, 5, 1)
        assert (pre, post) == (4.0, 5.0)
        # WARNING 路徑：回傳值滿足不變式
        assert pre + post + 1 <= 10
        assert any(rec.levelno == logging.WARNING for rec in caplog.records)

    def test_extreme_shrinks_both_to_zero_pre(self, caplog):
        with caplog.at_level(logging.WARNING, logger="event_capture"):
            pre, post = clamp_capture_window(10, 20, 20, 1)
        assert (pre, post) == (0.0, 9.0)
        assert pre + post + 1 <= 10
        assert any(rec.levelno == logging.WARNING for rec in caplog.records)


# ---------------------------------------------------------------------------
# EncodeWorker
# ---------------------------------------------------------------------------


class TestEncodeWorker:
    def test_submit_then_stop_drains_and_enqueues_once(self):
        encoder = FakeEncoder(path="/fake/one.mp4")
        eq = FakeEventQueue()
        worker = EncodeWorker(
            encoder, eq, node_id="glass_01", output_dir="/out", maxsize=2
        )
        worker.start()

        frames = [_frame(1), _frame(2)]
        meta = {"type": "test", "confidence": 0.9}
        assert worker.submit(frames, 1000.5, meta) is True

        # stop(drain=True)：先 join run loop，再同步排空剩餘任務 -> 交握完成
        worker.stop(drain=True)

        assert len(eq.calls) == 1
        call = eq.calls[0]
        assert call["node_id"] == "glass_01"
        assert call["mp4_path"] == "/fake/one.mp4"
        assert call["metadata"] is meta  # 原樣傳遞（同一 dict）

        # timestamp 應為 ISO 字串（由原始 float 轉換而來）
        assert isinstance(call["timestamp"], str)
        assert call["timestamp"] == datetime.fromtimestamp(1000.5).isoformat()
        # 可被 fromisoformat 解析
        datetime.fromisoformat(call["timestamp"])

        # 編碼器收到的是原始 float 時間戳與 frames、output_dir
        enc_frames, enc_node, enc_ts, enc_out = encoder.calls[0]
        assert enc_node == "glass_01"
        assert enc_ts == 1000.5
        assert enc_out == "/out"
        assert enc_frames is frames

    def test_encode_exception_skips_enqueue_and_worker_survives(self):
        raised = threading.Event()

        def flaky_encode(frames, node_id, timestamp, output_dir):
            if timestamp == 1.0:
                raised.set()
                raise RuntimeError("boom")
            return "/fake/ok.mp4"

        eq = FakeEventQueue()
        worker = EncodeWorker(
            flaky_encode, eq, node_id="n1", output_dir="/out", maxsize=4
        )
        worker.start()
        try:
            # 第一筆會拋例外 -> 不應 enqueue
            assert worker.submit([_frame()], 1.0, {"bad": True}) is True
            assert raised.wait(timeout=5.0)

            # 例外被吞掉，worker 仍存活
            assert worker.is_alive()

            # 第二筆成功 -> enqueue 一次
            assert worker.submit([_frame()], 2.0, {"good": True}) is True
            assert eq.enqueued.wait(timeout=5.0)

            # 只有成功那筆進了佇列（拋例外那筆沒有）
            assert len(eq.calls) == 1
            assert eq.calls[0]["metadata"] == {"good": True}
            assert eq.calls[0]["mp4_path"] == "/fake/ok.mp4"
        finally:
            worker.stop(drain=True)

    def test_bounded_queue_drops_newest_when_full(self, caplog):
        release = threading.Event()
        started = threading.Event()

        def blocking_encode(frames, node_id, timestamp, output_dir):
            started.set()
            # 阻塞直到測試放行，藉此讓佇列填滿
            assert release.wait(timeout=10.0)
            return "/fake/blocked.mp4"

        eq = FakeEventQueue()
        worker = EncodeWorker(
            blocking_encode, eq, node_id="n1", output_dir="/out", maxsize=1
        )
        worker.start()
        try:
            # 第一筆被 worker 取出並卡在編碼 -> 佇列清空、worker 忙碌
            assert worker.submit([_frame()], 1.0, {}) is True
            assert started.wait(timeout=5.0)

            # 佇列（maxsize=1）此刻為空；填入一筆使其滿載
            assert worker.submit([_frame()], 2.0, {}) is True

            # 佇列已滿 + worker 仍卡住 -> 下一筆 drop-newest，回傳 False
            with caplog.at_level(logging.WARNING, logger="event_capture"):
                assert worker.submit([_frame()], 3.0, {}) is False
            assert any(
                "encode queue full" in rec.getMessage() for rec in caplog.records
            )
        finally:
            # 放行後正常收尾，避免 join 卡死
            release.set()
            worker.stop(drain=True)
