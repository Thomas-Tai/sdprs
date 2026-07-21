# Webcam Client 設計文件

**日期：** 2026-07-21
**狀態：** 已核准，待實作

## 概述

為 SDPRS Dashboard 的 Monitor Wall 新增「Webcam 連線」功能。任一 Windows 電腦運行單一 exe 後，可將本機 webcam / USB webcam 畫面透過網路上傳到 Dashboard，與現有 Edge Cam 並列顯示。

## 需求摘要

| 項目 | 決定 |
|------|------|
| 影片模式 | 平時 1Hz JPEG（複用現有 snapshot 管線）+ 按需 H.264 HLS 串流 |
| 驗證 | Dashboard 產生 API Key → Client 填入（與現有 edge node X-API-Key 一致） |
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
│  │  [平時] 1Hz JPEG → POST /api/edge/{id}/snapshot       │  │
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
│  現有複用:                                                   │
│    POST /api/edge/{node_id}/snapshot   ← 1Hz JPEG            │
│    GET  /api/edge/{node_id}/snapshot/latest                  │
│    WS   /ws                            ← 新增 webcam 事件    │
│                                                              │
│  新增:                                                       │
│    POST /api/nodes (type=webcam)       ← 建立節點+產生 Key   │
│    POST /api/nodes/{id}/revoke-key     ← 撤銷 Key            │
│    PUT  /api/webcam/{node_id}/hls/{f}  ← Client 上傳 HLS    │
│    GET  /api/webcam/{node_id}/hls/{f}  ← Dashboard 播放 HLS │
│    POST /api/webcam/{node_id}/stream/start|stop              │
│    GET  /api/webcam/{node_id}/commands?timeout=5  ← Long-poll│
│                                                              │
│  狀態:                                                       │
│    frame_buffer: dict[node_id, jpeg]   ← 現有               │
│    viewer_count: dict[node_id, int]    ← 新增               │
│    command_queue: dict[node_id, list]  ← 新增               │
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

1. Dashboard webcam tile 預設顯示 1Hz JPEG（與 Edge Cam 一致）
2. 操作員點擊「即時觀看」→ Dashboard 呼叫 `POST /api/webcam/{id}/stream/start`
3. Server 將 `stream_start` 指令放入 command_queue
4. Client 下次 long-poll（≤5s）取得指令 → 啟動 FFmpeg 編碼
5. Client 產生 .ts 分段（每段 ~2s）→ PUT 上傳到 server
6. Server 寫入 `storage/hls/{node_id}/`，更新 playlist.m3u8（保留最近 5 段）
7. Dashboard hls.js 連接 playlist.m3u8 開始播放
8. 操作員關閉即時模式 → `POST .../stream/stop` → command_queue 放入 stop
9. Client 取得 stop → 終止 FFmpeg → 回到 1Hz JPEG
10. 安全機制：5 分鐘無 viewer → server 自動 stop + 清除 HLS 目錄

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

一個 API Key 對應一個「Webcam Client」（非每支攝影機各一個）。Client 首次連線時向 server 註冊其攝影機清單，server 自動分配 node_id。之後所有請求（snapshot、HLS 上傳）使用同一 API Key + 對應的 node_id。

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

### 打包產物

`SDPRS_Webcam.exe`（~80-100MB，含 OpenCV + FFmpeg + Python runtime），單檔雙擊即用。

## Server 端變更

### 新增 API 端點

| 端點 | 方法 | 用途 | 認證 |
|------|------|------|------|
| `/api/nodes` | POST | 建立 Webcam Client 節點 + 產生 API Key（一 key 多 cam） | Session |
| `/api/nodes/{id}/revoke-key` | POST | 撤銷並重新產生 Key | Session |
| `/api/webcam/cameras` | POST | Client 註冊/同步攝影機清單，server 分配 node_id | X-API-Key |
| `/api/webcam/{node_id}/hls/{filename}` | PUT | Client 上傳 .ts / .m3u8 | X-API-Key |
| `/api/webcam/{node_id}/hls/{filename}` | GET | Dashboard 取得 HLS 檔案 | Session |
| `/api/webcam/{node_id}/stream/start` | POST | 觸發開始串流 | Session |
| `/api/webcam/{node_id}/stream/stop` | POST | 觸發停止串流 | Session |
| `/api/webcam/{node_id}/commands` | GET | Client long-poll 取指令 | X-API-Key |

### 資料模型

nodes 表新增欄位：
- `node_type: TEXT` — "glass" | "pump" | "webcam"
- `api_key_hash: TEXT` — SHA-256 hash（明文僅建立時回傳一次）

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

### Viewer 計數

- `stream/start` → viewer_count++ → 若從 0→1 → enqueue stream_start
- `stream/stop` → viewer_count-- → 若從 1→0 → enqueue stream_stop
- 5 分鐘無 viewer → 強制 stop

### WebSocket 新事件

```json
{"type": "webcam_stream_started", "node_id": "webcam_01"}
{"type": "webcam_stream_stopped", "node_id": "webcam_01"}
```

## Dashboard 變更

### Monitor Wall Tile

- `node_type === "webcam"` → 顯示「Webcam」藍色 badge + 「即時觀看」按鈕
- `node_type !== "webcam"` → 顯示「Edge Cam」灰色 badge，無即時按鈕（現有 HLS 由 edge 管理）

**Tile 狀態機：**
```
[Snapshot] ──點擊即時──→ [Loading] ──HLS就緒──→ [Live]
    ↑                                              │
    └────────關閉 / 超時 / 錯誤降級────────────────┘
```

### 新元件

- **WebcamTile：** 擴展現有 tile，管理 snapshot/live 切換、badge、按鈕
- **HlsPlayer：** `<video>` + hls.js（vendor 於 `static/spa/vendor/hls.min.js`）
  - `liveDurationInfinity: true, maxBufferLength: 5`
  - 錯誤處理：3 次 recover 失敗 → 降回 snapshot + toast

### 節點管理（擴展 status.jsx 或新頁面）

- 「新增 Webcam Client」按鈕 → modal 輸入名稱（如「櫃台電腦」）→ POST /api/nodes → 顯示 API Key（僅一次）
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
| API Key 無效 (401) | 停止推送，通知重新設定，不重試 |
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
| Client 單元 | 動態幀率、設定讀寫、攝影機掃描 | pytest + mock cv2 |
| Client 整合 | USB webcam → POST → server 收到 | 手動 + 腳本 |
| Server 單元 | 新端點 CRUD、HLS 上傳/serve、viewer 計數 | pytest + httpx AsyncClient |
| Server 整合 | 模擬 client 上傳 → playlist 正確 → 自動 stop | pytest |
| Dashboard | Tile 切換、HlsPlayer 降級、節點管理 | 手動瀏覽器 |
| 端到端 | PC webcam → exe → HTTPS → Dashboard | 手動驗收 |

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
