# -*- coding: utf-8 -*-
"""
SDPRS 水泵節點 - 水泵控制器模組
Smart Disaster Prevention Response System - Pump Controller Module

此模組實作水泵繼電器與 LED 控制器類別。
純 MicroPython 語法，適用於 ESP32。
"""

import machine


class PumpController:
    """
    水泵繼電器與 LED 控制器。
    
    控制邏輯：
    - ON: 繼電器高電位 + 紅燈亮 + 綠燈滅
    - OFF: 繼電器低電位 + 綠燈亮 + 紅燈滅
    """

    def __init__(self, relay_pin, led_red_pin, led_green_pin):
        """
        初始化 GPIO 引腳。

        Args:
            relay_pin: 繼電器 GPIO 號（如 26）
            led_red_pin: 紅色 LED GPIO 號（如 27）
            led_green_pin: 綠色 LED GPIO 號（如 25）

        初始狀態：水泵 OFF，綠燈亮，紅燈滅。
        """
        # 初始化 GPIO 為輸出模式
        self._relay = machine.Pin(relay_pin, machine.Pin.OUT)
        self._led_red = machine.Pin(led_red_pin, machine.Pin.OUT)
        self._led_green = machine.Pin(led_green_pin, machine.Pin.OUT)
        
        # 內部狀態追蹤
        self._state = "OFF"
        
        # 確保初始狀態安全（水泵關閉）
        self.turn_off()

    def turn_on(self):
        """
        啟動水泵。
        - 繼電器引腳設為高電位 (1)
        - 紅色 LED 亮 (1)
        - 綠色 LED 滅 (0)
        - 更新內部 state 為 "ON"
        """
        # 先操作繼電器，確保泵先動作
        self._relay.value(1)
        # 再操作 LED
        self._led_red.value(1)
        self._led_green.value(0)
        # 更新狀態
        self._state = "ON"

    def turn_off(self):
        """
        關閉水泵。
        - 繼電器引腳設為低電位 (0)
        - 紅色 LED 滅 (0)
        - 綠色 LED 亮 (1)
        - 更新內部 state 為 "OFF"
        """
        # 先操作繼電器，確保泵先停止
        self._relay.value(0)
        # 再操作 LED
        self._led_red.value(0)
        self._led_green.value(1)
        # 更新狀態
        self._state = "OFF"

    @property
    def state(self):
        """返回當前水泵狀態字串: "ON" 或 "OFF"。"""
        return self._state