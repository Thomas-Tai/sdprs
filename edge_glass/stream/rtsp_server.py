"""
邊緣端串流管理器模組

控制 mediamtx + SSH 反向隧道的子進程生命週期，提供按需 HLS 串流。

啟動序列：
    [1] 啟動 mediamtx (子進程)
    [2] 啟動反向 SSH 隧道 (子進程)
    [3] 發布 MQTT 狀態
    [4] 啟動超時計時器（5 分鐘自動停止）

使用範例：
    from stream.rtsp_server import StreamManager

    def on_status(status_data):
        mqtt_client.publish_stream_status(status_data)

    manager = StreamManager(config, on_status)
    manager.start()

    # 停止
    manager.stop()
"""

import logging
import platform
import signal
import socket
import subprocess
import threading
import time
from typing import Callable, Dict, Optional

logger = logging.getLogger("stream_manager")


class StreamManager:
    """
    串流管理器。

    控制 mediamtx 和 SSH 反向隧道的子進程。
    """

    # mediamtx 預設端口
    MEDIAMTX_PORT = 8554

    # 等待端口可用的超時（秒）
    PORT_WAIT_TIMEOUT = 5.0

    # 優雅終止等待時間（秒）
    GRACEFUL_TIMEOUT = 3.0

    def __init__(
        self,
        config: dict,
        publish_status_callback: Callable[[Dict], None],
    ):
        """
        初始化串流管理器。

        Args:
            config: 配置字典，需包含：
                - node_id: 節點 ID
                - stream.tunnel_port: SSH 隧道遠端端口
                - stream.auto_stop_minutes: 自動停止時間（分鐘）
                - server.mqtt_broker: SSH 隧道目標主機（可選）
            publish_status_callback: 狀態發布回調，簽名 (status_data: dict) -> None
        """
        self._config = config
        self._publish_status = publish_status_callback

        self._node_id = config.get("node_id", "edge_node")

        stream_config = config.get("stream", {})
        self._tunnel_port = stream_config.get("tunnel_port", 18554)
        self._auto_stop_minutes = stream_config.get("auto_stop_minutes", 5)

        # SSH 隧道目標（預設使用 mqtt_broker）
        server_config = config.get("server", {})
        self._ssh_host = server_config.get("mqtt_broker", "localhost")

        # 子進程
        self._mediamtx_process: Optional[subprocess.Popen] = None
        self._ssh_process: Optional[subprocess.Popen] = None

        # 超時計時器
        self._timeout_timer: Optional[threading.Timer] = None

        # 運行狀態
        self._is_active = False
        self._lock = threading.Lock()

        logger.info(
            f"StreamManager initialized: tunnel_port={self._tunnel_port}, "
            f"auto_stop={self._auto_stop_minutes}min"
        )

    def is_active(self) -> bool:
        """返回是否正在串流。"""
        return self._is_active

    def start(self) -> bool:
        """
        啟動串流。

        Returns:
            是否成功啟動
        """
        with self._lock:
            if self._is_active:
                logger.warning("Stream already active")
                return True

            logger.info("Starting stream...")

            # [1] 啟動 mediamtx
            if not self._start_mediamtx():
                self._publish_error("mediamtx_failed")
                return False

            # [2] 啟動串流通道
            # cloud_mode: 跳過 SSH 隧道，改用 HTTP push（或暫停串流）
            cloud_mode = self._config.get("stream", {}).get("cloud_mode", False)
            if cloud_mode:
                logger.info("Cloud mode: SSH tunnel skipped")
            else:
                if not self._start_ssh_tunnel():
                    self._stop_mediamtx()
                    self._publish_error("ssh_tunnel_failed")
                    return False

            # [3] 發布 MQTT 狀態
            self._is_active = True
            self._publish_status({
                "status": "active",
                "tunnel_port": self._tunnel_port if not cloud_mode else 0,
                "format": "hls",
                "cloud_mode": cloud_mode,
            })

            # [4] 啟動超時計時器
            self._start_timeout_timer()

            logger.info("Stream started successfully")
            return True

    def stop(self):
        """停止串流。"""
        with self._lock:
            if not self._is_active:
                return

            logger.info("Stopping stream...")

            # 取消超時計時器
            if self._timeout_timer:
                self._timeout_timer.cancel()
                self._timeout_timer = None

            # 停止 SSH 隧道
            self._stop_ssh_tunnel()

            # 停止 mediamtx
            self._stop_mediamtx()

            # 發布 MQTT 狀態
            self._is_active = False
            self._publish_status({"status": "stopped"})

            logger.info("Stream stopped")

    def _start_mediamtx(self) -> bool:
        """啟動 mediamtx 子進程。"""
        try:
            # 在 Windows 上需要找到 mediamtx.exe
            if platform.system() == "Windows":
                cmd = ["mediamtx.exe"]
            else:
                cmd = ["mediamtx"]

            self._mediamtx_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            logger.info(f"mediamtx started (pid={self._mediamtx_process.pid})")

            # 等待端口可用
            if not self._wait_for_port(self.MEDIAMTX_PORT, self.PORT_WAIT_TIMEOUT):
                logger.error("mediamtx port not available")
                self._stop_mediamtx()
                return False

            return True

        except FileNotFoundError:
            logger.error("mediamtx not found")
            return False
        except Exception as e:
            logger.error(f"Failed to start mediamtx: {e}")
            return False

    def _stop_mediamtx(self):
        """停止 mediamtx 子進程。"""
        if self._mediamtx_process:
            self._graceful_kill(self._mediamtx_process, "mediamtx")
            self._mediamtx_process = None

    def _start_ssh_tunnel(self) -> bool:
        """啟動 SSH 反向隧道。"""
        try:
            # ssh -N -R {tunnel_port}:localhost:8554 user@host
            cmd = [
                "ssh",
                "-N",
                "-R",
                f"{self._tunnel_port}:localhost:{self.MEDIAMTX_PORT}",
                f"sdprs@{self._ssh_host}",
            ]

            self._ssh_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            logger.info(f"SSH tunnel started (pid={self._ssh_process.pid})")

            # 等待一下讓 SSH 連線
            time.sleep(1.0)

            # 檢查進程是否還活著
            if self._ssh_process.poll() is not None:
                logger.error("SSH tunnel process died immediately")
                return False

            return True

        except FileNotFoundError:
            logger.error("ssh command not found")
            return False
        except Exception as e:
            logger.error(f"Failed to start SSH tunnel: {e}")
            return False

    def _stop_ssh_tunnel(self):
        """停止 SSH 隧道子進程。"""
        if self._ssh_process:
            self._graceful_kill(self._ssh_process, "ssh")
            self._ssh_process = None

    def _wait_for_port(self, port: int, timeout: float) -> bool:
        """
        等待端口可用。

        Args:
            port: 端口號
            timeout: 超時時間（秒）

        Returns:
            端口是否可用
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.5)
                    result = sock.connect_ex(("localhost", port))
                    if result == 0:
                        return True
            except Exception:
                pass

            time.sleep(0.2)

        return False

    def _graceful_kill(self, process: subprocess.Popen, name: str):
        """
        優雅終止子進程。

        SIGTERM → 等 3 秒 → SIGKILL

        Args:
            process: 子進程
            name: 進程名稱（用於日誌）
        """
        try:
            # 嘗試優雅終止
            if platform.system() == "Windows":
                process.terminate()
            else:
                process.send_signal(signal.SIGTERM)

            # 等待進程結束
            try:
                process.wait(timeout=self.GRACEFUL_TIMEOUT)
                logger.debug(f"{name} terminated gracefully")
            except subprocess.TimeoutExpired:
                # 強制終止
                if platform.system() == "Windows":
                    process.kill()
                else:
                    process.send_signal(signal.SIGKILL)

                process.wait()
                logger.debug(f"{name} killed")

        except Exception as e:
            logger.warning(f"Error killing {name}: {e}")

    def _start_timeout_timer(self):
        """啟動自動停止計時器。"""
        self._timeout_timer = threading.Timer(
            self._auto_stop_minutes * 60,
            self._on_timeout,
        )
        self._timeout_timer.start()
        logger.debug(f"Auto-stop timer started: {self._auto_stop_minutes} minutes")

    def _on_timeout(self):
        """超時回調。"""
        logger.info("Stream auto-stop timeout reached")
        self.stop()

    def _publish_error(self, reason: str):
        """發布錯誤狀態。"""
        self._publish_status({
            "status": "error",
            "reason": reason,
        })


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)

    # 測試用配置
    config = {
        "node_id": "glass_node_01",
        "stream": {
            "tunnel_port": 18554,
            "auto_stop_minutes": 1,  # 測試用 1 分鐘
        },
        "server": {
            "mqtt_broker": "localhost",
        },
    }

    def on_status(status_data):
        print(f"Status: {status_data}")

    manager = StreamManager(config, on_status)

    print("StreamManager test")
    print(f"is_active: {manager.is_active()}")

    # 注意：需要安裝 mediamtx 和設定 SSH 才能完整測試
    # 這裡只測試基本結構

    print("Done.")