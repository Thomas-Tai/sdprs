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
|-- .dockerignore                    # Docker 建置忽略清單
|-- README.md                        # <-- 本文件
|
|-- central_server/                  # ===== 中央伺服器應用 =====
|   |-- __init__.py
|   |-- main.py                      # FastAPI 入口 + 儀表板路由
|   |-- config.py                    # pydantic-settings 環境變數管理
|   |-- auth.py                      # 三層認證 (API Key / Session / WS Cookie)
|   |-- database.py                  # 雙模資料庫：SQLite WAL（本地）/ PostgreSQL（雲端）
|   |-- requirements.txt             # Python 依賴清單
|   |-- api/                         # REST API 端點
|   |   |-- __init__.py
|   |   |-- alerts.py                #   警報 CRUD + 影片上傳
|   |   |-- nodes.py                 #   節點狀態 + 離線偵測
|   |   |-- snapshots.py             #   監控快照上傳/讀取
|   |   +-- stream.py                #   HLS 串流啟動/停止
|   |-- services/                    # 背景服務
|   |   |-- __init__.py
|   |   |-- mqtt_service.py          #   MQTT 訂閱 + 離線偵測
|   |   |-- websocket_service.py     #   WebSocket 即時推送
|   |   |-- event_service.py         #   事件業務邏輯
|   |   +-- retention_service.py     #   APScheduler 資料清理（每天凌晨3點）
|   |-- templates/                   # Jinja2 HTML 模板（繁體中文）
|   |   |-- base.html                #   基礎版面 + 導航 + WS 連線
|   |   |-- dashboard.html           #   儀表板首頁
|   |   |-- alert_detail.html        #   警報詳情 + 影片播放
|   |   |-- monitor.html             #   連續監控牆
|   |   |-- system_status.html       #   系統狀態頁
|   |   +-- login.html               #   登入頁面
|   |-- static/                      # 靜態資源
|   |   |-- css/styles.css
|   |   +-- js/
|   |       |-- dashboard.js         #   WebSocket + 警報音效
|   |       +-- monitor.js           #   監控牆自動刷新
|   |-- systemd/
|   |   +-- sdprs-server.service     # systemd 服務定義
|   +-- tests/                       # 單元測試
|       |-- __init__.py
|       |-- test_alerts_api.py
|       |-- test_retention.py
|       +-- test_snapshot_api.py
|
|-- edge_glass/                      # ===== 玻璃偵測邊緣節點 (Pi 4) =====
|   |-- __init__.py
|   |-- edge_glass_main.py           # 主程式（事件迴圈）
|   |-- config.yaml                  # 節點配置
|   |-- requirements.txt             # Python 依賴清單
|   |-- detectors/                   # 偵測模組
|   |   |-- __init__.py
|   |   |-- visual_detector.py       #   OpenCV 10步管線
|   |   |-- audio_detector.py        #   FFT 6步管線 + 自適應基線
|   |   +-- trigger_engine.py        #   融合觸發引擎
|   |-- buffer/                      # 環形緩衝
|   |   |-- __init__.py
|   |   +-- circular_buffer.py       #   RAM 環形緩衝 (~415MB)
|   |-- comms/                       # 通訊模組
|   |   |-- __init__.py
|   |   |-- mqtt_client.py           #   MQTT 發布
|   |   |-- api_uploader.py          #   HTTP 上傳佇列
|   |   +-- event_queue.py           #   事件排隊管理
|   |-- stream/                      # 串流模組
|   |   |-- __init__.py
|   |   +-- rtsp_server.py           #   mediamtx 管理
|   |-- utils/                       # 工具
|   |   |-- __init__.py
|   |   |-- config_loader.py         #   YAML 配置載入
|   |   |-- logger.py                #   日誌格式化
|   |   |-- mp4_encoder.py           #   MP4 編碼寫入
|   |   |-- snapshot.py              #   快照擷取
|   |   +-- thermal.py               #   CPU 溫度監控
|   |-- config.zeabur.yaml           # 雲端版配置（指向 Zeabur 伺服器）
|   |-- systemd/                     # systemd 服務定義
|   |   |-- sdprs-edge.service       #   主偵測服務（本地 LAN 模式）
|   |   |-- autossh-tunnel.service   #   SSH 反向隧道（本地 LAN 模式）
|   |   +-- sdprs-edge-cloud.service #   雲端模式服務（無 autossh 依賴）
|   +-- tests/                       # 單元測試
|       |-- test_audio_detector.py
|       |-- test_circular_buffer.py
|       |-- test_event_queue.py
|       |-- test_mp4_encoder.py
|       +-- test_visual_detector.py
|
|-- edge_pump/                       # ===== 水泵控制節點 (ESP32 MicroPython) =====
|   |-- __init__.py
|   |-- boot.py                      # WiFi 連線（開機自動執行）
|   |-- main.py                      # 主迴圈（滯後控制）
|   |-- config.py                    # WiFi/MQTT/GPIO 配置常數
|   |-- control_logic.py             # 純安全決策階梯（純函式，可桌面測試）
|   |-- sensors.py                   # 感測器 HAL（去彈跳數位 + ADC 中值）
|   |-- pump_controller.py           # 繼電器 GPIO 控制 + LED
|   +-- mqtt_client.py               # umqtt.simple 客戶端
|
|-- shared/                          # ===== 共用模組 =====
|   |-- __init__.py
|   +-- mqtt_topics.py               # 7個 MQTT 主題 + QoS 常數
|
|-- deploy/                          # ===== 部署配置 =====
|   |-- Dockerfile                   # 中央伺服器容器映像
|   |-- docker-compose.yml           # 三容器編排 (app+mosquitto+nginx)
|   |-- nginx.conf                   # Nginx 反向代理 + HLS + WebSocket
|   +-- mosquitto.conf               # MQTT Broker 配置
|
+-- scripts/                         # ===== 佈建腳本 =====
    |-- setup_server.sh              # 中央伺服器一鍵佈建
    |-- setup_pi.sh                  # 邊緣節點一鍵佈建
    |-- setup_esp32.sh               # ESP32 韌體燒錄 + 程式上傳
    |-- deploy_sync.sh               # rsync 增量部署到 Pi（開發用）
    |-- gen_qrcode.sh                # WiFi/伺服器 QR Code 生成
    |-- backup_from_zeabur.sh        # 從 Zeabur 拉取備份到 Pi（每日 cron）
    +-- restore_to_zeabur.sh         # 從 Pi 本地備份還原到 Zeabur PostgreSQL

|-- docs/
    +-- zeabur_migration_report.md   # 雲端遷移可行性分析報告
```

---

## 下一步

- [硬體清單與網路規劃](hardware-network.md) — 各節點硬體規格、接線圖與 IP／端口配置
- [部署指南](deployment/README.md) — 從燒錄 Pi OS 到各節點上線的完整步驟
