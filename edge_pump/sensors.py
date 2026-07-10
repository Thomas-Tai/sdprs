# -*- coding: utf-8 -*-
"""Sensor HAL + debounced readings. The debounce/conversion logic is pure
and desktop-testable; real hardware wiring is behind `build_readers()`
which imports `machine` lazily (never at module top level)."""

_ADC_SAMPLES = 3
_ADC_SAMPLE_GAP_MS = 10


def analog_to_level(raw_samples):
    """Median of raw ADC samples -> inverted level percent, clamped 0..100."""
    s = sorted(raw_samples)
    median = s[len(s) // 2]
    level = 100.0 - (median / 4095.0) * 100.0
    if level < 0.0:
        level = 0.0
    elif level > 100.0:
        level = 100.0
    return level


class DigitalSensor:
    """Debounced digital input. `read_raw()` returns 0/1. Logical assertion is
    raw==0 for active-low, raw==1 otherwise. State flips only after the new
    logical value has been stable for `debounce_ms`."""

    def __init__(self, read_raw, active_low, clock, debounce_ms):
        self._read_raw = read_raw
        self._active_low = active_low
        self._clock = clock
        self._debounce_ms = debounce_ms
        self._stable = None       # last committed logical state
        self._candidate = None
        self._since = None

    def _logical(self, raw):
        return (raw == 0) if self._active_low else (raw == 1)

    def update(self):
        now = self._clock.ticks_ms()
        val = self._logical(self._read_raw())
        if self._stable is None:
            self._stable = val
            self._candidate = val
            self._since = now
            return self._stable
        if val != self._candidate:
            self._candidate = val
            self._since = now
        elif val != self._stable and self._clock.ticks_diff(now, self._since) >= self._debounce_ms:
            self._stable = val
        return self._stable


class SensorSet:
    """Assembles the `readings` dict decide() consumes."""

    def __init__(self, config, readers, clock):
        self._config = config
        self._readers = readers
        self._clock = clock
        c = config
        self._float = DigitalSensor(readers["float"], c["float_active_low"], clock, c["debounce_ms"]) \
            if c["float_enabled"] else None
        self._rain = DigitalSensor(readers["rain"], c["rain_active_low"], clock, c["debounce_ms"]) \
            if c["rain_enabled"] else None
        self._high = DigitalSensor(readers["high_water"], c["high_water_active_low"], clock, c["debounce_ms"]) \
            if c["high_water_enabled"] else None

    def _read_level(self):
        if not self._config["level_enabled"]:
            return None
        adc = self._readers["adc"]
        return analog_to_level([adc() for _ in range(_ADC_SAMPLES)])

    def read_all(self):
        return {
            "level_pct": self._read_level(),
            "float_dry": self._float.update() if self._float else None,
            "raining": self._rain.update() if self._rain else None,
            "high_water": self._high.update() if self._high else None,
        }


def build_readers(config):
    """MicroPython-only: wire zero-arg readers to real GPIO/ADC.
    Imports `machine` lazily so this module stays desktop-importable."""
    import machine
    readers = {}
    adc = machine.ADC(machine.Pin(config["adc_pin"]))
    adc.atten(machine.ADC.ATTN_11DB)
    adc.width(machine.ADC.WIDTH_12BIT)
    readers["adc"] = adc.read
    for key, pin_key in (("float", "float_pin"), ("rain", "rain_pin"),
                         ("high_water", "high_water_pin")):
        pin = machine.Pin(config[pin_key], machine.Pin.IN, machine.Pin.PULL_UP)
        readers[key] = pin.value
    return readers
