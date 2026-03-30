# -*- coding: utf-8 -*-
"""
SDPRS 水泵節點 - 主程式
Smart Disaster Prevention Response System - Main Program

這是 ESP32 水泵控制系統的主程式入口。

核心功能：
1. 每秒讀取水位感測器
2. 根據滞後邏輯控制繼電器（80% ON / 20% OFF）
3. 每 10 秒透過 MQTT 回報狀態
4. 硬體看門狗 (WDT) 防止程式卡死
5. NTP 時間同步（在 WiFi 連線成功後執行，分散開機衝擊）

設計原則：離線自治 — WiFi/MQTT 失敗不影響水泵控制。

Usage:
    將此檔案上傳至 ESP32，命名為 main.py，上電後自動執行。
"""

import time
# WDT 導入在下方根據配置決定

# 載入配置
from config import (
    SSID, WIFI_PASS, MQTT_BROKER, MQTT_PORT, NODE_ID, MQTT_TOPIC_STATUS,
    HIGH_THRESHOLD, LOW_THRESHOLD,
    RELAY_PIN, LED_RED_PIN, LED_GREEN_PIN, ADC_PIN,
    PUBLISH_INTERVAL, POLL_INTERVAL,
    WDT_ENABLED, WDT_TIMEOUT,
    MQTT_USERNAME, MQTT_PASSWORD
)

# 載入模組
from water_sensor import init_adc, read_water_level
from pump_controller import PumpController
from mqtt_client import PumpMQTTClient


def sync_ntp():
    """
    同步 NTP 時間。嘗試多個伺服器，失敗不影響主程式。
    回傳 True 表示成功。呼叫方無論成功與否均不再重試。
    """
    import ntptime
    ntptime.timeout = 5  # 延長超時至 5 秒
    servers = ["pool.ntp.org", "time.cloudflare.com", "216.239.35.0"]
    for srv in servers:
        try:
            ntptime.host = srv
            ntptime.settime()
            t = time.localtime()
            print("[MAIN] NTP synced via %s: %04d-%02d-%02d %02d:%02d:%02d UTC" % (
                srv, t[0], t[1], t[2], t[3], t[4], t[5]))
            return True
        except Exception as e:
            print("[MAIN] NTP %s failed: %s" % (srv, str(e)))
    print("[MAIN] NTP unavailable, timestamps will be inaccurate")
    return False


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
        topic=MQTT_TOPIC_STATUS,
        username=MQTT_USERNAME,
        mqtt_password=MQTT_PASSWORD
    )

    # 4. 看門狗（可選）
    wdt = None
    if WDT_ENABLED:
        from machine import WDT
        wdt = WDT(timeout=WDT_TIMEOUT)
        print("[MAIN] Hardware watchdog enabled (%dms timeout)" % WDT_TIMEOUT)
    else:
        print("[MAIN] Hardware watchdog disabled (dev mode)")

    # 5. 計時器
    last_publish = time.ticks_ms()
    ntp_synced = False  # NTP 將在 WiFi 首次連線後執行

    print("[MAIN] Entering main loop...")
    print("[MAIN] HIGH_THRESHOLD=%d%%, LOW_THRESHOLD=%d%%" % (HIGH_THRESHOLD, LOW_THRESHOLD))

    # 主迴圈
    while True:
        try:
            # 餘狗
            if wdt:
                wdt.feed()

            # 1. 讀取水位
            water_level = read_water_level(adc)

            # 2. 滞後控制邏輯
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
                # NTP 同步：WiFi 首次連線後執行一次（不管成功與否都不重試）
                if not ntp_synced and mqtt._wifi_connected:
                    sync_ntp()
                    ntp_synced = True

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
