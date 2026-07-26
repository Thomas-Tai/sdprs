# sdprs/webcam_client/logging_setup.py
"""Rotating file logging for the frozen client.

The exe is built console=False, so logging.basicConfig()'s stdout handler writes
into a void -- there is no artifact to inspect when an operator reports a fault.
Logs land in %APPDATA%\\SDPRSWebcam\\logs\\webcam.log.

SECURITY: the API key must never reach this file. _RedactFilter is installed on
the HANDLER so it scrubs records from every module, and it consumes record.args
so lazy %-formatting cannot smuggle a secret past it.
"""
import logging
import logging.handlers

from .config import get_config_dir

LOG_FILENAME = "webcam.log"
# A failing client logs a warning per failed snapshot push (~1Hz), which rolls a
# small log in under an hour and destroys the ORIGIN of the fault -- the part
# that is actually diagnostic. 2MB x 5 = 10MB buys roughly a day of noisy
# failure and is trivial on any disk.
MAX_BYTES = 2_000_000
BACKUP_COUNT = 5
REDACTED = "***REDACTED***"

_handler = None
_redactor = None


def get_log_dir():
    return get_config_dir() / "logs"


class _RedactFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self._secrets = []

    def add(self, secret: str) -> None:
        # An unconfigured client has api_key == "": redacting the empty string
        # would match at every character boundary and destroy every message.
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    def filter(self, record):
        if not self._secrets:
            return True
        msg = record.getMessage()          # applies args NOW
        hit = False
        for s in self._secrets:
            if s in msg:
                msg = msg.replace(s, REDACTED)
                hit = True
        if hit:
            record.msg = msg
            record.args = ()               # already applied above
        return True


def setup_logging(level=logging.INFO):
    """Install the rotating file handler on the root logger. Idempotent."""
    global _handler, _redactor
    if _handler is not None:
        return _handler
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    _redactor = _RedactFilter()
    handler = logging.handlers.RotatingFileHandler(
        log_dir / LOG_FILENAME, maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    handler.addFilter(_redactor)
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    _handler = handler
    return handler


def add_secret(secret: str) -> None:
    """Register a value that must never appear in the log.

    Separate from setup_logging() because logging is configured BEFORE
    load_config() runs, so the API key is not known yet at that point.
    """
    if _redactor is not None:
        _redactor.add(secret)


def reset_for_tests() -> None:
    """Detach the handler so each test starts from a clean root logger."""
    global _handler, _redactor
    if _handler is not None:
        logging.getLogger().removeHandler(_handler)
        _handler.close()
    _handler = None
    _redactor = None
