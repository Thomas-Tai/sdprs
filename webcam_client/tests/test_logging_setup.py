# webcam_client/tests/test_logging_setup.py
"""console=False means basicConfig()'s stdout handler writes into a void -- when
an operator says "it stopped working" there is no artifact. These tests pin the
file sink AND the security rule that the API key never reaches it."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import webcam_client.logging_setup as ls


def _fresh(monkeypatch, tmp_path):
    """Point the log dir at tmp_path and reset module state between tests."""
    monkeypatch.setattr(ls, "get_config_dir", lambda: tmp_path)
    ls.reset_for_tests()
    return tmp_path / "logs" / ls.LOG_FILENAME


def test_creates_log_file_and_writes_records(monkeypatch, tmp_path):
    logfile = _fresh(monkeypatch, tmp_path)
    handler = ls.setup_logging()
    logging.getLogger("webcam_client.test").info("hello from the client")
    handler.flush()
    assert logfile.exists()
    assert "hello from the client" in logfile.read_text(encoding="utf-8")


def test_api_key_is_redacted(monkeypatch, tmp_path):
    logfile = _fresh(monkeypatch, tmp_path)
    handler = ls.setup_logging()
    ls.add_secret("SUPERSECRETKEY123")
    logging.getLogger("webcam_client.test").warning(
        "auth failed for key SUPERSECRETKEY123")
    handler.flush()
    body = logfile.read_text(encoding="utf-8")
    assert "SUPERSECRETKEY123" not in body, "API KEY LEAKED INTO THE LOG FILE"
    assert ls.REDACTED in body


def test_redaction_survives_lazy_percent_args(monkeypatch, tmp_path):
    # logger.warning("key %s", secret) formats at emit time -- redacting only
    # record.msg without consuming args would let the secret through.
    #
    # This test must assert MORE than "the secret is absent", because there is
    # a failure mode that makes that alone pass vacuously: if the filter
    # rewrites record.msg to the redacted, fully-substituted string but
    # forgets to clear record.args, then record.args still holds
    # ("LAZYSECRET999",) while record.msg no longer has a "%s" placeholder.
    # Formatter.format() later calls record.getMessage() again, which does
    # `msg % args` -- and raises TypeError("not all arguments converted
    # during string formatting"). logging's emit() catches that and routes it
    # to handleError(), so the WHOLE LINE is silently dropped: no secret, but
    # also no message, no REDACTED marker, nothing. "secret not in body" is
    # true of an empty file too. Assert the line was actually written AND
    # redacted so a dropped-record regression fails this test instead of
    # sailing through it.
    logfile = _fresh(monkeypatch, tmp_path)
    handler = ls.setup_logging()
    ls.add_secret("LAZYSECRET999")
    logging.getLogger("webcam_client.test").warning("key is %s", "LAZYSECRET999")
    handler.flush()
    body = logfile.read_text(encoding="utf-8")
    assert "LAZYSECRET999" not in body
    assert "key is" in body, "record was dropped, not redacted (see comment above)"
    assert ls.REDACTED in body


def test_exception_traceback_is_redacted(monkeypatch, tmp_path):
    # record.exc_info is rendered into traceback text by Formatter.format(),
    # AFTER the handler's filter has already run. A filter that only inspects
    # record.msg / record.args never sees that text, so a secret embedded in
    # an exception's own message (e.g. str(exc) echoing auth state) would
    # reach the log file completely unredacted through five logger.exception()
    # call sites in this codebase. Pin that the rendered traceback is scrubbed
    # too, and that the traceback genuinely made it into the file (so this
    # test cannot pass vacuously the way a dropped-record bug would).
    logfile = _fresh(monkeypatch, tmp_path)
    handler = ls.setup_logging()
    ls.add_secret("SECRETKEY_IN_EXC")
    try:
        raise ValueError("boom with SECRETKEY_IN_EXC")
    except ValueError:
        logging.getLogger("webcam_client.test").exception("handler failed")
    handler.flush()
    body = logfile.read_text(encoding="utf-8")
    assert "SECRETKEY_IN_EXC" not in body, "API KEY LEAKED VIA TRACEBACK"
    assert "ValueError" in body, "traceback did not make it into the log at all"
    assert ls.REDACTED in body


def test_empty_secret_is_ignored(monkeypatch, tmp_path):
    # An unconfigured client has api_key == "". Redacting "" would replace
    # every character boundary in every message.
    logfile = _fresh(monkeypatch, tmp_path)
    handler = ls.setup_logging()
    ls.add_secret("")
    logging.getLogger("webcam_client.test").info("perfectly normal message")
    handler.flush()
    assert "perfectly normal message" in logfile.read_text(encoding="utf-8")


def test_setup_is_idempotent(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    first = ls.setup_logging()
    second = ls.setup_logging()
    assert first is second, "repeated setup must not stack duplicate handlers"


def test_rotation_is_configured(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    handler = ls.setup_logging()
    assert handler.maxBytes == ls.MAX_BYTES
    assert handler.backupCount == ls.BACKUP_COUNT
