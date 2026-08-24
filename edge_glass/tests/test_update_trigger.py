"""Update-now trigger: launches the manual systemd unit with --no-block and
never lets a launch error escape (it runs on the MQTT callback thread)."""
import edge_glass_main as m


def test_trigger_builds_correct_argv():
    seen = {}

    def fake_runner(argv, **kwargs):
        seen["argv"] = argv
        class R:  # minimal CompletedProcess stand-in
            returncode = 0
        return R()

    assert m.trigger_manual_update(runner=fake_runner) is True
    argv = seen["argv"]
    assert argv[0] == "sudo"
    assert m.SYSTEMCTL_BIN in argv
    assert "start" in argv and "--no-block" in argv
    assert m.MANUAL_UPDATE_UNIT == "sdprs-edge-update-manual.service"
    assert argv[-1] == m.MANUAL_UPDATE_UNIT


def test_trigger_swallows_exception():
    def boom(argv, **kwargs):
        raise OSError("sudo missing")

    # Must return False, not raise — the MQTT dispatch thread must survive.
    assert m.trigger_manual_update(runner=boom) is False
