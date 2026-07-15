# 配置參考

本文件彙整中央伺服器、邊緣節點與 ESP32 水泵節點的完整配置項目與範例，供部署與運維人員查閱。
所有欄位皆對照現行程式碼（`central_server/config.py` 的 `Settings`、
`edge_glass/utils/config_loader.py` 的 `DEFAULTS`、`edge_pump/config.py`）確認。

← 返回[文件索引](../README.md)

## 中央伺服器 .env

以 `pydantic-settings` 讀取 `.env`＋環境變數；`central_server/config.py::Settings` 為單一定義源。

### 必填（四個之一未設定會 `pydantic ValidationError` crash）

| 變數           | 說明                                             |
| -------------- | ------------------------------------------------ |
| `DASHBOARD_USER` | 儀表板／API 唯一帳號                            |
| `DASHBOARD_PASS` | 儀表板密碼                                       |
| `EDGE_API_KEY`   | 邊緣節點呼叫 REST API 的 X-API-Key              |
| `SECRET_KEY`     | Session Cookie 簽章金鑰（由 Settings 讀取，禁用裸 `os.environ`） |

啟動時若偵測到佔位值（`changeme` / `your-secret-key` / `test-key` 等）會警告。

### 選填（有預設值）

| 變數                        | 預設                          | 說明                                                                 |
| --------------------------- | ----------------------------- | -------------------------------------------------------------------- |
| `MQTT_BROKER`               | `localhost`                   | MQTT broker 主機                                                     |
| `MQTT_PORT`                 | `1883`                        | MQTT TCP 埠                                                          |
| `MQTT_USERNAME`             | `""`                          | Mosquitto 認證用戶（雲端部署）                                       |
| `MQTT_PASSWORD`             | `""`                          | Mosquitto 認證密碼                                                   |
| `MQTT_USE_TLS`              | `false`                       | 啟用 TLS（雲端 Mosquitto 若前置 TLS 代理則設 `true`）                |
| `DATABASE_URL`              | `""`                          | **設定即啟用 PostgreSQL 後端**；為空時使用 SQLite                    |
| `DB_PATH`                   | `./data/sdprs.db`             | SQLite 檔案路徑（由 Settings 讀取，`DATABASE_URL` 空才生效）         |
| `RETENTION_DAYS`            | `30`                          | 事件與 MP4 保留天數（由 Settings 傳入排程器）                        |
| `STORAGE_PATH`              | `./storage`                   | **權威**的 MP4 儲存根目錄；下方有 `events/{node_id}/*.mp4` 子樹      |
| `STORAGE_DIR`               | —（不設）                     | **已棄用**；只有 `STORAGE_PATH` 仍為預設 `./storage` 時作為後備使用，並記警告日誌 |
| `SERVER_HOST`               | `0.0.0.0`                     | 伺服器綁定 IP                                                        |
| `SERVER_PORT`               | `8000`                        | 伺服器 TCP 埠                                                        |
| `COOKIE_SECURE`             | `false`                       | 生產部署於 HTTPS 後設 `true`（Session Cookie 加 `Secure`）           |
| `ALLOWED_NODE_IDS`          | `""`                          | 逗號分隔的允許 `node_id` 白名單；**空 = 允許全部（向後相容）**       |
| `LOGIN_MAX_ATTEMPTS`        | `5`                           | 同一 IP 登入失敗次數上限                                             |
| `LOGIN_LOCKOUT_SECONDS`     | `300`                         | 鎖定期（秒）                                                         |
| `CWA_API_KEY`               | `""`                          | 中央氣象署 Open Data 金鑰；**空 = 天氣服務完全停用**（`/api/weather/*` 回 503） |
| `CWA_STATION_ID`            | `C0Z100`                      | CWA 自動氣象站代碼                                                   |
| `CWA_TOWNSHIP`              | `新北市新店區`                | CWA 鄉鎮預報用地區名                                                 |
| `SITE_LAT`                  | `24.967`                      | 站台緯度（Open-Meteo 回退）                                          |
| `SITE_LON`                  | `121.541`                     | 站台經度                                                             |
| `WEATHER_REFRESH_SECONDS`   | `600`                         | 天氣資料重抓間隔                                                     |
| `WEATHER_CACHE_STALE_SECONDS` | `3600`                      | 快取失效判定閾值                                                     |
| `MEDIAMTX_METRICS_URL`      | `http://localhost:9998/metrics` | mediamtx Prometheus scrape；設為 `""` 則 `/api/stream/health` 停用   |

### 範例 `.env`

```bash
# === 必填 ===
DASHBOARD_USER=admin
DASHBOARD_PASS=你的強密碼
EDGE_API_KEY=隨機字串
SECRET_KEY=另一個隨機字串

# === 通常保持預設 ===
MQTT_BROKER=localhost
MQTT_PORT=1883
# 舊版 STORAGE_DIR 仍可使用但已棄用，請改用 STORAGE_PATH
STORAGE_PATH=/opt/sdprs/storage
DB_PATH=/opt/sdprs/data/sdprs.db
RETENTION_DAYS=30

# === 雲端部署 ===
MQTT_USERNAME=sdprs
MQTT_PASSWORD=zeabur-mqtt-secret
MQTT_USE_TLS=false
DATABASE_URL=postgresql+asyncpg://user:pw@host:5432/sdprs
CWA_API_KEY=CWA-XXXX
```

### 雲端環境變數速查（Zeabur / VPS）

| 變數 | 必填 | 預設 | 補充 |
| --- | --- | --- | --- |
| `DASHBOARD_USER` / `DASHBOARD_PASS` | 是 | — | 缺一即 crash |
| `EDGE_API_KEY` / `SECRET_KEY` | 是 | — | 缺一即 crash |
| `DATABASE_URL` | 建議 | `""` | 空 = SQLite；雲端容器應設 PostgreSQL |
| `COOKIE_SECURE` | 建議 | `false` | HTTPS 部署設 `true` |
| `CWA_API_KEY` | 選 | `""` | 空即停用天氣頁 |
| `MEDIAMTX_METRICS_URL` | 選 | `http://localhost:9998/metrics` | 空即隱藏串流健康 |

---

## 邊緣節點 config.yaml（玻璃節點）

`edge_glass/utils/config_loader.py` 以 deep-merge 匯入預設值後驗證必要欄位。

```yaml
node_id: "glass_node_01"              # 每台不同

# 攝像頭設定
camera:
  source: 0                           # 0 = 預設攝像頭 | "/dev/videoN" | "rtsp://..."
  resolution: [1280, 720]             # 720p（不要改成更高，記憶體限制）
  fps: 15                             # 幀率

# 環形緩衝（記憶體）
buffer:
  duration_seconds: 10                # 緩衝時長（秒）

# 事件擷取（非同步編碼路徑，預設關閉）
capture:
  async_encode: false                 # true 前需完成 §8 硬體台架驗證；預設 false = 走傳統阻塞編碼
  pre_roll_seconds: 4                 # 觸發前秒數（從環形緩衝切片）
  post_roll_seconds: 5                # 觸發後秒數
  encode_queue_size: 2                # 編碼工作線程佇列上限；滿即 drop-newest 並 WARNING

# 視覺偵測
visual:
  edge_density_threshold: 1.5
  baseline_window_seconds: 60
  brightness_anomaly_percent: 50
  min_contour_length_px: 100
  roi_polygon: [[100,50],[1180,50],[1180,670],[100,670]]
  canny_threshold1: 50
  canny_threshold2: 150
  anomaly_recovery_seconds: 3         # 亮度異常後恢復期（秒）— visual_detector 使用

# 音訊偵測
audio:
  device_index: 0                     # PyAudio 掃描（C920 通常為 0）
  mode: "adaptive"                    # "adaptive" | "fixed"
  sample_rate: 16000                  # C920：16000 或 32000（不支援 44100）
  channels: 1
  chunk_size: 512
  # 自適應模式
  rolling_baseline_seconds: 30
  delta_db_threshold: 20
  spectral_flatness_threshold: 0.3
  attack_time_ms: 10
  analysis_window_ms: 500
  # 固定模式（備用）
  fixed_db_threshold: -30             # dBFS（0 = 滿刻度），非 SPL——正值永遠無法觸發
  fixed_freq_threshold_hz: 3000

# 融合觸發
trigger:
  correlation_window_seconds: 2
  cooldown_seconds: 30

# 熱管理
thermal:
  fps_reduce_temp: 75
  pause_visual_temp: 80
  critical_alert_temp: 85

# 伺服器連線
server:
  api_url: "http://192.168.1.100:8000/api"
  api_key: "和伺服器 EDGE_API_KEY 相同"    # 必須修改
  mqtt_broker: "192.168.1.100"
  mqtt_port: 1883
  mqtt_username: ""                   # Mosquitto 帳號（雲端）
  mqtt_password: ""
  mqtt_use_tls: false

# HLS 串流
stream:
  auto_stop_minutes: 5
  tunnel_port: 18554                  # 每台不同（見端口映射表）
  cloud_mode: false                   # true = 跳過 SSH 隧道，改走 HTTP 上傳

# 連續監控快照（現已被 edge_glass_main 實際消費）
snapshot:
  enabled: true                       # false = 不啟動快照線程
  fps: 1                              # 正常幀率（秒 1 張）
  fps_degraded: 0.2                   # 降級模式（每 5 秒 1 張）— 熱管理／偵測降級時採用
  width: 854                          # 快照寬
  height: 480                         # 快照高
  jpeg_quality: 50                    # 1–100

# 事件本地儲存
events:
  local_backup_dir: "./events"        # 本地 MP4 備份目錄
  max_local_files: 20                 # 上限；超過就依 mtime 淘汰
```

**與舊版差異：**

- `stream.type` 已移除（`edge_glass` 不再讀取；程式一律走 HLS 由 mediamtx 提供）。
- 檔案層級 `timezone` 已移除（`edge_glass` 不使用；時區交由 OS）。
- `capture:` 為新增區塊；`async_encode` 出廠 `false`，等 §8 硬體台架驗證後才可啟用。
- `audio.fixed_db_threshold` 從舊值 `90` 改為 `-30`（dBFS 而非 SPL；舊值於 dBFS 尺度上永遠無法觸發）。
- `visual.anomaly_recovery_seconds` 為新增欄位（3 秒），由 `visual_detector` 消費。

## 雲端版 `config.zeabur.yaml`（Pi 端）

| 欄位                    | 說明                                                                                     |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| `server.api_url`        | Zeabur HTTPS URL（如 `https://sdprs.zeabur.app/api`）                                    |
| `server.api_key`        | 與雲端 `EDGE_API_KEY` 一致                                                               |
| `server.mqtt_broker`    | Mosquitto 公開 TCP 地址（非 Zeabur 內部名 `mosquitto`）                                  |
| `server.mqtt_username`  | 與雲端 `MQTT_USERNAME` 一致                                                              |
| `server.mqtt_password`  | 與雲端 `MQTT_PASSWORD` 一致                                                              |
| `server.mqtt_use_tls`   | Public TCP 用 `false`；WebSocket 443 用 `true`                                           |
| `stream.cloud_mode`     | `true` — 使 StreamManager 跳過 SSH 隧道                                                  |
| `audio.device_index`    | PyAudio 掃描結果（通常 `0`）                                                             |
| `audio.sample_rate`     | 麥克風支援值（C920：`16000`）                                                            |

> **MQTT 為可選。** MVP 若沒有 broker，可只走 HTTP POST 上傳快照與告警；設定
> `server.mqtt_broker` 才會啟用 MQTT 通道。

---

## ESP32 水泵節點 config.py（`edge_pump/config.py`）

純 MicroPython 語法，`setup_esp32.sh` 於刷寫時將以下 placeholder 值替換為現場設定
（`SSID` / `WIFI_PASS` / `MQTT_BROKER` / `MQTT_PORT` / `MQTT_USERNAME` / `MQTT_PASSWORD` / `NODE_ID`）。

```python
# ============ WiFi ============
SSID = "YOUR_WIFI_SSID"                # WiFi SSID（舊名 WIFI_SSID 已改為 SSID —— setup_esp32.sh 也用 SSID）
WIFI_PASS = "YOUR_WIFI_PASSWORD"       # WiFi 密碼

# ============ MQTT ============
MQTT_BROKER = "YOUR_BROKER_IP"         # 中央伺服器或雲端 Mosquitto 公開 IP
MQTT_PORT = 1883
MQTT_USERNAME = "pump_node_01"
MQTT_PASSWORD = "YOUR_MQTT_PASSWORD"
NODE_ID = "pump_node_01"

# 主題（依 shared/mqtt_topics.py 慣例，ESP32 不隨附該模組，字面值即可）
MQTT_TOPIC_STATUS = "sdprs/edge/" + NODE_ID + "/pump_status"
MQTT_TOPIC_HEARTBEAT = "sdprs/edge/" + NODE_ID + "/heartbeat"  # 保留未用

# ============ 水位滯後控制 ============
HIGH_THRESHOLD = 80    # >= 80 % 開泵
LOW_THRESHOLD = 20     # <= 20 % 關泵

# ============ GPIO ============
RELAY_PIN = 26         # 繼電器
LED_RED_PIN = 27       # 紅燈（泵運行）
LED_GREEN_PIN = 25     # 綠燈（待機）
ADC_PIN = 34           # 水位 ADC1_CH6

# 電池監測（出廠 None —— 未接線時懸空引腳會發布雜訊；接線後改為 35 / 21，見 §6 台架驗證）
BATTERY_ADC_PIN = None
POWER_SOURCE_PIN = None

# ============ 時間間隔（秒 / ms） ============
PUBLISH_INTERVAL = 10             # MQTT 發布間隔（秒）
POLL_INTERVAL = 1                 # 水位輪詢間隔（秒）
WIFI_RETRY_INTERVAL = 60          # WiFi 重連間隔（秒）
WIFI_CONNECT_TIMEOUT = 15         # 單次 WiFi 連線等待（秒）— mqtt_client._wait_wifi 使用
# 舊版 WIFI_MAX_RETRIES 已移除（改由 WIFI_RETRY_INTERVAL + 每次呼叫的短逾時控制）

# ============ 看門狗 ============
WDT_ENABLED = True                # 生產預設 True；開發除錯可暫時 False
WDT_TIMEOUT = 30000               # ms

# ============ 新增數位感測器（學生示範合併） ============
FLOAT_PIN = 32                    # 底部防干燒浮球（dry = LOW，內部上拉）
RAIN_PIN = 33                     # 雨水模組 DO（下雨 = LOW；模組供電 3.3V）
HIGH_WATER_PIN = 13               # 選用高水位感測器

# 感測器旗標：ship OFF 直到 §6 台架逐一驗證極性後才 True
# sensors.py 會將旗標為 False 的感測器降級為 None，
# control_logic 忽略 None，代表未台架驗證的節點自動回退為「僅類比水位」的舊行為。
LEVEL_ENABLED = True              # 類比水位 —— 主要，一直開啟
FLOAT_ENABLED = False             # 台架驗證極性後改 True
RAIN_ENABLED = False              # 台架驗證極性後改 True
HIGH_WATER_ENABLED = False

FLOAT_ACTIVE_LOW = True           # 依接線；模組供電 3.3V + 內部上拉時多為 True
RAIN_ACTIVE_LOW = True            # 「下雨 = LOW」時 True
HIGH_WATER_ACTIVE_LOW = False

# ============ 控制參數（ms） ============
RAIN_ON_THRESHOLD = 60            # 確認下雨後降低開泵門檻（80 → 60）
RAIN_CONFIRM_MS = 30000
DRY_OFF_DELAY_MS = 30000
BURST_ON_MS = 60000
BURST_COOLDOWN_MS = 30000
CONFLICT_MAX_MS = 900000          # 15 分鐘後 CONFLICT_LATCH_OFF
MAX_RUN_MS = 600000
REST_MS = 60000
DEBOUNCE_MS = 2500
SOCKET_TIMEOUT_S = 3              # MQTT socket 逾時（秒）— mqtt_client 套用於 broker socket
```

**極性提醒：** `*_ACTIVE_LOW` 依接線與上拉電阻設定；若拉高／拉低搞錯，感測器會呈「永遠觸發」
或「永遠不觸發」。§6 台架驗證的核心即在於逐一確認每個 `*_ACTIVE_LOW` 值。

---

## 部署腳本相關環境變數

`scripts/deploy_sync.sh` 讀取以下環境變數（未設定就走 mDNS `sdprs-server.local` / `sdprs-glass-NN.local`）。

| 變數                | 預設                         | 說明                                                             |
| ------------------- | ---------------------------- | ---------------------------------------------------------------- |
| `SDPRS_SSH_USER`    | `pi`                         | SSH 用戶名                                                       |
| `SDPRS_SERVER_HOST` | `sdprs-server.local`         | 中央伺服器地址（可用 IP 或主機名）                               |
| `SDPRS_GLASS_HOST`  | `sdprs-glass-NN.local`       | 邊緣節點地址（`NN` 為 `01`／`02`…）                              |
| `SDPRS_GLASS_NODES` | `01`                         | 節點編號列表（逗號分隔）；用於 `deploy_sync.sh all`              |

`scripts/setup_esp32.sh` 支援命令列參數 `--wifi-ssid` / `--wifi-pass` / `--mqtt-broker` /
`--mqtt-port` / `--mqtt-username` / `--mqtt-password` / `--node-id` / `--skip-flash` /
`--skip-config`（互動缺省時會逐項詢問，密碼欄位不回顯）。
