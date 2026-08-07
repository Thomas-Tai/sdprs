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


class TestEventProvenance:
    """事件來源標記（trigger_source）。"""

    def test_fusion_event_has_trigger_source(self, engine):
        """相關配對產生的事件其 trigger_source 應為 'fusion'。"""
        event = engine.evaluate(_visual(), _audio(), current_time=BASE)
        assert event is not None
        assert event.trigger_source == "fusion"


class TestDwellRunTracking:
    """視覺持續觸發區間（dwell run）追蹤。"""

    def test_triggered_starts_run_and_start_is_stable(self, engine):
        """triggered=True 開始區間；後續 triggered 幀不移動起點。"""
        engine.evaluate(_visual(triggered=True), None, current_time=BASE)
        assert engine._visual_run_start == BASE
        engine.evaluate(_visual(triggered=True), None, current_time=BASE + 1.0)
        assert engine._visual_run_start == BASE  # 起點保持不變

    def test_triggered_false_clears_run(self, engine):
        """triggered=False 中斷區間 → 起點重置為 None。"""
        engine.evaluate(_visual(triggered=True), None, current_time=BASE)
        assert engine._visual_run_start == BASE
        engine.evaluate(_visual(triggered=False), None, current_time=BASE + 0.5)
        assert engine._visual_run_start is None

    def test_none_visual_leaves_run_unchanged(self, engine):
        """visual_result=None（節流幀）不改變區間。"""
        engine.evaluate(_visual(triggered=True), None, current_time=BASE)
        assert engine._visual_run_start == BASE
        engine.evaluate(None, None, current_time=BASE + 0.5)
        assert engine._visual_run_start == BASE


# 純視覺回退測試用配置與門檻
SOLO_TRIGGER_CONFIG = {
    "correlation_window_seconds": 2,
    "cooldown_seconds": 30,
    "visual_only_fallback": True,
}
# 提高後的信心門檻 = edge_density_threshold(1.5) × multiplier(1.5) = 2.25
SOLO_THRESHOLD = 2.25


@pytest.fixture
def solo_engine():
    """音訊無串流 + 回退啟用 + 提高門檻的引擎。"""
    return TriggerEngine(
        SOLO_TRIGGER_CONFIG,
        node_id="test_node",
        audio_available=False,
        solo_confidence_threshold=SOLO_THRESHOLD,
    )


class TestVisualOnlyFallback:
    """單感測器（純視覺）回退。"""

    def test_and_unchanged_when_audio_available(self):
        """audio_available=True：即使視覺持續高信心也絕不單獨觸發。"""
        eng = TriggerEngine(
            SOLO_TRIGGER_CONFIG, node_id="test_node",
            audio_available=True, solo_confidence_threshold=SOLO_THRESHOLD,
        )
        assert eng.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        assert eng.evaluate(_visual(confidence=10.0), None, current_time=BASE + 5.0) is None

    def test_flag_off_no_solo(self):
        """visual_only_fallback 未設（=off）：不單獨觸發。"""
        eng = TriggerEngine(
            {"correlation_window_seconds": 2, "cooldown_seconds": 30},
            node_id="test_node",
            audio_available=False, solo_confidence_threshold=SOLO_THRESHOLD,
        )
        assert eng.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        assert eng.evaluate(_visual(confidence=10.0), None, current_time=BASE + 5.0) is None

    def test_dwell_gate_before_window(self, solo_engine):
        """持續時間未達 correlation_window（<2s）→ 不觸發。"""
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 1.0) is None

    def test_confidence_gate_below_bar(self, solo_engine):
        """持續達 dwell 但本幀信心低於提高後門檻（2.0 < 2.25）→ 不觸發。"""
        assert solo_engine.evaluate(_visual(confidence=2.0), None, current_time=BASE) is None
        assert solo_engine.evaluate(_visual(confidence=2.0), None, current_time=BASE + 2.0) is None
        assert solo_engine.evaluate(_visual(confidence=2.0), None, current_time=BASE + 2.5) is None

    def test_solo_fire_after_dwell_and_bar(self, solo_engine):
        """持續達 dwell 且信心 >= 門檻 → 產生 visual_only 事件。"""
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        event = solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 2.0)
        assert event is not None
        assert event.trigger_source == "visual_only"
        assert event.is_simulation is False
        assert event.node_id == "test_node"

    def test_none_gap_does_not_break_dwell(self, solo_engine):
        """區間中的 None（節流幀）不重置 dwell → 仍於窗口末端觸發。"""
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        assert solo_engine.evaluate(None, None, current_time=BASE + 1.0) is None
        event = solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 2.0)
        assert event is not None
        assert event.trigger_source == "visual_only"

    def test_triggered_false_breaks_dwell(self, solo_engine):
        """triggered=False 中斷 dwell，需重新持續滿窗口才觸發。"""
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        assert solo_engine.evaluate(_visual(triggered=False), None, current_time=BASE + 1.0) is None
        # 於 BASE+1.5 重新開始；BASE+2.0 尚未滿窗口（0.5s）
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 1.5) is None
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 2.0) is None
        # 自 BASE+1.5 起滿窗口 → 於 BASE+3.5 觸發
        event = solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 3.5)
        assert event is not None
        assert event.trigger_source == "visual_only"

    def test_solo_cooldown_suppresses_second(self, solo_engine):
        """觸發後 30 秒冷卻期內的第二次合格 solo 被抑制。"""
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE) is None
        first = solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 2.0)
        assert first is not None
        # 觸發後區間已重置；於 BASE+3.0 重新開始一段 run
        assert solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 3.0) is None
        # BASE+5.0 dwell 已滿（2s）但仍在冷卻期（距上次事件 3s）→ 抑制
        second = solo_engine.evaluate(_visual(confidence=10.0), None, current_time=BASE + 5.0)
        assert second is None
