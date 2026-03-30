# -*- coding: utf-8 -*-
"""
SDPRS 水泵節點 - 開機啟動腳本 (最小化版本)
Smart Disaster Prevention Response System - Boot Script (Minimal)

這是 ESP32 的開機啟動腳本，在 main.py 之前執行。
此版本不啟動 WiFi，避免開機時的電流峰值導致 brownout。

WiFi 連線由 main.py 的 mqtt_client 模組延遲處理。
"""

print("[BOOT] SDPRS Pump Node booting (minimal mode)...")
print("[BOOT] WiFi will be initialized lazily in main.py")
