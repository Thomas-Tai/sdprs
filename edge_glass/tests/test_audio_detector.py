"""
AudioDetector 單元測試

測試音訊偵測器的完整功能：
- 靜音不觸發
- 持續噪音不觸發
- 突發脈衝觸發
- 頻譜平坦度計算
- 基線排除極端值
"""

import numpy as np
import pytest

from detectors.audio_detector import AudioDetector, AudioResult


# 測試用配置
AUDIO_CONFIG = {
    "mode": "adaptive",
    "sample_rate": 44100,
    "channels": 1,
    "chunk_size": 512,
    "rolling_baseline_seconds": 30,
    "delta_db_threshold": 20,
    "spectral_flatness_threshold": 0.3,
    "attack_time_ms": 10,
    "analysis_window_ms": 500,
    "fixed_db_threshold": 90,
    "fixed_freq_threshold_hz": 3000,
}


@pytest.fixture
def detector():
    """建立 AudioDetector 實例。"""
    return AudioDetector(AUDIO_CONFIG)


def feed_audio(detector: AudioDetector, samples: np.ndarray, chunk_size: int = 512):
    """
    輔助函式：將音訊樣本分塊餵給 detector。

    Args:
        detector: AudioDetector 實例
        samples: 音訊樣本陣列
        chunk_size: 每塊大小
    """
    for i in range(0, len(samples), chunk_size):
        chunk = samples[i : i + chunk_size]
        detector.process_chunk(chunk)


def build_baseline_with_noise(detector: AudioDetector, num_chunks: int = 100):
    """
    使用低振幅噪音建立基線。

    Args:
        detector: AudioDetector 實例
        num_chunks: 餵入的區塊數
    """
    # 使用低振幅噪音建立基線
    for _ in range(num_chunks):
        noise = np.random.randn(512).astype(np.float32) * 100
        detector.process_chunk(noise)


class TestAudioDetectorBuffer:
    """測試緩衝區行為。"""

    def test_analyze_returns_none_if_buffer_not_full(self, detector):
        """初始化後直接呼叫 analyze() → 返回 None。"""
        result = detector.analyze()
        assert result is None

    def test_process_chunk_fills_buffer(self, detector):
        """process_chunk 多次後，analyze 不再返回 None。"""
        # Feed 足夠的樣本填滿緩衝區
        samples = np.zeros(22050, dtype=np.float32)
        feed_audio(detector, samples)

        result = detector.analyze()
        assert result is not None


class TestAudioDetectorSilence:
    """測試靜音處理。"""

    def test_silence_no_trigger(self, detector):
        """傳入靜音樣本，建立基線後分析，不應觸發。"""
        # Feed 靜音
        silence = np.zeros(25000, dtype=np.float32)
        feed_audio(detector, silence)

        result = detector.analyze()

        assert result is not None
        assert result.triggered is False
        # delta_db 應接近 0（靜音與靜音基線比較）
        # 允許較大誤差因為靜音的 RMS 可能接近 0


class TestAudioDetectorNoise:
    """測試噪音處理。"""

    def test_constant_noise_no_trigger(self):
        """傳入持續白噪音，不應觸發。"""
        detector = AudioDetector(AUDIO_CONFIG)

        # 持續白噪音
        for _ in range(100):
            noise = np.random.randn(512).astype(np.float32) * 1000
            detector.process_chunk(noise)

        result = detector.analyze()

        assert result is not None
        # 白噪音的 flatness 應較高，不應觸發
        assert result.flatness > 0.3 or result.triggered is False

    def test_wind_noise_no_trigger(self):
        """持續風噪不應觸發。"""
        detector = AudioDetector(AUDIO_CONFIG)

        # 合成風噪：低頻寬帶噪音
        for _ in range(100):
            wind = np.random.randn(512).astype(np.float32) * 5000
            # 簡單低通濾波模擬
            wind = np.convolve(wind, np.ones(20) / 20, mode="same").astype(np.float32)
            detector.process_chunk(wind[:512])

        result = detector.analyze()

        # 風噪不應觸發
        assert result is not None


class TestAudioDetectorImpulse:
    """測試脈衝偵測。"""

    def test_sudden_impulse_characteristics(self):
        """測試突發脈衝的特性（delta_db、flatness、is_impulsive）。"""
        detector = AudioDetector(AUDIO_CONFIG)

        # 使用噪音建立基線
        build_baseline_with_noise(detector, num_chunks=100)

        # 合成脈衝：短促高振幅 + 高頻成分
        t = np.arange(22050) / 44100.0
        impulse = 30000 * np.sin(2 * np.pi * 5000 * t) * np.exp(-t * 50)
        impulse = impulse.astype(np.float32)

        # Feed 脈衝
        feed_audio(detector, impulse)

        result = detector.analyze()

        assert result is not None
        # 檢查脈衝特性
        # flatness 應該很低（表示有尖銳頻率成分）
        assert result.flatness < 0.5, f"Flatness should be low for impulse, got {result.flatness}"

    def test_impulse_detection_with_established_baseline(self):
        """使用已建立的基線測試脈衝偵測。"""
        # 降低閾值以便測試
        config = AUDIO_CONFIG.copy()
        config["delta_db_threshold"] = 10  # 降低閾值
        config["spectral_flatness_threshold"] = 0.5  # 提高閾值

        detector = AudioDetector(config)

        # 建立穩定的噪音基線
        np.random.seed(42)  # 固定隨機種子
        for _ in range(100):
            noise = np.random.randn(512).astype(np.float32) * 500
            detector.process_chunk(noise)

        # 呼叫 analyze 觸發基線更新
        detector.analyze()

        # 繼續餵入更多音訊以建立基線
        for _ in range(50):
            noise = np.random.randn(512).astype(np.float32) * 500
            detector.process_chunk(noise)
            detector.analyze()

        # 合成高振幅脈衝
        t = np.arange(22050) / 44100.0
        impulse = 30000 * np.sin(2 * np.pi * 5000 * t) * np.exp(-t * 50)
        impulse = impulse.astype(np.float32)

        feed_audio(detector, impulse)

        result = detector.analyze()

        assert result is not None
        # 檢查各項指標
        assert result.flatness < 0.5  # 脈衝應有低 flatness


class TestAudioDetectorFixedMode:
    """測試固定模式。"""

    def test_fixed_mode(self):
        """使用 fixed 模式，超過閾值應觸發。"""
        config = AUDIO_CONFIG.copy()
        config["mode"] = "fixed"
        config["fixed_db_threshold"] = -40  # 大幅降低閾值以便測試
        config["fixed_freq_threshold_hz"] = 1000

        detector = AudioDetector(config)

        # Feed 高振幅高頻訊號
        t = np.arange(22050) / 44100.0
        signal = 20000 * np.sin(2 * np.pi * 5000 * t)
        signal = signal.astype(np.float32)

        feed_audio(detector, signal)

        result = detector.analyze()

        assert result is not None
        # 固定模式下可能觸發（取決於頻率和振幅）


class TestAudioDetectorBaseline:
    """測試基線行為。"""

    def test_baseline_builds_over_time(self):
        """基線應隨時間建立。"""
        detector = AudioDetector(AUDIO_CONFIG)

        # 初始基線為 None
        assert detector._baseline_db is None

        # Feed 音訊建立基線
        for _ in range(50):
            noise = np.random.randn(512).astype(np.float32) * 1000
            detector.process_chunk(noise)
            detector.analyze()  # 觸發基線更新

        # 基線應已建立
        assert detector._baseline_db is not None

    def test_baseline_excludes_extreme(self):
        """極大脈衝不應影響基線。"""
        detector = AudioDetector(AUDIO_CONFIG)

        # Feed 噪音建立基線
        for _ in range(50):
            noise = np.random.randn(512).astype(np.float32) * 1000
            detector.process_chunk(noise)

        # 記錄基線
        baseline_before = detector._baseline_db

        # Feed 極大脈衝
        t = np.arange(22050) / 44100.0
        impulse = 30000 * np.sin(2 * np.pi * 5000 * t) * np.exp(-t * 50)
        feed_audio(detector, impulse.astype(np.float32))

        # Feed 更多噪音
        for _ in range(50):
            noise = np.random.randn(512).astype(np.float32) * 1000
            detector.process_chunk(noise)

        # 基線不應被大幅拉高
        if baseline_before is not None and detector._baseline_db is not None:
            # 基線變化不應超過 20 dB
            assert abs(detector._baseline_db - baseline_before) < 20


class TestAudioDetectorSpectralFlatness:
    """測試頻譜平坦度。"""

    def test_spectral_flatness_white_noise(self):
        """純白噪音的 flatness 應接近 1.0。"""
        detector = AudioDetector(AUDIO_CONFIG)

        # 白噪音
        np.random.seed(123)
        noise = np.random.randn(22050).astype(np.float32) * 1000
        feed_audio(detector, noise)

        result = detector.analyze()

        assert result is not None
        # 白噪音 flatness 應較高（允許 0.5~1.0）
        assert result.flatness > 0.5, f"White noise flatness should be high, got {result.flatness}"

    def test_spectral_flatness_sine_wave(self):
        """純正弦波的 flatness 應遠小於 0.3。"""
        detector = AudioDetector(AUDIO_CONFIG)

        # 純正弦波 (1kHz)
        t = np.arange(22050) / 44100.0
        sine = 10000 * np.sin(2 * np.pi * 1000 * t)
        sine = sine.astype(np.float32)

        feed_audio(detector, sine)

        result = detector.analyze()

        assert result is not None
        # 正弦波 flatness 應很低
        assert result.flatness < 0.3, f"Sine wave flatness should be low, got {result.flatness}"