"""
音訊偵測器模組

使用 RMS、頻譜分析和 attack time 偵測玻璃碎裂。

資料流：
    USB 麥克風 (44.1kHz, mono, 16-bit)
    → PyAudio 回調 (~512 samples, ~11.6ms)
    → 音訊環形緩衝區 (最近 0.5 秒 = 22050 samples)
    → 分析：[1] RMS → [2] 滾動基線 → [3] FFT → [4] flatness → [5] attack → [6] 判定

使用範例：
    from detectors.audio_detector import AudioDetector, AudioResult

    detector = AudioDetector(config["audio"])

    # 由 PyAudio 回調調用
    detector.process_chunk(samples)

    # 主迴圈調用
    result = detector.analyze()
    if result and result.triggered:
        print(f"Glass break detected! delta_db={result.delta_db}")
"""

import collections
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AudioResult:
    """音訊偵測結果。"""

    triggered: bool
    delta_db: float = 0.0
    flatness: float = 1.0
    is_impulsive: bool = False


class AudioDetector:
    """
    音訊偵測器 — 使用 RMS、頻譜分析和 attack time 偵測玻璃碎裂。
    """

    # 脈衝性判定閾值（rise_rate 閾值）
    RISE_RATE_THRESHOLD = 100.0

    def __init__(self, config: dict):
        """
        Args:
            config: config.yaml 中 "audio" 區塊的字典，包含：
                - mode: "adaptive" | "fixed"
                - sample_rate: int (44100)
                - rolling_baseline_seconds: int (30)
                - delta_db_threshold: float (20)
                - spectral_flatness_threshold: float (0.3)
                - attack_time_ms: float (10)
                - analysis_window_ms: int (500)
                - fixed_db_threshold: float (90)
                - fixed_freq_threshold_hz: float (3000)
        """
        self._config = config
        self._sample_rate = config.get("sample_rate", 44100)
        self._mode = config.get("mode", "adaptive")

        # 分析窗口大小
        analysis_window_ms = config.get("analysis_window_ms", 500)
        self._window_size = int(self._sample_rate * analysis_window_ms / 1000)

        # 音訊環形緩衝區（預分配 numpy array）
        self._audio_buffer = np.zeros(self._window_size, dtype=np.float32)
        self._write_index = 0
        self._buffer_filled = False

        # RMS 滾動基線（最近 30 秒的 RMS dB 值）
        # 分析頻率 ≈ 每 50ms 一次 → 30s / 0.05s = 600 個值
        baseline_count = int(config.get("rolling_baseline_seconds", 30) / 0.05)
        self._baseline_rms_buffer: collections.deque = collections.deque(
            maxlen=baseline_count
        )
        self._baseline_db: Optional[float] = None

        # 常數
        self._reference = 32768.0  # 16-bit PCM 最大值
        self._epsilon = 1e-10

        # 閾值
        self._delta_db_threshold = config.get("delta_db_threshold", 20)
        self._spectral_flatness_threshold = config.get("spectral_flatness_threshold", 0.3)
        self._attack_time_ms = config.get("attack_time_ms", 10)
        self._fixed_db_threshold = config.get("fixed_db_threshold", 90)
        self._fixed_freq_threshold_hz = config.get("fixed_freq_threshold_hz", 3000)

    def process_chunk(self, samples: np.ndarray) -> None:
        """
        接收音訊樣本寫入環形緩衝區。
        由 PyAudio 回調調用（~512 samples per chunk）。

        Args:
            samples: float32 numpy array（正規化後的音訊樣本）
        """
        # 轉換為 float32
        if samples.dtype != np.float32:
            samples = samples.astype(np.float32)

        # 寫入環形緩衝區
        n = len(samples)
        for i in range(n):
            self._audio_buffer[self._write_index] = samples[i]
            self._write_index = (self._write_index + 1) % self._window_size

        # 標記緩衝區已填充
        if not self._buffer_filled and self._write_index >= self._window_size // 2:
            self._buffer_filled = True

    def _get_current_samples(self) -> np.ndarray:
        """
        讀取環形緩衝區的完整樣本（線性化）。

        Returns:
            線性化的音訊樣本陣列
        """
        # 使用 roll 將環形緩衝區展平
        return np.roll(self._audio_buffer, -self._write_index)

    # ============================================================
    # 步驟 [1] RMS 計算
    # ============================================================
    def _compute_rms_db(self, samples: np.ndarray) -> float:
        """
        步驟 [1]：計算 RMS 並轉為 dB。

        Args:
            samples: 音訊樣本陣列

        Returns:
            RMS dB 值
        """
        rms = np.sqrt(np.mean(samples ** 2))
        db = 20 * np.log10(rms / self._reference + self._epsilon)
        return db

    # ============================================================
    # 步驟 [2] 滾動基線更新
    # ============================================================
    def _update_baseline(self, current_db: float, delta_db: float) -> None:
        """
        步驟 [2]：更新滾動基線（中位數）。

        若 delta_db > 30dB，該窗口不計入基線更新。

        Args:
            current_db: 當前 RMS dB 值
            delta_db: 與基線的差值
        """
        # 若 delta_db 遠超閾值，不更新基線（避免爆炸聲拉高基線）
        if delta_db > 30:
            return

        self._baseline_rms_buffer.append(current_db)

        if len(self._baseline_rms_buffer) > 0:
            self._baseline_db = float(np.median(self._baseline_rms_buffer))

    # ============================================================
    # 步驟 [3] FFT 頻譜分析
    # ============================================================
    def _compute_spectrum(self, samples: np.ndarray) -> np.ndarray:
        """
        步驟 [3]：Hanning 窗 + FFT，返回頻譜幅度陣列。

        Args:
            samples: 音訊樣本陣列

        Returns:
            頻譜幅度陣列
        """
        # Hanning 窗避免頻譜洩漏
        windowed = samples * np.hanning(len(samples))

        # 計算 FFT（只需正頻率部分）
        spectrum = np.abs(np.fft.rfft(windowed))

        return spectrum

    def _get_freqs(self, n_samples: int) -> np.ndarray:
        """
        取得頻率軸。

        Args:
            n_samples: 樣本數

        Returns:
            頻率陣列
        """
        return np.fft.rfftfreq(n_samples, 1.0 / self._sample_rate)

    # ============================================================
    # 步驟 [4] 頻譜平坦度
    # ============================================================
    def _compute_spectral_flatness(self, spectrum: np.ndarray) -> float:
        """
        步驟 [4]：計算頻譜平坦度 (0~1)。

        頻譜平坦度 = 幾何平均 / 算術平均
        - 白噪音（均勻頻譜）→ flatness ≈ 1.0
        - 玻璃碎裂（含尖銳頻率成分）→ flatness < 0.3
        - 純音（單一頻率）→ flatness ≈ 0

        Args:
            spectrum: 頻譜幅度陣列

        Returns:
            頻譜平坦度 (0~1)
        """
        log_spectrum = np.log(spectrum + self._epsilon)
        geometric_mean = np.exp(np.mean(log_spectrum))
        arithmetic_mean = np.mean(spectrum + self._epsilon)

        if arithmetic_mean > 0:
            return geometric_mean / arithmetic_mean
        return 1.0

    # ============================================================
    # 步驟 [5] Attack Time
    # ============================================================
    def _compute_attack_time(self, samples: np.ndarray) -> tuple:
        """
        步驟 [5]：計算 attack time 和脈衝性判定。

        將分析窗口分成多個小片段，計算 RMS 上升速率。

        Args:
            samples: 音訊樣本陣列

        Returns:
            (rise_rate: float, is_impulsive: bool)
        """
        attack_samples = int(self._sample_rate * self._attack_time_ms / 1000)
        attack_samples = min(attack_samples, len(samples))

        # 取最後 attack_samples 個樣本
        recent = samples[-attack_samples:]

        # 分成 5 個等分片段
        segment_size = max(1, len(recent) // 5)
        segments_rms = []

        for i in range(5):
            start = i * segment_size
            end = min((i + 1) * segment_size, len(recent))
            if start < end:
                seg = recent[start:end]
                seg_rms = np.sqrt(np.mean(seg ** 2))
                segments_rms.append(seg_rms)

        if len(segments_rms) < 2:
            return 0.0, False

        # 計算上升速率
        peak_rms = max(segments_rms)
        pre_rms = segments_rms[0]  # 第一個片段作為起始
        time_delta = self._attack_time_ms / 1000.0

        if pre_rms > self._epsilon:
            rise_rate = (peak_rms - pre_rms) / (pre_rms * time_delta)
        else:
            rise_rate = 0.0

        # 判定脈衝性
        is_impulsive = rise_rate > self.RISE_RATE_THRESHOLD

        return rise_rate, is_impulsive

    # ============================================================
    # 步驟 [6a] 自適應模式綜合判定
    # ============================================================
    def _evaluate_adaptive(
        self, delta_db: float, flatness: float, is_impulsive: bool
    ) -> bool:
        """
        步驟 [6a]：自適應模式綜合判定。

        Args:
            delta_db: RMS dB 變化量
            flatness: 頻譜平坦度
            is_impulsive: 是否為脈衝性訊號

        Returns:
            是否觸發
        """
        triggered = (
            delta_db > self._delta_db_threshold
            and flatness < self._spectral_flatness_threshold
            and is_impulsive
        )

        if triggered:
            logger.info(
                f"Audio trigger: delta_db={delta_db:.1f}, "
                f"flatness={flatness:.3f}, is_impulsive={is_impulsive}"
            )

        return triggered

    # ============================================================
    # 步驟 [6b] 固定模式綜合判定
    # ============================================================
    def _evaluate_fixed(
        self, current_db: float, spectrum: np.ndarray, freqs: np.ndarray
    ) -> bool:
        """
        步驟 [6b]：固定模式綜合判定。

        Args:
            current_db: 當前 RMS dB 值
            spectrum: 頻譜幅度陣列
            freqs: 頻率陣列

        Returns:
            是否觸發
        """
        if current_db < self._fixed_db_threshold:
            return False

        # 計算峰值頻率
        peak_idx = np.argmax(spectrum)
        peak_freq = freqs[peak_idx] if peak_idx < len(freqs) else 0

        triggered = peak_freq > self._fixed_freq_threshold_hz

        if triggered:
            logger.info(
                f"Audio trigger (fixed): current_db={current_db:.1f}, "
                f"peak_freq={peak_freq:.0f}Hz"
            )

        return triggered

    # ============================================================
    # 主分析方法
    # ============================================================
    def analyze(self) -> Optional[AudioResult]:
        """
        分析環形緩衝區中的音訊。

        Returns:
            AudioResult 或 None（緩衝區未滿）
        """
        if not self._buffer_filled:
            return None

        # 取得當前樣本
        samples = self._get_current_samples()

        # [1] 計算 RMS dB
        current_db = self._compute_rms_db(samples)

        # [2] 計算 delta_db + 更新基線
        if self._baseline_db is not None:
            delta_db = current_db - self._baseline_db
        else:
            delta_db = 0.0

        # 更新基線
        self._update_baseline(current_db, delta_db)

        # [3] 計算 FFT 頻譜
        spectrum = self._compute_spectrum(samples)
        freqs = self._get_freqs(len(samples))

        # [4] 計算頻譜平坦度
        flatness = self._compute_spectral_flatness(spectrum)

        # [5] 計算 attack time
        rise_rate, is_impulsive = self._compute_attack_time(samples)

        # [6] 綜合判定
        triggered = False

        if self._mode == "adaptive":
            triggered = self._evaluate_adaptive(delta_db, flatness, is_impulsive)
        elif self._mode == "fixed":
            triggered = self._evaluate_fixed(current_db, spectrum, freqs)

        return AudioResult(
            triggered=triggered,
            delta_db=delta_db,
            flatness=flatness,
            is_impulsive=is_impulsive,
        )


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)

    # 測試配置
    config = {
        "mode": "adaptive",
        "sample_rate": 44100,
        "rolling_baseline_seconds": 30,
        "delta_db_threshold": 20,
        "spectral_flatness_threshold": 0.3,
        "attack_time_ms": 10,
        "analysis_window_ms": 500,
        "fixed_db_threshold": 90,
        "fixed_freq_threshold_hz": 3000,
    }

    detector = AudioDetector(config)

    # 測試靜音
    print("Testing silence...")
    silence = np.zeros(22050, dtype=np.float32)
    for _ in range(100):
        detector.process_chunk(silence[:512])

    result = detector.analyze()
    if result:
        print(f"Silence: delta_db={result.delta_db:.2f}, flatness={result.flatness:.3f}")

    # 測試白噪音
    print("\nTesting white noise...")
    detector2 = AudioDetector(config)
    for _ in range(100):
        noise = np.random.randn(512).astype(np.float32) * 1000
        detector2.process_chunk(noise)

    result = detector2.analyze()
    if result:
        print(f"White noise: delta_db={result.delta_db:.2f}, flatness={result.flatness:.3f}")

    # 測試脈衝（模擬玻璃碎裂）
    print("\nTesting impulse (simulated glass break)...")
    detector3 = AudioDetector(config)

    # 先 feed 靜音建立基線
    for _ in range(50):
        silence = np.zeros(512, dtype=np.float32)
        detector3.process_chunk(silence)

    # 合成脈衝
    t = np.arange(22050) / 44100.0
    impulse = 30000 * np.sin(2 * np.pi * 5000 * t) * np.exp(-t * 50)
    impulse = impulse.astype(np.float32)

    # Feed 脈衝
    for i in range(0, len(impulse), 512):
        detector3.process_chunk(impulse[i : i + 512])

    result = detector3.analyze()
    if result:
        print(
            f"Impulse: triggered={result.triggered}, "
            f"delta_db={result.delta_db:.2f}, "
            f"flatness={result.flatness:.3f}, "
            f"is_impulsive={result.is_impulsive}"
        )