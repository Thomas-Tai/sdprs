"""
邊緣端快照推送模組

每秒擷取一幀 480p JPEG 快照，POST 到中央伺服器供監控牆顯示。
使用背壓控制——若前一張上傳未完成，丟棄新的快照（不排隊）。

使用範例：
    from utils.snapshot import SnapshotPusher

    pusher = SnapshotPusher(config)
    pusher.start()

    # 在主迴圈中呼叫
    if pusher.is_idle:
        pusher.push(jpeg_bytes)

    # 停止
    pusher.stop()
"""

import logging
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

logger = logging.getLogger("snapshot_pusher")


class SnapshotPusher(threading.Thread):
    """
    快照推送線程。

    接收主迴圈傳入的 JPEG bytes，POST 到中央伺服器。
    背壓控制：若前一張上傳未完成，丟棄新的快照。
    """

    # HTTP 超時設定
    CONNECT_TIMEOUT = 3.0
    READ_TIMEOUT = 5.0

    def __init__(self, config: dict):
        """
        初始化快照推送線程。

        Args:
            config: 配置字典，需包含：
                - node_id: 節點 ID
                - server.api_url: API 基礎 URL
                - server.api_key: API 認證金鑰
        """
        super().__init__(daemon=True)

        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required. Install with: pip install httpx")

        self._config = config

        self._node_id = config.get("node_id", "edge_node")
        self._api_url = config.get("server", {}).get("api_url", "http://localhost:8000/api")
        self._api_key = config.get("server", {}).get("api_key", "")

        # 快照資料和狀態
        self._jpeg_bytes: Optional[bytes] = None
        self._is_idle = True
        self._lock = threading.Lock()
        self._new_snapshot_event = threading.Event()
        self._stop_event = threading.Event()

        # HTTP 客戶端
        self._client: Optional[httpx.Client] = None

        self.name = "SnapshotPusher"
        logger.info(f"SnapshotPusher initialized: node_id={self._node_id}")

    @property
    def is_idle(self) -> bool:
        """返回目前是否空閒（可接受新快照）。"""
        return self._is_idle

    def push(self, jpeg_bytes: bytes) -> bool:
        """
        推送快照。

        若目前非 idle，靜默丟棄（不排隊）。

        Args:
            jpeg_bytes: JPEG 影像 bytes

        Returns:
            是否接受（True = 已接受，False = 丟棄）
        """
        with self._lock:
            if not self._is_idle:
                logger.debug("Snapshot dropped (previous upload still in progress)")
                return False

            self._jpeg_bytes = jpeg_bytes
            self._is_idle = False
            self._new_snapshot_event.set()

        logger.debug(f"Snapshot accepted: size={len(jpeg_bytes)} bytes")
        return True

    def run(self):
        """主迴圈。"""
        # 建立 HTTP 客戶端
        self._client = httpx.Client(
            timeout=httpx.Timeout(self.READ_TIMEOUT, connect=self.CONNECT_TIMEOUT),
            headers={
                "X-API-Key": self._api_key,
                "Content-Type": "image/jpeg",
            },
        )

        logger.info("SnapshotPusher started")

        while not self._stop_event.is_set():
            # 等待新快照
            self._new_snapshot_event.wait(timeout=1.0)

            if self._stop_event.is_set():
                break

            # 取得快照資料
            with self._lock:
                jpeg_bytes = self._jpeg_bytes
                self._jpeg_bytes = None
                self._new_snapshot_event.clear()

            if jpeg_bytes:
                self._upload_snapshot(jpeg_bytes)

            # 設為 idle
            with self._lock:
                self._is_idle = True

        # 清理
        if self._client:
            self._client.close()

        logger.info("SnapshotPusher stopped")

    def stop(self):
        """停止線程。"""
        self._stop_event.set()
        self._new_snapshot_event.set()  # 喚醒等待中的線程

    def _upload_snapshot(self, jpeg_bytes: bytes):
        """
        上傳快照到伺服器。

        Args:
            jpeg_bytes: JPEG 影像 bytes
        """
        url = f"{self._api_url}/edge/{self._node_id}/snapshot"

        try:
            response = self._client.post(url, content=jpeg_bytes)

            if response.status_code == 204:
                logger.debug(f"Snapshot uploaded: size={len(jpeg_bytes)} bytes")
            else:
                logger.debug(
                    f"Snapshot upload failed: status={response.status_code}, "
                    f"response={response.text[:100]}"
                )

        except httpx.TimeoutException:
            logger.debug("Snapshot upload timeout")

        except httpx.ConnectError:
            logger.debug("Snapshot upload connection error")

        except Exception as e:
            logger.debug(f"Snapshot upload error: {e}")


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)

    # 測試用配置
    config = {
        "node_id": "glass_node_01",
        "server": {
            "api_url": "http://localhost:8000/api",
            "api_key": "test-api-key",
        },
    }

    # 建立假 JPEG 資料
    fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100 + b"\xff\xd9"

    try:
        pusher = SnapshotPusher(config)
        pusher.start()

        print("SnapshotPusher running. Testing push...")

        for i in range(10):
            if pusher.is_idle:
                pusher.push(fake_jpeg)
                print(f"Push {i}: accepted")
            else:
                print(f"Push {i}: dropped")
            time.sleep(0.5)

        print("Waiting for uploads to complete...")
        time.sleep(3)

        pusher.stop()
        pusher.join(timeout=5)

        print("Done.")

    except ImportError as e:
        print(f"Import error: {e}")
        print("Please install required packages: pip install httpx")