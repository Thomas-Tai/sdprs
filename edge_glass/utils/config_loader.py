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
        "fps": 15,
    },
    "buffer": {
        "duration_seconds": 10,
    },
    "visual": {
        "edge_density_threshold": 1.5,
        "baseline_window_seconds": 60,
        "brightness_anomaly_percent": 50,
        "min_contour_length_px": 100,
        "roi_polygon": [[100, 50], [1180, 50], [1180, 670], [100, 670]],
        "canny_threshold1": 50,
        "canny_threshold2": 150,
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
        "fixed_db_threshold": 90,
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
        "api_key": "changeme-random-secret-key",
        "mqtt_broker": "central-server",
        "mqtt_port": 1883,
    },
    "stream": {
        "type": "hls",
        "auto_stop_minutes": 5,
        "tunnel_port": 18554,
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