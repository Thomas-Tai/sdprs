"""
視覺偵測器模組

透過邊緣密度變化偵測玻璃裂縫。

處理管線：
    原始幀 (720p BGR) → [1] 灰度 → [2] 防震對齊 → [3] 異常幀排除
    → [4] 自適應基線 → [5] 差異計算 → [6] ROI 遮罩
    → [7] Canny → [8] 形態學 → [9] 輪廓分析 → [10] 結果

使用範例：
    from detectors.visual_detector import VisualDetector, VisualResult

    detector = VisualDetector(config["visual"], fps=15)
    result = detector.analyze(frame)
    if result and result.triggered:
        print(f"Crack detected! Confidence: {result.confidence}")
"""

import collections
import logging
from dataclasses import dataclass
from typing import Deque, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class VisualResult:
    """視覺偵測結果。"""

    triggered: bool
    confidence: float = 0.0


class VisualDetector:
    """
    視覺偵測器 — 透過邊緣密度變化偵測玻璃裂縫。

    處理管線：灰度 → 防震對齊 → 異常排除 → 基線 → 差異 → ROI →
              Canny → 形態學 → 輪廓 → 結果
    """

    def __init__(self, config: dict, fps: int = 15):
        """
        Args:
            config: config.yaml 中 "visual" 區塊的字典，包含：
                - edge_density_threshold: float (1.5)
                - baseline_window_seconds: int (60)
                - brightness_anomaly_percent: int (50)
                - min_contour_length_px: int (100)
                - roi_polygon: list of [x, y] points
                - canny_threshold1: int (50)
                - canny_threshold2: int (150)
            fps: 每秒幀數（用於計算基線窗口大小）
        """
        self._config = config
        self._fps = fps

        # 從 config 讀取參數
        self._edge_density_threshold = config.get("edge_density_threshold", 1.5)
        self._baseline_window_seconds = config.get("baseline_window_seconds", 60)
        self._brightness_anomaly_percent = config.get("brightness_anomaly_percent", 50)
        self._min_contour_length_px = config.get("min_contour_length_px", 100)
        self._canny_threshold1 = config.get("canny_threshold1", 50)
        self._canny_threshold2 = config.get("canny_threshold2", 150)

        # [2] 防震對齊
        self._prev_gray: Optional[np.ndarray] = None
        self._orb = cv2.ORB_create(nfeatures=500)
        self._bf_matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

        # [3] 異常幀排除
        self._baseline_brightness: Optional[float] = None

        # [4] 自適應基線
        baseline_maxlen = fps * self._baseline_window_seconds
        self._baseline_frames: Deque[np.ndarray] = collections.deque(maxlen=baseline_maxlen)
        self._baseline_image: Optional[np.ndarray] = None
        self._frame_count = 0

        # [6] ROI 遮罩（預生成）
        roi_polygon = config.get(
            "roi_polygon",
            [[100, 50], [1180, 50], [1180, 670], [100, 670]],
        )
        self._roi_mask = self._create_roi_mask(roi_polygon, 1280, 720)
        self._roi_pixel_count = np.count_nonzero(self._roi_mask)

        # [8] 形態學 kernel（預生成）
        self._morph_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

        # 防震對齊日誌節流（避免刷爆日誌）
        self._stabilize_warn_count = 0
        self._stabilize_warn_interval = fps * 30  # 每 30 秒記錄一次

        # [10] 邊緣密度基線
        self._baseline_edge_density: float = 0.0

    def _create_roi_mask(
        self, polygon: List[List[int]], width: int, height: int
    ) -> np.ndarray:
        """
        建立 ROI 遮罩。

        Args:
            polygon: ROI 多邊形頂點列表
            width: 影像寬度
            height: 影像高度

        Returns:
            二值遮罩（255=ROI 內，0=ROI 外）
        """
        mask = np.zeros((height, width), dtype=np.uint8)
        pts = np.array(polygon, dtype=np.int32)
        cv2.fillPoly(mask, [pts], 255)
        return mask

    # ============================================================
    # 步驟 [1] 灰度轉換
    # ============================================================
    def _to_gray(self, frame: np.ndarray) -> np.ndarray:
        """
        步驟 [1]：BGR → GRAY。

        Args:
            frame: BGR 影像

        Returns:
            灰度影像
        """
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # ============================================================
    # 步驟 [2] 防震對齊
    # ============================================================
    def _stabilize(self, gray: np.ndarray) -> np.ndarray:
        """
        步驟 [2]：防震對齊。

        使用 ORB 特徵點匹配進行影像對齊。

        Args:
            gray: 當前灰度影像

        Returns:
            對齊後的灰度影像
        """
        if self._prev_gray is None:
            return gray

        try:
            # 偵測特徵點
            kp1, des1 = self._orb.detectAndCompute(self._prev_gray, None)
            kp2, des2 = self._orb.detectAndCompute(gray, None)

            if des1 is None or des2 is None or len(des1) < 10 or len(des2) < 10:
                self._stabilize_warn_count += 1
                if self._stabilize_warn_count == 1 or self._stabilize_warn_count % self._stabilize_warn_interval == 0:
                    logger.info("Stabilization skipped: not enough feature points (count=%d)", self._stabilize_warn_count)
                return gray

            # 匹配特徵點
            matches = self._bf_matcher.match(des1, des2)

            if len(matches) < 10:
                self._stabilize_warn_count += 1
                if self._stabilize_warn_count == 1 or self._stabilize_warn_count % self._stabilize_warn_interval == 0:
                    logger.info("Stabilization skipped: not enough matches (count=%d)", self._stabilize_warn_count)
                return gray

            # 取得匹配點座標
            src_pts = np.float32([kp1[m.queryIdx].pt for m in matches]).reshape(
                -1, 1, 2
            )
            dst_pts = np.float32([kp2[m.trainIdx].pt for m in matches]).reshape(
                -1, 1, 2
            )

            # 計算仿射變換
            M, _ = cv2.estimateAffinePartial2D(src_pts, dst_pts)

            if M is None:
                return gray

            # 對齊
            h, w = gray.shape
            aligned = cv2.warpAffine(gray, M, (w, h))
            return aligned

        except Exception as e:
            logger.warning(f"Stabilization failed: {e}")
            return gray

    # ============================================================
    # 步驟 [3] 異常幀排除
    # ============================================================
    def _check_anomaly(self, gray: np.ndarray) -> bool:
        """
        步驟 [3]：異常幀排除。

        檢查亮度是否異常（如閃電、全黑）。

        Args:
            gray: 灰度影像

        Returns:
            True 表示正常幀，False 表示異常幀
        """
        mean_brightness = np.mean(gray)

        if self._baseline_brightness is None:
            # 第一幀，設定初始基線
            self._baseline_brightness = mean_brightness
            return True

        # 計算亮度變化百分比
        threshold = self._baseline_brightness * (self._brightness_anomaly_percent / 100.0)
        diff = abs(mean_brightness - self._baseline_brightness)

        if diff > threshold:
            logger.debug(
                f"Anomaly frame detected: brightness={mean_brightness:.1f}, "
                f"baseline={self._baseline_brightness:.1f}, diff={diff:.1f}"
            )
            return False

        # 更新亮度基線（滾動平均）
        alpha = 0.05
        self._baseline_brightness = (
            1 - alpha
        ) * self._baseline_brightness + alpha * mean_brightness

        return True

    # ============================================================
    # 步驟 [4] 自適應基線更新
    # ============================================================
    def _update_baseline(self, gray: np.ndarray) -> None:
        """
        步驟 [4]：將正常幀加入基線 deque，每 N 幀更新基線影像。

        Args:
            gray: 正常幀的灰度影像
        """
        self._baseline_frames.append(gray)
        self._frame_count += 1

        # 每 fps 幀（每秒）更新一次基線影像
        if self._frame_count % self._fps == 0 and len(self._baseline_frames) > 0:
            # 等間隔抽樣
            sample_step = max(1, len(self._baseline_frames) // 30)
            sampled = list(self._baseline_frames)[::sample_step]

            if sampled:
                self._baseline_image = np.mean(sampled, axis=0).astype(np.uint8)

    # ============================================================
    # 步驟 [5] 差異計算
    # ============================================================
    def _compute_diff(self, gray: np.ndarray) -> Optional[np.ndarray]:
        """
        步驟 [5]：計算灰度幀與基線的差異。

        Args:
            gray: 當前灰度影像

        Returns:
            差異影像，或 None（基線尚未建立）
        """
        if self._baseline_image is None:
            return None

        return cv2.absdiff(gray, self._baseline_image)

    # ============================================================
    # 步驟 [6] ROI 遮罩
    # ============================================================
    def _apply_roi(self, diff: np.ndarray) -> np.ndarray:
        """
        步驟 [6]：套用 ROI 遮罩。

        Args:
            diff: 差異影像

        Returns:
            遮罩後的差異影像
        """
        return cv2.bitwise_and(diff, diff, mask=self._roi_mask)

    # ============================================================
    # 步驟 [7]+[8] Canny 邊緣偵測 + 形態學閉運算
    # ============================================================
    def _detect_edges(self, diff_masked: np.ndarray) -> np.ndarray:
        """
        步驟 [7]+[8]：Canny 邊緣偵測 + 形態學閉運算。

        Args:
            diff_masked: 遮罩後的差異影像

        Returns:
            形態學處理後的邊緣影像
        """
        # [7] Canny 邊緣偵測
        edges = cv2.Canny(
            diff_masked,
            threshold1=self._canny_threshold1,
            threshold2=self._canny_threshold2,
        )

        # [8] 形態學閉運算
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, self._morph_kernel)

        return closed

    # ============================================================
    # 步驟 [9]+[10] 輪廓分析 + 置信度計算
    # ============================================================
    def _analyze_contours(self, closed: np.ndarray) -> Tuple[bool, float]:
        """
        步驟 [9]+[10]：輪廓分析 + 置信度計算。

        Args:
            closed: 形態學處理後的邊緣影像

        Returns:
            (triggered, confidence) 元組
        """
        # [9] 輪廓分析
        contours, _ = cv2.findContours(
            closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        significant_contours = []
        for contour in contours:
            length = cv2.arcLength(contour, closed=False)
            if length > self._min_contour_length_px:
                significant_contours.append(contour)

        # [10] 置信度計算
        # 計算當前邊緣密度
        current_edge_pixels = np.count_nonzero(closed)
        if self._roi_pixel_count > 0:
            current_edge_density = current_edge_pixels / self._roi_pixel_count
        else:
            current_edge_density = 0.0

        # 計算置信度
        confidence = 0.0
        triggered = False

        if current_edge_density > 0 and self._baseline_edge_density > 0:
            confidence = (current_edge_density / self._baseline_edge_density) - 1.0

            # 觸發判定
            if (
                confidence > self._edge_density_threshold
                and len(significant_contours) > 0
            ):
                triggered = True

        # 更新邊緣密度基線（正常幀時）
        if not triggered and current_edge_density > 0:
            alpha = 0.01
            self._baseline_edge_density = (
                1 - alpha
            ) * self._baseline_edge_density + alpha * current_edge_density

        return triggered, confidence

    # ============================================================
    # 主分析方法
    # ============================================================
    def analyze(self, frame: np.ndarray) -> Optional[VisualResult]:
        """
        分析一幀影像。

        完整執行 10 步處理管線。

        Args:
            frame: BGR numpy array (720p)

        Returns:
            VisualResult 或 None（異常幀）
        """
        # [1] 灰度轉換
        gray = self._to_gray(frame)

        # [2] 防震對齊
        aligned = self._stabilize(gray)

        # [3] 異常幀排除
        if not self._check_anomaly(aligned):
            return None

        # [4] 更新基線
        self._update_baseline(aligned)

        # [5] 計算差異
        diff = self._compute_diff(aligned)
        if diff is None:
            # 基線尚未建立
            self._prev_gray = aligned
            return VisualResult(triggered=False)

        # [6] 套用 ROI
        diff_masked = self._apply_roi(diff)

        # [7] Canny 邊緣偵測
        # [8] 形態學閉運算
        closed = self._detect_edges(diff_masked)

        # [9] 輪廓分析
        # [10] 置信度判定
        triggered, confidence = self._analyze_contours(closed)

        # 更新前一幀
        self._prev_gray = aligned

        return VisualResult(triggered=triggered, confidence=confidence)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)

    # 測試配置
    config = {
        "edge_density_threshold": 1.5,
        "baseline_window_seconds": 60,
        "brightness_anomaly_percent": 50,
        "min_contour_length_px": 100,
        "roi_polygon": [[100, 50], [1180, 50], [1180, 670], [100, 670]],
        "canny_threshold1": 50,
        "canny_threshold2": 150,
    }

    detector = VisualDetector(config, fps=15)

    # 測試正常幀
    normal_frame = np.full((720, 1280, 3), 128, dtype=np.uint8)

    print("Testing normal frames...")
    for i in range(30):
        result = detector.analyze(normal_frame)
        if result:
            print(f"Frame {i}: triggered={result.triggered}, confidence={result.confidence:.4f}")

    # 測試異常幀（全白）
    print("\nTesting anomaly frame (white)...")
    white_frame = np.full((720, 1280, 3), 255, dtype=np.uint8)
    result = detector.analyze(white_frame)
    print(f"White frame result: {result}")

    # 測試含裂縫的幀
    print("\nTesting crack frame...")
    crack_frame = np.full((720, 1280, 3), 128, dtype=np.uint8)
    cv2.line(crack_frame, (200, 200), (1000, 600), (255, 255, 255), 3)
    cv2.line(crack_frame, (300, 100), (900, 500), (255, 255, 255), 2)

    result = detector.analyze(crack_frame)
    if result:
        print(f"Crack frame: triggered={result.triggered}, confidence={result.confidence:.4f}")