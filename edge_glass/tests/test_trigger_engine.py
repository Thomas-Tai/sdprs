"""
TriggerEngine 單元測試

測試觸發引擎的融合邏輯：
- 只有視覺觸發不產生事件
- 視覺 + 音訊在相關窗口內產生事件
- 陳舊配對防護（久遠的視覺不與新鮮的音訊配對）
- 冷卻期抑制重複事件
- 冷卻期後可再次觸發
- 事件觸發後重置時間戳
- 強制觸發（模擬事件）
"""

from dataclasses import dataclass

import pytest

from detectors.trigger_engine import TriggerEngine, Event


# 測試用配置：相關窗口 2 秒、冷卻期 30 秒
TRIGGER_CONFIG = {
    "correlation_window_seconds": 2,
    "cooldown_seconds": 30,
}

# 使用大的類 epoch 時間基準，避免預設 30 秒冷卻期
# 意外阻擋「第一個」事件（_last_event_time 初始為 0）。
BASE = 1_000_000.0


# 模擬 VisualResult / AudioResult（僅需 evaluate() 讀取的欄位）
@dataclass
class FakeVisualResult:
    triggered: bool
    confidence: float = 0.0


@dataclass
class FakeAudioResult:
    triggered: bool
    delta_db: float = 0.0
    flatness: float = 1.0
    db_peak: float = 0.0
    freq_peak_hz: float = 0.0


@pytest.fixture
def engine():
    """建立 TriggerEngine 實例。"""
    return TriggerEngine(TRIGGER_CONFIG, node_id="test_node")


def _visual(triggered=True, confidence=2.0):
    return FakeVisualResult(triggered=triggered, confidence=confidence)


def _audio(triggered=True, delta_db=25.0, flatness=0.2):
    return FakeAudioResult(triggered=triggered, delta_db=delta_db, flatness=flatness)


class TestVisualOnly:
    """只有視覺觸發時不應產生事件。"""

    def test_visual_only_no_event(self, engine):
        """音訊從未觸發，僅視覺觸發 → 不產生事件。"""
        event = engine.evaluate(_visual(), _audio(triggered=False), current_time=BASE)
        assert event is None


class TestCorrelatedPair:
    """視覺 + 音訊在相關窗口內同時觸發。"""

    def test_visual_and_audio_within_window(self, engine):
        """視覺與音訊皆新鮮觸發 → 產生非模擬事件。"""
        event = engine.evaluate(_visual(), _audio(), current_time=BASE)
        assert event is not None
        assert isinstance(event, Event)
        assert event.is_simulation is False
        assert event.node_id == "test_node"

    def test_visual_then_audio_within_window(self, engine):
        """視覺先於音訊觸發，但仍在 2 秒窗口內 → 產生事件。"""
        # t=BASE：只有視覺
        assert engine.evaluate(_visual(), _audio(triggered=False), current_time=BASE) is None
        # t=BASE+1：音訊觸發，視覺仍在窗口內（1 <= 2）→ 事件
        event = engine.evaluate(
            _visual(triggered=False), _audio(), current_time=BASE + 1.0
        )
        assert event is not None
        assert event.is_simulation is False


class TestStalePairingGuard:
    """陳舊配對防護（本次修復的核心缺陷）。"""

    def test_stale_visual_does_not_pair_with_fresh_audio(self, engine):
        """
        視覺於 t=BASE 觸發，10 秒後音訊才觸發（遠超 2 秒窗口），
        且期間沒有新鮮的視覺觸發 → 不得產生事件。
        """
        # t=BASE：視覺觸發，音訊未觸發
        assert engine.evaluate(_visual(), _audio(triggered=False), current_time=BASE) is None

        # t=BASE+10：音訊觸發，但視覺已陳舊（10 秒前）且無新鮮視覺
        event = engine.evaluate(
            _visual(triggered=False), _audio(), current_time=BASE + 10.0
        )
        assert event is None


class TestCooldown:
    """冷卻期抑制與冷卻後再觸發。"""

    def test_second_pair_within_cooldown_suppressed(self, engine):
        """事件後 5 秒內的第二次相關配對被冷卻期抑制。"""
        # 第一次事件
        first = engine.evaluate(_visual(), _audio(), current_time=BASE)
        assert first is not None

        # 5 秒後的相關配對 → 仍在 30 秒冷卻期內 → 抑制
        second = engine.evaluate(_visual(), _audio(), current_time=BASE + 5.0)
        assert second is None

    def test_fresh_pair_after_cooldown(self, engine):
        """冷卻期（>30 秒）之後的新鮮相關配對 → 產生事件。"""
        first = engine.evaluate(_visual(), _audio(), current_time=BASE)
        assert first is not None

        # 冷卻期內被抑制
        assert engine.evaluate(_visual(), _audio(), current_time=BASE + 5.0) is None

        # 超過 30 秒後的新鮮配對 → 事件
        after = engine.evaluate(_visual(), _audio(), current_time=BASE + 31.0)
        assert after is not None
        assert after.is_simulation is False


class TestResetAfterFire:
    """事件觸發後重置時間戳。"""

    def test_single_detector_after_event_does_not_refire(self, engine):
        """
        事件觸發後時間戳被重置；緊接著的下一次呼叫只有單一偵測器
        觸發（另一個為 None）→ 不得再次觸發。
        """
        # 觸發事件
        event = engine.evaluate(_visual(), _audio(), current_time=BASE)
        assert event is not None

        # 觸發後時間戳應已重置為 None
        assert engine._last_visual_trigger_time is None
        assert engine._last_audio_trigger_time is None

        # 下一次呼叫只有視覺觸發、音訊為 None → 不再觸發
        again = engine.evaluate(_visual(), None, current_time=BASE + 0.5)
        assert again is None


class TestForceTrigger:
    """強制觸發（模擬模式）。"""

    def test_force_trigger_returns_simulation_event(self, engine):
        """force_trigger 應回傳 is_simulation=True 的事件。"""
        event = engine.force_trigger(current_time=BASE)
        assert event is not None
        assert isinstance(event, Event)
        assert event.is_simulation is True
        assert event.node_id == "test_node"
        assert event.timestamp == BASE

    def test_force_trigger_resets_timestamps(self, engine):
        """force_trigger 後觸發時間戳應被重置。"""
        # 先設定時間戳
        engine.evaluate(_visual(), _audio(triggered=False), current_time=BASE)
        assert engine._last_visual_trigger_time is not None

        engine.force_trigger(current_time=BASE + 1.0)
        assert engine._last_visual_trigger_time is None
        assert engine._last_audio_trigger_time is None
