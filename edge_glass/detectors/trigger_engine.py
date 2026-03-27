"""
觸發引擎模組

融合視覺和音訊偵測結果，只有在兩者在相關窗口內同時觸發才產生事件。

融合邏輯：
    Visual ─triggered──>┐
                        ├── 相關窗口 (2秒內) ──triggered──> 觸發事件
    Audio  ─triggered──>┘
                               │
                          冷卻期檢查 (30秒內不重複)

使用範例：
    from detectors.trigger_engine import TriggerEngine, Event

    engine = TriggerEngine(config["trigger"], node_id="glass_node_01")

    event = engine.evaluate(visual_result, audio_result)
    if event:
        print(f"Event triggered at {event.timestamp}")
"""

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from detectors.audio_detector import AudioResult
    from detectors.visual_detector import VisualResult

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """觸發事件資料。"""

    timestamp: float  # 事件觸發時間
    node_id: str  # 節點 ID
    visual_confidence: float  # 視覺置信度
    audio_delta_db: float  # 音訊 delta dB
    audio_flatness: float  # 頻譜平坦度
    audio_db_peak: float = 0.0  # 峰值音訊 dB
    audio_freq_peak_hz: float = 0.0  # 峰值頻率 Hz
    is_simulation: bool = False  # 是否為模擬事件


class TriggerEngine:
    """
    觸發引擎 — 融合視覺和音訊偵測結果。

    融合策略：Visual AND Audio 在相關窗口內同時觸發。
    冷卻期：防止單一持續事件的警報洪水。
    """

    def __init__(self, config: dict, node_id: str):
        """
        Args:
            config: config.yaml 中 "trigger" 區塊的字典，包含：
                - correlation_window_seconds: float (2)
                - cooldown_seconds: float (30)
            node_id: 節點 ID
        """
        self._correlation_window = config.get("correlation_window_seconds", 2)
        self._cooldown_seconds = config.get("cooldown_seconds", 30)
        self._node_id = node_id

        # 觸發時間戳
        self._last_visual_trigger_time: Optional[float] = None
        self._last_audio_trigger_time: Optional[float] = None

        # 上次事件時間
        self._last_event_time: float = 0

        # 最後一次觸發的詳細資訊
        self._last_visual_confidence: float = 0.0
        self._last_audio_delta_db: float = 0.0
        self._last_audio_flatness: float = 1.0
        self._last_audio_db_peak: float = 0.0
        self._last_audio_freq_peak_hz: float = 0.0

    def evaluate(
        self,
        visual_result: Optional["VisualResult"],
        audio_result: Optional["AudioResult"],
        current_time: Optional[float] = None,
    ) -> Optional[Event]:
        """
        評估是否觸發事件。

        每幀呼叫一次。更新觸發時間戳，檢查相關窗口和冷卻期。

        Args:
            visual_result: 視覺偵測結果（None 表示無結果/異常幀/暫停）
            audio_result: 音訊偵測結果（None 表示緩衝未滿）
            current_time: 當前時間戳（預設 time.time()，測試時可注入）

        Returns:
            Event 若觸發，否則 None
        """
        current_time = current_time or time.time()

        # 更新視覺觸發時間
        if visual_result is not None and visual_result.triggered:
            self._last_visual_trigger_time = current_time
            self._last_visual_confidence = visual_result.confidence
            logger.debug(
                f"Visual trigger at {current_time:.3f}, "
                f"confidence={visual_result.confidence:.2f}"
            )

        # 更新音訊觸發時間
        if audio_result is not None and audio_result.triggered:
            self._last_audio_trigger_time = current_time
            self._last_audio_delta_db = audio_result.delta_db
            self._last_audio_flatness = audio_result.flatness
            self._last_audio_db_peak = getattr(audio_result, 'db_peak', 0.0)
            self._last_audio_freq_peak_hz = getattr(audio_result, 'freq_peak_hz', 0.0)
            logger.debug(
                f"Audio trigger at {current_time:.3f}, "
                f"delta_db={audio_result.delta_db:.1f}"
            )

        # 檢查兩者是否都在窗口內觸發
        if not self._check_correlation(current_time):
            return None

        # 檢查冷卻期
        if not self._check_cooldown(current_time):
            logger.info("Trigger suppressed by cooldown")
            return None

        # 產生事件
        event = Event(
            timestamp=current_time,
            node_id=self._node_id,
            visual_confidence=self._last_visual_confidence,
            audio_delta_db=self._last_audio_delta_db,
            audio_flatness=self._last_audio_flatness,
            audio_db_peak=self._last_audio_db_peak,
            audio_freq_peak_hz=self._last_audio_freq_peak_hz,
            is_simulation=False,
        )

        # 更新上次事件時間
        self._last_event_time = current_time

        logger.info(
            f"EVENT TRIGGERED: confidence={event.visual_confidence:.2f}, "
            f"delta_db={event.audio_delta_db:.1f}, "
            f"flatness={event.audio_flatness:.3f}"
        )

        return event

    def _check_correlation(self, current_time: float) -> bool:
        """
        檢查視覺和音訊是否在相關窗口內同時觸發。

        Args:
            current_time: 當前時間

        Returns:
            是否滿足相關條件
        """
        if self._last_visual_trigger_time is None:
            return False

        if self._last_audio_trigger_time is None:
            return False

        # 計算兩次觸發的時間差
        time_diff = abs(
            self._last_visual_trigger_time - self._last_audio_trigger_time
        )

        if time_diff <= self._correlation_window:
            logger.debug(
                f"Correlation check passed: time_diff={time_diff:.3f}s"
            )
            return True

        return False

    def _check_cooldown(self, current_time: float) -> bool:
        """
        檢查是否在冷卻期外。

        Args:
            current_time: 當前時間

        Returns:
            是否可以觸發（True = 冷卻期外）
        """
        time_since_last = current_time - self._last_event_time
        return time_since_last > self._cooldown_seconds

    def force_trigger(self, current_time: Optional[float] = None) -> Event:
        """
        強制觸發（用於模擬模式）。

        忽略冷卻期，直接產生事件。

        Returns:
            Event (is_simulation=True)
        """
        current_time = current_time or time.time()

        event = Event(
            timestamp=current_time,
            node_id=self._node_id,
            visual_confidence=self._last_visual_confidence,
            audio_delta_db=self._last_audio_delta_db,
            audio_flatness=self._last_audio_flatness,
            audio_db_peak=self._last_audio_db_peak,
            audio_freq_peak_hz=self._last_audio_freq_peak_hz,
            is_simulation=True,
        )

        logger.info(f"SIMULATION EVENT TRIGGERED at {current_time:.3f}")

        return event


if __name__ == "__main__":
    import logging

    from dataclasses import dataclass

    logging.basicConfig(level=logging.DEBUG)

    # 模擬 VisualResult 和 AudioResult
    @dataclass
    class VisualResult:
        triggered: bool
        confidence: float = 0.0

    @dataclass
    class AudioResult:
        triggered: bool
        delta_db: float = 0.0
        flatness: float = 1.0
        is_impulsive: bool = False

    # 測試觸發引擎
    config = {
        "correlation_window_seconds": 2,
        "cooldown_seconds": 30,
    }

    engine = TriggerEngine(config, node_id="test_node")

    # 測試 1：只有視覺觸發 → 不應產生事件
    print("Test 1: Visual only")
    visual = VisualResult(triggered=True, confidence=1.5)
    audio = AudioResult(triggered=False)
    event = engine.evaluate(visual, audio, current_time=1.0)
    print(f"Result: {event}")

    # 測試 2：視覺 + 音訊在窗口內 → 應產生事件
    print("\nTest 2: Visual + Audio within window")
    visual = VisualResult(triggered=True, confidence=2.0)
    audio = AudioResult(triggered=True, delta_db=25, flatness=0.2)
    event = engine.evaluate(visual, audio, current_time=1.5)
    print(f"Result: {event}")

    # 測試 3：冷卻期內 → 不應產生事件
    print("\nTest 3: Within cooldown")
    visual = VisualResult(triggered=True, confidence=2.0)
    audio = AudioResult(triggered=True, delta_db=25, flatness=0.2)
    event = engine.evaluate(visual, audio, current_time=5.0)
    print(f"Result: {event}")

    # 測試 4：冷卻期外 → 應產生事件
    print("\nTest 4: After cooldown")
    event = engine.evaluate(visual, audio, current_time=35.0)
    print(f"Result: {event}")

    # 測試 5：強制觸發
    print("\nTest 5: Force trigger")
    event = engine.force_trigger(current_time=40.0)
    print(f"Result: {event}")