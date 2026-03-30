# -*- coding: utf-8 -*-
"""
SDPRS 水泵節點 - 水位感測器模組
Smart Disaster Prevention Response System - Water Sensor Module

此模組實作水位感測器的讀取功能，使用中位數濾波消除雜訊。
純 MicroPython 語法，適用於 ESP32。
"""

import machine
import time


def init_adc(pin_num):
    """
    初始化 ADC 物件。

    Args:
        pin_num: GPIO 引腳號（如 34）
    
    Returns:
        machine.ADC 物件，衰減設為 11dB (0-3.3V 全量程)
    """
    adc = machine.ADC(machine.Pin(pin_num))
    # 設定衰減為 11dB，可量測 0-3.3V
    adc.atten(machine.ADC.ATTN_11DB)
    # 設定位寬為 12-bit (0-4095)
    adc.width(machine.ADC.WIDTH_12BIT)
    return adc


def read_water_level(adc):
    """
    讀取水位感測器，返回 0.0-100.0 的百分比值。
    使用 3 次讀取的中位數濾波消除雜訊。

    Args:
        adc: machine.ADC 物件（已初始化）
    
    Returns:
        float: 雨量百分比 0.0-100.0（0=乾燥, 100=完全濕潤）
    """
    # 連續讀取 3 次，每次間隔 10ms
    readings = []
    for _ in range(3):
        readings.append(adc.read())
        time.sleep_ms(10)
    
    # 取中位數（手動排序）
    sorted_readings = sorted(readings)
    median = sorted_readings[1]  # 中間值
    
    # 將 ADC 值 (0-4095) 映射到 0.0-100.0%
    water_level = 100.0 - (median / 4095.0) * 100.0  # 反相：乾燥=低電壓=低ADC=高水位(修正)
    
    # 夾住範圍
    water_level = max(0.0, min(100.0, water_level))
    
    return water_level