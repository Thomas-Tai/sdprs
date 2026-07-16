# API 參考

本文件列出中央伺服器所有 REST／WebSocket 端點、認證方式與說明，供開發與整合人員查閱。
路徑對照 `central_server/main.py` 與 `central_server/api/*.py` 現行程式碼確認；
所有 `/api/*` 路徑均為 `main.py` 統一以 `prefix="/api"` 掛載後的完整路徑。

← 返回[文件索引](../README.md)

啟動後可存取自動生成的互動式文件：

- **Swagger UI**: `http://<host>:8000/docs`
- **ReDoc**: `http://<host>:8000/redoc`

## 認證方式速覽

| 方式                  | 相依函式                    | 使用場景                                                   |
| --------------------- | --------------------------- | ---------------------------------------------------------- |
| **無**                | —                           | 僅健康檢查                                                 |
| **X-API-Key**         | `verify_api_key`            | 邊緣節點上傳（POST/PUT）                                   |
| **Session**           | `get_current_user` / cookie | 儀表板讀取／變更（Session Cookie `sdprs_session`）         |
| **X-API-Key or Session** | `verify_api_key_or_session` | 兩端皆可能存取（快照 GET、警報 GET）                       |
| **Admin Session**     | Session + `user == DASHBOARD_USER` | 稽核紀錄、`/audit` 頁面                              |
| **Session Cookie (WS)** | WebSocket scope session   | `/ws` 需先登入才會 accept                                  |

> `verify_api_key_or_session` 在請求含 `X-API-Key` 標頭時走 API Key 驗證；否則檢查 Session Cookie。

## 健康檢查

| 方法 | 路徑          | 認證 | 說明                                              |
| ---- | ------------- | ---- | ------------------------------------------------- |
| GET  | `/api/health` | 無   | 存活探針，回 `{status, timestamp, service}`       |

## 警報（`central_server/api/alerts.py`）

| 方法  | 路徑                                | 認證                    | 說明                                                                 |
| ----- | ----------------------------------- | ----------------------- | -------------------------------------------------------------------- |
| POST  | `/api/alerts`                       | X-API-Key               | 邊緣節點建立新警報（進 PENDING_VIDEO），廣播 `new_alert`             |
| PUT   | `/api/alerts/{id}/video`            | X-API-Key               | 上傳 MP4（≤100MB，`video/mp4`），狀態轉 PENDING，觸發 `alert_updated` |
| PATCH | `/api/alerts/{id}/acknowledge`      | Session                 | 認領警報，記 audit + WS `alert_acknowledged`；同人重按為 no-op       |
| PATCH | `/api/alerts/{id}/resolve`          | Session                 | 解決警報（`resolved_by` 一律來自 session；忽略 body）                |
| POST  | `/api/alerts/bulk-resolve`          | Session                 | 批次解決（≤200 筆），回 succeeded / failures；部分失敗回 207          |
| GET   | `/api/alerts/rate`                  | Session                 | 警報率 sparkline（`bucket` 5m/15m/1h，`window` 1h/4h/24h）           |
| GET   | `/api/alerts`                       | X-API-Key or Session    | 列表；`status_filter` 支援逗號分隔多狀態、`limit`/`offset` 分頁      |
| GET   | `/api/alerts/{id}`                  | X-API-Key or Session    | 單筆警報詳情                                                         |

## 節點與水泵（`central_server/api/nodes.py`）

| 方法   | 路徑                                | 認證     | 說明                                                                                     |
| ------ | ----------------------------------- | -------- | ---------------------------------------------------------------------------------------- |
| GET    | `/api/nodes`                        | Session  | 全節點狀態；含 DB 增補欄位（`location`、`battery_voltage`、`power_source`、`snoozed_until`） |
| GET    | `/api/nodes/summary`                | Session  | 節點統計（glass_nodes / pump_nodes 之 online/offline/active）                            |
| GET    | `/api/nodes/{node_id}`              | Session  | 單一節點狀態（含 snapshot_stale 判定）                                                   |
| PATCH  | `/api/nodes/{node_id}`              | Session  | 編輯節點欄位（目前僅 `location`）；不存在則自動 upsert                                   |
| POST   | `/api/nodes/{node_id}/snooze`       | Session  | 靜音節點純音訊觸發 1–480 分鐘；同時透過 MQTT push cmd/snooze                             |
| DELETE | `/api/nodes/{node_id}/snooze`       | Session  | 清除 snooze                                                                              |
| GET    | `/api/pump/{node_id}/history`       | Session  | 水位歷史（ISO-8601 `start`/`end`，`limit` ≤ 20000）                                       |
| GET    | `/api/pump/{node_id}/cycles`        | Session  | 單一水泵 ON→OFF 次數（`window` 15m/1h/6h/24h），>20 觸發 `alert`                          |
| GET    | `/api/pumps/cycles`                 | Session  | **所有水泵**一次批次（避免 SPA N+1），格式 `{window, nodes:{...}}`                        |

## 快照（`central_server/api/snapshots.py`）

| 方法   | 路徑                                       | 認證                    | 說明                                                                       |
| ------ | ------------------------------------------ | ----------------------- | -------------------------------------------------------------------------- |
| POST   | `/api/edge/{node_id}/snapshot`             | X-API-Key               | 邊緣節點上傳 JPEG（≤5MB），記憶體儲存＋更新 `nodes.last_upload_at`         |
| GET    | `/api/edge/{node_id}/snapshot/latest`      | X-API-Key or Session    | 取最新 JPEG；無資料時回灰底 placeholder。**不再公開**（強制認證）          |
| DELETE | `/api/edge/{node_id}/snapshot`             | X-API-Key               | 清除快照                                                                   |
| GET    | `/api/edge/snapshots/status`               | X-API-Key               | 各節點快照狀態（有無、時間戳、大小）                                       |

## 串流控制（`central_server/api/stream.py`）

| 方法 | 路徑                             | 認證    | 說明                                                            |
| ---- | -------------------------------- | ------- | --------------------------------------------------------------- |
| POST | `/api/stream/{node_id}/start`    | Session | 透過 MQTT `cmd/stream_start` 觸發節點啟動 HLS；節點需 ONLINE     |
| POST | `/api/stream/{node_id}/stop`     | Session | 透過 MQTT `cmd/stream_stop` 停止（離線也可送）                   |
| GET  | `/api/stream/{node_id}/status`   | Session | 讀取節點回報的最新 stream_status                                |
| GET  | `/api/stream`                    | Session | 所有節點的串流狀態總覽（`active_streams`, `streams`）           |
| GET  | `/api/stream/health`             | Session | 從 `MEDIAMTX_METRICS_URL` scrape Prometheus，回 per-node bitrate/dropped/viewers |

## 天氣（`central_server/api/weather.py`；受 `CWA_API_KEY` 閘控）

`CWA_API_KEY` 空時服務未啟動，`/current`／`/forecast`／`/typhoon`／`/refresh` 回 503。

| 方法 | 路徑                    | 認證    | 說明                                                    |
| ---- | ----------------------- | ------- | ------------------------------------------------------- |
| GET  | `/api/weather/config`   | Session | 讀取當前站台配置（座標、station_name）                  |
| PUT  | `/api/weather/config`   | Session | 更新座標（會即時套用給 WeatherService，restart 生效）    |
| GET  | `/api/weather/current`  | Session | 現況（風速、雨量、溫度、濕度、來源）                    |
| GET  | `/api/weather/forecast` | Session | 36 小時預報 buckets                                     |
| GET  | `/api/weather/typhoon`  | Session | 熱帶氣旋警報；無時回 `null`（前端據此隱藏徽章）         |
| GET  | `/api/weather/health`   | Session | 服務健康資訊（含 `enabled` 旗標）                       |
| POST | `/api/weather/refresh`  | Session | 立即重抓資料                                            |

## 交接備註（`central_server/api/handover.py`）

| 方法 | 路徑                  | 認證    | 說明                                                          |
| ---- | --------------------- | ------- | ------------------------------------------------------------- |
| GET  | `/api/handover/note`  | Session | 讀取全域備註；24 小時 TTL，過期時 `expired: true` 且 `note=""` |
| PUT  | `/api/handover/note`  | Session | 覆寫備註（`note` ≤ 2000 chars），記 audit `HANDOVER_EDIT`      |

## 稽核（`central_server/api/audit.py`）

| 方法 | 路徑         | 認證          | 說明                                                                 |
| ---- | ------------ | ------------- | -------------------------------------------------------------------- |
| GET  | `/api/audit` | Admin Session | 稽核紀錄；`limit` 1–500、`offset`、`operator`、`action_type` 篩選     |

## Session 管理

| 方法 | 路徑                    | 認證    | 說明                                             |
| ---- | ----------------------- | ------- | ------------------------------------------------ |
| POST | `/api/session/extend`   | Session | 重寫 `login_at` → 觸發 Set-Cookie，延長 24h max_age |

## 儀表板頁面（`central_server/main.py`）

Session cookie 認證；未登入自動跳 `/login`。

| 方法    | 路徑                    | 認證          | 說明                                                        |
| ------- | ----------------------- | ------------- | ----------------------------------------------------------- |
| GET     | `/`                     | Session       | V2 SPA 入口（載入 `/static/spa/index.html`）                |
| GET/POST | `/login`               | 無            | 登入頁 / 表單處理（IP 每 `LOGIN_LOCKOUT_SECONDS` 秒限 5 次） |
| POST    | `/logout`               | Session       | 清除 session cookie 並記 audit                              |
| GET     | `/dashboard-legacy` `/monitor` `/system` `/audit` `/alerts/{id}` | —  | 舊 Jinja 儀表板 301 → `/`（2026-07-16 淘汰，SPA 全面接手）  |

## WebSocket

| 端點  | 認證              | 說明                                                                   |
| ----- | ----------------- | ---------------------------------------------------------------------- |
| `/ws` | Session Cookie    | 即時推送 `new_alert` / `alert_updated` / `alert_acknowledged` / `alert_resolved` / `node_status` / `pump_status` / `ping`。未登入呼叫回 close code 1008 |

## 相關文件

- MQTT 主題與命令通道請見 [MQTT 主題參考](mqtt-topics.md)。
- 環境變數與各節點配置請見 [配置參考](configuration.md)。
