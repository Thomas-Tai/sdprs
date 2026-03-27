# -*- coding: utf-8 -*-
"""
SDPRS 水泵節點配置檔案
Smart Disaster Prevention Response System - Pump Node Configuration

此檔案包含 ESP32 水泵控制系統的所有配置參數。
純 MicroPython 語法，適用於 ESP32 DevKit。

Usage:
    from config import *
"""

# ============ WiFi 設定 ============
SSID = "SDPRS_IoT"           # WiFi SSID
WIFI_PASS = "changeme"       # WiFi 密碼

# ============ MQTT 設定 ============
MQTT_BROKER = "192.168.1.100"  # 中央伺服器 IP
MQTT_PORT = 1883
NODE_ID = "pump_node_01"       # 此裝置的唯一識別碼
MQTT_TOPIC_STATUS = "sdprs/edge/" + NODE_ID + "/pump_status"   # 水泵狀態發布主題
MQTT_TOPIC_HEARTBEAT = "sdprs/edge/" + NODE_ID + "/heartbeat"  # 心跳主題（備用）

# ============ 水泵控制閾值（滯後控制 Hysteresis） ============
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