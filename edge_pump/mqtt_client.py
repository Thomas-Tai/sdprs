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
    """返回 ISO 8601 格式的當前時間字串。"""
    t = time.localtime()
    return "%04d-%02d-%02dT%02d:%02d:%02dZ" % (t[0], t[1], t[2], t[3], t[4], t[5])


class PumpMQTTClient:
    """
    水泵 MQTT 客戶端 — 負責 WiFi 管理和狀態發布。
    核心設計：所有網路操作失敗時靜默，不影響本地控制。
    """

    def __init__(self, ssid, password, broker, port, node_id, topic,
                 retry_interval=60, username="", mqtt_password=""):
        self._ssid = ssid
        self._password = password
        self._broker = broker
        self._port = port
        self._node_id = node_id
        self._topic = topic
        self._retry_interval = retry_interval
        self._mqtt_user = username
        self._mqtt_pass = mqtt_password

        self._wifi_connected = False
        self._mqtt_connected = False
        self._client = None
        self._last_wifi_attempt = -(self._retry_interval + 1)  # Allow immediate first attempt

        # 初始化 STA 模式並取消任何自動連線
        self._wlan = network.WLAN(network.STA_IF)
        self._wlan.active(True)
        # 若 WiFi 已在背景自動連線中，先斷開以清除狀態
        if not self._wlan.isconnected():
            try:
                self._wlan.disconnect()
            except:
                pass
            time.sleep(0.5)

    def _wait_wifi(self, timeout_sec=15):
        """
        以 1 秒間隔等待 WiFi 連線，最多等 timeout_sec 秒。
        使用倒數計時器避免 ticks_ms 溢位。
        """
        t = timeout_sec
        while not self._wlan.isconnected() and t > 0:
            time.sleep(1)
            t -= 1
        return self._wlan.isconnected()

    def ensure_connection(self):
        """確保 WiFi + MQTT 連線。"""
        if self._wlan.isconnected():
            self._wifi_connected = True
        else:
            self._wifi_connected = False
            self._mqtt_connected = False

            now_sec = time.time()
            if now_sec - self._last_wifi_attempt > self._retry_interval:
                self._last_wifi_attempt = now_sec
                try:
                    print("[MQTT] Connecting to WiFi SSID: %s" % self._ssid)
                    # 先斷線清除狀態，避免 "sta is connecting" 錯誤
                    self._wlan.disconnect()
                    time.sleep(0.5)
                    # 只呼叫一次 connect()，然後等待
                    self._wlan.connect(self._ssid, self._password)
                    if self._wait_wifi(timeout_sec=15):
                        self._wifi_connected = True
                        print("[MQTT] WiFi connected: %s" % self._wlan.ifconfig()[0])
                    else:
                        print("[MQTT] WiFi timeout after 15s")
                        return False
                except OSError as e:
                    print("[MQTT] WiFi error: %s" % str(e))
                    return False
            else:
                return False

        if self._wifi_connected and not self._mqtt_connected:
            try:
                print("[MQTT] Connecting to broker %s:%d..." % (self._broker, self._port))
                self._client = MQTTClient(
                    client_id=self._node_id,
                    server=self._broker,
                    port=self._port,
                    user=self._mqtt_user if self._mqtt_user else None,
                    password=self._mqtt_pass if self._mqtt_pass else None
                )
                self._client.connect()
                self._mqtt_connected = True
                print("[MQTT] Connected to broker!")
            except OSError as e:
                print("[MQTT] MQTT error: %s" % str(e))
                self._mqtt_connected = False
                self._client = None
                return False

        return self._wifi_connected and self._mqtt_connected

    def publish_status(self, pump_state, water_level):
        """發布水泵狀態到 MQTT。"""
        if not self.ensure_connection():
            return
        if self._client is None:
            return
        try:
            timestamp = format_timestamp()
            payload = '{"node_id":"%s","timestamp":"%s","pump_state":"%s","water_level":%.1f}' % (
                self._node_id, timestamp, pump_state, water_level
            )
            self._client.publish(self._topic, payload)
            print("[MQTT] Published: %s" % payload)
        except OSError as e:
            print("[MQTT] Publish error: %s" % str(e))
            self._mqtt_connected = False
            self._client = None

    def check_msg(self):
        """非阻塞檢查 MQTT 訊息。失敗時靜默。"""
        if self._client is None or not self._mqtt_connected:
            return
        try:
            self._client.check_msg()
        except OSError:
            self._mqtt_connected = False
            self._client = None

    def disconnect(self):
        """斷開 MQTT 連線。"""
        if self._client is not None:
            try:
                self._client.disconnect()
            except:
                pass
        self._mqtt_connected = False
        self._client = None
        print("[MQTT] Disconnected")
