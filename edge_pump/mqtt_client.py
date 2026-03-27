# -*- coding: utf-8 -*-
"""
SDPRS 水泵節點 - MQTT 客戶端模組
Smart Disaster Prevention Response System - MQTT Client Module

此模組實作 ESP32 水泵系統的 MQTT 客戶端，負責 WiFi 管理和狀態發布。
純 MicroPython 語法，適用於 ESP32。

設計原則：WiFi/MQTT 失敗不影響本地水泵控制邏輯（離線自治）。
"""

import network
import time
from umqtt.simple import MQTTClient


def format_timestamp():
    """
    返回 ISO 8601 格式的當前時間字串。
    MicroPython 的 time.localtime() 返回 tuple: (year, month, day, hour, minute, second, weekday, yearday)
    
    Returns:
        str: 格式化為 "2026-03-03T12:00:00Z"
    """
    t = time.localtime()
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % (t[0], t[1], t[2], t[3], t[4], t[5])


def connect_wifi(ssid, password, timeout=10):
    """
    連接 WiFi。

    Args:
        ssid: WiFi SSID
        password: WiFi 密碼
        timeout: 最大等待秒數
    
    Returns:
        bool: 是否成功連線
    """
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    if wlan.isconnected():
        return True
    
    wlan.connect(ssid, password)
    
    # 等待連線
    start = time.ticks_ms()
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), start) > timeout * 1000:
            return False
        time.sleep_ms(100)
    
    return True


class PumpMQTTClient:
    """
    水泵 MQTT 客戶端 — 負責 WiFi 管理和狀態發布。
    
    核心設計：所有網路操作失敗時靜默，不影響本地控制。
    """

    def __init__(self, ssid, password, broker, port, node_id, topic, retry_interval=60):
        """
        初始化 MQTT 客戶端（不自動連線）。

        Args:
            ssid: WiFi SSID
            password: WiFi 密碼
            broker: MQTT broker IP
            port: MQTT broker port
            node_id: 節點 ID
            topic: 發布主題
            retry_interval: WiFi 重連間隔（秒）
        """
        self._ssid = ssid
        self._password = password
        self._broker = broker
        self._port = port
        self._node_id = node_id
        self._topic = topic
        self._retry_interval = retry_interval
        
        # 內部狀態
        self._wifi_connected = False
        self._mqtt_connected = False
        self._client = None
        self._last_wifi_attempt = 0
        
        # WLAN 物件
        self._wlan = network.WLAN(network.STA_IF)
        self._wlan.active(True)

    def ensure_connection(self):
        """
        確保 WiFi + MQTT 連線。

        邏輯：
        1. 檢查 WiFi 是否連線
        2. 若未連線且距上次嘗試 > retry_interval 秒：嘗試連線
        3. WiFi 連線成功但 MQTT 未連線：建立 MQTT 連線
        4. 任何步驟失敗 → 靜默返回 False

        Returns:
            bool: True 若 WiFi + MQTT 均已連線
        """
        now = time.ticks_ms()
        
        # 檢查 WiFi 狀態
        if self._wlan.isconnected():
            self._wifi_connected = True
        else:
            self._wifi_connected = False
            self._mqtt_connected = False
            
            # 檢查是否需要重試 WiFi
            if time.ticks_diff(now, self._last_wifi_attempt) > self._retry_interval * 1000:
                self._last_wifi_attempt = now
                try:
                    print("[MQTT] Connecting to WiFi...")
                    self._wlan.connect(self._ssid, self._password)
                    
                    # 等待連線（最多 10 秒）
                    start = time.ticks_ms()
                    while not self._wlan.isconnected():
                        if time.ticks_diff(time.ticks_ms(), start) > 10000:
                            print("[MQTT] WiFi connection timeout")
                            return False
                        time.sleep_ms(100)
                    
                    self._wifi_connected = True
                    print("[MQTT] WiFi connected: %s" % self._wlan.ifconfig()[0])
                    
                except OSError as e:
                    print("[MQTT] WiFi error: %s" % str(e))
                    return False
        
        # 檢查 MQTT 狀態
        if self._wifi_connected and not self._mqtt_connected:
            try:
                print("[MQTT] Connecting to broker...")
                self._client = MQTTClient(
                    client_id=self._node_id,
                    server=self._broker,
                    port=self._port
                )
                self._client.connect()
                self._mqtt_connected = True
                print("[MQTT] Connected to broker: %s:%d" % (self._broker, self._port))
                
            except OSError as e:
                print("[MQTT] MQTT error: %s" % str(e))
                self._mqtt_connected = False
                self._client = None
                return False
        
        return self._wifi_connected and self._mqtt_connected

    def publish_status(self, pump_state, water_level):
        """
        發布水泵狀態到 MQTT。

        Args:
            pump_state: "ON" 或 "OFF"
            water_level: 0.0-100.0 浮點數
        """
        # 確保連線
        if not self.ensure_connection():
            return
        
        if self._client is None:
            return
        
        try:
            # 組裝 JSON 字串（使用字串格式化）
            timestamp = format_timestamp()
            payload = '{"node_id":"%s","timestamp":"%s","pump_state":"%s","water_level":%.1f}' % (
                self._node_id, timestamp, pump_state, water_level
            )
            
            # 發布
            self._client.publish(self._topic, payload)
            print("[MQTT] Published: %s" % payload)
            
        except OSError as e:
            print("[MQTT] Publish error: %s" % str(e))
            self._mqtt_connected = False
            self._client = None

    def check_msg(self):
        """
        非阻塞檢查 MQTT 訊息（目前水泵不訂閱任何主題，保留擴展）。
        失敗時靜默。
        """
        if self._client is None or not self._mqtt_connected:
            return
        
        try:
            self._client.check_msg()
        except OSError:
            self._mqtt_connected = False
            self._client = None

    def disconnect(self):
        """斷開 MQTT 連線（清理用）。"""
        if self._client is not None:
            try:
                self._client.disconnect()
            except:
                pass
        self._mqtt_connected = False
        self._client = None
        print("[MQTT] Disconnected")