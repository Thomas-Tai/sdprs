"""
邊緣端熱管理模組

監控 CPU 溫度，根據閾值設定共享旗標，讓主迴圈降低 FPS 或暫停視覺處理。

溫度閾值與行為：
    < 70°C:  正常
    70-75°C: 警告
    75-80°C: 降級（FPS 降低）
    80-85°C: 視覺暫停
    > 85°C:  緊急（發送 CRITICAL 警報）

使用範例：
    from utils.thermal import ThermalMonitor

    monitor = ThermalMonitor(config)
    monitor.start()

    # 在主迴圈中讀取
    if monitor.visual_paused:
        # 跳過視覺處理
        pass

    fps = monitor.current_fps

    # 停止
    monitor.stop()
"""

import logging
import platform
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("thermal_monitor")


class ThermalMonitor(threading.Thread):
    """
    熱管理監控線程。

    每 5 秒讀取 CPU 溫度，根據閾值設定共享旗標。
    """

    # 監控間隔（秒）
    MONITOR_INTERVAL = 5.0

    # 預設閾值
    WARNING_TEMP = 70.0
    FPS_REDUCE_TEMP = 75.0
    PAUSE_VISUAL_TEMP = 80.0
    CRITICAL_TEMP = 85.0

    def __init__(
        self,
        config: dict,
        critical_callback: Optional[Callable[[float], None]] = None,
    ):
        """
        初始化熱管理監控線程。

        Args:
            config: 配置字典，需包含：
                - thermal.fps_reduce_temp: 降低 FPS 的溫度閾值
                - thermal.pause_visual_temp: 暫停視覺處理的溫度閾值
                - thermal.critical_alert_temp: 發送 CRITICAL 警報的溫度閾值
                - camera.fps: 正常 FPS
            critical_callback: >85°C 時調用的回調，簽名 (temp: float) -> None
        """
        super().__init__(daemon=True)

        self._config = config

        # 從配置讀取閾值
        thermal_config = config.get("thermal", {})
        self._fps_reduce_temp = thermal_config.get("fps_reduce_temp", self.FPS_REDUCE_TEMP)
        self._pause_visual_temp = thermal_config.get("pause_visual_temp", self.PAUSE_VISUAL_TEMP)
        self._critical_temp = thermal_config.get("critical_alert_temp", self.CRITICAL_TEMP)

        # 正常 FPS
        self._normal_fps = config.get("camera", {}).get("fps", 15)

        # 共享屬性（供主迴圈讀取）
        self._current_fps = self._normal_fps
        self._visual_paused = False
        self._snapshot_interval = 1.0  # 正常快照間隔

        # 回調
        self._critical_callback = critical_callback

        # 狀態
        self._stop_event = threading.Event()
        self._current_temp = 50.0  # 當前溫度

        self.name = "ThermalMonitor"
        logger.info(
            f"ThermalMonitor initialized: "
            f"fps_reduce={self._fps_reduce_temp}°C, "
            f"pause_visual={self._pause_visual_temp}°C, "
            f"critical={self._critical_temp}°C"
        )

    @property
    def current_fps(self) -> int:
        """返回當前目標 FPS。"""
        return self._current_fps

    @property
    def visual_paused(self) -> bool:
        """返回是否暫停視覺處理。"""
        return self._visual_paused

    @property
    def snapshot_interval(self) -> float:
        """返回快照間隔（秒）。"""
        return self._snapshot_interval

    @property
    def current_temp(self) -> float:
        """返回當前 CPU 溫度。"""
        return self._current_temp

    def run(self):
        """主迴圈。"""
        logger.info("ThermalMonitor started")

        while not self._stop_event.wait(self.MONITOR_INTERVAL):
            try:
                temp = self._read_cpu_temp()
                self._current_temp = temp
                self._evaluate_temperature(temp)
            except Exception as e:
                logger.error(f"Error reading temperature: {e}")

        logger.info("ThermalMonitor stopped")

    def stop(self):
        """停止監控線程。"""
        self._stop_event.set()

    def _read_cpu_temp(self) -> float:
        """
        讀取 CPU 溫度。

        Returns:
            CPU 溫度（°C），若無法讀取則返回 50.0（安全值）
        """
        if platform.system() == "Linux":
            try:
                with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                    temp = int(f.read().strip()) / 1000.0
                    return round(temp, 1)
            except FileNotFoundError:
                logger.debug("thermal_zone0 not found, using mock value")
            except Exception as e:
                logger.warning(f"Error reading thermal_zone0: {e}")

        # 非 Linux 或讀取失敗，返回安全的 mock 值
        return 50.0

    def _evaluate_temperature(self, temp: float):
        """
        根據溫度評估並設定共享旗標。

        Args:
            temp: CPU 溫度（°C）
        """
        # < 70°C: 正常
        if temp < self.WARNING_TEMP:
            self._set_normal()

        # 70-75°C: 警告（僅記錄）
        elif temp < self._fps_reduce_temp:
            logger.warning(f"CPU temperature elevated: {temp}°C")
            # 不改變運行參數

        # 75-80°C: 降級
        elif temp < self._pause_visual_temp:
            self._set_degraded(temp)

        # 80-85°C: 視覺暫停
        elif temp < self._critical_temp:
            self._set_visual_paused(temp)

        # > 85°C: 緊急
        else:
            self._set_critical(temp)

    def _set_normal(self):
        """設定正常模式。"""
        if self._current_fps != self._normal_fps or self._visual_paused:
            logger.info("Temperature normal, restoring full performance")

        self._current_fps = self._normal_fps
        self._visual_paused = False
        self._snapshot_interval = 1.0

    def _set_degraded(self, temp: float):
        """設定降級模式。"""
        if self._current_fps == self._normal_fps and not self._visual_paused:
            logger.warning(f"FPS reduced to 10 due to high temperature: {temp}°C")

        self._current_fps = 10
        self._visual_paused = False
        self._snapshot_interval = 5.0  # 5 秒一張快照

    def _set_visual_paused(self, temp: float):
        """設定視覺暫停模式。"""
        if not self._visual_paused:
            logger.error(f"Visual processing paused due to high temperature: {temp}°C")

        self._current_fps = 10
        self._visual_paused = True
        self._snapshot_interval = 5.0

    def _set_critical(self, temp: float):
        """設定緊急模式。"""
        if not self._visual_paused:
            logger.critical(f"CRITICAL temperature: {temp}°C")

        self._current_fps = 10
        self._visual_paused = True
        self._snapshot_interval = 5.0

        # 調用回調
        if self._critical_callback:
            try:
                self._critical_callback(temp)
            except Exception as e:
                logger.error(f"Critical callback error: {e}")


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)

    # 測試用配置
    config = {
        "camera": {"fps": 15},
        "thermal": {
            "fps_reduce_temp": 75,
            "pause_visual_temp": 80,
            "critical_alert_temp": 85,
        },
    }

    def on_critical(temp):
        print(f"CRITICAL callback: temperature={temp}°C")

    monitor = ThermalMonitor(config, on_critical)
    monitor.start()

    print("ThermalMonitor running. Press Ctrl+C to stop.")
    print(f"Initial: fps={monitor.current_fps}, visual_paused={monitor.visual_paused}")

    try:
        while True:
            time.sleep(5)
            print(
                f"temp={monitor.current_temp}°C, "
                f"fps={monitor.current_fps}, "
                f"visual_paused={monitor.visual_paused}, "
                f"snapshot_interval={monitor.snapshot_interval}s"
            )
    except KeyboardInterrupt:
        print("\nStopping...")
        monitor.stop()
        monitor.join(timeout=5)