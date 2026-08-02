# -*- coding: utf-8 -*-
"""
SDPRS 水泵節點配置檔案
Smart Disaster Prevention Response System - Pump Node Configuration

此檔案包含 ESP32 水泵控制系統的所有配置參數。
純 MicroPython 語法，適用於 ESP32 DevKit。

部署前請修改 WiFi 和 MQTT 設定以匹配實際環境。

Usage:
    from config import *
"""

# ============ WiFi 設定 ============
# 部署前請修改為實際的 WiFi SSID 和密碼
SSID = "YOUR_WIFI_SSID"           # WiFi SSID
WIFI_PASS = "YOUR_WIFI_PASSWORD"       # WiFi 密碼

# ============ MQTT 設定 ============
MQTT_BROKER = "YOUR_BROKER_IP"  # 中央伺服器 IP (Pi 5)
MQTT_PORT = 1883
MQTT_USERNAME = "pump_node_01"
MQTT_PASSWORD = "YOUR_MQTT_PASSWORD"
NODE_ID = "pump_node_01"       # 此裝置的唯一識別碼
# canonical scheme 見 shared/mqtt_topics.py（ESP32 不隨附該模組，故此處為字面值）
MQTT_TOPIC_STATUS = "sdprs/edge/" + NODE_ID + "/pump_status"   # 水泵狀態發布主題
MQTT_TOPIC_HEARTBEAT = "sdprs/edge/" + NODE_ID + "/heartbeat"  # 保留未用 — 伺服器由 pump_status 推斷存活（PUMP_OFFLINE_TIMEOUT）

# ============ 水泵控制閾值（滞後控制 Hysteresis） ============
HIGH_THRESHOLD = 80    # 水位 >= 80% → 啟動水泵
LOW_THRESHOLD = 20     # 水位 <= 20% → 關閉水泵
# 在 20%~80% 之間維持當前狀態，防止頻繁開關

# ============ GPIO 引腳定義（ESP32 DevKit） ============
# Pinout aligned with bench build 2026-07-19 (student sketch authoritative):
#   RAIN=25  HIGH_WATER=26  FLOAT=32  RELAY=33
# LED pins moved off 25 (now RAIN) to avoid conflict.
RELAY_PIN = 33         # 繼電器控制（高電位 = ON）
LED_RED_PIN = 27       # 紅色 LED（水泵運行中）
LED_GREEN_PIN = 14     # 綠色 LED（待機） — was 25, moved for RAIN
ADC_PIN = 34           # 水位感測器 ADC 輸入（ADC1_CH6, 只讀引腳）— unwired; LEVEL_ENABLED=False

# Item 12: 電池監測引腳（選用）— 未接線時懸空引腳會發布雜訊電壓/來源跳動，
# 故出廠預設 None（跳過建構、payload 省略欄位）。接線後改為 35 / 21（§6 台架驗證）。
BATTERY_ADC_PIN = None    # 電池電壓 ADC 輸入（接線後改為 35，ADC1_CH7 只讀引腳）
POWER_SOURCE_PIN = None   # 電源來源檢測 GPIO（接線後改為 21；高電位 = 外接電源/UPS；低電位 = 電池）

# ============ 時間間隔 ============
# PUBLISH_INTERVAL lowered from 10→2 (2026-07-19) so the dashboard reacts
# within ~2s of a sensor state change instead of feeling like it lags on the
# 20s SPA safety-net poll cadence. One node at 2s ≈ 30 msgs/min, well
# below the broker throughput used by the glass fleet's heartbeats.
PUBLISH_INTERVAL = 2    # MQTT 發布間隔（秒）
POLL_INTERVAL = 1       # 水位讀取間隔（秒）
WIFI_RETRY_INTERVAL = 60  # WiFi 重連間隔（秒）
WIFI_CONNECT_TIMEOUT = 15  # 單次 WiFi 連線等待（秒）— 由 mqtt_client._wait_wifi 使用

# ============ 看門狗 (WDT) ============
WDT_ENABLED = True       # 生產預設為 True；開發除錯時可暫時改為 False
WDT_TIMEOUT = 30000       # WDT 逾時（毫秒）

# ============ 新增數位感測器（學生示範合併） ============
# Pin map aligned with bench build 2026-07-19 (see RELAY_PIN comment above).
FLOAT_PIN = 32          # 底部防干燒浮球開關（機械雙線）
RAIN_PIN = 25           # MHRD 雨水模組 DO — was 33 (which is now RELAY)
HIGH_WATER_PIN = 26     # XKC-Y25-V 高水位感測器 OUT（黃色線）— was 13

# Bench build 2026-07-19: no analog probe wired to GPIO 34 — LEVEL disabled so
# the phantom "100%" from an unwired input-only pin stops driving the pump ON.
# The 3 digital sensors ARE wired, so enable them; control_logic supports
# digital-only mode (HIGH_WATER as sole ON trigger, HYSTERESIS_ON gated on
# level_pct is not None). Polarity (ACTIVE_LOW flags) still needs Section A
# bench polarity check per pump-bench-commissioning.md before trusting the
# pump-ON decision — the values below are best-guess defaults.
LEVEL_ENABLED = False         # no analog probe wired
FLOAT_ENABLED = True          # verify polarity per §A before deploying
RAIN_ENABLED = True           # verify polarity per §A before deploying
HIGH_WATER_ENABLED = True     # verify polarity per §A before deploying

FLOAT_ACTIVE_LOW = True       # bottom float, contacts close when dry (pull-up idle HIGH)
RAIN_ACTIVE_LOW = True        # MHRD DO: LOW when raining (module active-low)
HIGH_WATER_ACTIVE_LOW = False # XKC-Y25-V: HIGH when water detected (typical)

# ============ 控制參數 ============
RAIN_ON_THRESHOLD = 60      # 確認下雨後降低開泵門檻（80 -> 60）
RAIN_CONFIRM_MS = 30000
DRY_OFF_DELAY_MS = 30000
BURST_ON_MS = 60000
BURST_COOLDOWN_MS = 30000
CONFLICT_MAX_MS = 900000    # 15 分鐘後鎖定 OFF 並持續告警
MAX_RUN_MS = 600000
REST_MS = 60000
DEBOUNCE_MS = 2500
SOCKET_TIMEOUT_S = 3        # MQTT socket 逾時（秒）— 由 mqtt_client 套用於 broker socket

# ============ 致動器 profile 與運作模式（spec §5.1）============
# 兩個正交軸，互不參照：
#   ACTUATOR_PROFILE — GPIO 33 另一端是什麼（安全時序、周邊是否存在）
#   PUMP_MODE        — 水位代表什麼意義（觸發邏輯）
# 預設值即為現行行為，既有節點重新燒錄後行為不變（spec §12.2）。
ACTUATOR_PROFILE = "PUMP_12V"   # "SOCKET_220V" | "PUMP_12V"
PUMP_MODE = "DRAIN"             # "DRAIN" | "COLLECT"

# ============ 市電箱周邊腳位（僅 SOCKET_220V 建構）============
CT_ADC_PIN = 39        # 電流互感器 — ADC1_CH3 只讀腳，WiFi 安全。35 已保留給電池
HOA_HAND_PIN = 13      # HOA 面板開關第 2 極；HIGH_WATER 移至 26 後釋出

# HOA 拉向「非 HAND」：斷線時韌體判定為 AUTO，熔接偵測維持啟用（傾向誤報）。
# 反向配置會讓斷線永久停用熔接偵測（傾向漏報）。誤報優於盲點。
HOA_HAND_ACTIVE_LOW = True

# CT 分級門檻（ADC counts，非安培 — spec §6.1 禁止發布精確電流值）。
# 這些是佔位值，必須在 §8.3 台架以已知電阻性負載校正後改寫。
CT_BAND_LOW = 40       # 低於此值視為無電流
CT_BAND_NORMAL = 120   # 低於此值視為輕載
CT_BAND_HIGH = 900     # 高於此值視為過載
CT_SAMPLE_RATE_HZ = 1000
CT_SAMPLE_CYCLES = 3   # 整數個市電週期，否則殘留基波會偏移 RMS
