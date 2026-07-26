"""
配置載入模組

載入 YAML 配置文件並驗證必要欄位，合併預設值。

使用範例：
    from utils.config_loader import load_config

    config = load_config("config.yaml")
    print(config["node_id"])
"""

import os
import logging
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)

# 預設值字典（用於 deep merge）
DEFAULTS: Dict[str, Any] = {
    "node_id": "glass_node_01",
    "camera": {
        "source": 0,
        "resolution": [1280, 720],
        # 擷取幀率（供緩衝與快照）；偵測以 visual.detect_fps 執行。15→12 降低擷取/ISP
        # 負載，對事件回放平順度幾乎無感。節點可在自己的 config 覆寫。
        "fps": 12,
    },
    "buffer": {
        "duration_seconds": 10,
    },
    # 事件擷取：非阻塞編碼路徑為預設。事件不再凍結主迴圈（錄 5s + 編碼約 15s 的
    # 720p）數十秒，快照與偵測在事件期間持續運作。節點可在自己的 config 覆寫。
    "capture": {
        "async_encode": True,
        "pre_roll_seconds": 4,
        "post_roll_seconds": 5,
        "encode_queue_size": 2,
    },
    "visual": {
        # 視覺偵測以此幀率執行，而非每一台攝像頭幀。CV（Canny／輪廓）是 Pi 的主要
        # 發熱來源，玻璃破裂偵測不需要 15fps。攝像頭仍以 camera.fps 擷取供緩衝／快照。
        # 夾在 [1, camera.fps]（見 edge_glass_main.resolve_detect_fps）。
        "detect_fps": 5,
        # 偵測降採樣：偵測管線在半解析度工作副本上執行（約 ¼ 畫素）。錄影/快照仍原尺寸。
        "detect_scale": 0.5,
        # 防震對齊開關：預設開啟。剛性固定攝像頭可關閉以省下最貴的 ORB 階段。
        "stabilize": True,
        "edge_density_threshold": 1.5,
        "baseline_window_seconds": 60,
        "brightness_anomaly_percent": 50,
        "min_contour_length_px": 100,
        "roi_polygon": [[100, 50], [1180, 50], [1180, 670], [100, 670]],
        "canny_threshold1": 50,
        "canny_threshold2": 150,
        "anomaly_recovery_seconds": 3,   # 亮度異常後的恢復期（visual_detector 讀取）
    },
    "audio": {
        "device_index": 1,
        "mode": "adaptive",
        "sample_rate": 44100,
        "channels": 1,
        "chunk_size": 512,
        "rolling_baseline_seconds": 30,
        "delta_db_threshold": 20,
        "spectral_flatness_threshold": 0.3,
        "attack_time_ms": 10,
        "analysis_window_ms": 500,
        "fixed_db_threshold": -30,   # dBFS（0 = 滿刻度），非 SPL——正值永遠無法觸發
        "fixed_freq_threshold_hz": 3000,
    },
    "trigger": {
        "correlation_window_seconds": 2,
        "cooldown_seconds": 30,
    },
    "thermal": {
        "fps_reduce_temp": 75,
        "pause_visual_temp": 80,
        "critical_alert_temp": 85,
    },
    "server": {
        "api_url": "http://central-server:8000/api",
        # 心跳間隔（秒）：節點狀態／CPU 溫度／健康的上報頻率。調低讓儀表板更即時；
        # 仍遠小於伺服器約 90 秒的離線逾時。節點可在自己的 config 覆寫。
        "heartbeat_interval": 10,
        "api_key": "changeme-random-secret-key",
        "mqtt_broker": "central-server",
        "mqtt_port": 1883,
        "mqtt_username": "",        # MQTT auth username (Zeabur Mosquitto)
        "mqtt_password": "",        # MQTT auth password (Zeabur Mosquitto)
        "mqtt_use_tls": False,      # Enable TLS for MQTT connection (cloud)
    },
    "stream": {
        "type": "hls",
        "auto_stop_minutes": 5,
        "tunnel_port": 18554,
        "cloud_mode": False,        # True = skip SSH tunnel, use HTTP push
    },
    "snapshot": {
        "enabled": True,
        "fps": 1,
        "fps_degraded": 0.2,
        "width": 854,
        "height": 480,
        "jpeg_quality": 50,
    },
    "events": {
        "local_backup_dir": "./events",
        "max_local_files": 20,
    },
    "timezone": "Asia/Macau",
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    遞迴合併兩個字典。

    override 的值覆蓋 base 的值，但對於嵌套字典會遞迴合併。

    Args:
        base: 基礎字典（預設值）
        override: 覆蓋字典（用戶配置）

    Returns:
        合併後的字典
    """
    result = base.copy()
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_required(config: Dict[str, Any]) -> None:
    """
    驗證必要欄位。

    Args:
        config: 配置字典

    Raises:
        ValueError: 必要欄位缺失或無效
    """
    errors = []

    # node_id
    if "node_id" not in config or not config["node_id"]:
        errors.append("node_id is required and cannot be empty")

    # camera.source
    if "camera" not in config or "source" not in config["camera"]:
        errors.append("camera.source is required")

    # camera.resolution
    if "camera" in config:
        resolution = config["camera"].get("resolution")
        if resolution is None:
            errors.append("camera.resolution is required")
        elif not isinstance(resolution, list) or len(resolution) != 2:
            errors.append("camera.resolution must be a list of 2 integers [width, height]")

        # camera.fps
        fps = config["camera"].get("fps")
        if fps is None:
            errors.append("camera.fps is required")
        elif not isinstance(fps, int) or fps <= 0:
            errors.append("camera.fps must be a positive integer")

    # buffer.duration_seconds
    if "buffer" in config:
        duration = config["buffer"].get("duration_seconds")
        if duration is None:
            errors.append("buffer.duration_seconds is required")
        elif not isinstance(duration, int) or duration <= 0:
            errors.append("buffer.duration_seconds must be a positive integer")

    # server.api_url
    if "server" not in config or "api_url" not in config["server"]:
        errors.append("server.api_url is required")

    # server.mqtt_broker
    if "server" not in config or "mqtt_broker" not in config["server"]:
        errors.append("server.mqtt_broker is required")

    if errors:
        raise ValueError("Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors))


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """
    載入 YAML 配置並驗證必要欄位。

    Args:
        config_path: YAML 配置檔路徑

    Returns:
        合併預設值後的完整配置字典

    Raises:
        FileNotFoundError: 配置檔不存在
        ValueError: 必要欄位缺失或無效
    """
    abs_path = os.path.abspath(config_path)

    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Configuration file not found: {abs_path}")

    logger.info(f"Loading configuration from: {abs_path}")

    with open(abs_path, "r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f)

    if user_config is None:
        user_config = {}

    # 合併預設值
    config = _deep_merge(DEFAULTS, user_config)

    # 驗證必要欄位
    _validate_required(config)

    logger.info(f"Configuration loaded successfully for node: {config.get('node_id')}")

    return config


if __name__ == "__main__":
    # 測試載入
    import sys

    logging.basicConfig(level=logging.INFO)

    try:
        config = load_config("config.yaml")
        print("\n=== Configuration Loaded ===")
        print(f"Node ID: {config['node_id']}")
        print(f"Camera: {config['camera']['resolution']} @ {config['camera']['fps']} fps")
        print(f"Buffer: {config['buffer']['duration_seconds']} seconds")
        print(f"Audio mode: {config['audio']['mode']}")
        print(f"Server API: {config['server']['api_url']}")
        print("\n=== All keys ===")
        for key in sorted(config.keys()):
            print(f"  - {key}")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"Validation Error: {e}")
        sys.exit(1)