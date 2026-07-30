# sdprs/webcam_client/tests/test_config.py
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from webcam_client.config import load_config, save_config, get_config_path, DEFAULT_CONFIG


def test_load_config_default(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    config = load_config()
    assert config["server_url"] == ""
    assert config["cameras"] == []
    assert config["motion_threshold"] == 25


def test_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    config = {"server_url": "https://example.com", "api_key": "sk-test", "cameras": [{"name": "Cam1"}]}
    save_config(config)
    loaded = load_config()
    assert loaded["server_url"] == "https://example.com"
    assert loaded["api_key"] == "sk-test"       # round-trips through DPAPI in memory
    assert loaded["cameras"] == [{"name": "Cam1"}]
    assert loaded["motion_threshold"] == 25  # default merged


def test_api_key_encrypted_at_rest(tmp_path, monkeypatch):
    # spec §258: the key must never touch disk in plaintext.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    save_config({"server_url": "https://example.com", "api_key": "sk-secret-xyz"})
    raw = get_config_path().read_text(encoding="utf-8")
    assert "sk-secret-xyz" not in raw            # plaintext key must not hit disk
    assert "api_key_encrypted" in raw
    loaded = load_config()
    assert loaded["api_key"] == "sk-secret-xyz"  # decrypted in memory
    assert "api_key_encrypted" not in loaded     # blob not surfaced to callers


def test_bad_encrypted_blob_is_unconfigured(tmp_path, monkeypatch):
    # Decrypt failure must degrade to unconfigured, never crash or leak.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"server_url": "https://x", "api_key_encrypted": "!!!not-base64!!!"}',
                    encoding="utf-8")
    assert load_config()["api_key"] == ""


def test_save_config_unserialisable_preserves_existing_file(tmp_path, monkeypatch):
    # A non-serialisable value (e.g. a numpy frame hitching a ride on a camera
    # dict) must NOT destroy the guard's saved config. Truncate-then-fail would
    # leave a 0-byte file, load_config would fall back to DEFAULT_CONFIG, and the
    # next launch would drop the guard into the first-run wizard.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    save_config({"server_url": "https://good.example", "api_key": "sk-keepme",
                 "cameras": [{"name": "Cam1"}]})
    path = get_config_path()
    before = path.read_bytes()

    with pytest.raises(TypeError):
        save_config({"server_url": "https://bad.example", "api_key": "sk-keepme",
                     "cameras": [{"name": "Cam1", "frame": object()}]})

    assert path.read_bytes() == before          # byte-for-byte intact
    reloaded = load_config()
    assert reloaded["server_url"] == "https://good.example"
    assert reloaded["api_key"] == "sk-keepme"
    assert reloaded["cameras"] == [{"name": "Cam1"}]


def test_save_config_unserialisable_leaves_no_partial_files(tmp_path, monkeypatch):
    # The failed save must not litter the config dir with a temp/partial file.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    save_config({"server_url": "https://good.example"})
    path = get_config_path()

    with pytest.raises(TypeError):
        save_config({"server_url": "https://bad.example", "cameras": [{"frame": object()}]})

    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_save_config_no_existing_file_still_raises(tmp_path, monkeypatch):
    # First-ever save with a bad payload: raise, and create nothing.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = get_config_path()
    with pytest.raises(TypeError):
        save_config({"server_url": "https://bad.example", "cameras": [{"frame": object()}]})
    assert not path.exists()


def test_autostart_defaults_false(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert DEFAULT_CONFIG["autostart"] is False
    assert load_config()["autostart"] is False


def test_existing_config_without_autostart_loads_false(tmp_path, monkeypatch):
    # Existing users' config files predate the key; the DEFAULT_CONFIG merge in
    # load_config must supply it with no migration step.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"server_url": "https://x", "motion_threshold": 40}', encoding="utf-8")
    loaded = load_config()
    assert loaded["autostart"] is False
    assert loaded["server_url"] == "https://x"
    assert loaded["motion_threshold"] == 40


def test_autostart_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    save_config({"server_url": "https://x", "autostart": True})
    assert json.loads(get_config_path().read_text(encoding="utf-8"))["autostart"] is True
    assert load_config()["autostart"] is True
