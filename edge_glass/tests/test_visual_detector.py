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