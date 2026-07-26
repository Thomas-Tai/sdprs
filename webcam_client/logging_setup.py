# sdprs/webcam_client/logging_setup.py
"""Rotating file logging for the frozen client.

The exe is built console=False, so logging.basicConfig()'s stdout handler writes
into a void -- there is no artifact to inspect when an operator reports a fault.
Logs land in %APPDATA%\\SDPRSWebcam\\logs\\webcam.log.

SECURITY: the API key must never reach this file. _RedactFilter is installed on
the HANDLER so it scrubs records from every module, and it consumes record.args
so lazy %-formatting cannot smuggle a secret past it.

_RedactFilter alone is NOT sufficient: it only inspects record.msg/record.args.
record.exc_info is rendered into traceback text by Formatter.format(), which
runs AFTER filters -- so a secret embedded in an exception's own message (str(exc))
would sail straight past the filter through any logger.exception() call. The
_RedactingFormatter below re-scrubs the fully rendered line -- message and
traceback both -- as an independent second layer, so there is no format-time
seam left for a secret to escape through.
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

    def redact(self, text: str) -> str:
        """Scrub any registered secret out of an arbitrary string.

        Shared by filter() (scrubs record.msg/args pre-formatting) and
        _RedactingFormatter (scrubs the fully rendered line, including any
        exception traceback, post-formatting) -- one secrets list, two
        independent scrub points, so neither a message-only nor a
        traceback-only leak has a way through.
        """
        for s in self._secrets:
            if s in text:
                text = text.replace(s, REDACTED)
        return text

    def filter(self, record):
        if not self._secrets:
            return True
        msg = record.getMessage()          # applies args NOW
        redacted = self.redact(msg)
        if redacted != msg:
            record.msg = redacted
            record.args = ()               # already applied above
        return True


class _RedactingFormatter(logging.Formatter):
    """Second, independent redaction layer over the fully rendered line.

    _RedactFilter runs before formatting and only ever sees record.msg /
    record.args. record.exc_info is turned into traceback text inside
    Formatter.format() (via formatException), which happens AFTER the filter
    chain -- so a secret that only appears in an exception's own str() (e.g.
    a logger.exception() call) would bypass the filter entirely. Re-scrubbing
    the final string here closes that seam without weakening the filter.
    """

    def __init__(self, fmt, redactor):
        super().__init__(fmt)
        self._redactor = redactor

    def format(self, record):
        return self._redactor.redact(super().format(record))


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
    handler.setFormatter(_RedactingFormatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s", _redactor))
    handler.addFilter(_redactor)
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    # The ring above is sized against THIS CLIENT's own ~1Hz failure warnings
    # (see MAX_BYTES/BACKUP_COUNT comment). httpx emits one INFO record per
    # HTTP request and propagates to root; before this branch that landed in
    # basicConfig()'s stdout handler, which console=False silently discards.
    # Now it reaches this file handler too, and with 2 cameras pushing at
    # ~1Hz plus command polling, that per-request noise alone drains the
    # ring in 1-10 hours ON THE HEALTHY PATH -- evicting the one diagnostic
    # line an operator needs before anyone reads it. Quiet it at the source.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
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
