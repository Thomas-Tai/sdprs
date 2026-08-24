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
import os
import platform
import socket
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

SERVER_HOLD_TTL = 900  # seconds: a pushed server-hold self-expires if the
# server stops re-asserting, so a dead/unreachable server never pins a node held.


def _write_hold_file(path: str, held: bool) -> None:
    """Write the update-hold flag ("1"/"0") for the on-node updater to read.
    Best-effort: a write failure (dir missing, perms) must never break the
    heartbeat. The edge is the SOLE writer of this file."""
    try:
        with open(path, "w") as f:
            f.write("1" if held else "0")
    except OSError as e:
        logger.warning(f"could not write hold file {path}: {e}")


def _read_deployed_version(path: str):
    """Return the deployed commit SHA from the marker file, or None.

    Must never raise: a missing/unreadable marker (a node bootstrapped before
    Phase 2, or a dev run) simply reports no version rather than breaking the
    heartbeat.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            sha = fh.read().strip()
        return sha or None
    except Exception:
        return None


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
                - server.mqtt_username: Mosquitto 用戶名（雲端部署）
                - server.mqtt_password: Mosquitto 密碼（雲端部署）
                - server.mqtt_use_tls: 是否啟用 TLS（雲端部署）
        """
        if not PAHO_AVAILABLE:
            raise ImportError("paho-mqtt is required. Install with: pip install paho-mqtt")

        self._config = config
        self._node_id = config.get("node_id", "edge_node")
        self._broker = config.get("server", {}).get("mqtt_broker", "localhost")
        self._port = config.get("server", {}).get("mqtt_port", 1883)
        # 心跳間隔可由 server.heartbeat_interval 覆寫（預設維持 30 秒）。調低讓
        # 儀表板的節點狀態／CPU 溫度／健康更即時；仍遠小於伺服器約 90 秒的離線逾時。
        try:
            self._heartbeat_interval = float(
                config.get("server", {}).get("heartbeat_interval", self.HEARTBEAT_INTERVAL)
            )
        except (TypeError, ValueError):
            self._heartbeat_interval = float(self.HEARTBEAT_INTERVAL)
        if self._heartbeat_interval <= 0:
            self._heartbeat_interval = float(self.HEARTBEAT_INTERVAL)
        self._username = config.get("server", {}).get("mqtt_username", "")
        self._password = config.get("server", {}).get("mqtt_password", "")
        self._use_tls = config.get("server", {}).get("mqtt_use_tls", False)

        # 指令回調字典
        self._command_handlers: Dict[str, Callable] = {}

        # 共享狀態（由主迴圈設定）
        self._buffer_health = "ok"
        # 偵測器健康狀態（telemetry-only，由主迴圈設定）
        self._visual_health = "unknown"
        self._audio_health = "unknown"

        # 啟動時間
        self._start_time = time.monotonic()

        # Deployed software version (full edge-release SHA) for the dashboard.
        # Store the marker PATH and re-read it on every heartbeat (NOT cached at
        # startup): the OTA updater restarts this service BEFORE it advances the
        # marker (edge_autoupdate.sh: restart -> health-check -> write SHA), so a
        # value cached at boot would report the PRE-update SHA until the next
        # restart. Re-reading each send makes the reported version self-correct
        # within one heartbeat after any update. Path overridable for tests.
        self._version_file = os.environ.get(
            "EDGE_DEPLOYED_SHA_FILE", "/opt/sdprs/.edge_deployed_sha"
        )

        # Update-hold: the edge is the sole writer of /run/sdprs/update_hold.
        # Aggregates the two hold sources (local capture + server alert) and is
        # written every heartbeat; the on-node updater reads it (with an mtime
        # TTL) to defer a SCHEDULED update. --manual bypasses it.
        self._hold_file = os.environ.get(
            "EDGE_UPDATE_HOLD_FILE", "/run/sdprs/update_hold"
        )
        self._local_capture_hold = False
        self._local_capture_reason = None
        self._server_hold = False
        self._server_hold_reason = None
        self._server_hold_ts = None
        self._clock = time.monotonic  # injectable for tests

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

        # LWT: if this node drops ungracefully (crash / power loss), the broker
        # publishes this offline marker to our heartbeat topic. The server's
        # _handle_heartbeat treats `online: false` as an immediate OFFLINE,
        # rather than waiting out the ~90s heartbeat timeout. retain=False so a
        # stale will can't linger and flap the node offline on server restart.
        try:
            self._client.will_set(
                topic_heartbeat(self._node_id),
                json.dumps({"node_id": self._node_id, "status": "OFFLINE", "online": False}),
                qos=0,
                retain=False,
            )
        except Exception as e:
            logger.warning(f"Failed to set MQTT LWT: {e}")

        # 設定自動重連
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)

        # 認證（雲端部署）
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

        # 使用非阻塞 connect_async：broker 在開機時暫時無法連線也不會卡住啟動；
        # loop_start() 會執行實際連線並在斷線時依 reconnect_delay_set 自動重連。
        try:
            self._client.connect_async(self._broker, self._port, keepalive=60)
            logger.info(f"Connecting (async) to MQTT broker: {self._broker}:{self._port}")
        except Exception as e:
            # connect_async only records host/port; failure is unexpected but must
            # NOT prevent loop_start() — the network loop is what retries.
            logger.error(f"connect_async setup failed (will still start loop for retry): {e}")

        # 啟動背景迴圈（負責實際連線 + 自動重連）
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
                time.sleep(self._heartbeat_interval)

        self._heartbeat_timer = threading.Thread(target=heartbeat_loop, daemon=True)
        self._heartbeat_timer.start()

    def _publish_heartbeat(self):
        """發布心跳訊息。"""
        # 收集心跳資料
        local_ip = self._get_local_ip()  # 計算一次，供 ip 與 mac 共用
        held, hold_reason = self._compute_hold()
        heartbeat_data = {
            "node_id": self._node_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "online",
            "cpu_temp": self._get_cpu_temp(),
            "buffer_health": self._buffer_health,
            "visual_health": self._visual_health,
            "audio_health": self._audio_health,
            "uptime_seconds": int(time.monotonic() - self._start_time),
            "memory_usage_percent": self._get_memory_usage(),
            # LAN 位址與主機名（telemetry-only）：伺服器只看得到節點的對外／代理 IP，
            # 心跳裡沒有這兩項就無從得知該台 Pi 的 SSH 可達位址。讓儀表板顯示「找節點」。
            "ip": local_ip,
            "hostname": socket.gethostname(),
            # 承載該 IP 之網卡的 MAC——讓儀表板能對照硬體清冊辨識實體機。
            "mac": self._get_mac(local_ip),
            # Deployed edge-release SHA so the dashboard can show each Pi's
            # version + whether an update is available (Phase 2). Re-read each
            # send (see __init__) so it reflects the CURRENT marker after an OTA
            # update without needing a service restart. None until a Phase-1+
            # node has a marker file.
            "version": _read_deployed_version(self._version_file),
            # Update-hold: aggregated from local capture + server alert (see
            # _compute_hold). Lets the dashboard show "held" and lets the
            # on-node updater's own file read stay in sync via the write below.
            "update_held": held,
            "hold_reason": hold_reason,
        }

        # 發布
        topic = topic_heartbeat(self._node_id)
        payload = json.dumps(heartbeat_data)

        if self._client:
            self._client.publish(topic, payload, qos=0)
            logger.debug(f"Heartbeat published: {heartbeat_data}")

        # The edge is the sole writer of the hold file; refresh it every
        # heartbeat so the on-node updater's TTL-based read stays current.
        # Best-effort — see _write_hold_file docstring.
        _write_hold_file(self._hold_file, held)

    def _get_local_ip(self) -> Optional[str]:
        """取得本機 LAN IP（送出心跳的來源介面位址）。

        用「connect 一個外部位址的 UDP socket 再讀 getsockname」的慣用法找出到達
        外網所用的介面 IP——不會真的送出封包。回傳點分四段字串，或在無法判定時
        回傳 None（絕不拋例外——找不到 IP 不得中斷心跳）。
        """
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))  # 不送封包，僅用於選出對外介面
            return s.getsockname()[0]
        except Exception:
            return None
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass

    def _get_mac(self, ip: Optional[str] = None) -> Optional[str]:
        """回傳承載 `ip` 之網卡（即 LAN 介面）的 MAC，冒號分隔小寫，如
        `dc:a6:32:2e:37:7f`；找不到或 psutil 不可用時回傳 None。絕不拋例外——
        取不到 MAC 不得中斷心跳。用 IP 對應介面，才不會回報 VPN／回環等錯的網卡。
        """
        if not ip or not PSUTIL_AVAILABLE:
            return None
        try:
            addrs = psutil.net_if_addrs()
            target_iface = None
            for iface, addr_list in addrs.items():
                if any(a.family == socket.AF_INET and a.address == ip for a in addr_list):
                    target_iface = iface
                    break
            if target_iface is None:
                return None
            for a in addrs[target_iface]:
                if a.family == psutil.AF_LINK and a.address:
                    return a.address.replace("-", ":").lower()
        except Exception:
            return None
        return None

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

    def set_detector_health(self, visual: Optional[str] = None, audio: Optional[str] = None) -> None:
        """設定偵測器健康狀態（telemetry-only）。visual/audio 例：ok/paused/blinded/disabled/stale。"""
        if visual is not None:
            self._visual_health = visual
        if audio is not None:
            self._audio_health = audio

    def set_local_capture_hold(self, active: bool, reason=None) -> None:
        """Raised by the main loop while an event is mid-capture/cooldown."""
        self._local_capture_hold = bool(active)
        self._local_capture_reason = reason if active else None

    def set_server_hold(self, hold: bool, reason=None) -> None:
        """Set from a server 'hold' command; stamped so it self-expires after
        SERVER_HOLD_TTL if the server stops re-asserting."""
        self._server_hold = bool(hold)
        self._server_hold_reason = reason if hold else None
        self._server_hold_ts = self._clock() if hold else None

    def _compute_hold(self):
        """(held, reason_code). Local capture wins the reason over server."""
        if self._local_capture_hold:
            return True, self._local_capture_reason
        if self._server_hold and self._server_hold_ts is not None \
                and (self._clock() - self._server_hold_ts) <= SERVER_HOLD_TTL:
            return True, self._server_hold_reason
        return False, None

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