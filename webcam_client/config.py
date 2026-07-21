# sdprs/webcam_client/config.py
import base64
import ctypes
import json
import logging
import os
from ctypes import wintypes
from pathlib import Path
from typing import Optional

logger = logging.getLogger("webcam_client.config")

_APP_NAME = "SDPRSWebcam"
_CONFIG_FILENAME = "config.json"

DEFAULT_CONFIG = {
    "server_url": "",
    "api_key": "",
    "cameras": [],
    "motion_threshold": 25,
    "heartbeat_interval": 30,
}


# --- Windows DPAPI (spec §258) ------------------------------------------------
# api_key is encrypted at rest, scoped to the current Windows user, via
# CryptProtectData / CryptUnprotectData reached through ctypes (no third-party
# package). On disk the field is "api_key_encrypted" (base64 blob); in memory
# load_config() presents a plaintext "api_key" so every downstream consumer is
# unchanged. Decrypt failure == unconfigured; NEVER fall back to a plaintext key.

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_protect(plaintext: str) -> str:
    data = plaintext.encode("utf-8")
    buf = ctypes.create_string_buffer(data, len(data))  # keep alive across the call
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptProtectData failed")
    try:
        return base64.b64encode(
            ctypes.string_at(blob_out.pbData, blob_out.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(blob_b64: str) -> Optional[str]:
    try:
        raw = base64.b64decode(blob_b64, validate=True)
    except Exception:
        return None
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        return None
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def get_config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / ".config"
    return base / _APP_NAME


def get_config_path() -> Path:
    return get_config_dir() / _CONFIG_FILENAME


def load_config() -> dict:
    path = get_config_path()
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to load config: {e}")
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    # Decrypt api_key from its DPAPI blob. A plaintext "api_key" left on disk is
    # deliberately NOT honored (spec §258): decrypt failure -> unconfigured.
    enc = merged.pop("api_key_encrypted", "")
    plaintext = _dpapi_unprotect(enc) if enc else None
    if enc and plaintext is None:
        logger.error("api_key decrypt failed -- treating as unconfigured")
    merged["api_key"] = plaintext or ""
    return merged


def save_config(config: dict) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    to_write = dict(config)
    api_key = to_write.pop("api_key", "")
    to_write.pop("api_key_encrypted", None)
    if api_key:
        to_write["api_key_encrypted"] = _dpapi_protect(api_key)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_write, f, ensure_ascii=False, indent=2)
    logger.info(f"Config saved to {path}")


def is_first_run() -> bool:
    return not get_config_path().exists()
