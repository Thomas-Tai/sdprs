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

        # 偵測降採樣比例（visual.detect_scale）：偵測管線在原尺寸的 detect_scale 倍
        # 工作影像上執行，畫素量約 detect_scale² 倍。錄影緩衝／快照仍用原尺寸幀，
        # 證據與儀表板畫面不受影響。夾在 (0, 1]。
        self._detect_scale = min(1.0, max(0.01, float(config.get("detect_scale", 1.0))))
        # 固定物理長度在低解析度下佔用較少畫素，故有效輪廓門檻按 detect_scale 縮放。
        self._min_contour_length_effective = self._min_contour_length_px * self._detect_scale

        # [2] 防震對齊
        self._orb = cv2.ORB_create(nfeatures=500)
        self._bf_matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        # 前一幀的 ORB 特徵快取：每幀只算一次特徵，重用上一幀已算好的描述子（原本
        # 每幀對 prev 與 current 各算一次，等於重算了上一幀）。以 _prev_des 是否為
        # None 作為「是否有前一幀」的哨兵，取代原本的 _prev_gray。
        self._prev_kp = None
        self._prev_des: Optional[np.ndarray] = None
        # 防震對齊開關（visual.stabilize）：預設開啟（維持現行行為）。剛性固定的攝像頭
        # 可關閉，省下整段 ORB＋配對＋warpAffine（管線最貴的部分）。
        self._stabilize_enabled = bool(config.get("stabilize", True))

        # [3] 異常幀排除
        self._baseline_brightness: Optional[float] = None

        # [3] 持續性異常復原（避免偵測器被日夜/燈光/移位永久致盲）
        self._anomaly_recovery_seconds = config.get("anomaly_recovery_seconds", 3)
        self._anomaly_recovery_frames = max(1, int(fps * self._anomaly_recovery_seconds))
        self._consecutive_anomaly_count = 0
        # 公開旗標：持續性異常導致視覺偵測實質失效時為 True
        self.blinded = False

        # [4] 自適應基線
        baseline_maxlen = fps * self._baseline_window_seconds
        self._baseline_frames: Deque[np.ndarray] = collections.deque(maxlen=baseline_maxlen)
        self._baseline_image: Optional[np.ndarray] = None
        self._frame_count = 0

        # [6] ROI 遮罩（預生成）
        # 偵測基準畫布固定為 1280x720（既有假設）。降採樣時，遮罩與工作影像一律縮到
        # 此畫布的 detect_scale 倍，兩者尺寸永遠一致；ROI 多邊形亦以 detect_scale 縮放。
        roi_polygon = config.get(
            "roi_polygon",
            [[100, 50], [1180, 50], [1180, 670], [100, 670]],
        )
        _BASE_W, _BASE_H = 1280, 720
        self._work_w = max(1, int(round(_BASE_W * self._detect_scale)))
        self._work_h = max(1, int(round(_BASE_H * self._detect_scale)))
        scaled_polygon = [
            [int(round(x * self._detect_scale)), int(round(y * self._detect_scale))]
            for x, y in roi_polygon
        ]
        self._roi_mask = self._create_roi_mask(scaled_polygon, self._work_w, self._work_h)
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
    def _to_work_size(self, frame: np.ndarray) -> np.ndarray:
        """把任何進來的幀縮到偵測工作畫布，讓它與預生成的 ROI 遮罩尺寸永遠一致。

        無條件執行，包含 detect_scale == 1.0。原本 1.0 會跳過縮放，於是兩條路徑的
        解析度語意不同：ROI 遮罩固定建在 1280x720 畫布上，但 camera.resolution 是
        可設定的，所以 1080p 節點在 detect_scale=1.0 下會直接在 `_apply_roi` 的
        bitwise_and 拋出 `cv::binary_op` 尺寸斷言，而 detect_scale=0.5 卻正常。
        「不降採樣」不該順帶改變支援的攝像頭解析度。

        已經是目標尺寸就原樣返回，避免對 720p 節點多做一次無意義的 resize。
        """
        if frame.shape[1] == self._work_w and frame.shape[0] == self._work_h:
            return frame
        return cv2.resize(
            frame, (self._work_w, self._work_h), interpolation=cv2.INTER_AREA
        )

    def _stabilize(self, gray: np.ndarray) -> np.ndarray:
        """
        步驟 [2]：防震對齊。

        只對「當前幀」計算一次 ORB 特徵，與上一幀「已快取」的特徵配對；對齊基準是
        上一幀的原始灰度（raw），非上一幀對齊後的輸出（這才使快取成立）。

        Args:
            gray: 當前灰度影像

        Returns:
            對齊後的灰度影像
        """
        try:
            # 只算一次：當前幀特徵
            kp2, des2 = self._orb.detectAndCompute(gray, None)
            prev_kp, prev_des = self._prev_kp, self._prev_des
            # 先讀舊值、後存新值：快取當前幀特徵供下一幀使用。
            self._prev_kp, self._prev_des = kp2, des2

            if (
                prev_des is None
                or des2 is None
                or len(prev_des) < 10
                or len(des2) < 10
            ):
                self._stabilize_warn_count += 1
                if self._stabilize_warn_count == 1 or self._stabilize_warn_count % self._stabilize_warn_interval == 0:
                    logger.info("Stabilization skipped: not enough feature points (count=%d)", self._stabilize_warn_count)
                return gray

            # 匹配特徵點（上一幀快取 vs 當前幀）
            matches = self._bf_matcher.match(prev_des, des2)

            if len(matches) < 10:
                self._stabilize_warn_count += 1
                if self._stabilize_warn_count == 1 or self._stabilize_warn_count % self._stabilize_warn_interval == 0:
                    logger.info("Stabilization skipped: not enough matches (count=%d)", self._stabilize_warn_count)
                return gray

            # 取得匹配點座標
            src_pts = np.float32([prev_kp[m.queryIdx].pt for m in matches]).reshape(
                -1, 1, 2
            )
            dst_pts = np.float32([kp2[m.trainIdx].pt for m in matches]).reshape(
                -1, 1, 2
            )

            # 計算仿射變換：dst→src，也就是「當前幀 → 前一幀」。
            #
            # 這裡原本寫成 (src_pts, dst_pts)，求出的是「前一幀 → 當前幀」，卻套用在
            # **當前幀**上，等於把位移再往前加一次。實測 8px 位移下的平均絕對差：
            # 未對齊 4.34、原本寫法 8.53、方向反過來 0.04——整條管線最貴的一段，
            # 效果比完全不做還差一倍，而且從 2026-07-26 起就是這樣。
            #
            # src_pts 取自前一幀、dst_pts 取自當前幀（見上方 queryIdx/trainIdx），
            # 所以要把當前幀搬回前一幀的座標系，參數順序必須是 (dst, src)。
            M, _ = cv2.estimateAffinePartial2D(dst_pts, src_pts)

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
            # 異常幀：累計連續異常次數
            self._consecutive_anomaly_count += 1

            if self._consecutive_anomaly_count > self._anomaly_recovery_frames:
                # 持續性異常（日夜轉換、燈光切換、相機重新定位）：
                # 重新建立基線，避免視覺偵測被永久致盲、無法復原
                self._baseline_brightness = mean_brightness
                self._consecutive_anomaly_count = 0
                # 清空視覺基線，讓其重新乾淨建立
                self._baseline_frames.clear()
                self._baseline_image = None
                self._baseline_edge_density = 0.0
                self._frame_count = 0
                self.blinded = False
                logger.warning(
                    "Visual re-baselined after sustained anomaly (%d frames)",
                    self._anomaly_recovery_frames,
                )
                # 接受此幀作為新的正常基準
                return True

            # 尚未達到重建門檻：視覺偵測暫時致盲
            logger.debug(
                f"Anomaly frame detected: brightness={mean_brightness:.1f}, "
                f"baseline={self._baseline_brightness:.1f}, diff={diff:.1f}"
            )
            self.blinded = True
            return False

        # 正常幀：重置連續異常計數與致盲旗標
        self._consecutive_anomaly_count = 0
        self.blinded = False

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
            if length > self._min_contour_length_effective:
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
        # 偵測降採樣：只縮偵測用的工作副本（重新綁定區域變數 frame）；呼叫端傳入的
        # 原始 frame（供緩衝／快照）不受影響。
        frame = self._to_work_size(frame)

        # [1] 灰度轉換
        gray = self._to_gray(frame)

        # [3] 異常幀排除（廉價閘門先跑）：異常幀（夜間/閃電/切燈）直接返回，不用付出
        #     ORB 成本。平均亮度對仿射對齊近似不變，故在對齊前檢查與原行為等價。
        if not self._check_anomaly(gray):
            return None

        # [2] 防震對齊（可由 visual.stabilize 關閉）
        aligned = self._stabilize(gray) if self._stabilize_enabled else gray

        # [4] 更新基線
        self._update_baseline(aligned)

        # [5] 計算差異
        diff = self._compute_diff(aligned)
        if diff is None:
            # 基線尚未建立
            return VisualResult(triggered=False)

        # [6] 套用 ROI
        diff_masked = self._apply_roi(diff)

        # [7] Canny 邊緣偵測
        # [8] 形態學閉運算
        closed = self._detect_edges(diff_masked)

        # [9] 輪廓分析
        # [10] 置信度判定
        triggered, confidence = self._analyze_contours(closed)

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