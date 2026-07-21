# Webcam Client 設計文件

**日期：** 2026-07-21
**狀態：** 已核准；Task 1–3 已實作，其餘待實作
**修訂：** 2026-07-21（第二版，稽核後）

---

## 修訂記錄 — 2026-07-21 第二版

第一版與已核准的 brainstorming 設計、以及實際程式碼之間存在斷層。完整稽核見
`docs/superpowers/reviews/2026-07-21-webcam-spec-plan-audit.md`（3 critical、
3 high、8 medium、4 low，全部經實際程式碼驗證）。

**斷層的根源：** brainstorming 核准的設計中有一句關鍵要求：

> 現有 `nodes` 表新增欄位：`node_type`、`api_key_hash`
> **現有 edge node 的 X-API-Key 驗證邏輯擴展為也查 webcam 節點**

第二句在撰寫第一版 spec 時遺失。因此 1Hz JPEG 推送（本功能 99% 時間的預設模式）
會對每一次請求回傳 401，而且 client 端未呼叫 `raise_for_status()`，錯誤被靜默
吞掉、tray 圖示仍顯示綠色。三個 critical 問題全源自這一句遺失。

**本版的關鍵決策：不採用「擴展 `nodes` 表」的原方案。** 改為給 webcam 一條
獨立的 ingest 路由。這是**刻意偏離**已核准設計，理由記錄於 §資料模型；下次稽核
不應再將此列為遺失需求。

| 決策 | 內容 |
|------|------|
| Ingest 認證 | 新增 `POST /api/webcam/{node_id}/snapshot`，以 `verify_webcam_api_key` 驗證。edge 路徑完全不動 |
| Viewer 生命週期 | 租約（lease）+ 續約，取代「只在按下停止鈕時遞減」 |
| 串流就緒判定 | 輪詢 playlist，30 秒上限，取代固定 3 秒等待 |
| Client 金鑰保護 | Windows DPAPI 加密，取代明文儲存 |
| 攝影機預覽 | 恢復（第一版計畫中被靜默移除） |

**與已實作程式碼的對齊：** Task 1（`6e08de0`）、Task 2（`d8945ea`）、
Task 3（`728028b`）已提交。本版 spec 已就其實際行為修正，而非要求回頭改動可運作
的程式碼——例如端點名稱以實作的 `/api/nodes/webcam` 為準。

## 概述

為 SDPRS Dashboard 的 Monitor Wall 新增「Webcam 連線」功能。任一 Windows 電腦運行單一 exe 後，可將本機 webcam / USB webcam 畫面透過網路上傳到 Dashboard，與現有 Edge Cam 並列顯示。

## 需求摘要

| 項目 | 決定 |
|------|------|
| 影片模式 | 平時 1Hz JPEG（複用現有 snapshot 管線）+ 按需 H.264 HLS 串流 |
| 驗證 | Dashboard 產生 API Key → Client 填入。沿用 `X-API-Key` 標頭形式，但**憑證與驗證路徑與 edge node 完全分離**：edge 用單一全域金鑰，webcam 每台 Client PC 一把可獨立撤銷的金鑰（見 §資料模型） |
| Client UI | 首次設定精靈（tkinter）→ 背景運行 + system tray，可隨時重開設定 |
| 多攝影機 | 單一 exe 管理多支 webcam，各自獨立 node_id |
| 平台 | Windows exe（PyInstaller --onefile） |
| 網路 | 跨網際網路，HTTPS/TLS 必要；Client 全為 outbound 連線，無需開放 port |
| Dashboard 呈現 | Monitor Wall 同頁面，tile 標示「Webcam」/「Edge Cam」badge |
| 頻寬優化 | 按需串流 + 動態幀率（帧差偵測）+ 低解析度預設（640×480, Q40, 5-10fps） |

## 系統架構

```
┌──────────────────────────────────────────────────────────────┐
│  Webcam Client (Windows exe, PyInstaller)                    │
│                                                              │
│  ┌─────────┐ ┌─────────┐   ┌────────────────────────────┐   │
│  │ Cam 0   │ │ Cam 1   │   │  GUI (tkinter) + Tray      │   │
│  │ OpenCV  │ │ OpenCV  │   │  設定精靈 / 狀態 / 預覽    │   │
│  └────┬────┘ └────┬────┘   └────────────────────────────┘   │
│       │            │                                         │
│  ┌────▼────────────▼──────────────────────────────────────┐  │
│  │  Per-Camera Push Engine                                │  │
│  │                                                        │  │
│  │  [平時] 1Hz JPEG → POST /api/webcam/{id}/snapshot   │  │
│  │                                                        │  │
│  │  [按需] 收到 stream_start →                           │  │
│  │         OpenCV → FFmpeg subprocess → H.264 HLS        │  │
│  │         → .ts segments PUT 到 server                  │  │
│  │         → 無人觀看 → stream_stop → 回到 1Hz           │  │
│  │                                                        │  │
│  │  [動態幀率] 帧差偵測：靜止跳幀，動作時全幀率          │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Control Channel (HTTP Long-Poll, 每 5s)              │  │
│  │  接收: stream_start / stream_stop                      │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTPS (TLS)
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  Central Server (FastAPI)                                    │
│                                                              │
│  現有複用（不修改）:                                         │
│    GET  /api/edge/{node_id}/snapshot/latest  ← Dashboard 讀取│
│    WS   /ws                            ← 新增 webcam 事件    │
│                                                              │
│  新增:                                                       │
│    POST /api/nodes/webcam              ← 建立 Client+產生 Key│
│    POST /api/nodes/{id}/revoke-key     ← 撤銷 Key            │
│    POST /api/webcam/{node_id}/snapshot ← 1Hz JPEG（新增）    │
│    PUT  /api/webcam/{node_id}/hls/{f}  ← Client 上傳 HLS     │
│    GET  /api/webcam/{node_id}/hls/{f}  ← Dashboard 播放 HLS  │
│    POST /api/webcam/{node_id}/stream/start|renew|stop        │
│    GET  /api/webcam/{node_id}/commands?timeout=5  ← Long-poll│
│                                                              │
│  狀態（行程內，單一 worker）:                                │
│    frame_buffer: dict[node_id, jpeg]   ← 現有（webcam 共用） │
│    viewer_lease: dict[node_id, float]  ← 新增（租約到期時間）│
│    command_queue: dict[node_id, Queue] ← 新增               │
│    hls_segments: disk per node_id      ← 新增               │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  Dashboard SPA (React)                                       │
│                                                              │
│  Monitor Wall tile:                                          │
│  - 預設: <img> 1Hz snapshot（現有 SnapshotImage）            │
│  - 即時: <video> + hls.js 播放 HLS                          │
│  - Badge: "Webcam"（藍）/ "Edge Cam"（灰）                  │
│  - 點擊「即時觀看」→ 觸發串流 → 關閉 → 回到 1Hz            │
│                                                              │
│  節點管理:                                                   │
│  - 新增 Webcam → 產生 API Key（僅顯示一次）                 │
│  - 撤銷 Key / 刪除節點                                      │
└──────────────────────────────────────────────────────────────┘
```

## 按需串流流程

1. Dashboard webcam tile 預設顯示 1Hz JPEG（與 Edge Cam 一致）。該 JPEG 由
   Client 推到 `POST /api/webcam/{id}/snapshot`，Dashboard 仍由既有的
   `GET /api/edge/{id}/snapshot/latest` 讀取（共用同一份 frame buffer）
2. 操作員點擊「即時觀看」→ `POST /api/webcam/{id}/stream/start`
3. Server 建立 90 秒 viewer 租約；若 viewer 數 0→1，將 `stream_start` 放入
   該攝影機的 command queue
4. Client 下次 long-poll（≤5s）取得指令 → 啟動 FFmpeg 編碼
5. Client 產生 .ts 分段（每段 ~2s）→ PUT 上傳到 server
6. Server 寫入 `storage/hls/{node_id}/`，更新 playlist.m3u8（保留最近 5 段）
7. Dashboard 自 step 2 起每秒輪詢 playlist；**首次 200 才切入 Live** 並掛上
   hls.js。30 秒內未就緒 → 「Webcam 未回應」→ 回到 snapshot 並送出 stop
8. Live 期間 Dashboard 每 30s `POST .../stream/renew` 續約
9. 操作員關閉即時模式 → `POST .../stream/stop` → 釋放租約 → 若 viewer 歸零，
   command queue 放入 `stream_stop`
10. Client 取得 stop → 終止 FFmpeg → 回到 1Hz JPEG
11. **安全機制：租約過期（90 秒未續約）等同離開。** viewer 歸零時 server
    **必須送出 `stream_stop` 指令給 Client**，而不只是刪除自己的目錄——否則
    Client 毫不知情，會無限期繼續編碼與上傳。之後 60 秒清除 HLS 目錄

## Webcam Client 設計

### 技術選型

| 項目 | 選擇 | 理由 |
|------|------|------|
| 語言 | Python 3.11+ | 與 edge_glass 一致 |
| 攝影機擷取 | OpenCV cv2.VideoCapture | 支援 USB/內建鏡頭 |
| H.264 編碼 | FFmpeg subprocess | 穩定，PyInstaller 可打包 |
| GUI | tkinter + pystray | 零額外依賴、輕量 |
| HTTP | httpx | 與 edge_glass 一致，支援 TLS |
| 打包 | PyInstaller --onefile | 單一 exe |

### 執行緒模型

```
Main Thread: tkinter GUI event loop
├── Thread per Camera: capture + push engine
│   ├── [1Hz mode] grab → JPEG encode → POST snapshot
│   └── [HLS mode] pipe frames → FFmpeg stdin → .ts → PUT upload
├── Thread: Control Channel (HTTP long-poll loop)
│   └── GET /api/webcam/{node_id}/commands?timeout=5
└── Thread: Heartbeat (每 30s)
```

### GUI 行為

**首次啟動（設定精靈）：**
1. 輸入 Server URL（https://...）
2. 貼上 API Key（從 Dashboard 取得，一個 Key 對應此電腦所有攝影機）
3. 點「連線驗證」→ 成功則繼續
4. 自動掃描可用攝影機（cv2.VideoCapture(0..9)）
5. 勾選啟用的攝影機 + 命名
6. 每支顯示即時預覽縮圖
7. 點「開始」→ Client 呼叫 `POST /api/webcam/cameras` 註冊攝影機清單 → server 回傳各 node_id → 寫入設定檔 → 最小化到 tray

**之後啟動：**
- 讀取 `%APPDATA%/SDPRSWebcam/config.json` → 背景運行
- Tray 圖示：綠=連線中、紅=斷線
- Tray 右鍵：開啟設定 / 暫停 / 恢復 / 離開
- 雙擊 tray → 開啟設定視窗

### 動態幀率

```python
diff = cv2.absdiff(current_gray, prev_gray)
motion_ratio = (diff > threshold).sum() / diff.size

if motion_ratio < 0.01:      # 靜止
    effective_fps = 1
elif motion_ratio < 0.05:    # 輕微動作
    effective_fps = 3
else:                        # 明顯動作
    effective_fps = target_fps  # 設定值，預設 8
```

- 1Hz 模式：靜止時可跳過 POST（server 保留上一幀）
- HLS 模式：動態調整 FFmpeg 輸入幀率

### 驗證模型

一個 API Key 對應一個「Webcam Client」（非每支攝影機各一個）。Client 首次連線時向 server 註冊其攝影機清單，server 自動分配 node_id。之後所有請求（snapshot、HLS 上傳、command long-poll）使用同一 API Key + 對應的 node_id。

#### 金鑰驗證 ≠ 擁有權驗證（本版新增）

`verify_webcam_api_key` 只回答「這把金鑰有效嗎」，**不回答**「這個 node_id 屬於
這把金鑰嗎」。URL 中的 `node_id` 是呼叫端可任意指定的輸入。

**每一個以 X-API-Key 驗證、且路徑含 `{node_id}` 的端點，都必須另外檢查擁有權：**

```python
if not any(c["node_id"] == node_id for c in get_webcam_cameras(client_node_id)):
    raise HTTPException(status_code=403, detail="Camera not owned by this client")
```

適用於 `snapshot`、`hls/{filename}` (PUT)、`commands`。

Task 3 的實作在 `upload_hls_segment` 做了這件事，卻在同一檔案下方的
`poll_commands` 漏掉——任何持有**任一**有效金鑰的 Client 都能長輪詢**任一**
攝影機的指令佇列。由於 `asyncio.Queue.get()` 是單一消費者 FIFO，搶先取走的
一方會**靜默吃掉**原本要給正主的 `stream_start`，正主則永遠等不到指令。

這類漏洞不會被「金鑰無效」的測試抓到，因為金鑰是有效的。測試必須明確涵蓋
**跨租戶**情境：以 Client A 的金鑰存取 Client B 的攝影機，預期 403。

### 設定檔（`%APPDATA%/SDPRSWebcam/config.json`）

```json
{
  "server_url": "https://sdprs.example.com",
  "api_key": "sk-...",
  "cameras": [
    {
      "device_index": 0,
      "name": "大門 Webcam",
      "node_id": "webcam_01",
      "resolution": [640, 480],
      "jpeg_quality": 40,
      "target_fps": 8,
      "enabled": true
    }
  ],
  "motion_threshold": 25,
  "heartbeat_interval": 30
}
```

- `node_id` 由 server 於首次註冊時分配，寫回設定檔
- 新增/移除攝影機時，client 呼叫 `POST /api/webcam/cameras` 同步清單到 server

#### API Key 靜態保護 — Windows DPAPI（本版修訂 M8）

上方 `"api_key"` 欄位**不以明文儲存**。第一版直接寫入明文 JSON，任何能登入該
台電腦的使用者都能讀走——現場的櫃台／保安室電腦通常是多人共用帳號的環境。

改用 Windows DPAPI（`CryptProtectData` / `CryptUnprotectData`，經 `ctypes`
呼叫，無需額外套件），以**目前使用者**為範圍加密：

```json
{
  "server_url": "https://sdprs.example.com",
  "api_key_encrypted": "<base64 DPAPI blob>",
  ...
}
```

- 加密繫結到 Windows 使用者帳號；同機其他帳號無法解密
- 取捨：若使用者設定檔毀損或機器重灌，金鑰無法復原。這是可接受的——處理方式
  就是在 Dashboard 重新產生並重貼，本來就是既有流程
- 解密失敗時視同未設定，導向設定精靈，**不要**回退成明文讀取

### 打包產物

`SDPRS_Webcam.exe`（~80-100MB，含 OpenCV + FFmpeg + Python runtime），單檔雙擊即用。

## Server 端變更

### 新增 API 端點

| 端點 | 方法 | 用途 | 認證 |
|------|------|------|------|
| `/api/nodes/webcam` | POST | 建立 Webcam Client + 產生 API Key（一 key 多 cam） | Session |
| `/api/nodes/{id}/revoke-key` | POST | 撤銷並重新產生 Key | Session |
| `/api/webcam/cameras` | POST | Client 註冊/同步攝影機清單，server 分配 node_id | X-API-Key |
| **`/api/webcam/{node_id}/snapshot`** | **POST** | **Client 上傳 1Hz JPEG（本版新增）** | **X-API-Key** |
| `/api/webcam/{node_id}/hls/{filename}` | PUT | Client 上傳 .ts / .m3u8 | X-API-Key |
| `/api/webcam/{node_id}/hls/{filename}` | GET | Dashboard 取得 HLS 檔案 | Session |
| `/api/webcam/{node_id}/stream/start` | POST | 觸發開始串流，開啟 viewer 租約 | Session |
| **`/api/webcam/{node_id}/stream/renew`** | **POST** | **續約（本版新增）** | **Session** |
| `/api/webcam/{node_id}/stream/stop` | POST | 主動停止串流，釋放租約 | Session |
| `/api/webcam/{node_id}/commands` | GET | Client long-poll 取指令 | X-API-Key |

> **端點名稱：** 第一版寫 `POST /api/nodes`，實作為 `POST /api/nodes/webcam`
> （`d8945ea`）。以實作為準——把可運作的端點改回去以遷就過時文件是錯誤方向。

#### 1Hz JPEG ingest（本版新增，修正 C1/C2/C3/H3）

第一版讓 Client 把 1Hz JPEG 推到現有的 `POST /api/edge/{node_id}/snapshot`。
該端點以 `verify_api_key` 驗證，比對的是全域 `EDGE_API_KEY`，而 Client 送的是
每 Client 金鑰——**每一次推送都會 401**。

新端點 `POST /api/webcam/{node_id}/snapshot` 的行為：

1. 以 `verify_webcam_api_key` 驗證，取得已認證的 `client_node_id`
2. 以 `get_webcam_cameras(client_node_id)` 確認該 `node_id` 屬於此 Client
   （**不呼叫 `verify_node_id`**——動態分配的 node_id 無法列入 allowlist）
3. 寫入**與 edge 相同的記憶體 frame buffer**，故 Dashboard 既有的
   `GET /api/edge/{node_id}/snapshot/latest` 讀取路徑完全不變
4. 更新 `webcam_cameras.last_upload = utcnow().isoformat()`

第 3 點是「複用現有 snapshot 管線」的真正意思：共用的是 buffer 與讀取路徑，
不是路由與認證。第 4 點補上第一版完全沒有的 `last_upload` 寫入者——沒有它，
所有 webcam 會永遠顯示離線。

**Client 端必須呼叫 `raise_for_status()`。** 第一版的 `except Exception` 對
401 完全不會觸發（httpx 不會為 4xx 拋例外），導致失敗被靜默吞掉、tray 仍是綠燈。

### 資料模型

**不修改現有 `nodes` 表。** webcam 使用兩張獨立的表（Task 1 已實作）：

```
webcam_clients   node_id, name, api_key_hash, created_at, status
                 ← 一台 Client PC 一列，一把 API Key

webcam_cameras   node_id, client_id (FK), name, device_index,
                 resolution, quality, fps, status, last_upload
                 ← 一支攝影機一列，node_id 由 server 分配
```

`api_key_hash` 為 SHA-256（明文僅在建立/輪替時回傳一次）。兩種 backend
（SQLite / PostgreSQL）皆須支援。

#### 為何刻意偏離「擴展 nodes 表」的原設計

已核准的 brainstorming 設計要求把 `node_type` / `api_key_hash` 加到 `nodes`
表，並擴展 `verify_api_key` 一併查詢 webcam 節點。本版**不採用**，理由如下：

1. **爆炸半徑。** 擴展 `verify_api_key` 會讓每一個 edge node 請求（玻璃破裂
   偵測、抽水泵）都經過一條新的驗證分支。這些節點控制實體抽水設備，其驗證路徑
   是本系統風險最高的程式碼之一，不應為了新增功能而改動。
2. **`nodes` 自動建列的衝突。** `database.py:799–812` 在收到 snapshot 時會以
   `node_type='glass'` 自動建立 `nodes` 列。若 webcam 共用該路徑，同一支攝影機
   會同時存在於 `nodes`（標記為 glass）與 `webcam_cameras`，Monitor Wall 會把
   它顯示**兩次**——一次是「Edge Cam」，一次是「Webcam」。
3. **node_id allowlist 不相容。** `auth.py:94` 的 `verify_node_id` 依
   `ALLOWED_NODE_IDS` 白名單擋下未列出的 node_id。webcam 的 node_id 由 server
   於註冊時動態分配，本質上無法事先列入白名單——共用 edge 路徑等於讓本功能只能
   在「未啟用白名單」的部署上運作，也就是在防護較弱的部署上才會動。
4. **憑證模型本就不同。** edge node 共用一把全域 `EDGE_API_KEY`；webcam 每台
   Client PC 一把可獨立撤銷的金鑰。硬把兩者塞進同一條驗證路徑，會讓
   `revoke-key` 的語意變得含糊。

**取捨：** 系統中並存兩種信任模型。這是刻意的，且以路由清楚切分——
`/api/edge/*` 用全域金鑰，`/api/webcam/*` 用每 Client 金鑰。若日後要統一為全
fleet 每節點憑證，那是一個獨立的遷移專案（需重新佈建已部署的 Pi 5 與 ESP32），
不應夾帶在本功能中。

### HLS 儲存

```
storage/hls/{node_id}/
├── playlist.m3u8
├── seg_000123.ts
├── seg_000124.ts
└── ...（保留最近 5 段，~10 秒）
```

- 串流停止後 60s 清除目錄
- 排程任務每 5 分鐘清理孤兒目錄（無 viewer + 最後 .ts > 60s）

### Control Channel（HTTP Long-Poll）

```
GET /api/webcam/{node_id}/commands?timeout=5
回應: {"command": "stream_start", "params": {"fps": 8}}
      {"command": "stream_stop"}
      {"command": null}
```

- 純 HTTP，穿越任何 proxy/防火牆
- 延遲 ≤5s（操作員點即時後等幾秒開始，可接受）

### Viewer 計數 — 租約模型（本版修訂，修正 H1/H2）

第一版只在操作員按下「● LIVE ✕」時遞減。關掉分頁、切換頁面、瀏覽器當掉、
筆電闔蓋——計數都會**永久停留在 ≥1**，於是「5 分鐘自動停止」永遠不會觸發，
現場 PC 會無限期地持續 H.264 編碼與上傳。在一個把頻寬列為設計約束的系統上，
一個被遺忘的分頁就足以吃掉一條上行線路。

改為租約制：

```
stream/start  → 建立/更新租約，expires = now + 90s
                viewer_count++；若 0→1 → enqueue stream_start
stream/renew  → expires = now + 90s（Dashboard 每 30s 呼叫一次）
stream/stop   → 立即釋放租約
排程掃描      → 每 30s 檢查；expires 已過 → 視為離開
                viewer_count--；若 →0 → enqueue stream_stop
                                      → broadcast webcam_stream_stopped
                                      → 60s 後清除 HLS 目錄
```

90 秒 = 兩次續約失敗，可容忍一次網路抖動而不誤判。

**強制停止必須送出指令給 Client。** 第一版的 `cleanup_stale_streams()` 只刪除
server 端目錄，從不 enqueue `stream_stop`——Client 因此毫不知情，繼續編碼上傳。
清理目錄與停止串流是兩件事，兩件都要做。

**`HLS_VIEWER_TIMEOUT_SECONDS` 必須真正被使用。** 第一版將它讀入區域變數後
從未使用，實際門檻寫死為 60 秒（Task 3 的實作者也獨立發現了這點）。

### WebSocket 新事件

```json
{"type": "webcam_stream_started", "data": {"node_id": "webcam_01"}}
{"type": "webcam_stream_stopped", "data": {"node_id": "webcam_01"}}
```

> **payload 形狀（本版修正 M4）：** 第一版把 `node_id` 放在頂層。SPA 的
> `openSocket` 會把 `msg.data`（存在時）解包後傳給 `onEvent(type, data)`，
> 所以扁平形狀會讓 handler 收到整個訊息物件而非 payload。以巢狀 `data` 為準。

**新增 WS 事件是三處同動的變更，缺一則靜默失效：**

1. server 端 `ws_manager.broadcast({"type": ...})`
2. `api.jsx` 的 `_WS_EVENT_TYPES` 白名單——不在其中的 type 會被
   「unknown type — ignore silently」分支丟棄，**不報錯**
3. `app.jsx` `onEvent` 的 type 分支清單——它是明確列舉，**沒有 default 分支**，
   所以事件會抵達卻什麼也不做

另外 `central_server/tests/test_ws_event_contract.py` 會掃描 `central_server/`
下所有非測試 `.py` 的 broadcast payload type 字面值，任何不在其凍結集合中的
type 都會讓該測試失敗。四個檔案必須同一次提交。

這不是理論風險：`app.jsx` 中 `SHL-1` 註解記錄了一個 `weather` 事件，它在兩個
方向都是死的——不在白名單、也沒有任何後端發送——因此從未執行過。

## Dashboard 變更

### Monitor Wall Tile

- `node_type === "webcam"` → 顯示「Webcam」藍色 badge + 「即時觀看」按鈕
- `node_type !== "webcam"` → 顯示「Edge Cam」灰色 badge，無即時按鈕（現有 HLS 由 edge 管理）

**Tile 狀態機：**
```
[Snapshot] ──點擊即時──→ [Loading] ──playlist 200──→ [Live]
    ↑                        │                          │
    │                        │ 30s 無 playlist          │ 續約每 30s
    │                        ▼                          │
    └───────────────  「Webcam 未回應」  ←──────────────┘
                        錯誤降級 / 關閉 / 租約到期
```

**就緒判定（本版修訂 M5）：** 第一版在呼叫 `stream/start` 後固定等 3 秒就宣告
進入 Live。若 Client 早已離線，操作員會盯著一個永遠不會有畫面的黑色 `<video>`，
而且無法分辨「還在啟動」與「對方已死」。

改為輪詢真正的前提條件：

```
POST stream/start
  → [Loading]
  → 每 1s GET /api/webcam/{id}/hls/playlist.m3u8
      200      → [Live]，掛上 hls.js，開始每 30s 續約
      30s 逾時 → toast「Webcam 未回應」→ 回到 [Snapshot]，並呼叫 stream/stop
```

30 秒上限即 spec §錯誤處理-Server 端所承諾的逾時，第一版並未實作。

**Live 期間必須持續續約。** 每 30s `POST /api/webcam/{id}/stream/renew`；
離開 Live（按鈕、降級、元件卸載）時清除計時器並呼叫 `stream/stop`。

**WS 重連後需重新同步（M6）：** 重連可能錯過 `webcam_stream_stopped`。重連後
應重新確認目前 Live 中的 tile 之串流狀態，避免 tile 停在 Live 卻已無串流。

### 新元件

- **WebcamTile：** 擴展現有 tile，管理 snapshot/live 切換、badge、按鈕
- **HlsPlayer：** `<video>` + hls.js（vendor 於 `static/spa/vendor/hls.min.js`）
  - `liveDurationInfinity: true, maxBufferLength: 5`
  - 錯誤處理：3 次 recover 失敗 → 降回 snapshot + toast

### 節點管理（擴展 status.jsx 或新頁面）

- 「新增 Webcam Client」按鈕 → modal 輸入名稱（如「櫃台電腦」）→ `POST /api/nodes/webcam` → 顯示 API Key（僅一次）
- 管理員將 Key 交給現場人員填入 exe
- Client 首次連線後自動註冊攝影機，Dashboard 節點列表即顯示各 webcam（如「webcam_01 — 大門」）
- 節點列表顯示類型、狀態、操作（撤銷 Key / 刪除）

### 不變的部分

- Edge Cam tile 行為不改
- SnapshotImage 元件不改
- 其他頁面不受影響

## 錯誤處理

### Client 端

| 情境 | 處理 |
|------|------|
| Server 連線失敗 | 指數退避重試（1s→30s 封頂），tray 變紅 |
| API Key 無效 (401) | 停止推送，通知重新設定，不重試。**必須呼叫 `raise_for_status()`**——httpx 不會為 4xx 拋例外，第一版的 `except Exception` 因此永遠不會觸發，401 被靜默吞掉、tray 仍是綠燈 |
| 攝影機斷開 | 該 cam 停止，其他不受影響，每 10s 重試開啟 |
| FFmpeg 崩潰 | 重啟最多 3 次，失敗降回 1Hz |
| HLS 上傳失敗 | 跳過該段，繼續下一段 |
| 電腦休眠喚醒 | 偵測時間跳變 → 重新初始化 |

### Server 端

| 情境 | 處理 |
|------|------|
| Client 斷線（90s 無 snapshot） | 標記 OFFLINE → WS 通知 → tile 顯示離線 |
| HLS 目錄殘留 | 排程清理（無 viewer + >60s） |
| stream/start 後 client 未回應 | 30s 超時 → Dashboard 提示「未回應」→ 回到 snapshot |

### Dashboard 端

| 情境 | 處理 |
|------|------|
| HLS 載入失敗 | hls.recoverMediaError() × 3 → 降回 snapshot + toast |
| WS 斷線重連 | 重連後查詢串流狀態，同步 tile |
| 忘記關即時模式 | 5 分鐘 server 強制 stop |

## 測試策略

| 層級 | 範圍 | 方式 |
|------|------|------|
| Client 單元 | 動態幀率、設定讀寫（含 DPAPI 加解密）、攝影機掃描 | pytest + mock cv2 |
| Client 整合 | USB webcam → POST → server 收到 | 手動 + 腳本 |
| Server 單元 | 新端點 CRUD、snapshot ingest、HLS 上傳/serve、租約 | pytest + httpx AsyncClient |
| Server 安全 | **跨租戶：以 Client A 金鑰存取 Client B 攝影機 → 403**（每個 X-API-Key + `{node_id}` 端點各一） | pytest |
| Server 安全 | 路徑穿越：`..`／`..\` 形式的 filename 於**讀取與寫入**兩路徑皆須擋下 | pytest |
| Server 整合 | 模擬 client 上傳 → playlist 正確 → 租約過期 → **確認有送出 `stream_stop`** | pytest |
| Dashboard | Tile 狀態切換、badge、就緒輪詢逾時 | `tools/spa` jsdom render tests |
| Dashboard | HlsPlayer 降級、節點管理 CRUD | 手動瀏覽器 |
| 端到端 | PC webcam → exe → HTTPS → Dashboard | 手動驗收 |

**執行方式（本機環境限制）：** pytest 必須**一次一個測試檔**：

```bash
/c/Python314/python -m pytest <單一檔案> -q -p no:cacheprovider
```

自 repo 根目錄執行裸 `pytest`（或指定整個 `tests/` 目錄）會失敗——絕對路徑中的
`[Cloud]` 方括號會被 pytest 解析為 test-id 參數化。本機亦無 `python3` 別名。

**Dashboard 不再是「只能手動測試」。** `tools/spa/` 提供離線閘門
（`npm run check`：vendor 完整性、scope invariant、語法、未定義參照、
render tests）。SPA 無 build step，瀏覽器內即時編譯，語法或跨檔參照錯誤在載入
前不會有任何徵兆——這些閘門是唯一的離線信號，紅燈即停。

## 運作限制（本版新增）

**單一 worker。** viewer 租約、command queue、HLS 活動時間戳都是行程內記憶體
狀態（`dict` 與 `asyncio.Queue`）。本服務刻意以單一 uvicorn worker 運行：

- `deploy/Dockerfile` — 明確指定 `--workers 1`
- `Dockerfile`（Zeabur 路徑，見 `zbpack.json`）— 未指定，即 uvicorn 預設 1

這與既有決策一致：`main.py:332–337` 就登入節流狀態已記載相同限制，並註明
「多 worker／多節點部署需改用共享儲存（如 Redis）」。

**若日後調高 worker 數，webcam 串流會靜默失效**——`stream/start` 由 A worker
入列的指令，B worker 處理的 long-poll 看不到，串流永遠不會開始。屆時
viewer 租約與 command queue 都必須改為共享儲存。此限制須與登入節流一併記載。

**HLS 分段存於容器檔案系統**，屬暫存性質（保留 5 段、~10 秒）。容器重啟即遺失，
這是可接受的——分段本就短命，重新串流會重新產生。

## 頻寬估算

| 模式 | 每路頻寬 | 10 路同時 |
|------|----------|-----------|
| 1Hz JPEG（平時） | ~5 KB/s | ~50 KB/s |
| H.264 HLS（按需，640×480, 8fps） | ~30-50 KB/s | ~300-500 KB/s |
| 動態幀率靜止時 | ~5-10 KB/s | — |

## 檔案結構（新增）

```
sdprs/
├── webcam_client/                  ← 新目錄
│   ├── main.py                     ← 入口：GUI + 執行緒啟動
│   ├── camera_manager.py           ← 攝影機掃描、擷取、動態幀率
│   ├── push_engine.py              ← 1Hz JPEG + HLS 推送邏輯
│   ├── hls_encoder.py              ← FFmpeg subprocess 管理
│   ├── control_channel.py          ← HTTP long-poll 指令接收
│   ├── gui/
│   │   ├── setup_wizard.py         ← 首次設定精靈
│   │   ├── tray_app.py             ← System tray + 右鍵選單
│   │   └── preview.py              ← 攝影機預覽元件
│   ├── config.py                   ← 設定檔讀寫
│   ├── requirements.txt
│   └── build.spec                  ← PyInstaller spec
├── central_server/
│   ├── api/
│   │   ├── webcam.py               ← 新 router：HLS、stream control、commands
│   │   └── nodes.py                ← 修改：支援 webcam 類型 + API Key 產生
│   ├── services/
│   │   └── hls_service.py          ← HLS 儲存、清理、viewer 計數
│   └── static/spa/
│       ├── vendor/hls.min.js       ← hls.js vendor
│       ├── components.jsx          ← 新增 HlsPlayer、WebcamTile
│       └── pages/status.jsx        ← 擴展：節點管理 + 新增 Webcam
└── storage/hls/                    ← HLS 分段暫存（runtime 產生）
```
