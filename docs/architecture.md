# 系統架構

本文件說明 SDPRS 的系統定位、整體架構、關鍵技術決策與完整原始碼目錄結構。
適用對象：需要理解系統如何運作、模組如何分工的開發者、維運人員與技術評審。

← 返回[文件索引](README.md)

---

## 系統簡介

SDPRS 是一套基於**邊緣計算**的智能防災監測系統，專為颱風季節設計：

| 功能                   | 說明                                                                      |
| ---------------------- | ------------------------------------------------------------------------- |
| **玻璃破裂偵測** | 結合視覺（OpenCV 10步管線）與音訊（FFT 6步管線），融合觸發（2秒關聯窗口） |
| **即時警報推播** | Alert-First 模式 -- JSON 警報 <1秒送達儀表板，MP4 影片隨後上傳            |
| **HLS 即時串流** | 透過 mediamtx + SSH 反向隧道，瀏覽器直接觀看攝像頭畫面                    |
| **水泵自動控制** | 滯後控制（水位 >=80% 啟動 / <=20% 關閉），防止頻繁切換                    |
| **連續監控牆**   | 每秒上傳 480p 快照，儀表板即時顯示所有攝像頭                              |
| **離線自治**     | 邊緣節點網路中斷時獨立偵測、錄影，網路恢復後自動上傳                      |

### 事件狀態流程

```
邊緣節點:  QUEUED --> JSON_SENT --> UPLOADED
伺服器:   PENDING_VIDEO --> PENDING --> RESOLVED (由保安標記)
```

---

## 系統架構圖

### 標準架構（同區網部署）

```
+--------------------------------------------------------------+
|                    中央伺服器 (Pi 5 / Docker)                  |
|                                                              |
|  +-----------+  +-----------+  +-----------+                 |
|  |  FastAPI   |  | Mosquitto |  |   Nginx   |                |
|  |  (:8000)   |  |  (:1883)  |  |   (:80)   |                |
|  +-----+-----+  +-----+-----+  +-----------+                |
|        |               |                                      |
|  +-----v-----+  +-----v------+                               |
|  |  SQLite   |  |  WebSocket |--> 儀表板 (瀏覽器)             |
|  | WAL mode  |  |  Manager   |                               |
|  +-----------+  +------------+                               |
+--------------------------------------------------------------+
        | MQTT               ^ REST API + SSH隧道
        v                    |
+----------------+    +----------------+
| 邊緣節點 (Pi 4) |    | 水泵節點 (ESP32)|
| - 攝像頭+麥克風 |    | - 水位感測器    |
| - OpenCV 視覺  |    | - 繼電器控制    |
| - FFT 音訊     |    | - MQTT 回報    |
| - mediamtx HLS |    | - MicroPython  |
| - SSH 反向隧道  |    | - 離線自治      |
+----------------+    +----------------+
```

### 雲端部署架構（AP 隔離場地）

> 當場地 WiFi 啟用 AP 隔離，Pi 之間無法直接通訊時，使用此架構。
> 中央伺服器改部署在 Zeabur / Tencent Cloud，Pi 只需要出站網際網路存取。

```
+-----------------------------------------------+
|         Zeabur / Tencent Cloud (新加坡)        |
|                                               |
|  +-----------+  +-----------+  +-----------+  |
|  |  FastAPI   |  |   EMQX    |  | PostgreSQL|  |
|  |  (HTTPS)   |  | (TCP/WS)  |  |           |  |
|  +-----+-----+  +-----+-----+  +-----+-----+  |
|        |               |              |         |
|        +-------+-------+--------------+         |
|                |                               |
|        WebSocket Push                          |
|                |                               |
+---------------|-------------------------------+
                | 網際網路（出站）
                |
     +----------+-----------+
     |                      |
+----v---------+   +--------v------+
| 邊緣節點(Pi)  |   | 水泵節點(ESP32)|
| - 偵測 + MP4 |   | - MQTT over  |
| - HTTPS push |   |   Internet   |
| - EMQX MQTT  |   +--------------+
| cloud_mode=T |
+--------------+
                  瀏覽器 (任何地方)
                      |
              https://your-app.zeabur.app
```

---

## 技術決策摘要

> 完整技術決策文件見 tech_decisions.md

| 決策項目     | 選擇                       | 原因                                               |
| ------------ | -------------------------- | -------------------------------------------------- |
| 影像解析度   | 720p@15fps                 | Pi 4 記憶體限制（環形緩衝 ~415MB）                 |
| 音訊偵測模式 | 自適應基線                 | 颱風噪音重疊，固定閾值不適用                       |
| 警報模式     | Alert-First                | JSON <1秒送達，MP4 隨後上傳                        |
| 串流格式     | HLS (mediamtx)             | 瀏覽器原生支援，無需安裝外掛                       |
| 串流通道     | SSH 反向隧道               | 穿透 NAT/防火牆，不需公網 IP                       |
| 資料庫       | SQLite + WAL               | 適合 Pi 環境，未來可遷移 PostgreSQL                |
| Web 框架     | FastAPI                    | 原生 async，支援並發快照+MP4+WebSocket             |
| 前端         | Jinja2 + Tailwind CDN      | 伺服器端渲染，無需 Node.js 建置                    |
| 認證         | 三層分離                   | API Key (Edge) / Session (Dashboard) / Cookie (WS) |
| 部署         | systemd                    | Pi 原生支援，開機自啟，看門狗整合                  |
| 備用部署     | Docker Compose             | 筆電/伺服器開發測試環境                            |
| 雲端部署     | Zeabur / Tencent Cloud VPS | AP 隔離場地，繞過 Pi-to-Pi 連線限制                |
| 雲端資料庫   | PostgreSQL                 | 雲端容器無持久化，SQLite 不適用                    |
| 雲端 MQTT    | EMQX                       | 支援帳密認證、TLS、WebSocket 443                   |
| 備份策略     | Pi cron + SSH              | Pi 主動出站，繞過 AP 隔離限制                      |
| 冷卻期       | 30秒                       | 防止同一事件重複觸發                               |
| 水泵控制     | 滯後 80%/20%               | 防止頻繁啟停損壞泵                                 |

> 上表為摘要。完整 26 項技術決策的比較表與詳細取捨理由，收錄於專案工作區（本 repo 之外）的 `tech_decisions.md`。

---

## 完整目錄結構

```
sdprs/
|-- .env.example                     # 環境變數範本（複製為 .env 使用）
|-- .gitignore
|-- Dockerfile                       # 雲端部署映像（Zeabur / 任何 Docker 平台）
|-- .dockerignore
|-- README.md                        # 專案首頁（front door）
|-- zbpack.json                      # Zeabur 建置類型指令
|
|-- central_server/                  # ===== 中央伺服器應用 =====
|   |-- __init__.py
|   |-- main.py                      # FastAPI 入口 + SPA/舊 Jinja 路由 + login/logout
|   |-- config.py                    # pydantic-settings 環境變數管理
|   |-- auth.py                      # 三層認證 (X-API-Key / Session / X-API-Key or Session)
|   |-- database.py                  # 雙模資料庫：SQLite WAL（本地）/ PostgreSQL（雲端）
|   |-- timeutil.py                  # utcnow() 統一 naive-UTC helper
|   |-- requirements.txt
|   |-- .env.example
|   |-- api/                         # REST API 路由
|   |   |-- __init__.py
|   |   |-- alerts.py                #   警報 CRUD + 影片 + 認領/解決/批次/rate
|   |   |-- audit.py                 #   稽核紀錄（admin only）
|   |   |-- handover.py              #   班次交接備註（單筆全域，24h TTL）
|   |   |-- nodes.py                 #   節點狀態 + snooze + pump cycles
|   |   |-- snapshots.py             #   監控快照上傳/讀取
|   |   |-- stream.py                #   HLS 串流啟動/停止/health
|   |   +-- weather.py               #   CWA + Open-Meteo（受 CWA_API_KEY 閘控）
|   |-- services/                    # 背景服務
|   |   |-- __init__.py
|   |   |-- audit_service.py         #   操作稽核寫入
|   |   |-- event_service.py         #   事件業務邏輯（雙 backend dispatch）
|   |   |-- mqtt_service.py          #   MQTT 訂閱 + 離線偵測 + LWT + cmd 發送
|   |   |-- retention_service.py     #   APScheduler 資料清理（每天凌晨 3 點）
|   |   |-- weather_service.py       #   CWA + Open-Meteo 快取
|   |   +-- websocket_service.py     #   WebSocket 即時推送 + broadcast_from_sync
|   |-- templates/                   # 舊版 Jinja 儀表板（/dashboard-legacy 等）
|   |   |-- base.html · dashboard.html · alert_detail.html
|   |   |-- monitor.html · system_status.html · audit.html · login.html
|   |-- static/
|   |   |-- css/styles.css           # 舊版樣式
|   |   |-- js/
|   |   |   |-- dashboard.js · monitor.js
|   |   +-- spa/                     # V2 React SPA（/）
|   |       |-- index.html · styles.css
|   |       |-- app.jsx · api.jsx · components.jsx
|   |       |-- pages.jsx · icons.jsx · data.jsx · tweaks-panel.jsx
|   |       +-- vendor/              # React 18 + Tailwind + Babel（CDN 內嵌）
|   |-- systemd/
|   |   +-- sdprs-server.service
|   |-- storage/events/{node_id}/*.mp4   # 上傳的 MP4 落地（STORAGE_PATH 子樹）
|   +-- tests/                       # 115 個測試（含 dual-backend、LWT、audit、rate）
|
|-- edge_glass/                      # ===== 玻璃偵測邊緣節點 (Pi 4/5) =====
|   |-- __init__.py
|   |-- edge_glass_main.py           # 主程式（事件迴圈；含 async_encode 未啟用開關）
|   |-- config.yaml                  # 節點配置（本地 LAN）
|   |-- config.zeabur.yaml           # 雲端版配置（指向 Zeabur）
|   |-- requirements.txt
|   |-- detectors/
|   |   |-- visual_detector.py       #   OpenCV 10 步管線 + anomaly_recovery
|   |   |-- audio_detector.py        #   FFT 6 步管線 + 自適應基線（dBFS）
|   |   +-- trigger_engine.py        #   融合觸發 + is_simulation 支援
|   |-- buffer/
|   |   +-- circular_buffer.py       #   RAM 環形緩衝 (~415MB)
|   |-- comms/
|   |   |-- mqtt_client.py           #   心跳 + LWT + cmd 訂閱
|   |   |-- api_uploader.py          #   HTTP 上傳佇列
|   |   +-- event_queue.py           #   事件排隊管理
|   |-- stream/
|   |   +-- rtsp_server.py           #   mediamtx 管理
|   |-- utils/
|   |   |-- config_loader.py         #   YAML 載入 + DEFAULTS
|   |   |-- logger.py
|   |   |-- mp4_encoder.py
|   |   |-- snapshot.py
|   |   |-- thermal.py               #   CPU 溫度監控
|   |   +-- event_capture.py         #   非同步事件擷取 (flag-gated OFF)
|   |-- systemd/
|   |   |-- sdprs-edge.service       #   本地 LAN 模式
|   |   |-- autossh-tunnel.service   #   SSH 反向隧道
|   |   +-- sdprs-edge-cloud.service #   雲端模式（無 autossh 依賴）
|   +-- tests/                       # 124 個測試
|
|-- edge_pump/                       # ===== 水泵控制節點 (ESP32 MicroPython) =====
|   |-- __init__.py
|   |-- boot.py                      # 開機啟動
|   |-- main.py                      # 主迴圈（滯後控制 + WDT）
|   |-- config.py                    # WiFi/MQTT/GPIO 配置常數
|   |-- control_logic.py             # 純安全決策階梯（純函式，可桌面測試）
|   |-- sensors.py                   # 感測器 HAL（去彈跳數位 + ADC 中值）
|   |-- pump_controller.py           # 繼電器 GPIO 控制 + LED
|   |-- mqtt_client.py               # umqtt.simple + LWT + build_payload（publish-only）
|   |-- conftest.py                  # pytest sys.path 修補
|   +-- tests/                       # 48 個測試
|
|-- shared/                          # ===== 共用模組 =====
|   |-- __init__.py
|   +-- mqtt_topics.py               # 主題常數 + QoS + 生成函式
|
|-- firmware/                        # MicroPython 韌體快取
|   +-- micropython_esp32.bin        # setup_esp32.sh 首次刷寫時下載到此
|
|-- deploy/                          # ===== 部署配置 =====
|   |-- Dockerfile                   # 中央伺服器容器映像
|   |-- docker-compose.yml           # 三容器編排 (app + mosquitto + nginx)
|   |-- nginx.conf
|   |-- mosquitto.conf · mosquitto_acl.conf
|   |-- MQTT_SECURITY.md
|   +-- emqx/                        # Zeabur EMQX 建置
|
|-- scripts/                         # ===== 佈建與運維腳本 =====
|   |-- setup_server.sh              # 中央伺服器一鍵佈建（Pi 5 / 一般 Linux）
|   |-- setup_pi.sh                  # 邊緣節點一鍵佈建
|   |-- setup_esp32.sh               # ESP32 韌體燒錄 + 程式上傳
|   |-- deploy_sync.sh               # rsync 增量部署（開發用；SDPRS_* 環境變數）
|   |-- gen_qrcode.sh                # WiFi/伺服器 QR Code 生成
|   |-- backup_from_zeabur.sh        # 從 Zeabur 拉取備份（每日 cron）
|   |-- micropython_esp32.bin        # ESP32 MicroPython 韌體（同 firmware/，供 setup_esp32.sh 就近取用）
|   +-- restore_to_zeabur.sh         # 從備份還原到 Zeabur PostgreSQL
|
|-- storage/                         # ===== 執行時期本地事件儲存（根層） =====
|   +-- events/{node_id}/            # 邊緣節點上傳的 MP4 落地目錄
|
+-- docs/                            # 文件樹（本目錄）
    |-- README.md · PROJECT_STATUS.md · architecture.md · hardware-network.md
    |-- deployment/                  # 部署指南（README + 6 個方案／驗證清單）
    |-- operations/                  # 運維文件（dashboard-guide, runbook, troubleshooting）
    |-- reference/                   # 參考文件（configuration, api, mqtt-topics）
    |-- archive/                     # 已封存文件
    +-- superpowers/                 # 工程審計與規格工作流（PROGRESS.md 為權威進度）
```

**新／改動要點：**

- `central_server/api/` 現有 7 個路由檔（新增 `audit.py` / `handover.py` / `weather.py`）。
- `central_server/services/` 新增 `audit_service.py`、`weather_service.py`。
- `central_server/static/spa/` 為 V2 React SPA 主要載入路徑（`/` 直接載入）。
- `central_server/timeutil.py` 為 `utcnow()` 統一 helper（見 `PROGRESS.md`）。
- `edge_glass/utils/event_capture.py` 為非同步編碼工作管線（`capture.async_encode` 預設 OFF）。
- `edge_glass/config.zeabur.yaml` 為雲端變體。
- `edge_pump/tests/` 與 `edge_pump/conftest.py` 於 2026-07-13 合併補齊。
- `firmware/micropython_esp32.bin` 為權威快取；`scripts/micropython_esp32.bin` 為歷史殘留（見腳本 §deploy 待清）。

---

## 下一步

- [硬體清單與網路規劃](hardware-network.md) — 各節點硬體規格、接線圖與 IP／端口配置
- [部署指南](deployment/README.md) — 從燒錄 Pi OS 到各節點上線的完整步驟
