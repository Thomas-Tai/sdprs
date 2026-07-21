# sdprs/webcam_client/tests/test_config.py
import json
import sys
from pathlib import Path

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
