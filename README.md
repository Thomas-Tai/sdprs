# SDPRS - 智能防災監測與自動響應系統

**Smart Disaster Prevention Response System**

[![Python](https://img.shields.io/badge/Python-3.11+-green.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Raspberry%20Pi-red.svg)](https://www.raspberrypi.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-blue.svg)](https://fastapi.tiangolo.com/)

> **適用對象**: 本文件為完整部署指南，即使非技術人員（如保安人員）也能按步驟完成部署。
> 每一步都有**驗證方法**，請務必確認每步成功後再進入下一步。

---

## 目錄

- [一、系統簡介](#一系統簡介)
- [二、系統架構圖](#二系統架構圖)
- [三、完整目錄結構](#三完整目錄結構)
- [四、硬體清單與接線](#四硬體清單與接線)
- [五、網路規劃](#五網路規劃)
- [六、部署前準備：燒錄 Pi OS](#六部署前準備燒錄-pi-os)
- [七、中央伺服器部署（Pi 5）](#七中央伺服器部署pi-5)
- [八、中央伺服器部署（Docker 備用方案）](#八中央伺服器部署docker-備用方案)
- [八A、中央伺服器部署（Zeabur 雲端方案）](#八a中央伺服器部署zeabur-雲端方案)
- [九、玻璃偵測邊緣節點部署（Pi 4）](#九玻璃偵測邊緣節點部署pi-4)
- [十、水泵節點部署（ESP32）](#十水泵節點部署esp32)
- [十一、部署後完整驗證清單](#十一部署後完整驗證清單)
- [十二、儀表板使用說明](#十二儀表板使用說明)
- [十三、配置參考](#十三配置參考)
- [十四、API 參考](#十四api-參考)
- [十五、MQTT 主題參考](#十五mqtt-主題參考)
- [十六、日常運維](#十六日常運維)
- [十七、故障排除](#十七故障排除)
- [十八、安全建議](#十八安全建議)
- [十九、技術決策摘要](#十九技術決策摘要)
- [二十、Zeabur 雲端備份管理](#二十zeabur-雲端備份管理)

---

## 一、系統簡介

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

## 二、系統架構圖

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
| 邊緣節點(Pi 4)|   | 水泵節點(ESP32)|
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

## 三、完整目錄結構

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
|   |-- water_sensor.py              # ADC 水位讀取（中值濾波）
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

## 四、硬體清單與接線

### 4.1 中央伺服器

| 項目           | 規格               | 數量 | 備註                           |
| -------------- | ------------------ | ---- | ------------------------------ |
| Raspberry Pi 5 | **8GB RAM**  | 1    | 也可用筆電 Docker 替代         |
| NVMe SSD       | 128GB 以上         | 1    | 透過 PCIe HAT 連接             |
| 官方主動散熱器 | Pi 5 專用          | 1    | **必需**，否則會過熱降頻 |
| USB-C 電源     | **27W** 官方 | 1    | 必須用官方電源                 |
| 乙太網路線     | Cat5e/Cat6         | 1    | **強烈建議**有線連接     |
| microSD 卡     | 32GB (系統開機用)  | 1    | NVMe 為主要儲存                |

### 4.2 玻璃偵測邊緣節點（每個攝像頭一台）

| 項目                     | 規格              | 數量 | 備註               |
| ------------------------ | ----------------- | ---- | ------------------ |
| Raspberry Pi 4           | **4GB RAM** | 1    | 環形緩衝需 ~415MB  |
| USB 攝像頭 或 CSI 攝像頭 | 720p 以上         | 1    | 對準玻璃窗         |
| USB 麥克風               | PyAudio 相容      | 1    | 收音偵測玻璃破裂聲 |
| microSD 卡               | 32GB              | 1    | 事件暫存           |
| USB-C 電源               | 5V/3A             | 1    |                    |
| 乙太網路線               | Cat5e/Cat6        | 1    | 可用 WiFi 但不建議 |

### 4.3 水泵控制節點

| 項目             | 規格              | 數量 | 備註                     |
| ---------------- | ----------------- | ---- | ------------------------ |
| ESP32 DevKit v1  | 任何 ESP32 開發板 | 1    |                          |
| 超音波水位感測器 | 0~3.3V 類比輸出   | 1    | 接 GPIO 34 (ADC)         |
| 5V 繼電器模組    | 單路              | 1    | 接 GPIO 26               |
| 紅色 LED         | 3mm/5mm           | 1    | 接 GPIO 27（泵運行指示） |
| 綠色 LED         | 3mm/5mm           | 1    | 接 GPIO 25（泵停止指示） |
| 220 ohm 電阻     |                   | 2    | LED 限流                 |
| 5V 電源          |                   | 1    |                          |

**ESP32 接線圖:**

```
ESP32 GPIO 34 ---- 水位感測器 (類比輸出)
ESP32 GPIO 26 ---- 繼電器模組 (IN)
ESP32 GPIO 27 ---- 220ohm ---- 紅色LED ---- GND
ESP32 GPIO 25 ---- 220ohm ---- 綠色LED ---- GND
ESP32 3.3V   ---- 水位感測器 (VCC)
ESP32 5V/VIN ---- 繼電器模組 (VCC)
ESP32 GND    ---- 所有 GND 共地
```

---

## 五、網路規劃

### 網路前提條件

> **重要：** 所有設備（中央伺服器、邊緣節點、水泵節點）必須能互相通訊。
> 如果使用 WiFi，請確認路由器**未啟用 AP 隔離**（Client Isolation），
> 否則 WiFi 設備之間無法互相 ping 通。建議中央伺服器使用**有線連接**以確保穩定性。

### 建議 IP 分配

| 設備              | IP 位址       | 說明                          |
| ----------------- | ------------- | ----------------------------- |
| 中央伺服器 (Pi 5) | 192.168.1.100 | 固定 IP 或 mDNS: sdprs.local（建議有線） |
| 邊緣節點 01       | DHCP          | 透過 SSH 隧道連回伺服器       |
| 邊緣節點 02       | DHCP          | 透過 SSH 隧道連回伺服器       |
| 水泵節點          | DHCP          | 透過 WiFi 連接 MQTT           |
| 保安電腦/手機     | DHCP          | 瀏覽器存取 http://sdprs.local |

### 端口使用

| 端口  | 服務      | 說明                   |
| ----- | --------- | ---------------------- |
| 80    | Nginx     | 儀表板入口（對外）     |
| 8000  | FastAPI   | 後端 API（內部）       |
| 1883  | Mosquitto | MQTT Broker（內部）    |
| 18554 | SSH 隧道  | glass_node_01 HLS 串流 |
| 18555 | SSH 隧道  | glass_node_02 HLS 串流 |
| 18556 | SSH 隧道  | glass_node_03 HLS 串流 |

### 隧道端口映射規則

```
glass_node_01 -> 隧道端口 18554
glass_node_02 -> 隧道端口 18555
glass_node_03 -> 隧道端口 18556
...
glass_node_NN -> 隧道端口 18553 + NN
```

---

## 六、部署前準備：燒錄 Pi OS

> **此步驟適用於所有 Raspberry Pi（中央伺服器和邊緣節點都需要）**

### 所需工具

- 一台電腦（Windows/Mac/Linux）
- microSD 卡讀卡器
- microSD 卡（32GB）

### 步驟

1. **下載 Raspberry Pi Imager**

   - 到 https://www.raspberrypi.com/software/ 下載並安裝
2. **燒錄系統**

   - 打開 Raspberry Pi Imager
   - 選擇設備：Raspberry Pi 5（伺服器）或 Raspberry Pi 4（邊緣節點）
   - 選擇系統：**Raspberry Pi OS Lite (64-bit)** -- **必須選 Lite 版本（無桌面）**
   - 選擇 SD 卡
   - **點擊齒輪圖示設定：**
     - 設定主機名稱：伺服器填 `sdprs-server`，邊緣節點填 `sdprs-glass-01`
     - 啟用 SSH：選擇「使用密碼認證」
     - 設定用戶名：`pi`，密碼：你的密碼
     - 設定 WiFi（可選，建議用有線）
     - 設定時區：Asia/Macau
   - 點擊「燒錄」
3. **插入 SD 卡並開機**

   - 將燒錄好的 SD 卡插入 Pi
   - 接上網路線和電源
   - 等待約 1 分鐘開機完成
4. **SSH 連線到 Pi**

   ```bash
   # 從你的電腦連線（Windows 可用 PowerShell 或 PuTTY）
   ssh pi@sdprs-server.local
   # 或用 IP
   ssh pi@192.168.1.100

   # 輸入你設定的密碼
   ```

   **如果找不到 Pi 的 IP：**

   - 檢查路由器管理頁面的 DHCP 客戶端列表
   - 或將 Pi 接上螢幕和鍵盤，登入後執行 `hostname -I`

---

## 七、中央伺服器部署（Pi 5）

> **預計時間：15-20 分鐘**（取決於網速）

### 步驟 1：SSH 連線到 Pi 5

```bash
ssh pi@sdprs-server.local
# 或
ssh pi@192.168.1.100
```

### 步驟 2：下載專案程式碼

**方法 A：使用 Git（推薦）**

```bash
sudo apt-get update && sudo apt-get install -y git
sudo git clone <你的-repo-url> /opt/sdprs
```

**方法 B：使用 USB 隨身碟**

```bash
# 1. 在你的電腦上將 sdprs 資料夾複製到 USB
# 2. 插入 Pi 的 USB 口
# 3. 在 Pi 上執行：
sudo mkdir -p /opt/sdprs
sudo mount /dev/sda1 /mnt
sudo cp -r /mnt/sdprs/* /opt/sdprs/
sudo umount /mnt
```

**方法 C：使用 rsync 一鍵部署（推薦，適合開發階段反覆更新）**

```bash
# 在你的開發電腦上執行（需要 SSH 連線到 Pi）
cd sdprs/scripts
chmod +x deploy_sync.sh

# 首次初始化（自動建立 venv、裝依賴、設定 systemd 服務）
SDPRS_SERVER_HOST=192.168.1.100 ./deploy_sync.sh init-server

# 之後每次代碼更新只需：
SDPRS_SERVER_HOST=192.168.1.100 ./deploy_sync.sh server
```

> **提示：** 使用此方法可跳過步驟 3-6，`init-server` 會自動完成所有設定。
> 詳見 [十六、日常運維 &gt; 代碼同步部署](#代碼同步部署開發階段)。

**方法 A、B 或 C 完成後，驗證：**

```bash
ls /opt/sdprs/
# 應該看到: central_server  deploy  edge_glass  edge_pump  scripts  shared  README.md
```

### 步驟 3：執行一鍵佈建腳本

```bash
cd /opt/sdprs/scripts
sudo chmod +x setup_server.sh
sudo ./setup_server.sh
```

**可選 -- 設定固定 IP：**

```bash
sudo ./setup_server.sh --static-ip 192.168.1.100
```

佈建腳本會**自動**完成以下工作：

- 設定 hostname 為 sdprs-server
- 設定時區為 Asia/Macau
- 安裝 avahi-daemon（mDNS，讓你可以用 sdprs.local 存取）
- 安裝 Python 3、Mosquitto MQTT、Nginx、SQLite
- 建立 sdprs 系統用戶
- 建立 Python 虛擬環境並安裝所有依賴
- 建立 .env 環境變數檔案
- 配置 Mosquitto MQTT Broker
- 配置 Nginx 反向代理
- 安裝並啟動 systemd 服務

### 步驟 4：修改密碼（非常重要！）

```bash
sudo nano /opt/sdprs/.env
```

**必須修改的三個密碼：**

```bash
DASHBOARD_PASS=改成你的強密碼          # 儀表板登入密碼
EDGE_API_KEY=改成隨機字串              # 邊緣節點的 API 金鑰
SECRET_KEY=改成另一個隨機字串           # Session 加密金鑰
```

> **提示：** 可以用這個命令生成隨機密碼：
>
> ```bash
> python3 -c "import secrets; print(secrets.token_hex(24))"
> ```

按 Ctrl+O 儲存，Ctrl+X 退出。

### 步驟 5：重啟服務使密碼生效

```bash
sudo systemctl restart sdprs-server
```

### 步驟 6：驗證部署成功

```bash
# 檢查三個核心服務狀態
sudo systemctl status sdprs-server   # 應顯示 active (running)
sudo systemctl status mosquitto      # 應顯示 active (running)
sudo systemctl status nginx          # 應顯示 active (running)
```

**如果任何服務不是 active (running)，查看錯誤日誌：**

```bash
journalctl -u sdprs-server --since "5 minutes ago" --no-pager
```

### 步驟 7：打開儀表板

在同一網路的任何電腦或手機瀏覽器中打開：

```
http://sdprs.local
```

或

```
http://192.168.1.100
```

使用以下帳號登入：

- **帳號：** admin（或你在 .env 中設定的 DASHBOARD_USER）
- **密碼：** 你在步驟 4 設定的 DASHBOARD_PASS

> **看到儀表板頁面 = 中央伺服器部署成功！**

---

## 八、中央伺服器部署（Docker 備用方案）

> 適用於**筆電**或**非 Raspberry Pi** 的 Linux/Mac/Windows 電腦。

### 前提條件

- 安裝 [Docker Desktop](https://www.docker.com/products/docker-desktop/) 或 Docker Engine
- 安裝 Docker Compose（Docker Desktop 已內建）

### 步驟 1：準備環境變數

```bash
cd sdprs
cp .env.example .env
```

編輯 .env，修改三個密碼（同上面步驟 4）。

> **注意：** `.env` 檔案必須在 `sdprs/` 專案根目錄下，Docker Compose 會自動引用此路徑。

### 步驟 2：啟動所有容器

```bash
cd deploy
docker compose up -d
```

> **注意：** 必須在 `deploy/` 目錄下執行 `docker compose` 命令。建置上下文為上層 `sdprs/` 目錄。

這會啟動三個容器：

| 容器      | 服務         | 端口         |
| --------- | ------------ | ------------ |
| sdprs-app | FastAPI 應用 | 8000（內部） |
| mosquitto | MQTT Broker  | 1883         |
| nginx     | 反向代理     | 80（對外）   |

### 步驟 3：驗證

```bash
# 查看容器狀態
docker compose ps
# 所有容器應顯示 Up (healthy)
# 健康檢查會定期存取 /api/health 端點確認服務正常

# 查看日誌
docker compose logs -f sdprs-app
```

瀏覽器打開 http://localhost 即可存取儀表板。

### Docker 常用命令

```bash
docker compose down          # 停止所有容器
docker compose restart       # 重啟所有容器
docker compose up -d --build # 重新建置並啟動
docker compose logs -f       # 查看所有容器日誌
```

---

## 八A、中央伺服器部署（Zeabur 雲端方案）

> **適用場景：WiFi AP 隔離、不允許有線連接、無法修改路由器設定。**
> 中央伺服器部署至 Zeabur 雲端（新加坡），Raspberry Pi 只需要出站網際網路連線。

### 前提條件

- GitHub 帳號（Zeabur 透過 GitHub 自動部署）
- Zeabur 帳號 (https://zeabur.com) 或 Tencent Cloud 帳號
- 已將 `sdprs` repo push 至 GitHub（含 `Dockerfile`）

### 步驟 1：建立 Zeabur 專案並部署伺服器

1. 登入 Zeabur → **新建專案**
2. 選擇 **Deploy from GitHub** → 選擇 `sdprs` repo
3. Zeabur 自動偵測到 `Dockerfile` → 點擊部署
4. 進入服務設定 → **Variables** 頁面，添加以下環境變數：

   | 變數名稱 | 値 | 說明 |
   |---|---|---|
   | `SECRET_KEY` | 隨機 48 字元字串 | Session 加密金鑰（**必填**） |
   | `EDGE_API_KEY` | 隨機字串 | Pi 端 API 金鑰 |
   | `DASHBOARD_USER` | admin | 儀表板帳號 |
   | `DASHBOARD_PASS` | 強密碼 | 儀表板密碼 |
   | `MQTT_BROKER` | EMQX 部署後填入 | EMQX 服務地址 |
   | `MQTT_PORT` | 1883 | MQTT TCP 端口 |
   | `MQTT_USERNAME` | sdprs | EMQX 認證用戶名 |
   | `MQTT_PASSWORD` | 強密碼 | EMQX 認證密碼 |
   | `DATABASE_URL` | PostgreSQL 部署後自動注入 | PostgreSQL 連線串 |

   > **生成隨機密鑰：**
   > ```bash
   > python3 -c "import secrets; print(secrets.token_hex(24))"
   > ```

### 步驟 2：部署 PostgreSQL 資料庫

1. 在同一個 Zeabur 專案中，點擊 **新增服務**
2. 選擇 **Marketplace** → **PostgreSQL**
3. 部署完成後，Zeabur 會自動將 `DATABASE_URL` 注入到同專案所有服務
4. FastAPI 服務自動重新部署

### 步驟 3：部署 EMQX MQTT Broker

1. 點擊 **新增服務** → **Marketplace** → **EMQX**
   (或手動輸入 Docker image: `emqx/emqx:5`)
2. EMQX 部署後，在 **Networking** 頁面查看 TCP 端口
3. 記下外部 TCP 端口，填入 FastAPI 服務的 `MQTT_BROKER` 和 `MQTT_PORT`
4. 登入 EMQX Dashboard（預設 http://your-emqx:18083，帳密 admin/public）建立 MQTT 用戶：
   - 用戶名：`sdprs`
   - 密碼：與 `MQTT_PASSWORD` 環境變數相同

### 步驟 4：驗證雲端伺服器

部署完成後，在瀏覽器打開 Zeabur 提供的 URL：

```
https://your-app.zeabur.app/api/health
# 應回傳 {"status": "ok", "db_mode": "postgresql"}
```

登入儀表板：

```
https://your-app.zeabur.app/login
```

### 步驟 5：設定邀緣節點連接雲端

**5.1 修改 config.zeabur.yaml：**

```bash
sudo nano /opt/sdprs/edge_glass/config.zeabur.yaml
```

將 TODO 項目全部填入實際値：

```yaml
server:
  api_url: "https://your-app.zeabur.app/api"
  mqtt_broker: "hkg1.clusters.zeabur.com"  # 實際地址
  mqtt_port: 34567                          # 實際分配端口
  mqtt_username: "sdprs"
  mqtt_password: "你的MQTT密碼"
  api_key: "你的EDGE_API_KEY"
```

**5.2 安裝雲端模式 systemd 服務：**

```bash
sudo cp /opt/sdprs/edge_glass/systemd/sdprs-edge-cloud.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sdprs-edge-cloud
sudo systemctl start sdprs-edge-cloud

# 驗證
sudo systemctl status sdprs-edge-cloud
journalctl -u sdprs-edge-cloud -f
# 應看到 "Connected to MQTT" 連線日誌
```

> **注意：** 雲端模式不需要啟動 `autossh-tunnel.service`。
> `config.zeabur.yaml` 中 `stream.cloud_mode: true` 會自動跳過 SSH 隧道。

### 步驟 6：設定自動備份（可選，強烈建議）

見 [二十、Zeabur 雲端備份管理](#二十zeabur-雲端備份管理)

### 雲端部署驗證清單

- [ ] `https://your-app.zeabur.app/api/health` 回傳 `{"status": "ok"}`
- [ ] 儀表板可以成功登入
- [ ] Pi 端 `journalctl -u sdprs-edge-cloud -f` 看到 MQTT 連線成功
- [ ] 儀表板「系統狀態」頁顯示 Pi 節點為「在線」

---

## 九、玻璃偵測邊緣節點部署（Pi 4/Pi 5）

> **每個攝像頭對應一個邊緣節點。預計時間：20-25 分鐘**

### 步驟 1：燒錄 Pi OS 並開機

參照 [六、部署前準備](#六部署前準備燒錄-pi-os)，主機名稱設為 sdprs-glass-01。

### 步驟 2：SSH 連線到邊緣節點 Pi

```bash
ssh pi@sdprs-glass-01.local
```

### 步驟 3：下載專案程式碼

**方法 A：使用 Git**

```bash
sudo apt-get update && sudo apt-get install -y git
sudo git clone <你的-repo-url> /opt/sdprs
```

**方法 B：使用 USB 隨身碟**（同中央伺服器步驟 2 方法 B）

**方法 C：使用 rsync 一鍵部署（推薦）**

```bash
# 在你的開發電腦上執行
cd sdprs/scripts
chmod +x deploy_sync.sh

# 首次初始化（自動建 venv、裝依賴、設定 systemd）
SDPRS_GLASS_HOST=192.168.1.101 ./deploy_sync.sh init-glass 01
```

> **提示：** 使用此方法可跳過步驟 4-6，`init-glass` 會自動完成環境設定。
> 初始化後直接跳到 [步驟 7：修改邊緣節點的 API Key](#步驟-7修改邊緣節點的-api-key)。

### 步驟 4：執行一鍵佈建腳本

> **如果你使用了方法 C，請跳過此步驟，直接到步驟 7。**

```bash
cd /opt/sdprs/scripts
sudo chmod +x setup_pi.sh
sudo ./setup_pi.sh glass_node_01 192.168.1.100
```

**參數說明：**

- `glass_node_01`：這個節點的唯一 ID（第二台用 glass_node_02，依此類推）
- `192.168.1.100`：中央伺服器的 IP 位址

佈建腳本會**自動**完成：

- 設定 hostname、時區
- 配置 tmpfs 保護 SD 卡
- 啟用硬體 watchdog（異常自動重啟）
- 安裝 Python 3、FFmpeg、AutoSSH、mediamtx
- 安裝編譯依賴（python3-dev、portaudio19-dev，供 NumPy/PyAudio 編譯）
- 建立 Python 虛擬環境和依賴
- 生成 SSH 金鑰對
- 建立 config.yaml 和 .env.tunnel
- 安裝 systemd 服務

### 步驟 5：配置 SSH 金鑰（關鍵步驟！）

佈建腳本會顯示一個公鑰，類似：

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG... sdprs@sdprs-glass-01
```

**你需要將這個公鑰加入中央伺服器：**

```bash
# 1. 在邊緣節點上複製公鑰（已經顯示在螢幕上，也可以重新查看）
cat /home/sdprs/.ssh/id_ed25519.pub

# 2. SSH 到中央伺服器（開一個新的終端視窗）
ssh pi@192.168.1.100

# 3. 在中央伺服器上執行（將公鑰貼上去）
sudo mkdir -p /home/sdprs/.ssh
echo "ssh-ed25519 AAAAC3N...這裡貼上完整的公鑰..." | sudo tee -a /home/sdprs/.ssh/authorized_keys
sudo chown -R sdprs:sdprs /home/sdprs/.ssh
sudo chmod 600 /home/sdprs/.ssh/authorized_keys

# 4. 回到邊緣節點的終端
```

### 步驟 6：啟動 SSH 隧道

```bash
sudo systemctl start autossh-tunnel
```

### 步驟 7：修改邊緣節點的 API Key

```bash
sudo nano /opt/sdprs/edge_glass/config.yaml
```

找到 api_key 行，改成你在中央伺服器 .env 中設定的 EDGE_API_KEY 值：

```yaml
server:
  api_key: "改成和中央伺服器 EDGE_API_KEY 相同的值"
```

### 步驟 8：重啟邊緣服務

```bash
sudo systemctl restart sdprs-edge
```

### 步驟 9：驗證

```bash
# 檢查服務狀態
sudo systemctl status sdprs-edge        # 應顯示 active (running)
sudo systemctl status autossh-tunnel    # 應顯示 active (running)

# 查看即時日誌
journalctl -u sdprs-edge -f
# 應看到 "Connected to MQTT" 和偵測迴圈日誌
```

**在中央伺服器儀表板上確認：**

- 系統狀態頁應顯示此節點為 **在線**（綠色）

### 部署第二台邊緣節點

重複步驟 1-9，但修改：

- 主機名稱：sdprs-glass-02
- 佈建命令：`sudo ./setup_pi.sh glass_node_02 192.168.1.100`

---

## 十、水泵節點部署（ESP32）

### 步驟 1：安裝工具（在你的電腦上）

```bash
pip install esptool mpremote
```

### 步驟 2：使用佈建腳本

```bash
cd sdprs/scripts
chmod +x setup_esp32.sh

# 將 ESP32 用 USB 線連接到電腦
./setup_esp32.sh /dev/ttyUSB0
# Windows 用: ./setup_esp32.sh COM3
```

腳本會自動：下載 MicroPython 韌體、清除 ESP32 Flash、燒錄 MicroPython、上傳所有程式碼。

### 步驟 3：修改配置

```bash
mpremote connect /dev/ttyUSB0 edit config.py
```

**必須修改的值：**

```python
WIFI_SSID = "你的WiFi名稱"
WIFI_PASS = "你的WiFi密碼"
MQTT_BROKER = "192.168.1.100"    # 中央伺服器 IP
```

### 步驟 4：重啟 ESP32

```bash
mpremote connect /dev/ttyUSB0 reset
```

### 步驟 5：驗證

```bash
# 在中央伺服器上監聽 MQTT 水泵狀態
mosquitto_sub -h localhost -t "sdprs/edge/pump_node_01/pump_status" -v
# 應該每 10 秒看到一條 JSON 消息
```

---

## 十一、部署後完整驗證清單

在所有節點部署完成後，逐一確認以下項目：

### 中央伺服器

- [ ] `sudo systemctl status sdprs-server` 顯示 active (running)
- [ ] `sudo systemctl status mosquitto` 顯示 active (running)
- [ ] `sudo systemctl status nginx` 顯示 active (running)
- [ ] 瀏覽器打開 http://sdprs.local 能看到登入頁面
- [ ] 能用帳號密碼成功登入儀表板
- [ ] 儀表板右上角 WebSocket 狀態顯示綠色圓點

### 邊緣節點

- [ ] `sudo systemctl status sdprs-edge` 顯示 active (running)
- [ ] `sudo systemctl status autossh-tunnel` 顯示 active (running)
- [ ] 儀表板「系統狀態」頁面顯示此節點為「在線」
- [ ] 儀表板「監控牆」頁面能看到攝像頭畫面

### 水泵節點

- [ ] ESP32 上綠色 LED 亮起（表示泵停止，正常待機）
- [ ] `mosquitto_sub -h 192.168.1.100 -t "sdprs/edge/pump_node_01/pump_status"` 有資料
- [ ] 儀表板顯示水泵節點狀態

### MQTT 通訊測試

```bash
# 在中央伺服器上測試 MQTT 是否正常工作

# 終端 1：訂閱所有主題
mosquitto_sub -h localhost -t "sdprs/#" -v

# 終端 2：發送測試消息
mosquitto_pub -h localhost -t "sdprs/test" -m "hello"
# 終端 1 應該看到這條消息
```

---

## 十二、儀表板使用說明

所有頁面使用**繁體中文**介面，支援桌面和手機瀏覽器。

### 頁面功能

| 頁面     | 網址         | 功能                           |
| -------- | ------------ | ------------------------------ |
| 登入     | /login       | 輸入帳號密碼登入               |
| 儀表板   | /            | 警報列表、統計數字、即時更新   |
| 警報詳情 | /alerts/{id} | 查看影片、啟動串流、標記已處理 |
| 監控牆   | /monitor     | 所有攝像頭即時快照（每秒刷新） |
| 系統狀態 | /system      | 節點列表、CPU/記憶體、連線狀態 |

### 保安人員操作流程

1. **收到警報** -- 瀏覽器頁面自動彈出新警報行 + 播放警示音
2. **查看詳情** -- 點擊「查看」按鈕，觀看錄製的影片
3. **啟動串流** -- 點擊「啟動串流」按鈕，即時查看攝像頭畫面
4. **標記處理** -- 確認情況後，點擊「標記已處理」按鈕
5. **靜音/取消靜音** -- 點擊右上角的喇叭圖示

### WebSocket 即時推送事件

| 事件           | 觸發時機       | 儀表板效果                   |
| -------------- | -------------- | ---------------------------- |
| new_alert      | 偵測到玻璃破裂 | 新行插入 + 黃色閃爍 + 音效   |
| alert_updated  | 影片上傳完成   | 狀態更新為「待處理」         |
| alert_resolved | 保安標記已處理 | 狀態更新為「已處理」（綠色） |
| node_status    | 節點上線/離線  | 更新系統狀態頁               |
| pump_status    | 水泵啟動/停止  | 更新水泵計數                 |

---

## 十三、配置參考

### 13.1 中央伺服器 .env

```bash
# === 必須修改的值 ===
DASHBOARD_USER=admin                     # 儀表板帳號
DASHBOARD_PASS=你的強密碼                 # 儀表板密碼
EDGE_API_KEY=隨機字串                     # 邊緣節點 API 金鑰
SECRET_KEY=另一個隨機字串                  # Session 加密金鑰

# === 通常不需要修改 ===
MQTT_BROKER=localhost                    # Docker 環境改為 mosquitto
MQTT_PORT=1883
DB_PATH=/opt/sdprs/data/sdprs.db        # Docker 環境: /app/data/sdprs.db
STORAGE_PATH=/opt/sdprs/storage          # MP4 影片儲存根目錄
RETENTION_DAYS=30                        # 資料保留天數
SERVER_HOST=0.0.0.0                      # 伺服器監聽地址
SERVER_PORT=8000                         # 伺服器監聽端口

# === Zeabur 雲端部署專用（本地小機可不設）===
MQTT_USERNAME=                           # EMQX 認證用戶名
MQTT_PASSWORD=                           # EMQX 認證密碼
MQTT_USE_TLS=false                       # 是否啟用 TLS
DATABASE_URL=                            # PostgreSQL 連線串（空 = 使用 SQLite）
```

### 13.2 邊緣節點 config.yaml

```yaml
node_id: "glass_node_01"                # 每台不同

# 攝像頭設定
camera:
  source: 0                             # 0 = 預設攝像頭，或裝置路徑
  resolution: [1280, 720]               # 720p（不要改成更高，記憶體限制）
  fps: 15                               # 幀率

# 環形緩衝
buffer:
  duration_seconds: 10                  # 緩衝時長（秒）

# 視覺偵測（OpenCV 10步管線）
visual:
  edge_density_threshold: 1.5           # 相對基線倍率
  baseline_window_seconds: 60           # 自適應基線窗口
  brightness_anomaly_percent: 50        # 亮度異常排除閾值
  min_contour_length_px: 100            # 最小輪廓長度（像素）
  roi_polygon: [[100,50],[1180,50],[1180,670],[100,670]]
  canny_threshold1: 50
  canny_threshold2: 150

# 音訊偵測（FFT 6步管線 + 自適應基線）
audio:
  device_index: 1                       # 麥克風裝置編號
  mode: "adaptive"                      # "adaptive" | "fixed"
  sample_rate: 44100
  channels: 1
  chunk_size: 512
  rolling_baseline_seconds: 30          # 滾動基線窗口
  delta_db_threshold: 20                # 突增 dB 閾值
  spectral_flatness_threshold: 0.3
  attack_time_ms: 10
  analysis_window_ms: 500
  # 固定模式參數（備用）
  fixed_db_threshold: 90
  fixed_freq_threshold_hz: 3000

# 融合觸發引擎
trigger:
  correlation_window_seconds: 2         # 視覺+音訊關聯窗口
  cooldown_seconds: 30                  # 冷卻期

# 熱管理
thermal:
  fps_reduce_temp: 75                   # 降低幀率溫度
  pause_visual_temp: 80                 # 暫停視覺處理溫度
  critical_alert_temp: 85               # 嚴重警報溫度

# 伺服器連線
server:
  api_url: "http://192.168.1.100:8000/api"
  api_key: "和伺服器 EDGE_API_KEY 相同"   # 必須修改！
  mqtt_broker: "192.168.1.100"
  mqtt_port: 1883

# HLS 串流
stream:
  type: "hls"
  auto_stop_minutes: 5
  tunnel_port: 18554                    # 每台不同！見端口映射表

# 連續監控快照
snapshot:
  enabled: true
  fps: 1                               # 快照幀率
  fps_degraded: 0.2                     # 降級模式（每5秒1張）
  width: 854
  height: 480
  jpeg_quality: 50

# 事件本地儲存
events:
  local_backup_dir: "./events"
  max_local_files: 20

# 時區
timezone: "Asia/Macau"
```

### 13.3 雲端版 config.zeabur.yaml (重要項目)

修改此檔案中所有標記 `# TODO` 的行：

| 項目 | 說明 |
|---|---|
| `server.api_url` | Zeabur 提供的 HTTPS URL |
| `server.mqtt_broker` | EMQX 服務地址 |
| `server.mqtt_port` | Zeabur 分配的 TCP 端口 |
| `server.mqtt_password` | 與雲端 `MQTT_PASSWORD` 環境變數一致 |
| `server.api_key` | 與雲端 `EDGE_API_KEY` 環境變數一致 |

### 13.4 ESP32 config.py

```python
WIFI_SSID = "你的WiFi名稱"
WIFI_PASS = "你的WiFi密碼"
MQTT_BROKER = "192.168.1.100"          # 中央伺服器 IP
MQTT_PORT = 1883
NODE_ID = "pump_node_01"

# 水位閾值（百分比）
HIGH_THRESHOLD = 80                    # >=80% 啟動泵
LOW_THRESHOLD = 20                     # <=20% 關閉泵

# GPIO 腳位
RELAY_PIN = 26                         # 繼電器
LED_RED_PIN = 27                       # 紅燈（泵運行）
LED_GREEN_PIN = 25                     # 綠燈（泵停止）
ADC_PIN = 34                           # 水位感測器

# 時間間隔（秒）
PUBLISH_INTERVAL = 10                  # 每 10 秒上報一次
POLL_INTERVAL = 1                      # 每 1 秒讀取水位
```

---

## 十四、API 參考

啟動後可存取自動生成的互動式文件：

- **Swagger UI**: http://sdprs.local/docs
- **ReDoc**: http://sdprs.local/redoc

### 端點總覽

| 方法  | 路徑                        | 認證方式            | 說明               |
| ----- | --------------------------- | ------------------- | ------------------ |
| GET   | /api/health                 | 無                  | 健康檢查           |
| POST  | /api/alerts                 | X-API-Key           | 建立新警報         |
| GET   | /api/alerts                 | X-API-Key / Session | 取得警報列表       |
| GET   | /api/alerts/{id}            | X-API-Key / Session | 取得警報詳情       |
| PUT   | /api/alerts/{id}/video      | X-API-Key           | 上傳 MP4 影片      |
| PATCH | /api/alerts/{id}/resolve    | Session             | 標記已處理         |
| POST  | /api/snapshots/{node_id}    | X-API-Key           | 上傳快照           |
| GET   | /api/snapshots/{node_id}    | Session             | 取得最新快照       |
| GET   | /api/nodes                  | Session             | 節點列表           |
| GET   | /api/nodes/summary          | Session             | 節點統計           |
| POST  | /api/stream/{node_id}/start | Session             | 啟動串流           |
| POST  | /api/stream/{node_id}/stop  | Session             | 停止串流           |
| WS    | /ws                         | Session Cookie      | WebSocket 即時推送 |

---

## 十五、MQTT 主題參考

| 主題                                      | QoS | 方向           | 說明                            |
| ----------------------------------------- | --- | -------------- | ------------------------------- |
| sdprs/edge/{node_id}/heartbeat            | 0   | Edge -> Server | 心跳（CPU溫度、記憶體，每30秒） |
| sdprs/edge/{node_id}/pump_status          | 0   | Pump -> Server | 水泵狀態 + 水位（每10秒）       |
| sdprs/edge/{node_id}/stream_status        | 1   | Edge -> Server | 串流狀態（啟動/停止）           |
| sdprs/edge/{node_id}/cmd/stream_start     | 1   | Server -> Edge | 啟動串流命令                    |
| sdprs/edge/{node_id}/cmd/stream_stop      | 1   | Server -> Edge | 停止串流命令                    |
| sdprs/edge/{node_id}/cmd/update           | 1   | Server -> Edge | 遠端更新觸發                    |
| sdprs/edge/{node_id}/cmd/simulate_trigger | 1   | Server -> Edge | 測試觸發                        |

---

## 十六、日常運維

### 服務管理命令

```bash
# ===== 中央伺服器 =====
sudo systemctl start sdprs-server       # 啟動
sudo systemctl stop sdprs-server        # 停止
sudo systemctl restart sdprs-server     # 重啟
sudo systemctl status sdprs-server      # 查看狀態
journalctl -u sdprs-server -f           # 即時日誌
journalctl -u sdprs-server --since today # 今天的日誌

# ===== 邊緣節點 =====
sudo systemctl restart sdprs-edge
journalctl -u sdprs-edge -f

# ===== SSH 隧道 =====
sudo systemctl restart autossh-tunnel
journalctl -u autossh-tunnel -f

# ===== MQTT =====
sudo systemctl status mosquitto

# ===== Nginx =====
sudo nginx -t                           # 測試配置語法
sudo systemctl reload nginx             # 重載配置（不中斷連線）
```

### 日誌位置

| 日誌         | 查看方式                            |
| ------------ | ----------------------------------- |
| FastAPI 應用 | journalctl -u sdprs-server -f       |
| 邊緣節點     | journalctl -u sdprs-edge -f         |
| SSH 隧道     | journalctl -u autossh-tunnel -f     |
| Nginx 存取   | cat /var/log/nginx/sdprs-access.log |
| Nginx 錯誤   | cat /var/log/nginx/sdprs-error.log  |
| MQTT Broker  | journalctl -u mosquitto -f          |

### 備份

```bash
# 備份資料庫
sudo cp /opt/sdprs/data/sdprs.db /backup/sdprs-$(date +%Y%m%d).db

# 備份事件影片
sudo rsync -av /opt/sdprs/storage/events/ /backup/events/

# 備份配置
sudo cp /opt/sdprs/.env /backup/.env.bak
```

### 代碼同步部署（開發階段）

使用 `deploy_sync.sh` 將本地修改增量同步到 Pi，自動重啟服務。

#### 首次部署（init）

```bash
cd sdprs/scripts
chmod +x deploy_sync.sh

# 首次初始化中央伺服器（建立 venv、裝依賴、設定 systemd）
SDPRS_SERVER_HOST=192.168.1.100 ./deploy_sync.sh init-server

# 首次初始化邊緣節點（用 IP 連線）
SDPRS_GLASS_HOST=192.168.1.101 ./deploy_sync.sh init-glass 01
```

> **重要：** venv 必須在 Pi 上建立（ARM 架構），不能從開發機（x86）同步。
> `init-server` / `init-glass` 會自動處理 piwheels 連線問題、建立系統用戶、安裝 systemd 服務。
> **前提：** `init` 命令需要 SSH 金鑰認證（密碼認證會導致遠端腳本卡住），請先執行 `ssh-copy-id`。

初始化完成後需在邊緣節點上修改 `config.yaml`：

```bash
ssh pi@192.168.1.101 'sudo nano /opt/sdprs/edge_glass/config.yaml'
# 修改 server.api_url、server.api_key、server.mqtt_broker 指向中央伺服器
```

#### 日常代碼更新

```bash
# 同步到中央伺服器 (Pi 5) — 自動更新依賴 + 重啟服務
SDPRS_SERVER_HOST=192.168.1.100 ./deploy_sync.sh server

# 同步到邊緣節點 01 (Pi 4/5) — 用 IP 連線
SDPRS_GLASS_HOST=192.168.1.101 ./deploy_sync.sh glass 01

# 同步到所有節點（需配置 mDNS 或 /etc/hosts）
SDPRS_GLASS_NODES="01,02,03" ./deploy_sync.sh all

# 預覽模式（不實際執行，只顯示會同步的檔案）
./deploy_sync.sh --dry-run server
```

#### SSH 免密碼登入（強烈建議）

腳本涉及多次 SSH 連線（檢查、rsync、pip、重啟），不配置金鑰需要反覆輸入密碼。

**WSL / Linux / Mac：**

```bash
# 1. 生成金鑰（如果還沒有）
ssh-keygen -t ed25519
# 按 Enter 使用預設路徑，密碼留空（或設定 passphrase）

# 2. 複製公鑰到 Pi
ssh-copy-id pi@192.168.1.100

# 3. 測試免密碼登入
ssh pi@192.168.1.100
# 應該直接登入，不要求密碼
```

**Windows PowerShell（無 WSL）：**

Windows 沒有 `ssh-copy-id` 命令，需手動操作：

```powershell
# 1. 生成金鑰（如果還沒有）
ssh-keygen -t ed25519
# 金鑰預設存放在 C:\Users\你的用戶名\.ssh\id_ed25519

# 2. 查看公鑰內容（複製輸出）
type $env:USERPROFILE\.ssh\id_ed25519.pub

# 3. 將公鑰傳送到 Pi（一行命令）
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh pi@192.168.1.100 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
# 輸入一次密碼後，之後就不需要了

# 4. 測試免密碼登入
ssh pi@192.168.1.100
```

**Windows CMD：**

```cmd
# 1. 生成金鑰
ssh-keygen -t ed25519

# 2. 查看公鑰
type %USERPROFILE%\.ssh\id_ed25519.pub

# 3. 手動複製公鑰到 Pi
# 方法：將上面輸出的公鑰整行複製，然後 SSH 到 Pi 貼上
ssh pi@192.168.1.100
mkdir -p ~/.ssh
echo "ssh-ed25519 AAAA...你的公鑰..." >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
exit
```

> **提示：** 如果你同時有中央伺服器和邊緣節點，需要對每台 Pi 都執行一次公鑰複製。

> **安全提示：** 腳本會自動排除 `.env`、`data/`、`storage/`、`venv/` 等目錄，不會覆蓋目標機器上的密碼和資料。

### 自動資料清理

系統每天凌晨 3:00 自動清理超過 RETENTION_DAYS（預設 30 天）的：

- 資料庫中的舊事件記錄
- 對應的 MP4 影片檔案

### 資料庫維護

```bash
# 檢查 WAL 模式是否正常
sqlite3 /opt/sdprs/data/sdprs.db "PRAGMA journal_mode;"
# 應返回: wal

# 手動整理資料庫（可選，每月一次）
sqlite3 /opt/sdprs/data/sdprs.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

---

## 十七、故障排除

### 問題 1：儀表板打不開 (HTTP 502 Bad Gateway)

```bash
# 1. 檢查 FastAPI 是否在運行
sudo systemctl status sdprs-server
# 如果是 failed，查看原因：
journalctl -u sdprs-server --since "10 minutes ago" --no-pager

# 2. 檢查 Nginx 配置
sudo nginx -t

# 3. 手動啟動試試
cd /opt/sdprs
sudo -u sdprs /opt/sdprs/central_server/venv/bin/uvicorn central_server.main:app --port 8000
# 看看有什麼錯誤訊息
```

### 問題 2：邊緣節點顯示離線

```bash
# 在邊緣節點上檢查
sudo systemctl status sdprs-edge

# 檢查網路是否通
ping 192.168.1.100

# 檢查 MQTT 連線
mosquitto_sub -h 192.168.1.100 -t "sdprs/#" -v
# 如果連不上，檢查中央伺服器的 Mosquitto
```

### 問題 3：串流沒有畫面

```bash
# 1. 檢查 SSH 隧道是否正常
sudo systemctl status autossh-tunnel

# 2. 檢查 mediamtx 是否在運行
ps aux | grep mediamtx

# 3. 檢查隧道端口是否在中央伺服器上監聽
# 在中央伺服器上執行：
ss -tlnp | grep 18554
# 應該看到 LISTEN 狀態
```

### 問題 4：WebSocket 斷連（儀表板右上角紅色圓點）

- 確認你已經**登入**（WebSocket 需要 Session Cookie）
- 重新整理頁面（按 F5）
- 檢查 FastAPI 服務是否在運行

### 問題 5：水泵不動作

```bash
# 1. 檢查 ESP32 是否在線（監聽 MQTT）
mosquitto_sub -h 192.168.1.100 -t "sdprs/edge/pump_node_01/pump_status" -v
# 如果沒有資料，檢查 ESP32 WiFi 和 MQTT 配置

# 2. 手動發送控制命令測試
mosquitto_pub -h 192.168.1.100 \
  -t "sdprs/edge/pump_node_01/cmd" \
  -m '{"action":"ON"}'
```

### 問題 6：Pi 過熱重啟

```bash
# 查看 CPU 溫度
vcgencmd measure_temp
# 如果超過 80 度，檢查散熱器是否安裝正確

# 查看是否有降頻
vcgencmd get_throttled
# 0x0 = 正常，其他值 = 有問題
```

### 問題 7：pip install 極慢或不斷重試 (Connection reset by peer)

Pi OS 預設配置了 piwheels.org 作為 pip 額外索引源，但該伺服器連線不穩定。

```bash
# 檢查是否存在 piwheels 配置
cat /etc/pip.conf

# 移除 piwheels（備份後）
sudo mv /etc/pip.conf /etc/pip.conf.bak

# 重新安裝依賴
/opt/sdprs/central_server/venv/bin/pip install -r /opt/sdprs/central_server/requirements.txt --prefer-binary
```

> **注意：** `deploy_sync.sh init-server` 已自動處理此問題。

### 問題 8：節點之間 WiFi 無法互相 ping 通 (Destination Host Unreachable)

如果同一子網的設備互相 ping 顯示 `Destination Host Unreachable`，但開發電腦能 ping 到所有設備：

**原因：** 路由器啟用了 **AP 隔離（Client Isolation）**，阻止 WiFi 客戶端之間直接通訊。

**解法（選一個）：**
1. **關閉 AP 隔離**（推薦）— 登入路由器管理頁面，找 WiFi 設定 → AP Isolation → 關閉
2. **中央伺服器改用有線連接** — 有線和 WiFi 之間通常不受 AP 隔離限制
3. **兩台都接有線**（最穩定，正式部署建議）
4. **部署中央伺服器至雲端**（場地不允許有線/路由器設定時）— 見 [八A、Zeabur 雲端方案](#八a中央伺服器部署zeabur-雲端方案)

**驗證：**
```bash
# 在邊緣節點上 ping 中央伺服器
ping -c 2 <中央伺服器IP>
# 應該看到回應，不是 Unreachable
```

### 問題 9：SD 卡寫入錯誤

邊緣節點已配置 tmpfs，日誌不會寫入 SD 卡。但如果仍有問題：

```bash
# 檢查 SD 卡健康度
sudo dmesg | grep -i "error\|fail\|mmc"

# 檢查 tmpfs 掛載
df -h | grep tmpfs
```

### 問題 10：Zeabur 服務不斷 CRASH

**其一：Build 日誌為空**

檢查 `Dockerfile` 是否在 repo 根目錄：

```bash
ls sdprs/Dockerfile
# 如果不存在，重新推送：
git add Dockerfile && git commit -m "add Dockerfile" && git push
```

**其二：Build 成功但 Runtime crash**

在 Zeabur 面板查看 Runtime Logs，最常見原因：

| 錯誤訊息 | 原因 | 解法 |
|---|---|---|
| `SECRET_KEY not set` | SessionMiddleware 必需此項 | 在 Variables 添加 `SECRET_KEY` |
| `No module named 'asyncpg'` | 依賴未安裝 | 檢查 requirements.txt 含 asyncpg |
| `Address already in use` | PORT 衝突 | 檢查 CMD 使用 `$PORT` |
| `StaticFiles directory not found` | 静態檔目錄缺失 | 檢查 `central_server/static/` 目錄 |

**其三：EMQX TCP 端口連不上**

```bash
# 在 Pi 上測試連線
nc -zv hkg1.clusters.zeabur.com 34567
# 如果超時，改用 WebSocket 模式：
# mqtt_broker: "your-app.zeabur.app"
# mqtt_port: 443
# mqtt_use_tls: true
```

**其四：儀表板 WebSocket 斷線**

- 確認已登入（WebSocket 需要 Session Cookie）
- 檢查 `SECRET_KEY` 環境變數是否已設定
- Zeabur 自動提供 HTTPS + wss://，确認 URL 使用 `https://`

---

## 十八、安全建議

1. **修改所有預設密碼** -- 佈建後**立即**修改 .env 中的三個密碼
2. **啟用 HTTPS** -- 生產環境在 Nginx 配置 SSL 憑證（Let's Encrypt）
3. **網路隔離** -- 將 IoT 設備放在獨立 VLAN，與辦公網路隔離
4. **定期輪換金鑰** -- 每季更換 SSH 金鑰和 API Key
5. **防火牆** -- 只開放端口 80 和 1883，隧道端口只綁定 127.0.0.1
6. **定期備份** -- 每週備份資料庫和影片到外部儲存
7. **系統更新** -- 每月執行 `sudo apt update && sudo apt upgrade`
8. **MQTT 認證** -- 生產環境啟用 Mosquitto 帳號密碼（見 `deploy/mosquitto.conf` 中的說明）
9. **上傳驗證** -- MP4 影片上傳限制 100MB 且僅允許 video/* MIME 類型；快照限制 5MB
10. **XSS 防護** -- 儀表板 JavaScript 使用 DOM API（textContent/createElement），不使用 innerHTML
11. **SSH 安全** -- 反向隧道使用 `StrictHostKeyChecking=accept-new`（首次連線自動接受，後續驗證）
12. **systemd 速率限制** -- 所有服務配置 `StartLimitIntervalSec` 和 `StartLimitBurst`，防止崩潰迴圈
13. **佔位密碼偵測** -- 伺服器啟動時自動檢查 .env 中是否仍使用預設佔位密碼，並發出警告
14. **Zeabur 環境變數安全** -- 所有密鑰透過 Zeabur Variables 設定，禁止將 `changeme` 密鑰上傳至 GitHub
15. **備份可用性驗證** -- 定期執行 `restore_to_zeabur.sh` 驗證備份檔案可屬恢復
16. **EMQX 安全** -- 雲端 EMQX 建議啟用認證，禁用匿名登入

---

## 十九、技術決策摘要

> 完整技術決策文件見 tech_decisions.md

| 決策項目     | 選擇                  | 原因                                               |
| ------------ | --------------------- | -------------------------------------------------- |
| 影像解析度   | 720p@15fps            | Pi 4 記憶體限制（環形緩衝 ~415MB）                 |
| 音訊偵測模式 | 自適應基線            | 颱風噪音重疊，固定閾值不適用                       |
| 警報模式     | Alert-First           | JSON <1秒送達，MP4 隨後上傳                        |
| 串流格式     | HLS (mediamtx)        | 瀏覽器原生支援，無需安裝外掛                       |
| 串流通道     | SSH 反向隧道          | 穿透 NAT/防火牆，不需公網 IP                       |
| 資料庫       | SQLite + WAL          | 適合 Pi 環境，未來可遷移 PostgreSQL                |
| Web 框架     | FastAPI               | 原生 async，支援並發快照+MP4+WebSocket             |
| 前端         | Jinja2 + Tailwind CDN | 伺服器端渲染，無需 Node.js 建置                    |
| 認證         | 三層分離              | API Key (Edge) / Session (Dashboard) / Cookie (WS) |
| 部署         | systemd               | Pi 原生支援，開機自啟，看門狗整合                  |
| 備用部署     | Docker Compose        | 筆電/伺服器開發測試環境                            |
| 雲端部署     | Zeabur / Tencent Cloud VPS | AP 隔離場地，繞過 Pi-to-Pi 連線限制             |
| 雲端資料庫   | PostgreSQL            | 雲端容器無持久化，SQLite 不適用                    |
| 雲端 MQTT    | EMQX                  | 支援帳密認證、TLS、WebSocket 443                    |
| 備份策略     | Pi cron + SSH         | Pi 主動出站，繞過 AP 隔離限制                    |
| 冷卻期       | 30秒                  | 防止同一事件重複觸發                               |
| 水泵控制     | 滯後 80%/20%          | 防止頻繁啟停損壞泵                                 |

---

## 二十、Zeabur 雲端備份管理

> 適用雲端部署模式。備份由 Pi 主動發起，繞過 AP 隔離限制。

### 前提條件：設定 Pi SSH 金鑰

```bash
# 在 Pi 上執行（以 sdprs 用戶）
ssh-keygen -t ed25519 -f /home/sdprs/.ssh/zeabur_backup -N ""

# 查看公鑰
cat /home/sdprs/.ssh/zeabur_backup.pub
# 複製輸出，登入 Zeabur 伺服器將公鑰貼入 ~/.ssh/authorized_keys
```

### 修改備份腳本

```bash
sudo nano /opt/sdprs/scripts/backup_from_zeabur.sh
```

填入以下兩個必填變數：

```bash
ZEABUR_HOST="xxx.xxx.xxx.xxx"   # Zeabur 伺服器 IP
DATABASE_URL="postgresql://..."  # Zeabur PostgreSQL 連線串
```

### 設定 Cron 自動備份

```bash
# 以 sdprs 用戶身份編輯 crontab
sudo crontab -u sdprs -e

# 加入（每天凌晨 3 點執行）
0 3 * * * /opt/sdprs/scripts/backup_from_zeabur.sh >> /var/log/sdprs_backup.log 2>&1
```

### 備份內容

| 類型 | 位置 | 保留 |
|---|---|---|
| PostgreSQL 即時檔 (.sql.gz) | `/opt/backup/sdprs/db/` | 30 天 |
| MP4 影片 (rsync 增量) | `/opt/backup/sdprs/storage/` | 全部 |

### 手動還原

```bash
# 還原最新備份
/opt/sdprs/scripts/restore_to_zeabur.sh

# 還原指定日期的備份
/opt/sdprs/scripts/restore_to_zeabur.sh sdprs_20260327_030000.sql.gz
```

### 驗證備份

```bash
# 手動執行一次，確認腳本正常
sudo -u sdprs /opt/sdprs/scripts/backup_from_zeabur.sh

# 檢查備份檔案
ls -lh /opt/backup/sdprs/db/
du -sh /opt/backup/sdprs/
```

---

## 授權

MIT License

## 貢獻

歡迎提交 Issue 和 Pull Request。
