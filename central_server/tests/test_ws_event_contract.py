# -*- coding: utf-8 -*-
"""
WebSocket event whitelist contract test.

Guards the sync between the server-side WS event emitters
(`ws_manager.broadcast(...)` / `broadcast_from_sync(...)`) and the
client-side whitelist (`_WS_EVENT_TYPES` in central_server/static/spa/api.jsx).

If either drifts (server starts emitting a new type without adding it to
the SPA whitelist, or the SPA whitelist grows a type the server never
sends), this test fails with a clear diff.

Frozen contract:
- Types visible to the SPA (dispatched to onEvent):
      alert_updated, alert_acknowledged, alert_resolved,
      node_status, pump_status, node_deleted, auth_expired
- Types handled inside the SPA WS layer, NOT surfaced through onEvent:
      new_alert  -> routed to onNewAlert(alertObj)
      ping       -> pure keepalive, absorbed silently

`node_deleted` was emitted by the server (nodes.py, DELETE /api/nodes/{id}),
whitelisted in api.jsx and handled in app.jsx, but was never added to
EXPECTED_ALL_TYPES below — so this test sat red for a reason unrelated to any
real drift. A permanently-red drift detector is worse than none: it trains
readers to ignore the one signal it exists to raise. If this test fails,
confirm which side actually moved before editing the frozen set.
"""
import re
from pathlib import Path

import pytest


# Frozen contract — the union of every WS `type` string the SPA is prepared
# to receive. Any addition on either side of the wire MUST update this set
# AND the corresponding source-of-truth (server-side broadcast + SPA
# _WS_EVENT_TYPES). Keep alphabetized.
EXPECTED_ALL_TYPES = frozenset({
    "alert_acknowledged",
    "alert_resolved",
    "alert_updated",
    "auth_expired",
    "new_alert",
    "node_deleted",
    "node_status",
    "ping",
    "pump_status",
    "webcam_stream_started",
    "webcam_stream_stopped",
})

# Subset the SPA whitelists explicitly (onEvent dispatch set). new_alert is
# routed via onNewAlert; ping is handled internally.
EXPECTED_SPA_ONEVENT_TYPES = EXPECTED_ALL_TYPES - {"new_alert", "ping"}


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CENTRAL_SERVER = REPO_ROOT / "central_server"
SPA_API_JSX = CENTRAL_SERVER / "static" / "spa" / "api.jsx"


# Explicit map of server-side broadcast call-sites we know about, so the
# test remains readable when someone adds a new one. If the sweep below
# finds a `type` NOT in the union of both sets, the test fails and the
# developer is expected to either (a) add it to EXPECTED_ALL_TYPES and the
# SPA whitelist, or (b) document why the emitter is intentionally not
# surfaced to the client.
INTERNAL_ONLY_TYPES = frozenset({
    "ping",       # WebSocketManager._ping_loop keepalive
    "new_alert",  # routed via onNewAlert, not onEvent
})


def _read_spa_whitelist() -> frozenset[str]:
    """Extract _WS_EVENT_TYPES from api.jsx. The literal shape is:

        const _WS_EVENT_TYPES = new Set([
          'alert_updated', 'alert_acknowledged', 'alert_resolved',
          'node_status', 'pump_status', 'node_deleted',
          'auth_expired',
        ]);
    """
    text = SPA_API_JSX.read_text(encoding="utf-8")
    m = re.search(
        r"_WS_EVENT_TYPES\s*=\s*new\s+Set\(\s*\[(?P<body>.*?)\]\s*\)",
        text,
        re.DOTALL,
    )
    assert m, "api.jsx: could not locate _WS_EVENT_TYPES literal"
    body = m.group("body")
    # Pull every quoted string. Handles both single and double quotes.
    names = re.findall(r"['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]", body)
    return frozenset(names)


_BROADCAST_CALL_RE = re.compile(
    r"""
    (?<![a-zA-Z_])                     # word boundary, no leading identifier char
    (?:                                # match one of these call names:
        (?:ws_manager|self|mgr|manager)\.broadcast   # method-style broadcast to all
      | broadcast_from_sync                          # sync-context helper
      | (?:websocket|ws)\.send_json                  # per-connection send
    )
    \s*\(                              # opening paren
    """,
    re.VERBOSE,
)

_TYPE_LITERAL_RE = re.compile(
    r"""["']type["']\s*:\s*["']([a-zA-Z_][a-zA-Z0-9_]*)["']"""
)


def _sweep_server_broadcast_types() -> frozenset[str]:
    """Scan every .py under central_server/ for broadcast payload `type`
    string literals.

    Precise anchor: only look at windows that START at a REAL broadcast
    CALL (matching _BROADCAST_CALL_RE). Comments mentioning "broadcast"
    and unrelated state-dict `"type": "glass"` entries no longer produce
    false positives, because they don't appear at the top of a
    broadcast(... call window.
    """
    found: set[str] = set()
    for py_file in CENTRAL_SERVER.rglob("*.py"):
        # Skip the tests dir — tests build their own fake payloads that
        # aren't part of the runtime broadcast surface.
        if "tests" in py_file.parts:
            continue
        text = py_file.read_text(encoding="utf-8")
        for call in _BROADCAST_CALL_RE.finditer(text):
            # The dict literal argument is inside this call and spans up
            # to a few lines. Grab the next ~400 chars as the window —
            # more than enough for the largest payload we emit.
            window = text[call.end() : call.end() + 400]
            for m in _TYPE_LITERAL_RE.finditer(window):
                found.add(m.group(1))
    return frozenset(found)


def test_spa_whitelist_matches_frozen_contract():
    """The SPA _WS_EVENT_TYPES onEvent-dispatch set must equal the
    subset of EXPECTED_ALL_TYPES that is intended for onEvent."""
    spa_set = _read_spa_whitelist()
    assert spa_set == EXPECTED_SPA_ONEVENT_TYPES, (
        "SPA WS whitelist drift detected.\n"
        f"  Missing from api.jsx:  {sorted(EXPECTED_SPA_ONEVENT_TYPES - spa_set)}\n"
        f"  Extra in api.jsx:      {sorted(spa_set - EXPECTED_SPA_ONEVENT_TYPES)}\n"
        "If you intentionally added a WS event type, update both\n"
        "EXPECTED_ALL_TYPES here AND _WS_EVENT_TYPES in\n"
        "central_server/static/spa/api.jsx."
    )


def test_server_broadcast_types_are_all_expected():
    """Every WS `type` the server emits must be in EXPECTED_ALL_TYPES —
    otherwise the SPA either ignores it (silent feature loss) or falls
    through to the unknown-type branch."""
    server_set = _sweep_server_broadcast_types()
    # We expect the sweep to find at least the core types. If the sweep
    # heuristic ever misses a source, this assert warns loudly.
    assert server_set, (
        "sweep_server_broadcast_types() found ZERO WS payload literals — "
        "the heuristic likely broke. Check central_server/api/alerts.py "
        'for `"type": "new_alert"` etc.'
    )
    unexpected = server_set - EXPECTED_ALL_TYPES
    assert not unexpected, (
        "Server emits WS types the SPA is not prepared to handle:\n"
        f"  Unexpected types: {sorted(unexpected)}\n"
        "Either add them to EXPECTED_ALL_TYPES + api.jsx _WS_EVENT_TYPES,\n"
        "or route them through onNewAlert / internal-handling explicitly."
    )


def test_expected_types_are_actually_emitted_or_internal():
    """No dead entries in EXPECTED_ALL_TYPES — every listed type must
    either be observed in the server sweep or explicitly marked as
    INTERNAL_ONLY."""
    server_set = _sweep_server_broadcast_types()
    orphans = EXPECTED_ALL_TYPES - server_set - INTERNAL_ONLY_TYPES
    assert not orphans, (
        "EXPECTED_ALL_TYPES contains types no server emitter matches:\n"
        f"  Orphaned types: {sorted(orphans)}\n"
        "If a broadcast was removed, drop the type from both\n"
        "EXPECTED_ALL_TYPES here AND api.jsx _WS_EVENT_TYPES. If it is\n"
        "expected to only ever be sent from an internal path, add it to\n"
        "INTERNAL_ONLY_TYPES with a comment."
    )
