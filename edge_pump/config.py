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
MQTT_TOPIC_STATUS = "sdprs/edge/" + NODE_ID + "/pump_status"   # 水泵狀態發布主題
MQTT_TOPIC_HEARTBEAT = "sdprs/edge/" + NODE_ID + "/heartbeat"  # 心跳主題（備用）

# ============ 水泵控制閾值（滞後控制 Hysteresis） ============
HIGH_THRESHOLD = 80    # 水位 >= 80% → 啟動水泵
LOW_THRESHOLD = 20     # 水位 <= 20% → 關閉水泵
# 在 20%~80% 之間維持當前狀態，防止頻繁開關

# ============ GPIO 引腳定義（ESP32 DevKit） ============
RELAY_PIN = 26         # 繼電器控制（高電位 = ON）
LED_RED_PIN = 27       # 紅色 LED（水泵運行中）
LED_GREEN_PIN = 25     # 綠色 LED（待機）
ADC_PIN = 34           # 水位感測器 ADC 輸入（ADC1_CH6, 只讀引腳）

# ============ 時間間隔 ============
PUBLISH_INTERVAL = 10   # MQTT 發布間隔（秒）
POLL_INTERVAL = 1       # 水位讀取間隔（秒）
WIFI_RETRY_INTERVAL = 60  # WiFi 重連間隔（秒）
WIFI_CONNECT_TIMEOUT = 3  # 單次 WiFi 連線等待（秒）
WIFI_MAX_RETRIES = 10     # boot 時最大重試次數

# ============ 看門狗 (WDT) ============
WDT_ENABLED = False       # 開發階段設 False，部署生產時改為 True
WDT_TIMEOUT = 30000       # WDT 逾時（毫秒）
