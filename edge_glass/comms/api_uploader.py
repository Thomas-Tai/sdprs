"""
邊緣端上傳工作線程模組

負責將本地佇列中的事件（JSON + MP4）上傳到中央伺服器。

上傳流程：
    QUEUED ──[POST /api/alerts]──> JSON_SENT ──[PUT /api/alerts/{id}/video]──> UPLOADED

使用範例：
    from comms.event_queue import EventQueue
    from comms.api_uploader import UploadWorker

    queue = EventQueue("event_queue.db")
    worker = UploadWorker(queue, config)
    worker.start()

    # 停止
    worker.stop()
"""

import logging
import os
import threading
import time
from typing import Optional

# 嘗試導入 httpx
try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None

from comms.event_queue import EventQueue

logger = logging.getLogger("api_uploader")


class UploadWorker(threading.Thread):
    """
    上傳工作線程。

    掃描 SQLite 佇列，將事件依序上傳到中央伺服器。
    """

    # 掃描間隔（秒）
    SCAN_INTERVAL = 1

    # HTTP 超時設定
    CONNECT_TIMEOUT = 5.0
    READ_TIMEOUT = 30.0

    def __init__(self, event_queue: EventQueue, config: dict):
        """
        初始化上傳工作線程。

        Args:
            event_queue: EventQueue 實例
            config: 配置字典，需包含：
                - server.api_url: API 基礎 URL
                - server.api_key: API 認證金鑰
        """
        super().__init__(daemon=True)

        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required. Install with: pip install httpx")

        self._queue = event_queue
        self._config = config

        self._api_url = config.get("server", {}).get("api_url", "http://localhost:8000/api")
        self._api_key = config.get("server", {}).get("api_key", "")

        self._running = False
        self._stop_event = threading.Event()

        # HTTP 客戶端
        self._client: Optional[httpx.Client] = None

        self.name = "UploadWorker"
        logger.info(f"UploadWorker initialized: api_url={self._api_url}")

    def run(self):
        """主迴圈。"""
        self._running = True

        # 建立 HTTP 客戶端
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=self.CONNECT_TIMEOUT,
                read=self.READ_TIMEOUT,
            ),
            headers={
                "X-API-Key": self._api_key,
            },
        )

        logger.info("UploadWorker started")

        while not self._stop_event.is_set():
            try:
                self._process_events()
            except Exception as e:
                logger.error(f"Error in upload loop: {e}")

            # 等待下次掃描
            self._stop_event.wait(self.SCAN_INTERVAL)

        # 清理
        if self._client:
            self._client.close()

        logger.info("UploadWorker stopped")

    def stop(self):
        """停止工作線程。"""
        self._running = False
        self._stop_event.set()

    def _process_events(self):
        """處理待上傳事件。"""
        events = self._queue.get_pending()

        for event in events:
            if self._stop_event.is_set():
                break

            row_id = event["id"]
            status = event["status"]

            if status == "QUEUED":
                self._upload_json(event)
            elif status == "JSON_SENT":
                self._upload_video(event)

    def _upload_json(self, event: dict) -> bool:
        """
        上傳 JSON metadata。

        Args:
            event: 事件字典

        Returns:
            是否成功
        """
        row_id = event["id"]
        node_id = event["node_id"]
        timestamp = event["timestamp"]
        metadata = event.get("metadata", {})

        # 構建請求 body
        body = {
            "node_id": node_id,
            "timestamp": timestamp,
            "visual_confidence": metadata.get("visual_confidence", 0.0),
            "audio_db_peak": metadata.get("audio_db_peak", 0.0),
            "audio_freq_peak_hz": metadata.get("audio_freq_peak_hz", 0.0),
        }

        url = f"{self._api_url}/alerts"

        try:
            response = self._client.post(url, json=body)

            if response.status_code == 200:
                data = response.json()
                alert_id = data.get("alert_id")
                self._queue.update_status(row_id, "JSON_SENT", event_id=str(alert_id))
                logger.info(f"JSON uploaded: row_id={row_id}, alert_id={alert_id}")
                return True

            elif 400 <= response.status_code < 500:
                # 4xx 錯誤：不重試
                logger.error(
                    f"JSON upload failed (client error): row_id={row_id}, "
                    f"status={response.status_code}, response={response.text}"
                )
                return False

            else:
                # 5xx 錯誤：重試
                logger.warning(
                    f"JSON upload failed (server error): row_id={row_id}, "
                    f"status={response.status_code}"
                )
                self._queue.increment_retry(row_id)
                return False

        except httpx.TimeoutException:
            logger.warning(f"JSON upload timeout: row_id={row_id}")
            self._queue.increment_retry(row_id)
            return False

        except httpx.ConnectError as e:
            logger.warning(f"JSON upload connection error: row_id={row_id}, error={e}")
            self._queue.increment_retry(row_id)
            return False

        except Exception as e:
            logger.error(f"JSON upload error: row_id={row_id}, error={e}")
            self._queue.increment_retry(row_id)
            return False

    def _upload_video(self, event: dict) -> bool:
        """
        上傳 MP4 影片。

        Args:
            event: 事件字典

        Returns:
            是否成功
        """
        row_id = event["id"]
        event_id = event.get("event_id")
        mp4_path = event.get("mp4_path")

        if not event_id:
            logger.error(f"Missing event_id for video upload: row_id={row_id}")
            return False

        if not mp4_path or not os.path.exists(mp4_path):
            logger.error(f"MP4 file not found: row_id={row_id}, path={mp4_path}")
            # 標記為已上傳避免重複嘗試（檔案已遺失）
            self._queue.update_status(row_id, "UPLOADED")
            return False

        url = f"{self._api_url}/alerts/{event_id}/video"

        try:
            with open(mp4_path, "rb") as f:
                files = {"file": (os.path.basename(mp4_path), f, "video/mp4")}
                response = self._client.put(url, files=files)

            if response.status_code == 204:
                self._queue.update_status(row_id, "UPLOADED")
                logger.info(f"Video uploaded: row_id={row_id}, alert_id={event_id}")

                # 可選：刪除本地 MP4
                # self._delete_local_mp4(mp4_path)

                return True

            elif 400 <= response.status_code < 500:
                # 4xx 錯誤：不重試
                logger.error(
                    f"Video upload failed (client error): row_id={row_id}, "
                    f"status={response.status_code}, response={response.text}"
                )
                return False

            else:
                # 5xx 錯誤：重試
                logger.warning(
                    f"Video upload failed (server error): row_id={row_id}, "
                    f"status={response.status_code}"
                )
                self._queue.increment_retry(row_id)
                return False

        except httpx.TimeoutException:
            logger.warning(f"Video upload timeout: row_id={row_id}")
            self._queue.increment_retry(row_id)
            return False

        except httpx.ConnectError as e:
            logger.warning(f"Video upload connection error: row_id={row_id}, error={e}")
            self._queue.increment_retry(row_id)
            return False

        except Exception as e:
            logger.error(f"Video upload error: row_id={row_id}, error={e}")
            self._queue.increment_retry(row_id)
            return False

    def _delete_local_mp4(self, mp4_path: str):
        """刪除本地 MP4 檔案（上傳成功後）。"""
        try:
            if os.path.exists(mp4_path):
                os.remove(mp4_path)
                logger.debug(f"Deleted local MP4: {mp4_path}")
        except Exception as e:
            logger.warning(f"Failed to delete local MP4: {mp4_path}, error={e}")


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)

    # 測試用配置
    config = {
        "server": {
            "api_url": "http://localhost:8000/api",
            "api_key": "test-api-key",
        },
    }

    # 建立事件佇列
    queue = EventQueue(":memory:")

    # 插入測試事件
    queue.enqueue(
        node_id="glass_node_01",
        timestamp="2026-03-03T12:00:00Z",
        mp4_path="./test.mp4",
        metadata={"visual_confidence": 0.87, "audio_db_peak": 102.3, "audio_freq_peak_hz": 4500},
    )

    # 建立上傳工作線程
    try:
        worker = UploadWorker(queue, config)
        worker.start()

        print("UploadWorker running. Press Ctrl+C to stop.")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping...")
        worker.stop()
        worker.join(timeout=5)
        queue.close()

    except ImportError as e:
        print(f"Import error: {e}")
        print("Please install required packages: pip install httpx")