# 儀表板使用說明（V2 SPA）

本文件說明現行儀表板（V2 React SPA）各頁面與保安人員操作流程，供第一線值班與運維人員使用。

← 返回[文件索引](../README.md)

登入後 `/` 直接載入 V2 SPA（React 18 + Tailwind CDN，`central_server/static/spa/`）。
所有資料透過 `/api/*` 讀取，變動事件透過 `/ws` WebSocket 推送。舊版 Jinja 儀表板已於
2026-07-16 淘汰，舊路徑（`/dashboard-legacy` `/monitor` `/system` `/audit` `/alerts/{id}`）
一律 301 重導向到 `/`。介面全繁體中文，桌面／4K 牆面／手機皆可用。

---

## 頁面總覽

| 頁面     | 網址                | 快捷鍵 | 功能摘要                                                                 |
| -------- | ------------------- | ------ | ------------------------------------------------------------------------ |
| 警報     | `/`（`alerts`）     | `1`    | 主頁：作用中／歷史警報清單＋詳情面板（影片、時間軸、Runbook、認領／解決） |
| 監看牆   | `/`（`monitor`）    | `2`    | 全節點即時快照卡片（1s／5s 刷新），依 全部／攝影機／抽水站 分頁          |
| 節點狀態 | `/`（`status`）     | `3`    | 節點總表：心跳、上傳、串流健康、CPU 溫度、水位、電源、動作按鈕           |
| 抽水站   | `/`（`pumps`）      | `4`    | 專屬水泵頁：水位計、啟動頻率、雨感／dry-run 保護、電壓/電源              |
| 天氣     | `/`（`weather`）    | `5`    | CWA／Open-Meteo 資料：風速／雨量／雷擊／36 小時預報（需 `CWA_API_KEY`）  |
| 交接     | `/`（`handover`）   | `6`    | 班次交接備註（單筆全域 note，24 小時自動失效）＋歷史清單                 |
| 稽核     | `/`（`audit`）      | `7`    | 操作稽核紀錄（僅 `DASHBOARD_USER` 可讀，其他人為空清單）                  |
| 登入／登出 | `/login` `/logout` | —      | Session Cookie（`sdprs_session`，24h），失敗五次鎖 5 分鐘                |

> V2 SPA 內部以路由狀態切換頁面（單一 `/` URL），並非多個瀏覽器頁面。

---

## 主要操作流程

### 收到警報 → 認領 → 解決

1. **新警報進線** — WebSocket 推送 `new_alert`，SPA 彈出浮動橫幅、標題列出現「(N) SDPRS」計數、
   若未靜音會於 30 秒節奏重播告警音。頁面標題閃爍協助牆面辨識。
2. **選警報** — 點選列表列，或按 `↑ / ↓` 切換。右側詳情面板顯示：
   - HLS 影片預覽（可切換 1×／1.5×／2× 倍速、逐格前後、下載）
   - 時間軸（`EDGE_CREATED` → `JSON_SENT` → `UPLOADED` → `ACKNOWLEDGED` → `RESOLVED`）
   - Floorplan 場域配置＋節點高亮
   - 同節點其他作用中警報清單（可跳轉）
   - Runbook 建議下一步
3. **認領（Ack）** — 按 `A` 鍵或按「認領」按鈕（`PATCH /api/alerts/{id}/acknowledge`）。
   - 停止該警報的告警音；出現「認領 by X」徽章；SPA 自動跳到下一筆未認領警報。
   - `Shift+A` 認領但不跳筆。
4. **解決（Resolve）** — 需先認領。輸入處置備註（或按 `1–6` 套用內建模板）後按 `R`
   （`PATCH /api/alerts/{id}/resolve`）。備註為必填才會啟用「解決」按鈕。
5. **批次解決** — 用列表列的核取方塊勾選多筆（≤ 200）→ 「批次解決」
   （`POST /api/alerts/bulk-resolve`）；伺服器逐筆處理並回報 succeeded / failures。
6. **延期節點（Snooze）** — 從詳情面板「延期節點」下拉選 30/60/120 分鐘
   （`POST /api/nodes/{id}/snooze`）；伺服器只抑制純音訊觸發，視覺 + 音訊 AND-gate 不受影響。
   `DELETE /api/nodes/{id}/snooze` 可提前解除。

### 監看牆（Monitor）

- 分「全部／攝影機／抽水站」三頁；卡片依 離線 → 嚴重 → 警告 → 正常 排序。
- 攝影機卡：顯示狀態燈、作用中警報徽章、CAM/PUMP 標籤、心跳／上傳秒數、CPU 溫度、
  偵測器健康（visual / audio）。上傳 > 60s 顯示「畫面凍結」。
- 水泵卡：水位計（85/70 閾值虛線）＋啟動頻率＋雨量／電池電壓／電源類型
  ＋近 12 個 5min 啟動次數 sparkline。`sensor_conflict` 觸發紅色橫幅。
- 卡片點選開啟右側節點側面板（可編輯 `location`；`PATCH /api/nodes/{id}`）。

### 串流控制

- 詳情面板影片播放區觀看即時 HLS。
- 節點狀態頁「動作」欄的按鈕觸發 `POST /api/stream/{node_id}/start`｜`/stop`
  （伺服器透過 MQTT `sdprs/edge/{node_id}/cmd/stream_start` 發送指令）。
- 節點必須 `ONLINE` 才能啟動；停止指令即使離線也會發送。
- 串流健康資料（bitrate、drops）由 `GET /api/stream/health` 從 mediamtx `/metrics` 拉取，
  未設定 `MEDIAMTX_METRICS_URL` 時該欄顯示 `—`。

### 交接備註（Handover）

- 單筆全域備註（`GET/PUT /api/handover/note`）。輸入區支援「自動產生本班次摘要」
  （警報處理數、嚴重／警告／資訊筆數、承接筆數等）。
- 24 小時未更新自動失效（讀取時判斷，非後台工作）。頁尾常駐顯示現行備註。

### 稽核（Audit）

- 只有 `DASHBOARD_USER` 帳號能看到內容（`GET /api/audit`，非 admin 回 403 → SPA 顯示空列）。
- 記錄操作：`LOGIN`、`LOGOUT`、`ACKNOWLEDGE`、`RESOLVE`、`BULK_RESOLVE`、`SNOOZE`、
  `UNSNOOZE`、`LOCATION_EDIT`、`HANDOVER_EDIT`。
- 可用「本班 · 我的動作」與操作者／動作／日期篩選器，支援匯出 CSV。

### Session 延長

- Session 預設 24 小時。SPA 快到期時可呼叫 `POST /api/session/extend`
  （後端重寫 `login_at` 觸發 Set-Cookie）。

---

## WebSocket 即時推送事件（`/ws`）

Session Cookie 認證。SPA 每次事件都以 300ms trailing-debounce 合流，觸發一次 `/api/*` 重抓。

| 事件                  | 觸發時機                         | SPA 效果                                     |
| --------------------- | -------------------------------- | -------------------------------------------- |
| `new_alert`           | 邊緣節點建立新警報               | 彈出橫幅、標題 (N)、播放告警音（未靜音時）   |
| `alert_updated`       | MP4 上傳完成 → 狀態改為 PENDING  | 列表狀態更新                                 |
| `alert_acknowledged`  | 操作員按「認領」                 | 該筆顯示「認領 by X」徽章                    |
| `alert_resolved`      | 單筆或批次解決                   | 從作用中列表移除                             |
| `node_status`         | 節點 ONLINE ↔ OFFLINE 轉態       | 節點卡狀態燈更新（LWT 可即時 OFFLINE）       |
| `pump_status`         | 水泵每 10s 送出 pump_status      | 水泵卡水位/狀態更新                          |
| `ping`                | 伺服器保活心跳                   | 重置 SPA `liveSec`（頂列 Live 秒數）         |

---

## 主要鍵盤快捷鍵

| 鍵            | 動作                                   |
| ------------- | -------------------------------------- |
| `1`–`7`       | 切換 警報／監看牆／狀態／抽水站／天氣／交接／稽核 |
| `A` / `Shift+A` | 認領當前警報（跳筆／不跳筆）          |
| `R`           | 解決當前警報（需備註）                 |
| `N`           | 跳到下一筆未認領警報                   |
| `↑ / ↓`       | 於作用中警報間切換                     |
| `1`–`6`（詳情面板打開） | 套用第 N 個處置備註模板       |
| `M`           | 開啟靜音抽屜                            |
| `T`           | 切換深色／淺色主題                     |
| `Shift+D`     | 切換緊湊／舒適密度                     |
| `Ctrl+K` / `⌘K` | 開啟 Command Palette                 |
| `Ctrl+.`      | 專注模式（隱藏資訊級警報）             |
| `Alt+←`       | 回到上一頁                              |
| `?`           | 顯示所有快捷鍵                          |
| `/`           | 聚焦搜尋輸入框                          |
| `Esc`         | 關閉所有面板／浮層                      |

---

## 舊版儀表板（已淘汰）

2026-07-16 已刪除 Jinja 版儀表板（`base.html` 及 5 個頁面、`dashboard.js` + `monitor.js`），
既有 SOP／書籤指向 `/dashboard-legacy` `/monitor` `/system` `/audit` `/alerts/{id}` 者
均以 301 導向 SPA 首頁 `/`；若需要特定頁面請登入後直接切換。
