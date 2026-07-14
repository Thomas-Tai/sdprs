# 配置參考

本文件彙整中央伺服器、邊緣節點與 ESP32 水泵節點的完整配置項目與範例，供部署與運維人員查閱。

← 返回[文件索引](../README.md)

## 中央伺服器 .env

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

## 邊緣節點 config.yaml

```yaml
node_id: "glass_node_01"                # 每台不同

# 攝像頭設定
camera:
  source: 0                             # 0 = 預設攝像頭 | "/dev/videoN" | "rtsp://..." 
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
  device_index: 0                       # PyAudio 裝置索引（用掃描腳本確認）
  mode: "adaptive"                      # "adaptive" | "fixed"
  sample_rate: 16000                    # C920: 16000 或 32000（不支援 44100）
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

## Zeabur 環境變數（中央伺服器）

| 變數名稱 | 必填 | 預設值 | 說明 |
|---|---|---|---|
| `DASHBOARD_USER` | **是** | — | 儀表板帳號 |
| `DASHBOARD_PASS` | **是** | — | 儀表板密碼 |
| `EDGE_API_KEY` | **是** | — | Pi 端 API 金鑰 |
| `SECRET_KEY` | **是** | — | Session 加密金鑰 |
| `MQTT_BROKER` | 否 | `localhost` | MQTT broker 地址 |
| `MQTT_PORT` | 否 | `1883` | MQTT TCP 端口 |
| `MQTT_USERNAME` | 否 | `""` | EMQX 認證用戶名 |
| `MQTT_PASSWORD` | 否 | `""` | EMQX 認證密碼 |
| `DATABASE_URL` | 否 | `""` | PostgreSQL 連線串（空 = SQLite） |

> **缺少前 4 個必填變數會導致 `pydantic ValidationError` 並 crash。**

## 雲端版 config.zeabur.yaml (Pi 端)

| 項目 | 說明 |
|---|---|
| `server.api_url` | Zeabur HTTPS URL（如 `https://sdprs.zeabur.app/api`） |
| `server.api_key` | 與雲端 `EDGE_API_KEY` 一致 |
| `server.mqtt_broker` | EMQX 公開 TCP 地址（非 Zeabur 內部名 `emqx`） |
| `audio.device_index` | PyAudio 掃描得到的 index（通常 `0`） |
| `audio.sample_rate` | 麥克風支援的 rate（C920: `16000`） |

> **MQTT 為可選。** MVP 方案不需要 MQTT，Pi 透過 HTTP POST 上傳快照和告警。

## ESP32 config.py

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
