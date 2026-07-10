"""Desktop test doubles for the ESP32 hardware/clock. No `machine`/`time` deps."""


class FakeClock:
    """Deterministic monotonic clock in milliseconds."""

    def __init__(self, start_ms=0):
        self._t = start_ms

    def ticks_ms(self):
        return self._t

    def ticks_diff(self, a, b):
        # Desktop: plain subtraction. On-device the real clock uses
        # time.ticks_diff for wrap-around safety.
        return a - b

    def advance(self, ms):
        self._t += ms
        return self._t


class FakePin:
    """Mimics machine.Pin's value(v=None) get/set contract."""

    def __init__(self, value=1):
        self._v = value

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = v


def make_reader(seq_or_value):
    """Return a zero-arg callable. If given a list, yields successive items
    and repeats the last; if given a scalar, always returns it."""
    if isinstance(seq_or_value, (list, tuple)):
        box = {"i": 0, "seq": list(seq_or_value)}

        def rd():
            i = box["i"]
            seq = box["seq"]
            val = seq[i] if i < len(seq) else seq[-1]
            if i < len(seq) - 1:
                box["i"] = i + 1
            return val

        return rd
    return lambda: seq_or_value
