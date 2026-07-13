# -*- coding: utf-8 -*-
"""Regression guard for central_server.timeutil.utcnow().

The whole point of the helper is to be a *naive*-UTC drop-in for the deprecated
datetime.utcnow(). If someone "modernizes" it to return an aware datetime, its
.isoformat() would gain a "+00:00" suffix and silently break the codebase's
naive-UTC timestamp comparisons (retention delimiter logic, last_heartbeat math).
These tests lock the contract in.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server.timeutil import utcnow


def test_utcnow_is_naive():
    now = utcnow()
    assert isinstance(now, datetime)
    assert now.tzinfo is None, "utcnow() must be naive (tzinfo=None) — an aware value breaks naive-UTC comparisons"


def test_utcnow_isoformat_has_no_tz_suffix():
    # A naive isoformat must NOT carry a +00:00 / Z suffix.
    s = utcnow().isoformat()
    assert "+" not in s and not s.endswith("Z")


def test_utcnow_value_matches_real_utc():
    # Value equals the real UTC now (within a small tolerance), just tz-stripped.
    ref = datetime.now(timezone.utc).replace(tzinfo=None)
    delta = abs((utcnow() - ref).total_seconds())
    assert delta < 5.0
