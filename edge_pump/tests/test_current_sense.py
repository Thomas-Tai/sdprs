import math

import current_sense as cs


def sine(n, amplitude, offset=2048.0, cycles=3):
    return [offset + amplitude * math.sin(2 * math.pi * cycles * i / n)
            for i in range(n)]


def test_rms_of_silence_is_zero():
    assert cs.rms_from_samples([2048] * 60) == 0.0


def test_rms_of_an_empty_buffer_is_zero():
    # A failed ADC read must read as "no current", not crash the loop.
    assert cs.rms_from_samples([]) == 0.0


def test_rms_of_a_sine_is_amplitude_over_root_two():
    samples = sine(600, 1000.0)
    assert abs(cs.rms_from_samples(samples) - 1000.0 / math.sqrt(2)) < 15.0


def test_rms_ignores_the_bias_level():
    # The bias network has component tolerance; a shifted mid-rail must not
    # read as phantom current.
    a = cs.rms_from_samples(sine(600, 800.0, offset=2048.0))
    b = cs.rms_from_samples(sine(600, 800.0, offset=1900.0))
    assert abs(a - b) < 1.0


def test_rms_scales_with_amplitude():
    small = cs.rms_from_samples(sine(600, 200.0))
    large = cs.rms_from_samples(sine(600, 800.0))
    assert large > small * 3.5


def test_band_boundaries():
    t = cs.build_thresholds(40, 120, 900)
    assert cs.classify_band(0, t) == "none"
    assert cs.classify_band(39, t) == "none"
    assert cs.classify_band(40, t) == "low"
    assert cs.classify_band(119, t) == "low"
    assert cs.classify_band(120, t) == "normal"
    assert cs.classify_band(899, t) == "normal"
    assert cs.classify_band(900, t) == "high"
    assert cs.classify_band(4095, t) == "high"


def test_build_thresholds_rejects_non_ascending_values():
    import pytest
    with pytest.raises(ValueError):
        cs.build_thresholds(120, 40, 900)


def test_sample_count_covers_whole_cycles():
    # 1000 Hz, 3 cycles at 50 Hz -> 60 samples == 60 ms.
    assert cs.sample_count_for_cycles(1000, cycles=3, mains_hz=50) == 60
    assert cs.sample_count_for_cycles(1000, cycles=1, mains_hz=50) == 20
