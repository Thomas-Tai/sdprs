"""
UploadWorker 單元測試

驗證上傳工作線程的失敗處理：
    - 4xx（非 429）→ 終態 FAILED，不再重試
    - 429 → 可重試（退避），仍為 QUEUED
    - 5xx 超過 MAX_RETRIES → 終態 FAILED
    - MP4 遺失 → 終態 FAILED（非誤標 UPLOADED）
    - 204 → UPLOADED（成功路徑）

使用真實的 in-memory EventQueue，並注入 FakeClient 避免任何網路呼叫。
"""

import pytest

# httpx 為 UploadWorker.__init__ 的硬性依賴（已安裝）；若缺失則跳過整個模組。
pytest.importorskip("httpx")

from comms.api_uploader import UploadWorker
from comms.event_queue import EventQueue


CONFIG = {"server": {"api_url": "http://x/api", "api_key": "k"}}


class FakeResponse:
    """假的 httpx 回應物件，僅提供 status_code / json() / text。"""

    def __init__(self, status_code: int, payload: dict = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class FakeClient:
    """假的 httpx client，post/put 依設定回傳 FakeResponse，不做任何網路。"""

    def __init__(self, status=200, payload=None, text=""):
        self._status = status
        self._payload = payload if payload is not None else {"alert_id": "42"}
        self._text = text
        self.calls = []

    def post(self, url, json=None):
        self.calls.append(("post", url, json))
        return FakeResponse(self._status, self._payload, self._text)

    def put(self, url, files=None):
        self.calls.append(("put", url, "<files>"))
        return FakeResponse(self._status, self._payload, self._text)


@pytest.fixture
def queue():
    """建立 in-memory EventQueue 實例。"""
    q = EventQueue(":memory:")
    yield q
    q.close()


def _status(queue, row_id):
    """直接查詢某 row 的狀態字串（get_pending 會過濾終態/退避中的列）。"""
    cursor = queue._conn.cursor()
    cursor.execute("SELECT status FROM upload_queue WHERE id = ?", (row_id,))
    row = cursor.fetchone()
    return row[0] if row else None


def _make_worker(queue, client):
    """建立 worker 並注入 FakeClient，不啟動線程/真實 client。"""
    worker = UploadWorker(queue, CONFIG)
    worker._client = client
    return worker


def _pending_ids(queue):
    return {e["id"] for e in queue.get_pending()}


def test_4xx_json_marked_failed_not_retried(queue):
    """403（4xx 非 429）→ JSON 上傳後該列狀態為 FAILED 且不再出現在 get_pending。"""
    row_id = queue.enqueue("glass_node_01", "2026-03-03T12:00:00Z", "./x.mp4", {})
    event = queue.get_pending()[0]

    worker = _make_worker(queue, FakeClient(status=403, text="forbidden node"))
    result = worker._upload_json(event)

    assert result is False
    assert _status(queue, row_id) == "FAILED"
    # 終態 → 不再被重新派送
    assert row_id not in _pending_ids(queue)


def test_429_json_is_retried_not_failed(queue):
    """429 → 可重試：狀態仍為 QUEUED（非 FAILED），且 retry_count 遞增、設定 next_retry。"""
    row_id = queue.enqueue("glass_node_01", "2026-03-03T12:00:00Z", "./x.mp4", {})
    event = queue.get_pending()[0]

    worker = _make_worker(queue, FakeClient(status=429, text="slow down"))
    result = worker._upload_json(event)

    assert result is False
    # 仍為 QUEUED（可重試），未被標為 FAILED
    assert _status(queue, row_id) == "QUEUED"

    cursor = queue._conn.cursor()
    cursor.execute("SELECT retry_count, next_retry FROM upload_queue WHERE id = ?", (row_id,))
    retry_count, next_retry = cursor.fetchone()
    assert retry_count == 1
    assert next_retry is not None
    # 因退避，暫時不會被 get_pending 派送
    assert row_id not in _pending_ids(queue)


def test_5xx_json_retries_then_fails_after_max(queue):
    """retry_count 已達 MAX_RETRIES-1 時，再一次 500 → 轉為終態 FAILED。"""
    row_id = queue.enqueue("glass_node_01", "2026-03-03T12:00:00Z", "./x.mp4", {})
    event = queue.get_pending()[0]
    # 模擬此列已退避重試 MAX_RETRIES-1 次
    event["retry_count"] = UploadWorker.MAX_RETRIES - 1

    worker = _make_worker(queue, FakeClient(status=500))
    result = worker._upload_json(event)

    assert result is False
    assert _status(queue, row_id) == "FAILED"
    assert row_id not in _pending_ids(queue)


def test_5xx_json_below_max_still_retries(queue):
    """retry_count 尚未到上限時 500 → 退避重試，狀態仍 QUEUED。"""
    row_id = queue.enqueue("glass_node_01", "2026-03-03T12:00:00Z", "./x.mp4", {})
    event = queue.get_pending()[0]  # retry_count == 0

    worker = _make_worker(queue, FakeClient(status=500))
    result = worker._upload_json(event)

    assert result is False
    assert _status(queue, row_id) == "QUEUED"
    cursor = queue._conn.cursor()
    cursor.execute("SELECT retry_count FROM upload_queue WHERE id = ?", (row_id,))
    assert cursor.fetchone()[0] == 1


def test_video_missing_mp4_marked_failed(queue):
    """JSON_SENT 事件但 mp4_path 指向不存在檔案 → _upload_video 標記 FAILED（非 UPLOADED）。"""
    row_id = queue.enqueue(
        "glass_node_01", "2026-03-03T12:00:00Z", "./does_not_exist_12345.mp4", {}
    )
    queue.update_status(row_id, "JSON_SENT", event_id="42")
    event = queue.get_pending()[0]
    assert event["status"] == "JSON_SENT"

    worker = _make_worker(queue, FakeClient(status=204))
    result = worker._upload_video(event)

    assert result is False
    assert _status(queue, row_id) == "FAILED"
    assert row_id not in _pending_ids(queue)


def test_video_204_marks_uploaded(queue, tmp_path):
    """成功路徑：put 回傳 204 → 狀態 UPLOADED。"""
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"\x00\x01\x02fake mp4 bytes")

    row_id = queue.enqueue("glass_node_01", "2026-03-03T12:00:00Z", str(mp4), {})
    queue.update_status(row_id, "JSON_SENT", event_id="42")
    event = queue.get_pending()[0]

    worker = _make_worker(queue, FakeClient(status=204))
    result = worker._upload_video(event)

    assert result is True
    assert _status(queue, row_id) == "UPLOADED"
    assert row_id not in _pending_ids(queue)


def test_video_4xx_marked_failed(queue, tmp_path):
    """影片 4xx（非 429）→ 終態 FAILED，不再重試。"""
    mp4 = tmp_path / "clip.mp4"
    mp4.write_bytes(b"fake")

    row_id = queue.enqueue("glass_node_01", "2026-03-03T12:00:00Z", str(mp4), {})
    queue.update_status(row_id, "JSON_SENT", event_id="42")
    event = queue.get_pending()[0]

    worker = _make_worker(queue, FakeClient(status=422, text="unprocessable"))
    result = worker._upload_video(event)

    assert result is False
    assert _status(queue, row_id) == "FAILED"
    assert row_id not in _pending_ids(queue)


def test_json_200_marks_json_sent(queue):
    """成功路徑：post 回傳 200 → 狀態 JSON_SENT 並記錄 alert_id。"""
    row_id = queue.enqueue("glass_node_01", "2026-03-03T12:00:00Z", "./x.mp4", {})
    event = queue.get_pending()[0]

    worker = _make_worker(queue, FakeClient(status=200, payload={"alert_id": "99"}))
    result = worker._upload_json(event)

    assert result is True
    assert _status(queue, row_id) == "JSON_SENT"
    cursor = queue._conn.cursor()
    cursor.execute("SELECT event_id FROM upload_queue WHERE id = ?", (row_id,))
    assert cursor.fetchone()[0] == "99"
