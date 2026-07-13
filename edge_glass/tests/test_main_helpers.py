"""
edge_glass_main 純函式輔助工具單元測試

測試偵測器健康狀態計算的真值表：
- compute_audio_health：disabled 優先於 stale，stale 優先於 ok
- compute_visual_health：blinded 優先於 paused，paused 優先於 ok

備註：從 edge_glass 目錄執行 pytest 時，`import edge_glass_main` 可正常運作；
環境已安裝 cv2，因此頂層 import 不會失敗。
"""

import pytest

from edge_glass_main import compute_audio_health, compute_visual_health


# ========== compute_audio_health 真值表 ==========
# 參數：(audio_stream_present, audio_stale) -> 預期健康狀態


@pytest.mark.parametrize(
    "audio_stream_present, audio_stale, expected",
    [
        # 無串流：一律 disabled（disabled 優先於 stale）
        (False, False, "disabled"),
        (False, True, "disabled"),
        # 有串流：視資料是否過舊而定
        (True, False, "ok"),
        (True, True, "stale"),
    ],
)
def test_compute_audio_health(audio_stream_present, audio_stale, expected):
    assert compute_audio_health(audio_stream_present, audio_stale) == expected


def test_compute_audio_health_disabled_beats_stale():
    """無串流時，即使資料過舊也回報 disabled。"""
    assert compute_audio_health(False, True) == "disabled"


def test_compute_audio_health_stale_beats_ok():
    """有串流但資料過舊時回報 stale，而非 ok。"""
    assert compute_audio_health(True, True) == "stale"


# ========== compute_visual_health 真值表 ==========
# 參數：(thermal_paused, visual_blinded) -> 預期健康狀態


@pytest.mark.parametrize(
    "thermal_paused, visual_blinded, expected",
    [
        # 遮蔽：一律 blinded（blinded 優先於 paused）
        (True, True, "blinded"),
        (False, True, "blinded"),
        # 未遮蔽：視熱管理是否暫停而定
        (True, False, "paused"),
        (False, False, "ok"),
    ],
)
def test_compute_visual_health(thermal_paused, visual_blinded, expected):
    assert compute_visual_health(thermal_paused, visual_blinded) == expected


def test_compute_visual_health_blinded_beats_paused():
    """畫面遮蔽時，即使熱管理暫停也回報 blinded。"""
    assert compute_visual_health(True, True) == "blinded"


def test_compute_visual_health_paused_beats_ok():
    """未遮蔽但熱管理暫停時回報 paused，而非 ok。"""
    assert compute_visual_health(True, False) == "paused"
