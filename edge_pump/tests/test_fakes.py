from tests.fakes import FakeClock, FakePin, make_reader


def test_clock_advances_and_diffs():
    c = FakeClock(1000)
    assert c.ticks_ms() == 1000
    c.advance(250)
    assert c.ticks_ms() == 1250
    assert c.ticks_diff(c.ticks_ms(), 1000) == 250


def test_pin_get_set():
    p = FakePin(0)
    assert p.value() == 0
    p.value(1)
    assert p.value() == 1


def test_reader_sequence_repeats_last():
    rd = make_reader([1, 0, 0])
    assert [rd(), rd(), rd(), rd()] == [1, 0, 0, 0]
    assert make_reader(1)() == 1
