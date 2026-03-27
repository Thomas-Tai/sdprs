"""
EventQueue 單元測試

測試本地事件佇列的所有功能。
"""

import json
from datetime import datetime, timedelta
from unittest import mock

import pytest

from comms.event_queue import EventQueue


@pytest.fixture
def queue():
    """建立 in-memory EventQueue 實例。"""
    q = EventQueue(":memory:")
    yield q
    q.close()


class TestEnqueueAndGetPending:
    """測試 enqueue 和 get_pending。"""

    def test_enqueue_and_get_pending(self, queue):
        """enqueue 一筆事件 → get_pending 返回 1 筆，欄位正確。"""
        row_id = queue.enqueue(
            node_id="glass_node_01",
            timestamp="2026-03-03T12:00:00Z",
            mp4_path="./events/test.mp4",
            metadata={"visual_confidence": 0.87},
        )

        assert row_id == 1

        events = queue.get_pending()
        assert len(events) == 1

        event = events[0]
        assert event["node_id"] == "glass_node_01"
        assert event["timestamp"] == "2026-03-03T12:00:00Z"
        assert event["mp4_path"] == "./events/test.mp4"
        assert event["status"] == "QUEUED"
        assert event["metadata"] == {"visual_confidence": 0.87}

    def test_get_pending_order(self, queue):
        """enqueue 多筆 → get_pending 按 created_at ASC 排序。"""
        queue.enqueue("node1", "2026-03-03T12:00:00Z", "./1.mp4", {})
        queue.enqueue("node2", "2026-03-03T12:01:00Z", "./2.mp4", {})
        queue.enqueue("node3", "2026-03-03T12:02:00Z", "./3.mp4", {})

        events = queue.get_pending()
        assert len(events) == 3
        assert events[0]["node_id"] == "node1"
        assert events[1]["node_id"] == "node2"
        assert events[2]["node_id"] == "node3"


class TestUpdateStatus:
    """測試狀態更新。"""

    def test_update_status_to_json_sent(self, queue):
        """enqueue → update_status(status='JSON_SENT', event_id='abc')。"""
        row_id = queue.enqueue("node1", "2026-03-03T12:00:00Z", "./test.mp4", {})

        queue.update_status(row_id, "JSON_SENT", event_id="abc123")

        events = queue.get_pending()
        assert len(events) == 1
        assert events[0]["status"] == "JSON_SENT"
        assert events[0]["event_id"] == "abc123"

    def test_json_sent_still_pending(self, queue):
        """status=JSON_SENT 的事件仍出現在 get_pending。"""
        row_id = queue.enqueue("node1", "2026-03-03T12:00:00Z", "./test.mp4", {})
        queue.update_status(row_id, "JSON_SENT", event_id="42")

        events = queue.get_pending()
        assert len(events) == 1
        assert events[0]["status"] == "JSON_SENT"

    def test_uploaded_not_pending(self, queue):
        """status=UPLOADED → get_pending 不返回。"""
        row_id = queue.enqueue("node1", "2026-03-03T12:00:00Z", "./test.mp4", {})
        queue.update_status(row_id, "UPLOADED")

        events = queue.get_pending()
        assert len(events) == 0


class TestRetry:
    """測試重試邏輯。"""

    def test_increment_retry(self, queue):
        """increment_retry → retry_count += 1，next_retry 在未來。"""
        row_id = queue.enqueue("node1", "2026-03-03T12:00:00Z", "./test.mp4", {})

        queue.increment_retry(row_id)

        # 直接查詢資料庫
        import sqlite3

        conn = sqlite3.connect(":memory:", check_same_thread=False)
        cursor = queue._conn.cursor()
        cursor.execute("SELECT retry_count, next_retry FROM upload_queue WHERE id = ?", (row_id,))
        row = cursor.fetchone()

        assert row[0] == 1  # retry_count
        assert row[1] is not None  # next_retry

    def test_exponential_backoff(self, queue):
        """retry 多次，驗證退避時間 1s, 2s, 4s, 8s, 16s, 32s, 60s（cap）。"""
        row_id = queue.enqueue("node1", "2026-03-03T12:00:00Z", "./test.mp4", {})

        expected_delays = [2, 4, 8, 16, 32, 60, 60]  # 2^1, 2^2, ... capped at 60

        for i, expected_delay in enumerate(expected_delays):
            queue.increment_retry(row_id)

            cursor = queue._conn.cursor()
            cursor.execute(
                "SELECT retry_count, next_retry FROM upload_queue WHERE id = ?",
                (row_id,),
            )
            row = cursor.fetchone()
            retry_count = row[0]
            next_retry_str = row[1]

            assert retry_count == i + 1

            # 驗證 next_retry 大約在 expected_delay 秒後
            next_retry = datetime.fromisoformat(next_retry_str)
            now = datetime.now()
            actual_delay = (next_retry - now).total_seconds()
            assert actual_delay >= expected_delay - 1  # 允許 1 秒誤差
            assert actual_delay <= expected_delay + 1

    def test_next_retry_filter(self, queue):
        """next_retry 在未來 → get_pending 不返回；next_retry 在過去 → 返回。"""
        row_id = queue.enqueue("node1", "2026-03-03T12:00:00Z", "./test.mp4", {})

        # 增加重試，設定 next_retry 在未來
        queue.increment_retry(row_id)

        # next_retry 在未來，get_pending 不應返回
        events = queue.get_pending()
        assert len(events) == 0

        # 直接將 next_retry 設為過去
        past_time = (datetime.now() - timedelta(seconds=10)).isoformat()
        cursor = queue._conn.cursor()
        cursor.execute(
            "UPDATE upload_queue SET next_retry = ? WHERE id = ?",
            (past_time, row_id),
        )
        queue._conn.commit()

        # 現在應該返回
        events = queue.get_pending()
        assert len(events) == 1


class TestDeleteUploaded:
    """測試刪除已上傳記錄。"""

    def test_delete_uploaded(self, queue):
        """enqueue → update to UPLOADED → delete_uploaded → 記錄消失。"""
        row_id = queue.enqueue("node1", "2026-03-03T12:00:00Z", "./test.mp4", {})
        queue.update_status(row_id, "UPLOADED")
        queue.delete_uploaded(row_id)

        # 直接查詢資料庫
        cursor = queue._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM upload_queue WHERE id = ?", (row_id,))
        count = cursor.fetchone()[0]
        assert count == 0


class TestMetadataSerialization:
    """測試 metadata 序列化。"""

    def test_metadata_serialization(self, queue):
        """enqueue 的 metadata dict 在 get_pending 中正確反序列化。"""
        metadata = {
            "visual_confidence": 0.87,
            "audio_db_peak": 102.3,
            "audio_freq_peak_hz": 4500,
            "nested": {"key": "value"},
        }

        row_id = queue.enqueue(
            node_id="node1",
            timestamp="2026-03-03T12:00:00Z",
            mp4_path="./test.mp4",
            metadata=metadata,
        )

        events = queue.get_pending()
        assert len(events) == 1
        assert events[0]["metadata"] == metadata

    def test_metadata_with_unicode(self, queue):
        """metadata 包含 Unicode 字元。"""
        metadata = {"notes": "玻璃破裂偵測", "severity": "高"}

        queue.enqueue("node1", "2026-03-03T12:00:00Z", "./test.mp4", metadata)

        events = queue.get_pending()
        assert events[0]["metadata"]["notes"] == "玻璃破裂偵測"
        assert events[0]["metadata"]["severity"] == "高"