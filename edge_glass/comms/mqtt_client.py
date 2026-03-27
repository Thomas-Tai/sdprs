"""
邊緣端 MQTT 客戶端模組

負責：
- 心跳發布（每 30 秒）
- 指令訂閱回調（stream_start/stop/update/simulate_trigger）
- 串流狀態發布

使用範例：
    from comms.mqtt_client import MQTTClient

    client = MQTTClient(config)
    client.register_command_handler("stream_start", handle_stream_start)
    client.start()

    # 發布串流狀態
    client.publish_stream_status({"status": "active", "tunnel_port": 18554})

    # 停止
    client.stop()
"""

import json
import logging
import platform
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

# 嘗試導入 psutil，若不可用則降級
try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# 嘗試導入 paho-mqtt
try:
    import paho.mqtt.client as mqtt

    PAHO_AVAILABLE = True
except ImportError:
    PAHO_AVAILABLE = False
    mqtt = None

# 匯入 MQTT 主題常數
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from shared.mqtt_topics import (
    QOS_CMD,
    QOS_STREAM_STATUS,
    SUB_ALL_HEARTBEAT,
    SUB_ALL_PUMP_STATUS,
    SUB_ALL_STREAM_STATUS,
    topic_cmd_simulate_trigger,
    topic_cmd_stream_start,
    topic_cmd_stream_stop,
    topic_cmd_update,
    topic_heartbeat,
    topic_stream_status,
    sub_cmd_all,
)

logger = logging.getLogger("mqtt_client")


class MQTTClient:
    """
    邊緣端 MQTT 客戶端。

    負責心跳發布、指令訂閱和串流狀態發布。
    """

    HEARTBEAT_INTERVAL = 30  # 心跳間隔（秒）

    def __init__(self, config: dict):
        """
        初始化 MQTT 客戶端。

        Args:
            config: config.yaml 載入的配置字典，需包含：
                - node_id: 節點 ID
                - server.mqtt_broker: MQTT broker 地址
                - server.mqtt_port: MQTT broker 端口
                - server.mqtt_username: EMQX 用戶名（雲端部署）
                - server.mqtt_password: EMQX 密碼（雲端部署）
                - server.mqtt_use_tls: 是否啟用 TLS（雲端部署）
        """
        if not PAHO_AVAILABLE:
            raise ImportError("paho-mqtt is required. Install with: pip install paho-mqtt")

        self._config = config
        self._node_id = config.get("node_id", "edge_node")
        self._broker = config.get("server", {}).get("mqtt_broker", "localhost")
        self._port = config.get("server", {}).get("mqtt_port", 1883)
        self._username = config.get("server", {}).get("mqtt_username", "")
        self._password = config.get("server", {}).get("mqtt_password", "")
        self._use_tls = config.get("server", {}).get("mqtt_use_tls", False)

        # 指令回調字典
        self._command_handlers: Dict[str, Callable] = {}

        # 共享狀態（由主迴圈設定）
        self._buffer_health = "ok"

        # 啟動時間
        self._start_time = time.monotonic()

        # 運行標誌
        self._running = False
        self._heartbeat_timer: Optional[threading.Thread] = None

        # MQTT 客戶端
        self._client: Optional[mqtt.Client] = None

        # 初始化客戶端
        self._init_client()

    def _init_client(self):
        """初始化 paho-mqtt 客戶端。"""
        self._client = mqtt.Client(client_id=f"sdprs-{self._node_id}")

        # 設定自動重連
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)

        # 認證（EMQX 雲端部署）
        if self._username:
            self._client.username_pw_set(self._username, self._password)
            logger.info("MQTT auth configured (username/password)")

        # TLS（外部 broker 加密連線）
        if self._use_tls:
            self._client.tls_set()
            logger.info("MQTT TLS enabled")

        # 設定回調
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        logger.info(f"MQTT client initialized for node: {self._node_id} "
                    f"-> {self._broker}:{self._port}")

    def _on_connect(self, client, userdata, flags, rc):
        """連線成功回調。"""
        if rc == 0:
            logger.info(f"Connected to MQTT broker: {self._broker}:{self._port}")

            # 訂閱該節點的所有指令主題
            cmd_topic = sub_cmd_all(self._node_id)
            client.subscribe(cmd_topic, qos=QOS_CMD)
            logger.info(f"Subscribed to: {cmd_topic}")

        else:
            logger.error(f"MQTT connection failed with code: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """斷線回調。"""
        if rc != 0:
            logger.warning(f"Unexpected MQTT disconnect (rc={rc}), will auto-reconnect")

    def _on_message(self, client, userdata, msg):
        """訊息接收回調。"""
        try:
            # 解析主題，提取指令名稱
            topic_parts = msg.topic.split("/")
            if len(topic_parts) >= 5:
                command = topic_parts[-1]  # 最後一段是指令名稱
            else:
                logger.warning(f"Received message on unexpected topic: {msg.topic}")
                return

            # 解析 payload
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
            except json.JSONDecodeError:
                payload = {}

            logger.debug(f"Received command: {command}, payload: {payload}")

            # 分發到已註冊的 handler
            if command in self._command_handlers:
                handler = self._command_handlers[command]
                try:
                    handler(payload)
                except Exception as e:
                    logger.error(f"Command handler error for '{command}': {e}")
            else:
                logger.warning(f"No handler registered for command: {command}")

        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def register_command_handler(self, command: str, handler: Callable):
        """
        註冊指令回調。

        Args:
            command: 指令名稱（如 "stream_start", "stream_stop"）
            handler: 回調函式，簽名 (payload: dict) -> None
        """
        self._command_handlers[command] = handler
        logger.info(f"Registered handler for command: {command}")

    def start(self):
        """啟動 MQTT 客戶端（連線 broker + 開始心跳）。"""
        if self._running:
            return

        self._running = True

        # 連線 broker
        try:
            self._client.connect(self._broker, self._port, keepalive=60)
            logger.info(f"Connecting to MQTT broker: {self._broker}:{self._port}")
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return

        # 啟動背景迴圈
        self._client.loop_start()

        # 啟動心跳定時器
        self._start_heartbeat_loop()

        logger.info("MQTT client started")

    def stop(self):
        """停止 MQTT 客戶端。"""
        self._running = False

        # 停止心跳
        if self._heartbeat_timer:
            self._heartbeat_timer = None

        # 停止 MQTT 迴圈
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()

        logger.info("MQTT client stopped")

    def _start_heartbeat_loop(self):
        """啟動心跳迴圈（背景線程）。"""

        def heartbeat_loop():
            while self._running:
                self._publish_heartbeat()
                time.sleep(self.HEARTBEAT_INTERVAL)

        self._heartbeat_timer = threading.Thread(target=heartbeat_loop, daemon=True)
        self._heartbeat_timer.start()

    def _publish_heartbeat(self):
        """發布心跳訊息。"""
        # 收集心跳資料
        heartbeat_data = {
            "node_id": self._node_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "online",
            "cpu_temp": self._get_cpu_temp(),
            "buffer_health": self._buffer_health,
            "uptime_seconds": int(time.monotonic() - self._start_time),
            "memory_usage_percent": self._get_memory_usage(),
        }

        # 發布
        topic = topic_heartbeat(self._node_id)
        payload = json.dumps(heartbeat_data)

        if self._client:
            self._client.publish(topic, payload, qos=0)
            logger.debug(f"Heartbeat published: {heartbeat_data}")

    def _get_cpu_temp(self) -> float:
        """
        取得 CPU 溫度。

        Returns:
            CPU 溫度（°C），若無法取得則返回 50.0
        """
        if platform.system() == "Linux":
            try:
                with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                    temp = int(f.read().strip()) / 1000.0
                    return round(temp, 1)
            except Exception:
                pass

        # 非 Linux 或讀取失敗，返回 mock 值
        return 50.0

    def _get_memory_usage(self) -> float:
        """
        取得記憶體使用率。

        Returns:
            記憶體使用百分比，若無法取得則返回 50.0
        """
        if PSUTIL_AVAILABLE:
            try:
                return round(psutil.virtual_memory().percent, 1)
            except Exception:
                pass

        # 降級：嘗試讀取 /proc/meminfo
        if platform.system() == "Linux":
            try:
                with open("/proc/meminfo", "r") as f:
                    meminfo = {}
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 2:
                            key = parts[0].rstrip(":")
                            value = int(parts[1])
                            meminfo[key] = value

                    total = meminfo.get("MemTotal", 1)
                    available = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
                    used_percent = ((total - available) / total) * 100
                    return round(used_percent, 1)
            except Exception:
                pass

        return 50.0

    def set_buffer_health(self, status: str):
        """
        設定緩衝區健康狀態。

        Args:
            status: 健康狀態（"ok", "warning", "error"）
        """
        self._buffer_health = status

    def publish_stream_status(self, status_data: dict):
        """
        發布串流狀態。

        Args:
            status_data: 狀態資料字典，如 {"status": "active", "tunnel_port": 18554}
        """
        topic = topic_stream_status(self._node_id)
        payload = json.dumps(status_data)

        if self._client:
            self._client.publish(topic, payload, qos=QOS_STREAM_STATUS)
            logger.info(f"Stream status published: {status_data}")

    def is_connected(self) -> bool:
        """檢查是否已連線。"""
        if self._client:
            return self._client.is_connected()
        return False


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)

    # 測試用配置
    config = {
        "node_id": "glass_node_01",
        "server": {
            "mqtt_broker": "localhost",
            "mqtt_port": 1883,
        },
    }

    # 指令處理器
    def handle_stream_start(payload):
        print(f"Stream start command received: {payload}")

    def handle_stream_stop(payload):
        print(f"Stream stop command received: {payload}")

    def handle_simulate_trigger(payload):
        print(f"Simulate trigger command received: {payload}")

    try:
        client = MQTTClient(config)
        client.register_command_handler("stream_start", handle_stream_start)
        client.register_command_handler("stream_stop", handle_stream_stop)
        client.register_command_handler("simulate_trigger", handle_simulate_trigger)

        client.start()

        print("MQTT client running. Press Ctrl+C to stop.")
        print(f"Heartbeat topic: {topic_heartbeat(config['node_id'])}")
        print(f"Command topic: {sub_cmd_all(config['node_id'])}")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping...")
        client.stop()
    except ImportError as e:
        print(f"Import error: {e}")
        print("Please install required packages: pip install paho-mqtt psutil")