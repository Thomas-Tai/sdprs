#!/usr/bin/env python3
"""
SDPRS 邊緣節點主程式 (M2 版本)

智能防災監測系統 - 玻璃破裂偵測邊緣節點

功能：
- 攝像頭擷取 → 循環緩衝區
- 視覺偵測（邊緣密度變化）
- 音訊偵測（RMS + FFT + attack time）
- 觸發引擎（視覺 + 音訊融合）
- MP4 編碼與本地儲存
- MQTT 心跳與指令接收
- 事件上傳佇列（JSON + MP4）
- 快照推送（背壓控制）
- 熱管理（CPU 溫度監控）
- 串流管理（按需 HLS）

執行緒模型（6 個線程）：
    Thread 1: 主迴圈 (Main Loop) - 攝像頭擷取、視覺偵測、融合判定
    Thread 2: 音訊擷取 (Audio Callback) - PyAudio 回調
    Thread 3: MQTT 客戶端 - 心跳發布、指令訂閱
    Thread 4: 上傳佇列 (Upload Worker) - JSON/MP4 上傳
    Thread 5: 快照推送 (Snapshot Pusher) - JPEG POST
    Thread 6: 熱管理 (Thermal Monitor) - CPU 溫度監控

使用方式：
    python edge_glass_main.py --config config.yaml
    python edge_glass_main.py --simulate  # 模擬觸發測試
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

# 加入專案路徑
sys.path.insert(0, str(Path(__file__).parent))

# M1 模組
from buffer.circular_buffer import CircularBuffer
from detectors.audio_detector import AudioDetector
from detectors.trigger_engine import Event, TriggerEngine
from detectors.visual_detector import VisualDetector
from utils.camera import open_camera as _open_camera_backend
from utils.config_loader import load_config
from utils.event_capture import PendingEventTracker, slice_window, clamp_capture_window, EncodeWorker
from utils.logger import setup as setup_logger
from utils.mp4_encoder import encode_mp4

# M2 模組
from comms.api_uploader import UploadWorker
from comms.event_queue import EventQueue
from comms.mqtt_client import MQTTClient
from stream.rtsp_server import StreamManager
from utils.snapshot import SnapshotPusher
from utils.thermal import ThermalMonitor

logger = logging.getLogger(__name__)

# 全域運行標誌
_running = True


def signal_handler(sig, frame):
    """處理終止信號。"""
    global _running
    _running = False
    logger.info("Shutdown signal received")


def start_audio_stream(
    audio_detector: AudioDetector, audio_config: dict
) -> Optional["pyaudio.Stream"]:
    """
    啟動 PyAudio 音訊擷取。

    Args:
        audio_detector: 音訊偵測器實例
        audio_config: 音訊配置

    Returns:
        PyAudio Stream 或 None（如果音訊不可用）
    """
    try:
        import pyaudio

        pa = pyaudio.PyAudio()
        # Store pa reference for proper cleanup
        start_audio_stream._pa = pa

        def audio_callback(in_data, frame_count, time_info, status):
            samples = np.frombuffer(in_data, dtype=np.int16).astype(np.float32)
            audio_detector.process_chunk(samples)
            return (None, pyaudio.paContinue)

        stream = pa.open(
            format=pyaudio.paInt16,
            channels=audio_config.get("channels", 1),
            rate=audio_config.get("sample_rate", 44100),
            input=True,
            input_device_index=audio_config.get("device_index"),
            frames_per_buffer=audio_config.get("chunk_size", 512),
            stream_callback=audio_callback,
        )
        stream.start_stream()
        logger.info("Audio stream started")
        return stream

    except Exception as e:
        logger.warning(f"Audio not available: {e}")
        logger.warning("Running in visual-only mode (AND logic will not trigger)")
        return None


def record_post_trigger(
    camera, seconds: int, fps: int
) -> List[Tuple[float, np.ndarray]]:
    """
    觸發後繼續錄製指定秒數。

    Args:
        camera: OpenCV VideoCapture 實例
        seconds: 錄製秒數
        fps: 幀率

    Returns:
        [(timestamp, frame), ...] 列表
    """
    frames = []
    target_count = seconds * fps

    for _ in range(target_count):
        if not _running:
            break

        ret, frame = camera.read()
        if ret and frame is not None:
            frames.append((time.time(), frame))
        time.sleep(1.0 / fps)

    return frames


def open_camera(cam_config: dict):
    """
    開啟攝像頭並套用解析度／幀率設定。

    初次開啟與失敗後重開皆使用此函式，確保重開後攝像頭參數一致，
    避免尺寸改變導致偵測器失效。

    Backend picked by ``utils.camera.open_camera``:
      * Raspberry Pi 5 → picamera2 (libcamera stack; cv2.VideoCapture
        cannot drive the rp1-cfe capture pipeline).
      * Anywhere else → cv2.VideoCapture (USB webcams, older Pi).

    Args:
        cam_config: 攝像頭配置（source / resolution / fps）

    Returns:
        Camera object with cv2.VideoCapture-compatible ``read()``/``set()``/
        ``get()``/``release()``/``isOpened()`` methods.
    """
    return _open_camera_backend(cam_config)


def compute_audio_health(audio_stream_present: bool, audio_stale: bool) -> str:
    """
    計算音訊偵測器健康狀態。

    優先順序：disabled（無串流）> stale（資料過舊）> ok。
    """
    if not audio_stream_present:
        return "disabled"
    if audio_stale:
        return "stale"
    return "ok"


def compute_visual_health(thermal_paused: bool, visual_blinded: bool) -> str:
    """
    計算視覺偵測器健康狀態。

    優先順序：blinded（畫面遮蔽）> paused（熱管理暫停）> ok。
    """
    if visual_blinded:
        return "blinded"
    if thermal_paused:
        return "paused"
    return "ok"


def main():
    """主函式。"""
    global _running

    # 解析命令列參數
    # 優先順序：--config 參數 > CONFIG_FILE 環境變數 > 預設 config.yaml
    import os as _os
    _default_config = _os.environ.get("CONFIG_FILE", "config.yaml")

    parser = argparse.ArgumentParser(description="SDPRS Edge Glass Node")
    parser.add_argument(
        "--config",
        type=str,
        default=_default_config,
        help="Path to config YAML (or set CONFIG_FILE env var)",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Trigger simulation event for testing",
    )
    args = parser.parse_args()

    # 載入配置
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"Configuration error: {e}")
        sys.exit(1)

    # 初始化日誌
    setup_logger(level=logging.INFO)

    logger.info(f"Starting SDPRS Edge Node: {config['node_id']}")
    logger.info(f"Camera: {config['camera']['resolution']} @ {config['camera']['fps']} fps")

    # 設置信號處理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 初始化攝像頭（套用解析度／幀率設定）
    camera = open_camera(config["camera"])
    if not camera.isOpened():
        logger.error("Failed to open camera")
        sys.exit(1)

    # 初始化組件
    buffer = CircularBuffer(
        fps=config["camera"]["fps"],
        duration_seconds=config["buffer"]["duration_seconds"],
    )

    visual_detector = VisualDetector(
        config["visual"],
        fps=config["camera"]["fps"],
    )

    audio_detector = AudioDetector(config["audio"])

    trigger_engine = TriggerEngine(
        config["trigger"],
        node_id=config["node_id"],
    )

    # 確保事件目錄存在
    events_dir = config["events"]["local_backup_dir"]
    max_local_files = int(config["events"].get("max_local_files", 20))
    Path(events_dir).mkdir(parents=True, exist_ok=True)

    # ============ M2 組件初始化 ============
    
    # 初始化本地事件佇列 (SQLite)
    queue_db_path = Path(events_dir) / "event_queue.db"
    event_queue = EventQueue(str(queue_db_path))
    logger.info("Event queue initialized")

    # 初始化 MQTT 客戶端
    mqtt_client: Optional[MQTTClient] = None
    try:
        mqtt_client = MQTTClient(config)
        logger.info("MQTT client initialized")
    except ImportError as e:
        logger.warning(f"MQTT not available: {e}")
        logger.warning("Running without MQTT (no heartbeat, no remote commands)")

    # 初始化串流管理器（需要 MQTT 發布狀態）
    stream_manager: Optional[StreamManager] = None
    if mqtt_client:
        stream_manager = StreamManager(
            config,
            publish_status_callback=mqtt_client.publish_stream_status,
        )
        logger.info("Stream manager initialized")

    # 初始化上傳工作線程
    upload_worker: Optional[UploadWorker] = None
    try:
        upload_worker = UploadWorker(event_queue, config)
        logger.info("Upload worker initialized")
    except ImportError as e:
        logger.warning(f"Upload worker not available: {e}")
        logger.warning("Events will not be uploaded to server")

    # 初始化快照推送線程（由 snapshot.enabled 控制；尺寸／品質由配置提供）
    snapshot_cfg = config.get("snapshot", {})
    snapshot_size = (
        int(snapshot_cfg.get("width", 854)),
        int(snapshot_cfg.get("height", 480)),
    )
    snapshot_jpeg_quality = int(snapshot_cfg.get("jpeg_quality", 50))
    snapshot_pusher: Optional[SnapshotPusher] = None
    if snapshot_cfg.get("enabled", True):
        try:
            snapshot_pusher = SnapshotPusher(config)
            logger.info("Snapshot pusher initialized")
        except ImportError as e:
            logger.warning(f"Snapshot pusher not available: {e}")
            logger.warning("Snapshots will not be pushed to server")
    else:
        logger.info("Snapshot pusher disabled by config (snapshot.enabled=false)")

    # 初始化熱管理監控線程
    def on_critical_temp(temp: float):
        """熱管理 CRITICAL 回調。"""
        logger.critical(f"CPU temperature critical: {temp}°C")
        # 可在此發送額外警報

    thermal_monitor = ThermalMonitor(config, critical_callback=on_critical_temp)
    logger.info("Thermal monitor initialized")

    # ============ 啟動線程 ============
    
    # 啟動音訊串流 (Thread 2)
    # PortAudio segfaults (not a Python exception — cannot be try/except'd)
    # when ``pa.open(input=True)`` is called on hardware with no capture
    # devices. Guard the entire call with ``audio.enabled`` so Pi nodes
    # without a USB mic can still run in visual-only mode.
    if config["audio"].get("enabled", True):
        audio_stream = start_audio_stream(audio_detector, config["audio"])
    else:
        logger.info("Audio disabled by config; running in visual-only mode")
        audio_stream = None

    # 模擬觸發請求標誌（供 MQTT simulate_trigger 指令設定，主迴圈讀取）
    sim_request = [False]

    # 啟動 MQTT 客戶端 (Thread 3)
    if mqtt_client:
        # 註冊指令回調
        if stream_manager:
            mqtt_client.register_command_handler(
                "stream_start", lambda payload: stream_manager.start()
            )
            mqtt_client.register_command_handler(
                "stream_stop", lambda payload: stream_manager.stop()
            )

        def handle_update(payload):
            """處理 update 指令。"""
            logger.info(f"Update command received: {payload}")
            # TODO: 實作自動更新邏輯

        def handle_simulate_trigger(payload):
            """處理 simulate_trigger 指令。"""
            logger.info(f"Simulate trigger command received: {payload}")
            # 設定一個標誌讓主迴圈觸發測試事件
            sim_request[0] = True

        mqtt_client.register_command_handler("update", handle_update)
        mqtt_client.register_command_handler("simulate_trigger", handle_simulate_trigger)

        mqtt_client.start()
        logger.info("MQTT client started")

    # 啟動上傳工作線程 (Thread 4)
    if upload_worker:
        upload_worker.start()
        logger.info("Upload worker started")

    # 啟動快照推送線程 (Thread 5)
    if snapshot_pusher:
        snapshot_pusher.start()
        logger.info("Snapshot pusher started")

    # 啟動熱管理監控線程 (Thread 6)
    thermal_monitor.start()
    logger.info("Thermal monitor started")

    # 模擬模式
    simulate_trigger = args.simulate

    # 主迴圈
    logger.info("Entering main loop...")

    # 初始化熱管理共享變數（來自 Thread 6）
    last_snapshot_time = time.time()
    last_health_time = time.time()       # 偵測器健康上報節流計時器（約每 5 秒）
    last_degraded_warn_time = 0.0        # 降級警告節流計時器（約每 30 秒）
    cooldown_until = 0
    fps_target = config["camera"]["fps"]

    # ============ 非同步事件擷取設定 (Thread 7，預設關閉) ============
    # async_encode=false 時完全維持傳統阻塞行為，以下僅做廉價讀取。
    cap_cfg = config.get("capture", {})
    async_encode = bool(cap_cfg.get("async_encode", False))
    encode_worker = None
    event_tracker = None
    if async_encode:
        pre_roll = float(cap_cfg.get("pre_roll_seconds", 4))
        post_roll = float(cap_cfg.get("post_roll_seconds", 5))
        buf_duration = float(config["buffer"]["duration_seconds"])
        pre_roll, post_roll = clamp_capture_window(buf_duration, pre_roll, post_roll, margin=1.0)
        event_tracker = PendingEventTracker(post_roll)
        encode_worker = EncodeWorker(
            partial(encode_mp4, max_local_files=max_local_files),
            event_queue,
            config["node_id"],
            events_dir,
            maxsize=int(cap_cfg.get("encode_queue_size", 2)),
        )
        encode_worker.start()
        logger.info(
            f"Async encode ENABLED (pre={pre_roll}s post={post_roll}s) — Thread 7 EncodeWorker started"
        )

    while _running:
      try:
        loop_start = time.time()

        # 1. 攝像頭擷取
        ret, frame = camera.read()
        if not ret or frame is None:
            camera_retry_count = getattr(main, "_cam_retry", 0) + 1
            main._cam_retry = camera_retry_count
            backoff = min(5 * camera_retry_count, 30)
            logger.warning(f"Camera read failed (attempt {camera_retry_count}), retrying in {backoff}s...")
            # 攝像頭讀取失敗：緩衝區停止進幀，心跳回報 degraded
            if mqtt_client:
                mqtt_client.set_buffer_health("degraded")
            if camera_retry_count > 10:
                logger.error("Camera failed too many times, exiting")
                _running = False
                break
            time.sleep(backoff)
            camera.release()
            camera = open_camera(config["camera"])
            if not camera.isOpened():
                continue
            continue

        # Reset camera retry counter on success
        if getattr(main, "_cam_retry", 0) > 0:
            main._cam_retry = 0
            # 攝像頭失敗後首次成功讀取：心跳回報 ok
            if mqtt_client:
                mqtt_client.set_buffer_health("ok")

        timestamp = time.time()

        # 2. 寫入循環緩衝區
        buffer.append(timestamp, frame)

        # 3. 視覺偵測（受熱管理控制）
        visual_result = None
        if not thermal_monitor.visual_paused:
            visual_result = visual_detector.analyze(frame)
        else:
            logger.debug("Visual processing paused due to high temperature")

        # 4. 音訊偵測
        audio_result = audio_detector.analyze()

        # 5. 融合判定
        event = trigger_engine.evaluate(visual_result, audio_result, current_time=timestamp)

        # 5b. 模擬觸發（--simulate 旗標或 MQTT simulate_trigger 指令）
        if simulate_trigger or sim_request[0]:
            simulate_trigger = False
            sim_request[0] = False
            event = trigger_engine.force_trigger(current_time=timestamp)
            logger.info("Simulation event created")

        # 6. 觸發處理（模擬事件不受冷卻時間限制）
        if event and (getattr(event, "is_simulation", False) or timestamp > cooldown_until):
            logger.info(
                f"EVENT TRIGGERED: confidence={event.visual_confidence:.2f}, "
                f"delta_db={event.audio_delta_db:.1f}"
            )

            # 事件元資料（telemetry-only）：非同步／傳統路徑共用同一份；
            # is_simulation 讓伺服器可區分演練事件與真實警報。
            event_metadata = {
                "visual_confidence": event.visual_confidence,
                "audio_db_peak": event.audio_db_peak,
                "audio_freq_peak_hz": event.audio_freq_peak_hz,
                "is_simulation": bool(getattr(event, "is_simulation", False)),
            }

            if async_encode:
                # 非阻塞路徑：僅登記事件；緩衝區持續填充，待 post_roll 經過後
                # （於下方 6b. due-drain 區塊）凍結、切片並交給編碼工作線程。
                # 主迴圈不會被錄製或編碼阻塞。
                event_tracker.add(event.timestamp, event_metadata)
            else:
                # ---- 傳統阻塞路徑（async_encode=false，行為不變）----
                # 7. 凍結緩衝區
                frozen_frames = buffer.freeze()
                logger.info(f"Frozen {len(frozen_frames)} frames from buffer")

                # 8. 後 5 秒錄製
                logger.info("Recording post-trigger frames...")
                post_frames = record_post_trigger(
                    camera,
                    seconds=5,
                    fps=int(thermal_monitor.current_fps),
                )
                logger.info(f"Recorded {len(post_frames)} post-trigger frames")

                # 9. 合併幀
                all_frames = frozen_frames + post_frames
                logger.info(f"Total frames to encode: {len(all_frames)}")

                # 10. 編碼 MP4
                mp4_path = None
                try:
                    mp4_path = encode_mp4(
                        all_frames,
                        node_id=config["node_id"],
                        timestamp=event.timestamp,
                        output_dir=events_dir,
                        max_local_files=max_local_files,
                    )
                    logger.info(f"MP4 saved: {mp4_path}")
                except Exception as e:
                    logger.error(f"MP4 encoding failed: {e}")

                # 11. 加入上傳佇列（Thread 4）
                if mp4_path and event_queue is not None:
                    try:
                        event_queue.enqueue(
                            node_id=config["node_id"],
                            timestamp=datetime.fromtimestamp(event.timestamp).isoformat(),
                            mp4_path=mp4_path,
                            metadata=event_metadata,
                        )
                        logger.info(f"Event enqueued for upload: {mp4_path}")
                    except Exception as e:
                        logger.error(f"Failed to enqueue event: {e}")

            # 設定冷卻時間（僅真實事件；模擬演練不得壓制真實警報 30 秒）
            if not getattr(event, "is_simulation", False):
                cooldown_until = timestamp + config["trigger"]["cooldown_seconds"]

        # 6b. 非同步擷取：對 post-roll 視窗已到期的事件凍結、切片並提交編碼。
        #     此區塊每次迭代皆執行（僅非同步模式）。
        if async_encode and event_tracker is not None:
            for ev in event_tracker.due(timestamp):
                frames = slice_window(buffer.freeze(), ev.trigger_ts - pre_roll, ev.trigger_ts + post_roll)
                submitted = encode_worker.submit(frames, ev.trigger_ts, ev.metadata)
                if not submitted:
                    logger.warning(f"Encode submit dropped (queue full) for event ts={ev.trigger_ts:.3f}")
                else:
                    logger.info(f"Event handed to encode worker: {len(frames)} frames, ts={ev.trigger_ts:.3f}")

        # 12. 快照推送（受熱管理控制）
        if timestamp - last_snapshot_time >= thermal_monitor.snapshot_interval:
            if snapshot_pusher and snapshot_pusher.is_idle:
                try:
                    small_frame = cv2.resize(frame, snapshot_size)
                    _, jpeg = cv2.imencode('.jpg', small_frame, [cv2.IMWRITE_JPEG_QUALITY, snapshot_jpeg_quality])
                    snapshot_pusher.push(jpeg.tobytes())
                    last_snapshot_time = timestamp
                except Exception as e:
                    logger.error(f"Failed to push snapshot: {e}")

        # 13. 偵測器健康上報（節流：約每 5 秒）
        if timestamp - last_health_time >= 5.0:
            last_health_time = timestamp
            audio_health = compute_audio_health(
                audio_stream is not None, audio_detector.is_stale(5.0)
            )
            visual_health = compute_visual_health(
                thermal_monitor.visual_paused,
                getattr(visual_detector, "blinded", False),
            )
            if mqtt_client:
                mqtt_client.set_detector_health(visual=visual_health, audio=audio_health)

            # 降級警告（節流：約每 30 秒）—— 節點可能無法告警
            if audio_health != "ok" or visual_health != "ok":
                if timestamp - last_degraded_warn_time >= 30.0:
                    last_degraded_warn_time = timestamp
                    logger.warning(
                        "Detector degraded: audio=%s visual=%s — node may be unable to alert",
                        audio_health,
                        visual_health,
                    )

        # 14. 動態 FPS 控制（受熱管理影響）
        elapsed = time.time() - loop_start
        target_interval = 1.0 / thermal_monitor.current_fps
        sleep_time = target_interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

      except Exception as loop_err:
        logger.error(f"Main loop error: {loop_err}", exc_info=True)
        time.sleep(1)  # Avoid tight loop on persistent errors

    # ============ 關閉序列 ============
    logger.info("Shutting down...")

    # 0. 排空並停止編碼工作線程 (Thread 7，僅非同步編碼模式)
    if encode_worker is not None:
        logger.info("Draining encode worker...")
        encode_worker.stop(drain=True)
        logger.info("Encode worker stopped")

    # 1. 停止快照推送線程
    if snapshot_pusher:
        snapshot_pusher.stop()
        logger.info("Snapshot pusher stopped")

    # 2. 停止上傳工作線程
    if upload_worker:
        upload_worker.stop()
        logger.info("Upload worker stopped")

    # 3. 停止 MQTT 客戶端
    if mqtt_client:
        mqtt_client.stop()
        logger.info("MQTT client stopped")

    # 4. 停止熱管理監控線程
    thermal_monitor.stop()
    logger.info("Thermal monitor stopped")

    # 5. 停止串流管理器
    if stream_manager:
        stream_manager.stop()
        logger.info("Stream manager stopped")

    # 6. 釋放攝像頭
    camera.release()
    logger.info("Camera released")

    # 7. 停止音訊串流
    if audio_stream is not None:
        audio_stream.stop_stream()
        audio_stream.close()
        # Terminate the original PyAudio instance (not a new one)
        pa_instance = getattr(start_audio_stream, "_pa", None)
        if pa_instance:
            try:
                pa_instance.terminate()
            except Exception:
                pass
        logger.info("Audio stream stopped")

    logger.info("Shutdown complete")


if __name__ == "__main__":
    main()