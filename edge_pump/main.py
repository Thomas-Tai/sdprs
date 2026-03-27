# -*- coding: utf-8 -*-
"""
SDPRS 水泵節點 - 主程式
Smart Disaster Prevention Response System - Main Program

這是 ESP32 水泵控制系統的主程式入口。

核心功能：
1. 每秒讀取水位感測器
2. 根據滯後邏輯控制繼電器（80% ON / 20% OFF）
3. 每 10 秒透過 MQTT 回報狀態

設計原則：離線自治 — WiFi/MQTT 失敗不影響水泵控制。

Usage:
    將此檔案上傳至 ESP32，命名為 main.py，上電後自動執行。
"""

import time

# 載入配置
from config import (
    SSID, WIFI_PASS, MQTT_BROKER, MQTT_PORT, NODE_ID, MQTT_TOPIC_STATUS,
    HIGH_THRESHOLD, LOW_THRESHOLD,
    RELAY_PIN, LED_RED_PIN, LED_GREEN_PIN, ADC_PIN,
    PUBLISH_INTERVAL, POLL_INTERVAL
)

# 載入模組
from water_sensor import init_adc, read_water_level
from pump_controller import PumpController
from mqtt_client import PumpMQTTClient


def main():
    """
    主程式入口。
    """
    print("[MAIN] SDPRS Pump Node starting...")
    print("[MAIN] Node ID: %s" % NODE_ID)
    
    # 1. 初始化 ADC
    print("[MAIN] Initializing ADC on pin %d..." % ADC_PIN)
    adc = init_adc(ADC_PIN)
    
    # 2. 初始化水泵控制器
    print("[MAIN] Initializing pump controller...")
    pump = PumpController(RELAY_PIN, LED_RED_PIN, LED_GREEN_PIN)
    print("[MAIN] Pump initial state: %s" % pump.state)
    
    # 3. 初始化 MQTT 客戶端
    print("[MAIN] Initializing MQTT client...")
    mqtt = PumpMQTTClient(
        ssid=SSID,
        password=WIFI_PASS,
        broker=MQTT_BROKER,
        port=MQTT_PORT,
        node_id=NODE_ID,
        topic=MQTT_TOPIC_STATUS
    )
    
    # 4. 計時器
    last_publish = time.ticks_ms()
    
    print("[MAIN] Entering main loop...")
    print("[MAIN] HIGH_THRESHOLD=%d%%, LOW_THRESHOLD=%d%%" % (HIGH_THRESHOLD, LOW_THRESHOLD))
    
    # 主迴圈
    while True:
        try:
            # 1. 讀取水位
            water_level = read_water_level(adc)
            
            # 2. 滯後控制邏輯
            if pump.state == "OFF" and water_level >= HIGH_THRESHOLD:
                pump.turn_on()
                print("[PUMP] ON - water_level=%.1f%%" % water_level)
            
            elif pump.state == "ON" and water_level <= LOW_THRESHOLD:
                pump.turn_off()
                print("[PUMP] OFF - water_level=%.1f%%" % water_level)
            
            # 注意：水位在 LOW_THRESHOLD~HIGH_THRESHOLD 之間時，不做任何切換
            
            # 3. MQTT 狀態發布（每 PUBLISH_INTERVAL 秒）
            now = time.ticks_ms()
            if time.ticks_diff(now, last_publish) >= PUBLISH_INTERVAL * 1000:
                mqtt.publish_status(pump.state, water_level)
                last_publish = now
            
            # 4. MQTT 訊息檢查（非阻塞）
            mqtt.check_msg()
            
            # 5. 等待下一輪
            time.sleep(POLL_INTERVAL)
        
        except KeyboardInterrupt:
            print("[MAIN] Shutting down...")
            pump.turn_off()
            mqtt.disconnect()
            break
        
        except Exception as e:
            # 任何未預期錯誤：印出但不中斷主迴圈
            print("[ERROR] %s" % str(e))
            time.sleep(POLL_INTERVAL)


# 程式入口
if __name__ == "__main__":
    main()