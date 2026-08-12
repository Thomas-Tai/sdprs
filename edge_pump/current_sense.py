# -*- coding: utf-8 -*-
"""CT front-end: raw ADC samples -> RMS -> coarse band (spec §4.6, §6.1).

The CT output is BIPOLAR and is biased to mid-rail in hardware before it
reaches the ADC, so the DC component in these samples is the bias, not
signal.

NEVER derive a published amp figure from this. An uncalibrated ESP32 ADC is
+-10-20% and non-monotonic near the rails; a payload field reading
`current_a: 3.7` gets believed by whoever sees it. Bands only.

Pure — no hardware, desktop-testable.
"""

_MAINS_HZ = 50


def rms_from_samples(samples):
    """True RMS of a bipolar waveform after removing its measured DC bias.

    The bias is taken from the samples rather than assumed to be exactly
    mid-rail, so tolerance in the bias network does not show up as phantom
    current on an idle socket.
    """
    n = len(samples)
    if n == 0:
        return 0.0
    mean = sum(samples) / float(n)
    acc = 0.0
    for s in samples:
        d = s - mean
        acc += d * d
    return (acc / n) ** 0.5


def build_thresholds(low, normal, high):
    """Validate and package the band edges, in ADC counts."""
    if not (low < normal < high):
        raise ValueError("CT band thresholds must ascend: %r < %r < %r"
                         % (low, normal, high))
    return {"low": low, "normal": normal, "high": high}


def classify_band(rms_counts, thresholds):
    """Map an RMS reading in ADC counts to a coarse band (spec §6.1)."""
    if rms_counts < thresholds["low"]:
        return "none"
    if rms_counts < thresholds["normal"]:
        return "low"
    if rms_counts < thresholds["high"]:
        return "normal"
    return "high"


def sample_count_for_cycles(sample_rate_hz, cycles=3, mains_hz=_MAINS_HZ):
    """Samples needed to cover a WHOLE number of mains cycles.

    Sampling a partial cycle leaves residual fundamental in the mean, which
    biases the RMS. 3 cycles at 50 Hz is 60 ms of blocking — the budget the
    spec flags as unverified against MQTT and the 30s WDT (§9.5).
    """
    return int(round(sample_rate_hz * cycles / float(mains_hz)))
