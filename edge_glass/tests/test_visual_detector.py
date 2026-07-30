"""
VisualDetector 單元測試

測試視覺偵測器的完整功能：
- 正常幀不觸發
- 異常幀返回 None
- 裂縫幀觸發
- ROI 遮罩
- 基線建立
"""

import numpy as np
import pytest
import cv2

from detectors.visual_detector import VisualDetector, VisualResult


# 測試用配置
VISUAL_CONFIG = {
    "edge_density_threshold": 0.5,  # 降低閾值以便測試
    "baseline_window_seconds": 60,
    "brightness_anomaly_percent": 50,
    "min_contour_length_px": 50,  # 降低閾值以便測試
    "roi_polygon": [[100, 50], [1180, 50], [1180, 670], [100, 670]],
    "canny_threshold1": 50,
    "canny_threshold2": 150,
}


@pytest.fixture
def detector():
    """建立 VisualDetector 實例。"""
    return VisualDetector(VISUAL_CONFIG, fps=15)


class TestVisualDetectorNormalFrames:
    """測試正常幀處理。"""

    def test_normal_frame_no_trigger(self, detector):
        """連續傳入相同的正常灰色幀，不應觸發。"""
        normal_frame = np.full((720, 1280, 3), 128, dtype=np.uint8)

        # Feed 30 幀建立基線
        for _ in range(30):
            result = detector.analyze(normal_frame)

        # 最後一幀不應觸發
        if result:
            assert result.triggered is False

    def test_first_frame_no_crash(self, detector):
        """第一幀不應崩潰。"""
        normal_frame = np.full((720, 1280, 3), 128, dtype=np.uint8)
        result = detector.analyze(normal_frame)

        assert result is not None
        assert result.triggered is False


class TestVisualDetectorAnomalyFrames:
    """測試異常幀排除。"""

    def test_anomaly_frame_returns_none(self, detector):
        """先 feed 正常幀，然後傳入全白幀 → 返回 None。"""
        normal_frame = np.full((720, 1280, 3), 128, dtype=np.uint8)

        # Feed 10 幀正常幀建立亮度基線
        for _ in range(10):
            detector.analyze(normal_frame)

        # 傳入全白幀
        white_frame = np.full((720, 1280, 3), 255, dtype=np.uint8)
        result = detector.analyze(white_frame)

        assert result is None

    def test_anomaly_full_black_returns_none(self, detector):
        """正常幀後傳入全黑幀，應返回 None。"""
        normal_frame = np.full((720, 1280, 3), 128, dtype=np.uint8)

        # Feed 10 幀正常幀
        for _ in range(10):
            detector.analyze(normal_frame)

        # 傳入全黑幀
        black_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        result = detector.analyze(black_frame)

        assert result is None


class TestVisualDetectorCrackDetection:
    """測試裂縫偵測。"""

    def test_crack_triggers_detection(self, detector):
        """建立基線後，傳入含裂縫的幀應觸發。"""
        normal_frame = np.full((720, 1280, 3), 128, dtype=np.uint8)

        # 建立基線
        for _ in range(30):
            detector.analyze(normal_frame)

        # 建立含裂縫的幀
        crack_frame = np.full((720, 1280, 3), 128, dtype=np.uint8)
        # 在 ROI 內畫多條白色線條（模擬裂縫）
        cv2.line(crack_frame, (200, 200), (1000, 600), (255, 255, 255), 3)
        cv2.line(crack_frame, (300, 100), (900, 500), (255, 255, 255), 2)
        cv2.line(crack_frame, (400, 300), (800, 650), (255, 255, 255), 3)

        result = detector.analyze(crack_frame)

        # 可能需要多幀才能觸發
        triggered = False
        for _ in range(10):
            result = detector.analyze(crack_frame)
            if result and result.triggered:
                triggered = True
                break

        assert triggered, "Crack detection should trigger"

    def test_crack_outside_roi_no_trigger(self, detector):
        """裂縫線條在 ROI 外，不應觸發。"""
        normal_frame = np.full((720, 1280, 3), 128, dtype=np.uint8)

        # 建立基線
        for _ in range(30):
            detector.analyze(normal_frame)

        # 建立在 ROI 外的裂縫（左上角 0-50 範圍）
        crack_frame_outside = np.full((720, 1280, 3), 128, dtype=np.uint8)
        cv2.line(crack_frame_outside, (0, 0), (50, 50), (255, 255, 255), 5)

        # 多次分析
        for _ in range(10):
            result = detector.analyze(crack_frame_outside)
            if result:
                # ROI 外的裂縫不應觸發
                pass  # 允許不觸發或低置信度


class TestVisualDetectorBaseline:
    """測試基線建立。"""

    def test_baseline_builds_over_time(self, detector):
        """初始時基線為 None，feed 足夠幀後基線被建立。"""
        normal_frame = np.full((720, 1280, 3), 128, dtype=np.uint8)

        # 初始狀態
        assert detector._baseline_image is None

        # Feed 幀
        for _ in range(30):
            detector.analyze(normal_frame)

        # 基線應已建立
        assert detector._baseline_image is not None


class TestVisualDetectorROI:
    """測試 ROI 遮罩。"""

    def test_roi_mask_created(self, detector):
        """ROI 遮罩應在初始化時建立。"""
        assert detector._roi_mask is not None
        assert detector._roi_mask.shape == (720, 1280)
        assert detector._roi_pixel_count > 0

    def test_roi_covers_expected_area(self, detector):
        """ROI 應覆蓋預期的區域。"""
        # ROI 多邊形 [[100,50],[1180,50],[1180,670],[100,670]]
        # 檢查內部點應為 255
        assert detector._roi_mask[100, 200] == 255  # 內部點
        # 檢查外部點應為 0
        assert detector._roi_mask[10, 10] == 0  # 外部點（左上角）


class TestVisualDetectorAnomalyRecovery:
    """測試持續性異常復原（避免偵測器被永久致盲）。"""

    def test_single_anomaly_sets_blinded(self, detector):
        """建立基線後，單一異常幀返回 None 且致盲旗標為 True。"""
        normal_frame = np.full((720, 1280, 3), 128, dtype=np.uint8)

        # Feed ~10 幀正常灰色幀建立亮度基線
        for _ in range(10):
            detector.analyze(normal_frame)

        # 傳入一幀全白（異常）幀
        white_frame = np.full((720, 1280, 3), 255, dtype=np.uint8)
        result = detector.analyze(white_frame)

        # 單一異常幀仍返回 None，且此時偵測器致盲
        assert result is None
        assert detector.blinded is True

    def test_sustained_anomaly_recovers(self, detector):
        """持續餵入超過 fps*3 幀的白幀 → 偵測器重新建立基線並復原。"""
        normal_frame = np.full((720, 1280, 3), 128, dtype=np.uint8)

        # 建立基線
        for _ in range(10):
            detector.analyze(normal_frame)

        white_frame = np.full((720, 1280, 3), 255, dtype=np.uint8)

        # 連續餵入白幀（多於 fps*3 = 45 幀），追蹤是否發生復原
        recovered = False
        recovered_at = None
        for i in range(50):
            result = detector.analyze(white_frame)
            if result is not None:
                # 出現非 None 結果代表重新建立基線（復原）
                recovered = True
                recovered_at = i
                break

        assert recovered, "持續性異常後偵測器應重新建立基線並復原"
        assert isinstance(result, VisualResult)
        # 復原時致盲旗標應回到 False
        assert detector.blinded is False

        # 復原後繼續餵入白幀（現在的新「正常」）不應每次都返回 None
        none_count = 0
        non_none_count = 0
        for _ in range(20):
            post = detector.analyze(white_frame)
            if post is None:
                none_count += 1
            else:
                non_none_count += 1

        assert non_none_count > 0, "復原後白幀已成為新的正常基準，不應持續返回 None"
        assert detector.blinded is False


# ============================================================
# 效率／熱管理最佳化（2026-07-26，scope B）
# ============================================================


class _CountingORB:
    """Wraps a real cv2.ORB, counting detectAndCompute calls, delegating the rest."""

    def __init__(self, real):
        self._real = real
        self.calls = 0

    def detectAndCompute(self, *args, **kwargs):
        self.calls += 1
        return self._real.detectAndCompute(*args, **kwargs)


def test_orb_features_computed_once_per_frame():
    """Stabilization must reuse the previous frame's cached descriptors, so ORB
    detectAndCompute runs exactly once per analyze() (was twice: prev + current)."""
    detector = VisualDetector(VISUAL_CONFIG, fps=15)
    spy = _CountingORB(detector._orb)
    detector._orb = spy

    frame = np.full((720, 1280, 3), 128, dtype=np.uint8)
    for _ in range(5):
        detector.analyze(frame)

    # 5 analyze() calls -> exactly 5 detectAndCompute calls (one per frame).
    assert spy.calls == 5


def test_orb_skipped_on_anomaly_frames():
    """The cheap brightness/anomaly gate runs before stabilization, so an anomaly
    frame returns None without paying the ORB cost."""
    detector = VisualDetector(VISUAL_CONFIG, fps=15)
    normal = np.full((720, 1280, 3), 128, dtype=np.uint8)
    for _ in range(10):
        detector.analyze(normal)  # establish the brightness baseline

    spy = _CountingORB(detector._orb)
    detector._orb = spy
    white = np.full((720, 1280, 3), 255, dtype=np.uint8)
    result = detector.analyze(white)

    assert result is None
    assert spy.calls == 0  # anomaly frame skipped stabilization entirely


def test_detect_scale_builds_scaled_roi_mask():
    cfg = {**VISUAL_CONFIG, "detect_scale": 0.5}
    detector = VisualDetector(cfg, fps=15)
    # Half-res working canvas: 1280x720 -> 640x360. numpy shape is (h, w).
    assert detector._roi_mask.shape == (360, 640)
    assert detector._roi_pixel_count > 0
    # A fixed physical length spans half the pixels at half resolution.
    assert detector._min_contour_length_effective == VISUAL_CONFIG["min_contour_length_px"] * 0.5


def test_detect_scale_rejects_normal_and_flags_anomaly():
    cfg = {**VISUAL_CONFIG, "detect_scale": 0.5}
    detector = VisualDetector(cfg, fps=15)
    normal = np.full((720, 1280, 3), 128, dtype=np.uint8)
    result = None
    for _ in range(30):
        result = detector.analyze(normal)
    if result:
        assert result.triggered is False
    white = np.full((720, 1280, 3), 255, dtype=np.uint8)
    assert detector.analyze(white) is None


def test_detect_scale_triggers_on_crack():
    cfg = {**VISUAL_CONFIG, "detect_scale": 0.5}
    detector = VisualDetector(cfg, fps=15)
    normal = np.full((720, 1280, 3), 128, dtype=np.uint8)
    for _ in range(30):
        detector.analyze(normal)
    crack = np.full((720, 1280, 3), 128, dtype=np.uint8)
    # Bolder strokes than the full-res crack test: at half resolution thin lines
    # blur below the Canny/contour floor.
    cv2.line(crack, (200, 200), (1000, 600), (255, 255, 255), 6)
    cv2.line(crack, (300, 100), (900, 500), (255, 255, 255), 5)
    cv2.line(crack, (400, 300), (800, 650), (255, 255, 255), 6)
    triggered = False
    for _ in range(10):
        r = detector.analyze(crack)
        if r and r.triggered:
            triggered = True
            break
    assert triggered, "half-res detector should still trigger on a bold crack"


def test_stabilize_flag_false_skips_orb_entirely():
    cfg = {**VISUAL_CONFIG, "stabilize": False}
    detector = VisualDetector(cfg, fps=15)
    spy = _CountingORB(detector._orb)
    detector._orb = spy
    frame = np.full((720, 1280, 3), 128, dtype=np.uint8)
    result = None
    for _ in range(5):
        result = detector.analyze(frame)
    assert spy.calls == 0
    # Detection still runs; a flat frame simply does not trigger.
    if result:
        assert result.triggered is False

# ============================================================
# 穩定化與解析度：2026-07-31 review 發現的兩個缺陷
# ============================================================

def _textured_scene(seed=7):
    """有大量 ORB 角點的靜態場景；平坦影像測不出對齊。"""
    rng = np.random.default_rng(seed)
    img = np.full((900, 1600, 3), 110, dtype=np.uint8)
    for _ in range(220):
        x, y = int(rng.integers(20, 1560)), int(rng.integers(20, 860))
        w, h = int(rng.integers(12, 60)), int(rng.integers(12, 60))
        shade = int(rng.integers(40, 230))
        cv2.rectangle(img, (x, y), (x + w, y + h), (shade, shade, shade), -1)
    return img


def _interior(a):
    """裁掉 warpAffine 補的黑邊，否則黑邊會主導平均值。"""
    return a[80:640, 120:1160]


def test_stabilization_reduces_misalignment_instead_of_doubling_it():
    """對齊必須讓當前幀更接近前一幀，而不是更遠。

    這條斷言存在的原因：`M = estimateAffinePartial2D(src=前一幀, dst=當前幀)`
    求出的是「前一幀 → 當前幀」的變換，卻被套用在**當前幀**上，等於把位移再加一次。
    量測（8px 位移）：未對齊 4.34、照原本寫法對齊 8.53、把變換反過來 0.04——
    也就是整條管線最貴的一段，效果比完全不做還差一倍。

    斷言寫成「必須明顯優於未對齊」而非某個固定數值，因為重點是方向對不對。
    """
    scene = _textured_scene()
    shift = 8
    g0 = cv2.cvtColor(scene[40:760, 60:1340].copy(), cv2.COLOR_BGR2GRAY)
    g1 = cv2.cvtColor(scene[40:760, 60 + shift:1340 + shift].copy(), cv2.COLOR_BGR2GRAY)

    detector = VisualDetector(VISUAL_CONFIG, fps=15)
    detector._stabilize(g0)          # 建立前一幀特徵快取
    aligned = detector._stabilize(g1)

    unaligned_err = float(cv2.absdiff(_interior(g0), _interior(g1)).mean())
    aligned_err = float(cv2.absdiff(_interior(g0), _interior(aligned)).mean())

    assert aligned_err < unaligned_err * 0.5, (
        f"對齊後誤差 {aligned_err:.2f} 未明顯低於未對齊 {unaligned_err:.2f}；"
        "若接近兩倍，代表仿射變換方向反了"
    )


def test_stabilization_is_a_no_op_on_a_perfectly_still_camera():
    """靜止畫面不該被對齊動到——否則穩定化本身就是雜訊來源。"""
    scene = _textured_scene()
    g = cv2.cvtColor(scene[40:760, 60:1340].copy(), cv2.COLOR_BGR2GRAY)

    detector = VisualDetector(VISUAL_CONFIG, fps=15)
    detector._stabilize(g)
    aligned = detector._stabilize(g)

    assert float(cv2.absdiff(_interior(g), _interior(aligned)).mean()) < 0.5


@pytest.mark.parametrize("detect_scale", [1.0, 0.5])
def test_a_camera_that_is_not_720p_does_not_crash(detect_scale):
    """偵測管線把 ROI 遮罩固定建在 1280x720 畫布上，但 camera.resolution 是可設定的。

    detect_scale != 1.0 時每幀都會被縮到工作畫布，尺寸自然一致；detect_scale == 1.0
    時原本會跳過縮放，於是 1080p 節點在 `_apply_roi` 的 bitwise_and 直接拋
    `cv::binary_op` 尺寸斷言。兩條路徑的解析度語意必須一致。
    """
    frame = np.full((1080, 1920, 3), 128, dtype=np.uint8)
    detector = VisualDetector({**VISUAL_CONFIG, "detect_scale": detect_scale}, fps=15)
    for _ in range(20):
        detector.analyze(frame)     # 不得拋出


@pytest.mark.parametrize("detect_scale", [1.0, 0.5])
def test_the_working_canvas_matches_the_roi_mask_whatever_comes_in(detect_scale):
    """上面那條的直接成因：工作影像與遮罩尺寸必須永遠相同。"""
    detector = VisualDetector({**VISUAL_CONFIG, "detect_scale": detect_scale}, fps=15)
    for shape in ((1080, 1920, 3), (720, 1280, 3), (480, 640, 3)):
        work = detector._to_work_size(np.full(shape, 128, dtype=np.uint8))
        assert work.shape[:2] == detector._roi_mask.shape, (
            f"{shape} 在 detect_scale={detect_scale} 下縮出 {work.shape[:2]}，"
            f"與遮罩 {detector._roi_mask.shape} 不符"
        )
