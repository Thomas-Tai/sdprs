"""
邊緣端本地事件佇列模組

使用 SQLite 儲存待上傳的警報事件，支援指數退避重試。

狀態機：
    QUEUED ──[JSON 上傳成功]──> JSON_SENT ──[MP4 上傳成功]──> UPLOADED

使用範例：
    from comms.event_queue import EventQueue

    queue = EventQueue("event_queue.db")
    row_id = queue.enqueue("glass_node_01", "2026-03-03T12:00:00Z", "./events/video.mp4", {"visual_confidence": 0.87})

    events = queue.get_pending()
    for event in events:
        print(event)

    queue.update_status(row_id, "JSON_SENT", event_id="42")
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger("event_queue")


class EventQueue:
    """
    本地事件持久化佇列。

    使用 SQLite 儲存待上傳的警報事件。
    """

    # 指數退避最大延遲（秒）
    MAX_BACKOFF_SECONDS = 60

    def __init__(self, db_path: str = "event_queue.db"):
        """
        初始化事件佇列。

        Args:
            db_path: SQLite 資料庫檔案路徑
        """
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

        # 初始化資料庫
        self._init_db()

        logger.info(f"EventQueue initialized: {db_path}")

    def _init_db(self):
        """初始化資料庫連線和表格。"""
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

        cursor = self._conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS upload_queue (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id    TEXT,
                node_id     TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'QUEUED',
                mp4_path    TEXT NOT NULL,
                metadata    TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                next_retry  TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)

        # 建立索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_upload_queue_status
            ON upload_queue(status)
        """)

        self._conn.commit()
        logger.debug("Database initialized")

    def enqueue(
        self,
        node_id: str,
        timestamp: str,
        mp4_path: str,
        metadata: Dict,
    ) -> int:
        """
        插入一筆新事件到佇列。

        Args:
            node_id: 節點 ID
            timestamp: 事件時間戳（ISO 格式）
            mp4_path: MP4 檔案路徑
            metadata: 事件元資料字典

        Returns:
            新插入的 row id
        """
        metadata_json = json.dumps(metadata)

        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                INSERT INTO upload_queue (node_id, timestamp, mp4_path, metadata, status)
                VALUES (?, ?, ?, ?, 'QUEUED')
                """,
                (node_id, timestamp, mp4_path, metadata_json),
            )
            self._conn.commit()
            row_id = cursor.lastrowid

        logger.info(f"Event enqueued: id={row_id}, node={node_id}, mp4={mp4_path}")
        return row_id

    def get_pending(self) -> List[Dict]:
        """
        查詢待處理的事件。

        返回 status IN ('QUEUED', 'JSON_SENT') 且
        (next_retry IS NULL OR next_retry <= now()) 的事件，
        按 created_at ASC 排序。

        Returns:
            事件字典列表
        """
        now = datetime.now().isoformat()

        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT * FROM upload_queue
                WHERE status IN ('QUEUED', 'JSON_SENT')
                  AND (next_retry IS NULL OR next_retry <= ?)
                ORDER BY created_at ASC
                """,
                (now,),
            )
            rows = cursor.fetchall()

        events = []
        for row in rows:
            event = dict(row)
            # 反序列化 metadata
            if event.get("metadata"):
                try:
                    event["metadata"] = json.loads(event["metadata"])
                except json.JSONDecodeError:
                    event["metadata"] = {}
            events.append(event)

        return events

    def update_status(
        self,
        row_id: int,
        status: str,
        event_id: Optional[str] = None,
    ):
        """
        更新事件狀態。

        Args:
            row_id: 事件 row id
            status: 新狀態（QUEUED, JSON_SENT, UPLOADED）
            event_id: 伺服器返回的 alert_id（可選）
        """
        with self._lock:
            cursor = self._conn.cursor()

            if event_id is not None:
                cursor.execute(
                    """
                    UPDATE upload_queue
                    SET status = ?, event_id = ?
                    WHERE id = ?
                    """,
                    (status, event_id, row_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE upload_queue
                    SET status = ?
                    WHERE id = ?
                    """,
                    (status, row_id),
                )

            self._conn.commit()

        logger.debug(f"Event {row_id} status updated to: {status}")

    def increment_retry(self, row_id: int):
        """
        增加重試計數並設定下次重試時間（指數退避）。

        Args:
            row_id: 事件 row id
        """
        with self._lock:
            cursor = self._conn.cursor()

            # 取得當前 retry_count
            cursor.execute(
                "SELECT retry_count FROM upload_queue WHERE id = ?",
                (row_id,),
            )
            row = cursor.fetchone()
            if not row:
                return

            retry_count = row["retry_count"] + 1

            # 計算指數退避時間
            delay = min(2**retry_count, self.MAX_BACKOFF_SECONDS)
            next_retry = (datetime.now() + timedelta(seconds=delay)).isoformat()

            cursor.execute(
                """
                UPDATE upload_queue
                SET retry_count = ?, next_retry = ?
                WHERE id = ?
                """,
                (retry_count, next_retry, row_id),
            )

            self._conn.commit()

        logger.warning(f"Event {row_id} retry incremented to {retry_count}, next retry at {next_retry}")

    def delete_uploaded(self, row_id: int):
        """
        刪除已上傳的事件記錄。

        Args:
            row_id: 事件 row id
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("DELETE FROM upload_queue WHERE id = ?", (row_id,))
            self._conn.commit()

        logger.debug(f"Event {row_id} deleted from queue")

    def close(self):
        """關閉資料庫連線。"""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("EventQueue closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)

    # 測試
    with EventQueue(":memory:") as queue:
        # 插入事件
        row_id = queue.enqueue(
            node_id="glass_node_01",
            timestamp="2026-03-03T12:00:00Z",
            mp4_path="./events/test.mp4",
            metadata={
                "visual_confidence": 0.87,
                "audio_db_peak": 102.3,
                "audio_freq_peak_hz": 4500,
            },
        )
        print(f"Enqueued event: row_id={row_id}")

        # 查詢待處理
        events = queue.get_pending()
        print(f"\nPending events: {len(events)}")
        for event in events:
            print(f"  - id={event['id']}, status={event['status']}, metadata={event['metadata']}")

        # 更新狀態
        queue.update_status(row_id, "JSON_SENT", event_id="42")
        print(f"\nUpdated to JSON_SENT with event_id=42")

        # 再次查詢（JSON_SENT 仍應在佇列中）
        events = queue.get_pending()
        print(f"Pending events after update: {len(events)}")

        # 標記為已上傳
        queue.update_status(row_id, "UPLOADED")

        # 查詢（UPLOADED 不應在佇列中）
        events = queue.get_pending()
        print(f"Pending events after UPLOADED: {len(events)}")

        # 測試重試
        row_id2 = queue.enqueue(
            node_id="glass_node_01",
            timestamp="2026-03-03T12:01:00Z",
            mp4_path="./events/test2.mp4",
            metadata={"visual_confidence": 0.95},
        )
        queue.increment_retry(row_id2)
        queue.increment_retry(row_id2)
        queue.increment_retry(row_id2)

        events = queue.get_pending()
        print(f"\nPending after retries (should be empty due to next_retry): {len(events)}")