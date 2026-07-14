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
RELAY_PIN = 26         # 繼電器控制（高電位 = ON）
LED_RED_PIN = 27       # 紅色 LED（水泵運行中）
LED_GREEN_PIN = 25     # 綠色 LED（待機）
ADC_PIN = 34           # 水位感測器 ADC 輸入（ADC1_CH6, 只讀引腳）

# Item 12: 電池監測引腳（選用）— 未接線時懸空引腳會發布雜訊電壓/來源跳動，
# 故出廠預設 None（跳過建構、payload 省略欄位）。接線後改為 35 / 21（§6 台架驗證）。
BATTERY_ADC_PIN = None    # 電池電壓 ADC 輸入（接線後改為 35，ADC1_CH7 只讀引腳）
POWER_SOURCE_PIN = None   # 電源來源檢測 GPIO（接線後改為 21；高電位 = 外接電源/UPS；低電位 = 電池）

# ============ 時間間隔 ============
PUBLISH_INTERVAL = 10   # MQTT 發布間隔（秒）
POLL_INTERVAL = 1       # 水位讀取間隔（秒）
WIFI_RETRY_INTERVAL = 60  # WiFi 重連間隔（秒）
WIFI_CONNECT_TIMEOUT = 15  # 單次 WiFi 連線等待（秒）— 由 mqtt_client._wait_wifi 使用

# ============ 看門狗 (WDT) ============
WDT_ENABLED = True       # 生產預設為 True；開發除錯時可暫時改為 False
WDT_TIMEOUT = 30000       # WDT 逾時（毫秒）

# ============ 新增數位感測器（學生示範合併） ============
FLOAT_PIN = 32          # 底部防干燒浮球開關（dry = LOW，內部上拉）
RAIN_PIN = 33           # 雨水模組 DO（下雨 = LOW；模組供電 3.3V）
HIGH_WATER_PIN = 13     # 選用數位高水位感測器

# Digital sensors ship OFF until bench-commissioned (spec §6). The student
# sketch, wiring doc, and toolkit pinout disagree on pin assignment / polarity,
# so an un-commissioned node runs ANALOG-ONLY (LEVEL): float/rain are ignored
# until each is polarity-verified on the bench, then flipped True here for field
# use. sensors.py degrades a disabled sensor to None and control_logic ignores
# it — so this is the safe default that reproduces the original analog behavior.
LEVEL_ENABLED = True          # analog water-level sensor — primary, always on
FLOAT_ENABLED = False         # enable ONLY after §6 bench commissioning
RAIN_ENABLED = False          # enable ONLY after §6 bench commissioning
HIGH_WATER_ENABLED = False

FLOAT_ACTIVE_LOW = True
RAIN_ACTIVE_LOW = True
HIGH_WATER_ACTIVE_LOW = False

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
