import pytest

import persist
from tests.fakes import FakeNVS


def test_missing_key_reads_as_zero():
    # First-ever boot: get_i32 raises OSError and that is normal flow.
    assert persist.read_boot_count(FakeNVS()) == 0


def test_register_boot_increments_and_commits():
    nvs = FakeNVS()
    assert persist.register_boot(nvs) == 1
    assert persist.register_boot(nvs) == 2
    assert persist.read_boot_count(nvs) == 2
    assert nvs.commits == 2


def test_clear_resets_to_zero():
    nvs = FakeNVS({"boot_count": 7})
    persist.clear_boot_count(nvs)
    assert persist.read_boot_count(nvs) == 0


def test_clear_is_a_no_op_when_already_zero():
    # Flash endurance: the healthy-uptime clear fires once per boot, but a
    # caller looping on it must not burn a write cycle every pass.
    nvs = FakeNVS({"boot_count": 0})
    persist.clear_boot_count(nvs)
    assert nvs.writes == 0


def test_contactor_ops_start_at_zero_and_increment():
    nvs = FakeNVS()
    assert persist.read_contactor_ops(nvs) == 0
    assert persist.bump_contactor_ops(nvs) == 1
    assert persist.read_contactor_ops(nvs) == 1


def test_reset_contactor_ops():
    nvs = FakeNVS({"contactor_ops": 12345})
    persist.reset_contactor_ops(nvs)
    assert persist.read_contactor_ops(nvs) == 0


def test_none_nvs_is_tolerated_everywhere():
    # A node whose NVS is unavailable must still run. Degraded protection
    # beats a boot loop caused by the loop detector itself.
    assert persist.read_boot_count(None) == 0
    assert persist.register_boot(None) == 0
    assert persist.read_contactor_ops(None) == 0
    assert persist.bump_contactor_ops(None) == 0
    persist.clear_boot_count(None)
    persist.reset_contactor_ops(None)


def test_write_failure_does_not_propagate():
    class ExplodingNVS(FakeNVS):
        def set_i32(self, key, value):
            raise OSError("flash worn out")

    assert persist.register_boot(ExplodingNVS()) == 0
