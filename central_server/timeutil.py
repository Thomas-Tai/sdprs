# -*- coding: utf-8 -*-
"""
SDPRS Central Server - Time utilities

Single home for the naive-UTC "now" used throughout the server. `datetime.utcnow()`
is deprecated (Python 3.12+) and scheduled for removal; this is its behavior-
preserving replacement.

CRITICAL: this returns a **naive** datetime (tzinfo=None) whose value equals the
old `datetime.utcnow()`. The rest of the codebase stores/compares naive-UTC
timestamps and relies on `.isoformat()` producing NO timezone suffix (e.g.
retention's delimiter-sensitive comparisons, node last_heartbeat math). Using an
aware datetime here would change `.isoformat()` to add "+00:00" and silently break
those paths, so we deliberately strip tzinfo.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC now — drop-in replacement for the deprecated ``datetime.utcnow()``.

    Returns:
        A naive ``datetime`` (tzinfo=None) equal to the current UTC time.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


__all__ = ["utcnow"]
