"""config_loader deep-merge delivers the perf tuning to a LEAN node config.

The Pi's config.zeabur.yaml carries only that node's secrets + basics, NOT the
throttling knobs — those live in config_loader.DEFAULTS and reach every node via
the deep-merge in load_config(). This keeps each Pi's config file untouched by
updates (git-pull never conflicts on a node's secrets) while still shipping the
optimization. These pin that the defaults actually arrive, and that a node's own
values still win.
"""
import os

from utils.config_loader import load_config

_ZEABUR_CFG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.zeabur.yaml"
)


def test_defaults_deliver_detect_fps_to_lean_config():
    # config.zeabur.yaml does NOT spell out detect_fps; it comes from DEFAULTS.
    cfg = load_config(_ZEABUR_CFG)
    assert cfg["visual"]["detect_fps"] == 5


def test_defaults_deliver_async_encode_to_lean_config():
    cfg = load_config(_ZEABUR_CFG)
    assert cfg["capture"]["async_encode"] is True


def test_defaults_deliver_heartbeat_interval_and_keep_node_values():
    cfg = load_config(_ZEABUR_CFG)
    # Tuning key added by DEFAULTS...
    assert cfg["server"]["heartbeat_interval"] == 10
    # ...while the node's own server keys (e.g. its broker) are preserved.
    assert cfg["server"]["mqtt_broker"]


def test_node_override_wins_over_default_detect_fps():
    # A node that explicitly sets detect_fps still overrides the default — the
    # deep-merge order (DEFAULTS as base, node config as override) guarantees it.
    from utils.config_loader import _deep_merge, DEFAULTS
    merged = _deep_merge(DEFAULTS, {"visual": {"detect_fps": 3}})
    assert merged["visual"]["detect_fps"] == 3


def test_defaults_deliver_detect_scale_to_lean_config():
    cfg = load_config(_ZEABUR_CFG)
    assert cfg["visual"]["detect_scale"] == 0.5


def test_defaults_deliver_stabilize_to_lean_config():
    cfg = load_config(_ZEABUR_CFG)
    assert cfg["visual"]["stabilize"] is True


def test_defaults_deliver_camera_fps_12_to_lean_config():
    # config.zeabur.yaml no longer pins camera.fps, so it inherits the 12 default.
    cfg = load_config(_ZEABUR_CFG)
    assert cfg["camera"]["fps"] == 12
