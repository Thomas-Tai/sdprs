# -*- coding: utf-8 -*-
"""
SDPRS 水泵節點 - 開機啟動腳本
Smart Disaster Prevention Response System - Boot Script

這是 ESP32 的開機啟動腳本，在 main.py 之前執行。
負責處理 WiFi 連線。

WiFi 連線失敗不會阻止 main.py 執行（水泵必須離線自治）。
"""

import network
import time

# 載入配置
from config import (
    SSID, WIFI_PASS, WIFI_MAX_RETRIES, WIFI_CONNECT_TIMEOUT
)


def main():
    """
    WiFi 啟動連線。
    """
    print("[BOOT] SDPRS Pump Node booting...")
    
    # 1. 關閉 AP 模式（防止 ESP32 預設開啟 AP）
    wlan_ap = network.WLAN(network.AP_IF)
    wlan_ap.active(False)
    
    # 2. 啟用 STA 模式
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    # 3. 若已連線，跳過（可能是軟重啟）
    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print("[BOOT] WiFi already connected! IP: %s" % ip)
        return
    
    # 4. 嘗試連線 WiFi
    print("[BOOT] Connecting to WiFi SSID: %s" % SSID)
    
    for i in range(WIFI_MAX_RETRIES):
        print("[BOOT] WiFi attempt %d/%d..." % (i + 1, WIFI_MAX_RETRIES))
        
        try:
            wlan.connect(SSID, WIFI_PASS)
            
            # 每次等待 WIFI_CONNECT_TIMEOUT 秒
            for _ in range(WIFI_CONNECT_TIMEOUT * 10):  # 100ms 間隔
                if wlan.isconnected():
                    break
                time.sleep_ms(100)
            
            if wlan.isconnected():
                break
        
        except Exception as e:
            print("[BOOT] WiFi error: %s" % str(e))
    
    # 5. 印出結果
    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print("[BOOT] WiFi connected! IP: %s" % ip)
    else:
        print("[BOOT] WiFi FAILED after %d attempts. Running offline." % WIFI_MAX_RETRIES)


# 執行
main()