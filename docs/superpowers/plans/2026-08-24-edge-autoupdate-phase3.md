# Edge auto-update Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the (currently inert) auto-update hold a real writer — from BOTH an edge-local "capture in progress" signal and a server-driven "unresolved alert" signal — surface it to the dashboard so 「立即更新」warns before overriding, and add a positive `edge_ready` readiness gate the updater waits for before advancing the deployed SHA.

**Architecture:** The **edge process is the sole writer** of `/run/sdprs/update_hold`, written `1`/`0` every heartbeat as `local_capture OR server_hold`. Local capture is derived live from the event cooldown/encode window; server hold arrives over a new `hold` MQTT command that a server-side reconcile publishes for any online node with an active alert. The updater's `held()` gains an mtime-TTL freshness guard (a dead writer's stale hold self-expires); its `health_check()` additionally waits for a fresh `/run/sdprs/edge_ready` (written by the edge once camera+loop+MQTT are genuinely up) before advancing the SHA, else rolls back. Hold-state rides the heartbeat to the dashboard, where 「立即更新」shows a stronger zh-TW confirm when held (the edge `--manual` path still bypasses the file — operator override).

**Tech Stack:** Python 3 (edge firmware + FastAPI server), bash + systemd + tmpfiles.d (Pi), React (no-build-step JSX SPA), MQTT (paho), apscheduler, pytest, jsdom.

**Spec:** `docs/superpowers/specs/2026-08-24-edge-autoupdate-phase3-design.md`

## Global Constraints

- No hardcoded credentials. The literal strings `Msc@2333`, `MSC-Person`, `broker.emqx.io` must NEVER appear in any diff (banned-string scan before every commit).
- User-facing SPA strings are **zh-TW Traditional Chinese**; on-node/journal/log strings stay English. **The heartbeat `hold_reason` is a stable English machine code** (`"event_capture"` | `"active_alert"` | `null`); the SPA maps the code to zh-TW display text — so on-node code stays English AND operator text stays zh-TW.
- All server timestamps are **naive UTC** via `central_server.timeutil.utcnow()` — never `datetime.utcnow()` or tz-aware.
- The edge is the **single writer** of `/run/sdprs/update_hold`. The updater only reads it. Never add a second writer.
- Every edge-side file write (`update_hold`, `edge_ready`) is best-effort: a failure is logged and **must never break the heartbeat or the main loop** (same discipline as the existing `version`/IP reads).
- Edge tests import as `from comms.mqtt_client import ...` / `import edge_glass_main as m` and run with `edge_glass/` on `sys.path` (pytest invoked from `edge_glass/`). Server tests `sys.path.insert(0, <repo root>)` and import `central_server...`, setting `DASHBOARD_USER`/`DASHBOARD_PASS`/`EDGE_API_KEY`/`SECRET_KEY` env (see `tests/test_update_command.py` header).
- SPA has **no build step**; the jsdom suite is `tools/spa/render_tests.js` (register a new section + add it to the `SECTIONS` list — **NOT** a `__tests__/*.jsx` file, RULING R5). Run: `node tools/spa/run_all.js` with `NODE_PATH` → the main checkout's `tools/spa/node_modules` (this worktree has none).
- Bash updater tests are the black-box harness `scripts/tests/test_edge_autoupdate.sh` (stubs git/rsync/systemctl/tar/chown; `SLEEP=:`). Run: `bash scripts/tests/test_edge_autoupdate.sh`.
- `--manual` / dashboard "Update now" **always bypass** the hold file (unchanged behavior). Do not gate them on the hold.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `edge_glass/comms/mqtt_client.py` (modify) | Hold setters + TTL; write `update_hold` each heartbeat; `update_held`/`hold_reason` in heartbeat | 1 |
| `edge_glass/tests/test_mqtt_hold.py` (create) | Setters, server-hold TTL, aggregate OR, file `1`/`0`, heartbeat fields, write-failure safety | 1 |
| `edge_glass/edge_glass_main.py` (modify) | `compute_local_capture_hold` helper + loop wiring; register `hold` command handler | 2 |
| `edge_glass/tests/test_hold_wiring.py` (create) | Pure helper cases + `hold` handler stores server hold | 2 |
| `edge_glass/edge_glass_main.py` (modify) | `edge_ready` clear-at-startup + mark-ready helpers + loop wiring | 3 |
| `edge_glass/tests/test_edge_ready.py` (create) | clear/mark helpers: create, delete, idempotent, missing-dir safe | 3 |
| `scripts/edge_autoupdate.sh` (modify) | `held()` mtime-TTL freshness guard | 4 |
| `edge_glass/systemd/sdprs-edge-update.conf` (modify) | `HOLD_MAX_AGE` default | 4 |
| `scripts/tests/test_edge_autoupdate.sh` (modify) | held-TTL cases (fresh/stale/`0`/absent) | 4 |
| `scripts/edge_autoupdate.sh` (modify) | `rm -f edge_ready` before restart; `health_check` waits for it (`REQUIRE_EDGE_READY`) | 5 |
| `edge_glass/systemd/sdprs-edge-update.conf` (modify) | `EDGE_READY_FILE`, `REQUIRE_EDGE_READY` defaults | 5 |
| `scripts/edge_autoupdate_install.sh` (modify) | Install `/etc/tmpfiles.d/sdprs.conf` (`/run/sdprs` owned `sdprs`) + `systemd-tmpfiles --create` | 5 |
| `scripts/tests/test_edge_autoupdate.sh` (modify) | edge_ready health-gate cases; systemctl stub touches edge_ready on restart | 5 |
| `scripts/tests/test_edge_autoupdate_units.sh` (modify) | Grep gate: installer writes the tmpfiles.d line | 5 |
| `central_server/services/mqtt_service.py` (modify) | Persist `update_held`/`hold_reason`; `send_hold_command` | 6 |
| `central_server/tests/test_hold_command.py` (create) | Persist fields; `send_hold_command` topic/payload | 6 |
| `central_server/services/mqtt_service.py` (modify) | `reconcile_alert_holds(active_lookup)` | 7 |
| `central_server/config.py` (modify) | `HOLD_RECONCILE_ENABLED`, `HOLD_RECONCILE_INTERVAL_S` | 7 |
| `central_server/main.py` (modify) | Schedule the reconcile job in lifespan | 7 |
| `central_server/tests/test_hold_reconcile.py` (create) | re-assert true / unhold-on-transition / skip never-held / offline-skip | 7 |
| `central_server/api/nodes.py` (modify) | `NodeStatus.update_held`/`hold_reason`; serialize (live + offline) | 8 |
| `central_server/tests/test_node_hold_api.py` (create) | Serialization live + offline | 8 |
| `central_server/static/spa/api.jsx` (modify) | `mapNode` `updateHeld`/`holdReason` | 9 |
| `central_server/static/spa/pages/status.jsx` (modify) | Held → stronger confirm naming the reason; `holdReasonText` mapper | 9 |
| `tools/spa/render_tests.js` (modify) | New `TEST_STATUS_UPDATE_HELD` section + register; extend `TEST_API` | 9 |

---

### Task 1: Edge — hold aggregation, writer, and heartbeat fields (`mqtt_client.py`)

**Files:**
- Modify: `edge_glass/comms/mqtt_client.py` (`__init__` ~line 143-156; `_publish_heartbeat` :316-350)
- Test: `edge_glass/tests/test_mqtt_hold.py` (create)

**Interfaces:**
- Produces:
  - `MQTTClient.set_local_capture_hold(active: bool, reason: Optional[str]) -> None`
  - `MQTTClient.set_server_hold(hold: bool, reason: Optional[str]) -> None` (stamps `self._clock()`)
  - `MQTTClient._compute_hold() -> tuple[bool, Optional[str]]` — `(held, reason_code)`; local wins over server for the reason
  - heartbeat JSON gains `"update_held": bool`, `"hold_reason": Optional[str]`
  - side effect: writes `1`/`0` to `self._hold_file` (default env `EDGE_UPDATE_HOLD_FILE` or `/run/sdprs/update_hold`) each `_publish_heartbeat`
  - module constant `SERVER_HOLD_TTL = 900`; `self._clock = time.monotonic` (injectable in tests)
  - module fn `_write_hold_file(path: str, held: bool) -> None` (best-effort; swallows OSError)

- [ ] **Step 1: Write the failing test**

Create `edge_glass/tests/test_mqtt_hold.py`:

```python
"""Update-hold aggregation + writer. No broker, no paho: skip _init_client and
inject a fake client (mirrors test_mqtt_version.py)."""
import json
from unittest import mock

from comms.mqtt_client import MQTTClient, SERVER_HOLD_TTL, _write_hold_file


class _FakeClient:
    def __init__(self):
        self.last_payload = None

    def publish(self, topic, payload, qos=0):
        self.last_payload = payload


def _make_client(tmp_path):
    config = {"node_id": "glass_node_01",
              "server": {"mqtt_broker": "localhost", "mqtt_port": 1883}}
    with mock.patch("comms.mqtt_client.PAHO_AVAILABLE", True), \
            mock.patch.object(MQTTClient, "_init_client", lambda self: None):
        client = MQTTClient(config)
    client._client = _FakeClient()
    client._hold_file = str(tmp_path / "update_hold")
    client._version_file = str(tmp_path / "sha")  # keep version read harmless
    return client


def _hold_content(client):
    with open(client._hold_file) as f:
        return f.read().strip()


def test_write_hold_file_emits_1_and_0(tmp_path):
    p = str(tmp_path / "h")
    _write_hold_file(p, True)
    assert open(p).read().strip() == "1"
    _write_hold_file(p, False)
    assert open(p).read().strip() == "0"


def test_write_hold_file_missing_dir_does_not_raise(tmp_path):
    _write_hold_file(str(tmp_path / "nodir" / "h"), True)  # must not raise


def test_not_held_by_default(tmp_path):
    client = _make_client(tmp_path)
    held, reason = client._compute_hold()
    assert held is False and reason is None


def test_local_capture_hold(tmp_path):
    client = _make_client(tmp_path)
    client.set_local_capture_hold(True, "event_capture")
    held, reason = client._compute_hold()
    assert held is True and reason == "event_capture"


def test_server_hold_within_ttl(tmp_path):
    client = _make_client(tmp_path)
    t = [1000.0]
    client._clock = lambda: t[0]
    client.set_server_hold(True, "active_alert")
    t[0] = 1000.0 + SERVER_HOLD_TTL - 1
    held, reason = client._compute_hold()
    assert held is True and reason == "active_alert"


def test_server_hold_expires_after_ttl(tmp_path):
    client = _make_client(tmp_path)
    t = [1000.0]
    client._clock = lambda: t[0]
    client.set_server_hold(True, "active_alert")
    t[0] = 1000.0 + SERVER_HOLD_TTL + 1
    held, reason = client._compute_hold()
    assert held is False and reason is None


def test_local_wins_reason_over_server(tmp_path):
    client = _make_client(tmp_path)
    client.set_server_hold(True, "active_alert")
    client.set_local_capture_hold(True, "event_capture")
    _, reason = client._compute_hold()
    assert reason == "event_capture"


def test_heartbeat_writes_hold_file_and_fields(tmp_path):
    client = _make_client(tmp_path)
    client.set_local_capture_hold(True, "event_capture")
    client._publish_heartbeat()
    payload = json.loads(client._client.last_payload)
    assert payload["update_held"] is True
    assert payload["hold_reason"] == "event_capture"
    assert _hold_content(client) == "1"


def test_heartbeat_writes_zero_when_not_held(tmp_path):
    client = _make_client(tmp_path)
    client._publish_heartbeat()
    payload = json.loads(client._client.last_payload)
    assert payload["update_held"] is False
    assert payload["hold_reason"] is None
    assert _hold_content(client) == "0"


def test_hold_write_failure_does_not_break_heartbeat(tmp_path):
    client = _make_client(tmp_path)
    client._hold_file = str(tmp_path / "nodir" / "update_hold")  # unwritable dir
    client._publish_heartbeat()  # must not raise
    payload = json.loads(client._client.last_payload)
    assert "update_held" in payload
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `edge_glass/`): `python -m pytest tests/test_mqtt_hold.py -v`
Expected: FAIL — `ImportError: cannot import name 'SERVER_HOLD_TTL'` / `_write_hold_file`, and `AttributeError` on the setters.

- [ ] **Step 3: Write minimal implementation**

In `edge_glass/comms/mqtt_client.py`, at module scope (near `_read_deployed_version`) add:

```python
import time  # already imported

SERVER_HOLD_TTL = 900  # seconds: a pushed server-hold self-expires if the
# server stops re-asserting, so a dead/unreachable server never pins a node held.


def _write_hold_file(path: str, held: bool) -> None:
    """Write the update-hold flag ("1"/"0") for the on-node updater to read.
    Best-effort: a write failure (dir missing, perms) must never break the
    heartbeat. The edge is the SOLE writer of this file."""
    try:
        with open(path, "w") as f:
            f.write("1" if held else "0")
    except OSError as e:
        logger.warning(f"could not write hold file {path}: {e}")
```

In `__init__` (after `self._version_file = ...`, ~line 152) add:

```python
        # Update-hold: the edge is the sole writer of /run/sdprs/update_hold.
        # Aggregates the two hold sources (local capture + server alert) and is
        # written every heartbeat; the on-node updater reads it (with an mtime
        # TTL) to defer a SCHEDULED update. --manual bypasses it.
        self._hold_file = os.environ.get(
            "EDGE_UPDATE_HOLD_FILE", "/run/sdprs/update_hold"
        )
        self._local_capture_hold = False
        self._local_capture_reason = None
        self._server_hold = False
        self._server_hold_reason = None
        self._server_hold_ts = None
        self._clock = time.monotonic  # injectable for tests
```

Add methods (near `set_buffer_health` / `set_detector_health`):

```python
    def set_local_capture_hold(self, active: bool, reason=None) -> None:
        """Raised by the main loop while an event is mid-capture/cooldown."""
        self._local_capture_hold = bool(active)
        self._local_capture_reason = reason if active else None

    def set_server_hold(self, hold: bool, reason=None) -> None:
        """Set from a server 'hold' command; stamped so it self-expires after
        SERVER_HOLD_TTL if the server stops re-asserting."""
        self._server_hold = bool(hold)
        self._server_hold_reason = reason if hold else None
        self._server_hold_ts = self._clock() if hold else None

    def _compute_hold(self):
        """(held, reason_code). Local capture wins the reason over server."""
        if self._local_capture_hold:
            return True, self._local_capture_reason
        if self._server_hold and self._server_hold_ts is not None \
                and (self._clock() - self._server_hold_ts) <= SERVER_HOLD_TTL:
            return True, self._server_hold_reason
        return False, None
```

In `_publish_heartbeat`, before building `heartbeat_data`, compute the hold and add the two fields; after publishing (or right before), write the file:

```python
        held, hold_reason = self._compute_hold()
        _write_hold_file(self._hold_file, held)
```
and inside `heartbeat_data`, after `"version": ...`:
```python
            "update_held": held,
            "hold_reason": hold_reason,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mqtt_hold.py -v`
Expected: PASS (all cases). Also run `python -m pytest tests/test_mqtt_version.py tests/test_mqtt_heartbeat.py -v` — unchanged, still green.

- [ ] **Step 5: Commit**

```bash
git add edge_glass/comms/mqtt_client.py edge_glass/tests/test_mqtt_hold.py
git commit -m "feat(edge): aggregate update-hold + write /run/sdprs/update_hold each heartbeat"
```

---

### Task 2: Edge — local-capture hold wiring + `hold` command handler (`edge_glass_main.py`)

**Files:**
- Modify: `edge_glass/edge_glass_main.py` (helper near top-level fns ~line 77; command registration :420-432; main loop after cooldown set :622 and 6b drain :626)
- Test: `edge_glass/tests/test_hold_wiring.py` (create)

**Interfaces:**
- Consumes (Task 1): `mqtt_client.set_local_capture_hold(active, reason)`, `set_server_hold(hold, reason)`.
- Produces: module fn `compute_local_capture_hold(now: float, cooldown_until: float, has_pending: bool) -> tuple[bool, Optional[str]]`; a registered `"hold"` command handler that calls `mqtt_client.set_server_hold(...)`.

- [ ] **Step 1: Write the failing test**

Create `edge_glass/tests/test_hold_wiring.py`:

```python
"""Local-capture hold helper + server 'hold' command handler wiring."""
import edge_glass_main as m


def test_capture_hold_true_during_cooldown():
    held, reason = m.compute_local_capture_hold(now=100.0, cooldown_until=130.0, has_pending=False)
    assert held is True and reason == "event_capture"


def test_capture_hold_true_when_pending_events():
    held, reason = m.compute_local_capture_hold(now=200.0, cooldown_until=0.0, has_pending=True)
    assert held is True and reason == "event_capture"


def test_capture_hold_false_when_idle():
    held, reason = m.compute_local_capture_hold(now=200.0, cooldown_until=130.0, has_pending=False)
    assert held is False and reason is None


class _FakeClient:
    def __init__(self):
        self.calls = []

    def set_server_hold(self, hold, reason=None):
        self.calls.append((hold, reason))


def test_hold_handler_sets_server_hold():
    client = _FakeClient()
    handler = m.make_hold_handler(client)
    handler({"hold": True, "reason": "active_alert"})
    handler({"hold": False})
    assert client.calls == [(True, "active_alert"), (False, None)]


def test_hold_handler_survives_bad_payload():
    client = _FakeClient()
    handler = m.make_hold_handler(client)
    handler({})  # missing keys -> treated as no-hold, must not raise
    assert client.calls == [(False, None)]
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `edge_glass/`): `python -m pytest tests/test_hold_wiring.py -v`
Expected: FAIL — `AttributeError: module 'edge_glass_main' has no attribute 'compute_local_capture_hold'` / `make_hold_handler`.

- [ ] **Step 3: Write minimal implementation**

In `edge_glass/edge_glass_main.py`, near the other module-level helpers (e.g. after `trigger_manual_update`, ~line 92), add:

```python
def compute_local_capture_hold(now, cooldown_until, has_pending):
    """(held, reason). Held while an event is mid-capture: inside the cooldown
    window OR there are undrained (async) events still to encode. Reason is a
    stable English code the dashboard maps to zh-TW."""
    if now <= cooldown_until or has_pending:
        return True, "event_capture"
    return False, None


def make_hold_handler(mqtt_client):
    """Build the MQTT 'hold' command handler. Server pushes {hold, reason}; the
    edge stores it (self-expiring via SERVER_HOLD_TTL). Never raises — runs on
    the MQTT dispatch thread."""
    def handle_hold(payload):
        hold = bool((payload or {}).get("hold", False))
        reason = (payload or {}).get("reason") if hold else None
        logger.info(f"Hold command received: hold={hold} reason={reason}")
        mqtt_client.set_server_hold(hold, reason)
    return handle_hold
```

Register the handler alongside the others (after `mqtt_client.register_command_handler("update", handle_update)`, ~line 431):

```python
        mqtt_client.register_command_handler("hold", make_hold_handler(mqtt_client))
```

Wire the local-capture signal into the main loop. Where `cooldown_until` is known (the loop already computes `timestamp` each iteration; `event_tracker` may be None in blocking mode), add — after the 6b async-drain block (~line 633), so `event_tracker` state is current:

```python
        # Update-hold (Phase 3): raise while an event is mid-capture so a
        # SCHEDULED auto-update won't restart the service and truncate a
        # recording. has_pending only applies to the async path.
        if mqtt_client:
            has_pending = bool(async_encode and event_tracker is not None
                               and len(event_tracker) > 0)
            held, reason = compute_local_capture_hold(timestamp, cooldown_until, has_pending)
            mqtt_client.set_local_capture_hold(held, reason)
```

> `event_tracker` is a `PendingEventTracker` (`edge_glass/utils/event_capture.py`); its `__len__` returns the count of events still awaiting their post-roll window (drained events are removed by the `.due()` call in the 6b block just above). So `len(event_tracker) > 0` ⇒ a capture is still in flight. Confirm the loop variable name is `event_tracker` (it is at `:573`/`:627`); the helper's `has_pending` bool is the contract.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hold_wiring.py -v`
Expected: PASS. Also `python -m pytest tests/test_main_helpers.py -v` — unchanged, green.

- [ ] **Step 5: Commit**

```bash
git add edge_glass/edge_glass_main.py edge_glass/tests/test_hold_wiring.py
git commit -m "feat(edge): local-capture hold + server 'hold' command handler"
```

---

### Task 3: Edge — `edge_ready` readiness signal (`edge_glass_main.py`)

**Files:**
- Modify: `edge_glass/edge_glass_main.py` (module helpers ~line 92; before the `while _running` loop :491; inside the loop after a successful `camera.read()` ~line 522)
- Test: `edge_glass/tests/test_edge_ready.py` (create)

**Interfaces:**
- Produces: `edge_ready_path() -> str` (env `EDGE_READY_FILE` or `/run/sdprs/edge_ready`); `clear_edge_ready(path=None) -> None`; `mark_edge_ready(path=None) -> None`. Both best-effort (swallow OSError).

- [ ] **Step 1: Write the failing test**

Create `edge_glass/tests/test_edge_ready.py`:

```python
"""Readiness signal: cleared at startup, created once the node is functional.
The updater's health-check waits for it, so a 'service active but camera never
opened' update fails the check and rolls back."""
import os
import edge_glass_main as m


def test_mark_then_clear(tmp_path):
    p = str(tmp_path / "edge_ready")
    m.mark_edge_ready(p)
    assert os.path.exists(p)
    m.clear_edge_ready(p)
    assert not os.path.exists(p)


def test_clear_is_idempotent_when_absent(tmp_path):
    m.clear_edge_ready(str(tmp_path / "nope"))  # must not raise


def test_mark_is_idempotent(tmp_path):
    p = str(tmp_path / "edge_ready")
    m.mark_edge_ready(p)
    m.mark_edge_ready(p)  # second call must not raise
    assert os.path.exists(p)


def test_mark_missing_dir_does_not_raise(tmp_path):
    m.mark_edge_ready(str(tmp_path / "nodir" / "edge_ready"))  # swallowed


def test_default_path_from_env(monkeypatch, tmp_path):
    target = str(tmp_path / "custom_ready")
    monkeypatch.setenv("EDGE_READY_FILE", target)
    assert m.edge_ready_path() == target
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_edge_ready.py -v`
Expected: FAIL — `AttributeError` on `mark_edge_ready`/`clear_edge_ready`/`edge_ready_path`.

- [ ] **Step 3: Write minimal implementation**

In `edge_glass/edge_glass_main.py`, near the other module helpers, add:

```python
def edge_ready_path(path=None):
    return path or os.environ.get("EDGE_READY_FILE", "/run/sdprs/edge_ready")


def clear_edge_ready(path=None):
    """Delete any stale readiness file. Called at startup: /run is tmpfs and a
    file from the PREVIOUS process survives a restart; the updater's post-restart
    health-check must observe the NEW process assert readiness, not a leftover."""
    p = edge_ready_path(path)
    try:
        os.remove(p)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"could not clear edge_ready {p}: {e}")


def mark_edge_ready(path=None):
    """Assert the node is genuinely functional (camera reading + loop iterating +
    MQTT up). Best-effort; never breaks the loop."""
    p = edge_ready_path(path)
    try:
        with open(p, "w") as f:
            f.write("1")
    except OSError as e:
        logger.warning(f"could not write edge_ready {p}: {e}")
```

Before the `while _running:` loop (~line 491), clear any stale file:

```python
    clear_edge_ready()  # startup: force the new process to re-assert readiness
    _edge_ready_marked = False
```

Inside the loop, right after a successful camera read resets the retry counter (~line 522, after the `set_buffer_health("ok")` block), mark ready once:

```python
        # Readiness (Phase 3): first successful read with MQTT up ⇒ genuinely
        # functional. The updater health-check waits for this file after a
        # restart, catching "service active but camera never opened".
        if not _edge_ready_marked and mqtt_client:
            mark_edge_ready()
            _edge_ready_marked = True
```

> `_edge_ready_marked` is a plain local in `main()`; if `main()` is structured so the flag can't be a loop-local, hang it on `main` like the existing `main._cam_retry` idiom.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_edge_ready.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add edge_glass/edge_glass_main.py edge_glass/tests/test_edge_ready.py
git commit -m "feat(edge): positive edge_ready readiness signal (clear-at-startup, mark-when-live)"
```

---

### Task 4: Updater — `held()` mtime-TTL freshness guard (`edge_autoupdate.sh`)

**Files:**
- Modify: `scripts/edge_autoupdate.sh` (`held()` :80-82; config block :28-42)
- Modify: `edge_glass/systemd/sdprs-edge-update.conf`
- Test: `scripts/tests/test_edge_autoupdate.sh` (extend)

**Interfaces:**
- Consumes: `HOLD_FILE` (`$RUN_DIR/update_hold`, already defined :40) written `1`/`0` by the edge (Task 1).
- Produces: `held()` returns true iff file content is `1` **and** mtime within `HOLD_MAX_AGE` seconds (new conf var, default `300`).

- [ ] **Step 1: Write the failing test**

In `scripts/tests/test_edge_autoupdate.sh`, replace the single hold case (block `# 4)`, lines ~136-148) with freshness-aware cases. Keep the existing "hold(scheduled) does not clone" and "hold(--manual) proceeds" assertions, and ADD:

```bash
# 4c) STALE hold (mtime older than HOLD_MAX_AGE) -> NOT held, update proceeds
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
echo 1 > "$SB/run/update_hold"
touch -d '2000-01-01 00:00:00' "$SB/run/update_hold"   # ancient mtime
run STUB_REMOTE_SHA=new STUB_RSYNC_ITEMIZE=">f marker" STUB_ISACTIVE=active \
    HOLD_MAX_AGE=300 NOW_OVERRIDE=04:00
A "stale-hold proceeds to clone" "$(calls | grep -q 'git clone' && echo 1 || echo 0)" "$(calls)"
cleanup

# 4d) hold file content "0" -> NOT held, update proceeds
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
echo 0 > "$SB/run/update_hold"
run STUB_REMOTE_SHA=new STUB_RSYNC_ITEMIZE=">f marker" STUB_ISACTIVE=active NOW_OVERRIDE=04:00
A "hold=0 proceeds to clone" "$(calls | grep -q 'git clone' && echo 1 || echo 0)" "$(calls)"
cleanup
```

> `touch -d` is GNU coreutils (Linux Pi + git-bash both have it). The existing `# 4)` fresh-hold block already proves fresh `1` defers.

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/tests/test_edge_autoupdate.sh`
Expected: FAIL — "stale-hold proceeds to clone" fails, because the current `held()` has no TTL and still defers a stale `1`.

- [ ] **Step 3: Write minimal implementation**

In `scripts/edge_autoupdate.sh` config block add (near :33):

```bash
: "${HOLD_MAX_AGE:=300}"
```

Replace `held()` (:80-82):

```bash
held() {
  [ -f "$HOLD_FILE" ] || return 1
  [ "$(cat "$HOLD_FILE" 2>/dev/null)" = "1" ] || return 1
  # Freshness: the edge rewrites this file every heartbeat, so a fresh mtime
  # proves a live writer. A stale "1" (dead edge process) self-expires, making a
  # genuinely dead node updatable. HOLD_MAX_AGE >> heartbeat interval.
  local now mtime
  now="$(date +%s)"
  mtime="$(stat -c %Y "$HOLD_FILE" 2>/dev/null || echo 0)"
  [ $(( now - mtime )) -le "$HOLD_MAX_AGE" ]
}
```

Add `HOLD_MAX_AGE="300"` to `edge_glass/systemd/sdprs-edge-update.conf` (with a one-line comment).

- [ ] **Step 4: Run test to verify it passes**

Run: `bash scripts/tests/test_edge_autoupdate.sh`
Expected: PASS — all prior cases + the new stale/`0` cases. (`bash -n scripts/edge_autoupdate.sh` clean.)

- [ ] **Step 5: Commit**

```bash
git add scripts/edge_autoupdate.sh edge_glass/systemd/sdprs-edge-update.conf scripts/tests/test_edge_autoupdate.sh
git commit -m "feat(updater): held() mtime-TTL freshness guard (stale hold self-expires)"
```

---

### Task 5: Updater — `edge_ready` health-gate + installer tmpfiles.d (`edge_autoupdate.sh`, installer)

**Files:**
- Modify: `scripts/edge_autoupdate.sh` (config :28-42; update path around restart :130-132; `health_check()` :186-200)
- Modify: `edge_glass/systemd/sdprs-edge-update.conf`
- Modify: `scripts/edge_autoupdate_install.sh` (runtime-dir step :32-40)
- Test: `scripts/tests/test_edge_autoupdate.sh` (extend; modify the systemctl stub) and `scripts/tests/test_edge_autoupdate_units.sh` (grep gate)

**Interfaces:**
- Consumes: `edge_ready` written by the edge (Task 3).
- Produces: `EDGE_READY_FILE` (default `$RUN_DIR/edge_ready`) + `REQUIRE_EDGE_READY` (default `1`) conf vars; the update path deletes `edge_ready` before restart; `health_check()` additionally waits for it to reappear within `HEALTH_TIMEOUT`. Installer writes `/etc/tmpfiles.d/sdprs.conf`.

- [ ] **Step 1: Write the failing test**

First, modify the systemctl stub in `new_sandbox` (the `case "$1" in restart)` — currently `restart` isn't handled, it just echoes) so a *healthy* restart simulates the edge coming up ready. In the `systemctl` stub heredoc, add a `restart` arm:

```bash
  restart)
    if [ -n "${STUB_EDGE_READY:-}" ]; then touch "${STUB_EDGE_READY_DIR:-/tmp}/edge_ready"; fi
    ;;
```

Then add health-gate cases (after the existing health-check block `# 7)`):

```bash
# 7b) edge_ready appears on restart -> health passes, SHA advances
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new STUB_RSYNC_ITEMIZE=">f marker" STUB_ISACTIVE=active \
    STUB_EDGE_READY=1 STUB_EDGE_READY_DIR="$SB/run" NOW_OVERRIDE=04:00
rc=$?
A "edge_ready present -> apply exits 0" "$([ $rc -eq 0 ] && echo 1)" "rc=$rc"
A "edge_ready present -> SHA advances" "$([ "$(sha)" = "new" ] && echo 1)" "$(sha)"
cleanup

# 7c) edge_ready never appears -> health FAILS -> rollback, SHA stays old
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new STUB_RSYNC_ITEMIZE=">f marker" STUB_ISACTIVE=active \
    NOW_OVERRIDE=04:00   # STUB_EDGE_READY unset => file never created
rc=$?
A "no edge_ready -> exits non-zero" "$([ $rc -ne 0 ] && echo 1)" "rc=$rc"
A "no edge_ready -> rollback (tar xzf)" "$(calls | grep -q 'tar xzf' && echo 1 || echo 0)" "$(calls)"
A "no edge_ready -> SHA stays old" "$([ "$(sha)" = "old" ] && echo 1)" "$(sha)"
cleanup

# 7d) REQUIRE_EDGE_READY=0 -> gate skipped, SHA advances without the file
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new STUB_RSYNC_ITEMIZE=">f marker" STUB_ISACTIVE=active \
    REQUIRE_EDGE_READY=0 NOW_OVERRIDE=04:00
A "gate off -> SHA advances" "$([ "$(sha)" = "new" ] && echo 1)" "$(sha)"
cleanup
```

Also, the existing case `# 6)` (apply applies) and `# 12)` (climbing NRestarts) do NOT set `STUB_EDGE_READY`, so with the gate ON they would now fail health. **Add `STUB_EDGE_READY=1 STUB_EDGE_READY_DIR="$SB/run"` to case `# 6)`'s `run` line** (it must reach a healthy state), and leave `# 7)`/`# 12)` as-is (they assert failure, which the missing edge_ready reinforces — but they fail via `STUB_ISACTIVE=failed` / climbing restarts BEFORE the edge_ready wait; verify ordering in Step 4 and, if the edge_ready wait runs first, set `STUB_EDGE_READY=1 STUB_EDGE_READY_DIR="$SB/run"` on those two so the ORIGINAL failure reason is what trips, keeping the test intent). In `test_edge_autoupdate_units.sh`, add:

```bash
grep -q 'tmpfiles.d/sdprs.conf' scripts/edge_autoupdate_install.sh \
  && ok "installer writes tmpfiles.d for /run/sdprs" \
  || bad "installer writes tmpfiles.d for /run/sdprs"
grep -q 'd /run/sdprs 0755 sdprs sdprs' scripts/edge_autoupdate_install.sh \
  && ok "tmpfiles line owns /run/sdprs as sdprs" \
  || bad "tmpfiles line owns /run/sdprs as sdprs"
```
(Match the assertion helpers already used in that file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `bash scripts/tests/test_edge_autoupdate.sh` and `bash scripts/tests/test_edge_autoupdate_units.sh`
Expected: FAIL — health gate not implemented (7c would pass health today → SHA advances → assertion fails); units grep for tmpfiles missing.

- [ ] **Step 3: Write minimal implementation**

In `scripts/edge_autoupdate.sh` config block add:

```bash
: "${EDGE_READY_FILE:=$RUN_DIR/edge_ready}"
: "${REQUIRE_EDGE_READY:=1}"
```

In `main()`, immediately before the restart (`"$SYSTEMCTL" restart "$SVC"`, :130), delete the readiness file so the wait observes the NEW process:

```bash
  rm -f "$EDGE_READY_FILE" 2>/dev/null || true
  "$SYSTEMCTL" restart "$SVC"
```

Extend `health_check()` — after the existing is-active / NRestarts poll loop returns success, wait for `edge_ready` (unless disabled):

```bash
health_check() {
  local polls base i active cur
  [ "${HEALTH_INTERVAL:-0}" -lt 1 ] && HEALTH_INTERVAL=1
  polls=$(( HEALTH_TIMEOUT / HEALTH_INTERVAL )); [ "$polls" -lt 1 ] && polls=1
  base="$("$SYSTEMCTL" show -p NRestarts --value "$SVC" 2>/dev/null || echo 0)"
  local ready=""
  for ((i=0; i<polls; i++)); do
    "$SLEEP" "$HEALTH_INTERVAL"
    active="$("$SYSTEMCTL" is-active "$SVC" 2>/dev/null || echo inactive)"
    cur="$("$SYSTEMCTL" show -p NRestarts --value "$SVC" 2>/dev/null || echo 0)"
    if [ "$active" != "active" ] || [ "$cur" -gt "$base" ]; then
      return 1
    fi
    [ -f "$EDGE_READY_FILE" ] && ready=1
    if [ "${REQUIRE_EDGE_READY:-1}" != "1" ] || [ -n "$ready" ]; then
      return 0
    fi
  done
  # Timed out: service stayed active but never asserted readiness.
  log "health-check: edge_ready ($EDGE_READY_FILE) never appeared within ${HEALTH_TIMEOUT}s"
  return 1
}
```

> This keeps the existing crash-loop/inactive failure semantics and, when `REQUIRE_EDGE_READY=1`, additionally requires the fresh `edge_ready` to appear before returning success. When `REQUIRE_EDGE_READY=0`, the first clean poll returns success as before.

Add to `edge_glass/systemd/sdprs-edge-update.conf`:
```bash
EDGE_READY_FILE="/run/sdprs/edge_ready"
REQUIRE_EDGE_READY="1"
```

In `scripts/edge_autoupdate_install.sh`, replace the bare `mkdir -p /run/sdprs` (:33) with a tmpfiles.d install so the `sdprs`-user edge can write `/run/sdprs`, reboot-safe:

```bash
# /run is tmpfs; the sdprs-user edge process writes update_hold + edge_ready
# here. tmpfiles.d recreates the dir owned by sdprs on every boot (before
# services start). --create applies it now.
cat > /etc/tmpfiles.d/sdprs.conf <<'EOF'
d /run/sdprs 0755 sdprs sdprs -
EOF
systemd-tmpfiles --create /etc/tmpfiles.d/sdprs.conf 2>/dev/null || mkdir -p /run/sdprs
echo "      installed /etc/tmpfiles.d/sdprs.conf (/run/sdprs owned by sdprs)"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bash scripts/tests/test_edge_autoupdate.sh` and `bash scripts/tests/test_edge_autoupdate_units.sh`
Expected: PASS. Confirm `# 7)` and `# 12)` still assert failure for their ORIGINAL reasons (adjust per the Step-1 note if ordering shifted). `bash -n` on both scripts clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/edge_autoupdate.sh scripts/edge_autoupdate_install.sh edge_glass/systemd/sdprs-edge-update.conf scripts/tests/test_edge_autoupdate.sh scripts/tests/test_edge_autoupdate_units.sh
git commit -m "feat(updater): edge_ready health-gate + tmpfiles.d ownership for /run/sdprs"
```

---

### Task 6: Server — persist hold fields + `send_hold_command` (`mqtt_service.py`)

**Files:**
- Modify: `central_server/services/mqtt_service.py` (`_handle_heartbeat` node_states :261-280 + metadata :283-291; new sender near `send_update_command` :554)
- Test: `central_server/tests/test_hold_command.py` (create)

**Interfaces:**
- Consumes: heartbeat now carries `update_held`, `hold_reason` (Task 1).
- Produces: `node_states[node_id]` + persisted `metadata` gain `update_held`, `hold_reason`; `MQTTService.send_hold_command(node_id: str, hold: bool, reason: Optional[str]) -> bool` publishing to `topic_cmd(node_id, "hold")`.

- [ ] **Step 1: Write the failing test**

Create `central_server/tests/test_hold_command.py` (mirror `test_update_command.py` header):

```python
import os, sys, json, threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

from central_server.services.mqtt_service import MQTTService
from shared.mqtt_topics import topic_cmd


def make_service():
    svc = MQTTService.__new__(MQTTService)
    svc._lock = threading.Lock()
    svc.node_states = {}
    svc.db = None
    svc._loop = None
    return svc


def test_heartbeat_persists_hold_fields(monkeypatch):
    svc = make_service()
    captured = {}
    monkeypatch.setattr("central_server.services.mqtt_service.upsert_node",
                        lambda nid, ntype, status, meta: captured.update(meta=meta))
    payload = json.dumps({"node_id": "glass_node_01", "status": "online",
                          "update_held": True, "hold_reason": "active_alert"})
    svc._handle_heartbeat("glass_node_01", payload)
    assert svc.node_states["glass_node_01"]["update_held"] is True
    assert svc.node_states["glass_node_01"]["hold_reason"] == "active_alert"
    assert captured["meta"]["update_held"] is True
    assert captured["meta"]["hold_reason"] == "active_alert"


def test_heartbeat_hold_defaults(monkeypatch):
    svc = make_service()
    monkeypatch.setattr("central_server.services.mqtt_service.upsert_node",
                        lambda *a, **k: None)
    svc._handle_heartbeat("glass_node_01", json.dumps({"node_id": "glass_node_01"}))
    assert svc.node_states["glass_node_01"]["update_held"] is False
    assert svc.node_states["glass_node_01"]["hold_reason"] is None


def test_send_hold_command_topic_and_payload():
    svc = make_service()
    calls = []
    svc.publish = lambda topic, payload, qos=1: calls.append((topic, payload, qos)) or True
    assert svc.send_hold_command("glass_node_01", True, "active_alert") is True
    assert calls[0][0] == topic_cmd("glass_node_01", "hold")
    assert calls[0][1]["hold"] is True
    assert calls[0][1]["reason"] == "active_alert"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest central_server/tests/test_hold_command.py -v`
Expected: FAIL — `update_held` missing from node_states; `send_hold_command` undefined.

- [ ] **Step 3: Write minimal implementation**

In `_handle_heartbeat`, add to the `node_states[node_id]` dict (after `"version": ...`, :278):

```python
                    "update_held": bool(data.get("update_held", False)),
                    "hold_reason": data.get("hold_reason"),
```

and to the `metadata` dict (after `"version": ...`, :290):

```python
                "update_held": bool(data.get("update_held", False)),
                "hold_reason": data.get("hold_reason"),
```

Add the sender after `send_update_command` (:564):

```python
    def send_hold_command(self, node_id: str, hold: bool, reason=None) -> bool:
        """Tell an edge node to hold/release SCHEDULED auto-updates. The edge
        folds this into /run/sdprs/update_hold (self-expiring via SERVER_HOLD_TTL
        if we stop re-asserting). --manual bypasses the hold regardless."""
        topic = topic_cmd(node_id, "hold")
        payload = {"hold": bool(hold), "reason": reason, "timestamp": utcnow().isoformat()}
        logger.info(f"Sending hold={hold} to {node_id} (reason={reason})")
        return self.publish(topic, payload, qos=1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest central_server/tests/test_hold_command.py central_server/tests/test_update_command.py -v`
Expected: PASS (new + existing version-persist tests green).

- [ ] **Step 5: Commit**

```bash
git add central_server/services/mqtt_service.py central_server/tests/test_hold_command.py
git commit -m "feat(server): persist update_held/hold_reason + send_hold_command"
```

---

### Task 7: Server — alert-hold reconcile + config + lifespan job

**Files:**
- Modify: `central_server/services/mqtt_service.py` (new method `reconcile_alert_holds`)
- Modify: `central_server/config.py` (settings)
- Modify: `central_server/main.py` (lifespan scheduler job ~line 140-152)
- Test: `central_server/tests/test_hold_reconcile.py` (create)

**Interfaces:**
- Consumes (Task 6): `send_hold_command(node_id, hold, reason)`; `node_states`.
- Produces: `MQTTService.reconcile_alert_holds(active_lookup: Callable[[str], bool] | None = None) -> None`. For each ONLINE glass node: `hold = active_lookup(node_id)`; send `hold=True` every tick (re-assert, refreshes edge TTL), send `hold=False` **once** on a held→clear transition, and never message a node that has never been held. `active_lookup` defaults to a real active-alert count via `event_service.list_events`.
- Settings: `HOLD_RECONCILE_ENABLED: bool = True`, `HOLD_RECONCILE_INTERVAL_S: int = 60`.

- [ ] **Step 1: Write the failing test**

Create `central_server/tests/test_hold_reconcile.py`:

```python
import os, sys, threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

from central_server.services.mqtt_service import MQTTService


def make_service(states):
    svc = MQTTService.__new__(MQTTService)
    svc._lock = threading.Lock()
    svc.node_states = states
    svc.db = None
    svc._loop = None
    svc._hold_asserted = {}
    svc._sent = []
    svc.send_hold_command = lambda nid, hold, reason=None: svc._sent.append((nid, hold, reason)) or True
    return svc


def _online_glass(nid):
    return {"type": "glass", "status": "ONLINE"}


def test_reasserts_hold_for_active_alert():
    svc = make_service({"g1": _online_glass("g1")})
    svc.reconcile_alert_holds(active_lookup=lambda nid: True)
    svc.reconcile_alert_holds(active_lookup=lambda nid: True)  # re-assert each tick
    assert svc._sent == [("g1", True, "active_alert"), ("g1", True, "active_alert")]


def test_unhold_once_on_transition():
    svc = make_service({"g1": _online_glass("g1")})
    svc.reconcile_alert_holds(active_lookup=lambda nid: True)     # -> hold
    svc._sent.clear()
    svc.reconcile_alert_holds(active_lookup=lambda nid: False)    # -> unhold once
    svc.reconcile_alert_holds(active_lookup=lambda nid: False)    # -> silent
    assert svc._sent == [("g1", False, None)]


def test_never_held_node_is_silent():
    svc = make_service({"g1": _online_glass("g1")})
    svc.reconcile_alert_holds(active_lookup=lambda nid: False)
    assert svc._sent == []


def test_offline_and_pump_nodes_skipped():
    svc = make_service({
        "g_off": {"type": "glass", "status": "OFFLINE"},
        "p1": {"type": "pump", "status": "ONLINE"},
    })
    svc.reconcile_alert_holds(active_lookup=lambda nid: True)
    assert svc._sent == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest central_server/tests/test_hold_reconcile.py -v`
Expected: FAIL — `reconcile_alert_holds` undefined.

- [ ] **Step 3: Write minimal implementation**

In `mqtt_service.py`, ensure `self._hold_asserted` exists (init it in `__init__`, or lazily in the method). Add:

```python
    def _default_active_lookup(self, node_id: str) -> bool:
        """True iff the node has an active (unresolved) alert."""
        try:
            from .event_service import list_events
            res = list_events(status_filter="PENDING_VIDEO,PENDING,ACKNOWLEDGED",
                              node_filter=node_id, page_size=1)
            return res.get("total", 0) > 0
        except Exception as e:  # DB hiccup — do not raise into the scheduler
            logger.warning(f"active-alert lookup failed for {node_id}: {e}")
            return False

    def reconcile_alert_holds(self, active_lookup=None) -> None:
        """Per online glass node, hold iff it has an unresolved alert. Re-assert
        hold=True each tick (refreshes the edge's SERVER_HOLD_TTL); send
        hold=False once on a held→clear transition; stay silent for nodes that
        were never held. Best-effort — never raises into the scheduler."""
        if active_lookup is None:
            active_lookup = self._default_active_lookup
        if getattr(self, "_hold_asserted", None) is None:
            self._hold_asserted = {}
        with self._lock:
            targets = [(nid, st) for nid, st in self.node_states.items()
                       if st.get("type") == "glass" and st.get("status") == "ONLINE"]
        for nid, _st in targets:
            try:
                want = bool(active_lookup(nid))
            except Exception as e:
                logger.warning(f"hold reconcile lookup error for {nid}: {e}")
                continue
            if want:
                self.send_hold_command(nid, True, "active_alert")
                self._hold_asserted[nid] = True
            elif self._hold_asserted.get(nid):
                self.send_hold_command(nid, False, None)
                self._hold_asserted[nid] = False
```

In `central_server/config.py`, add to the settings model (next to the `UPDATE_CHECK_*` fields):

```python
    HOLD_RECONCILE_ENABLED: bool = True
    HOLD_RECONCILE_INTERVAL_S: int = 60
```
(and the same in the non-pydantic fallback `Settings` dataclass if one exists, for parity.)

In `central_server/main.py` lifespan, after the release-check poller block (:152), add a reconcile job (guarded like the others):

```python
    # Alert-hold reconcile (Phase 3). Self-degrading; never blocks startup.
    try:
        if settings.HOLD_RECONCILE_ENABLED and getattr(app.state, "scheduler", None):
            mqtt_svc = get_mqtt_service()  # however the server exposes it in lifespan
            if mqtt_svc is not None:
                app.state.scheduler.add_job(
                    mqtt_svc.reconcile_alert_holds, "interval",
                    seconds=settings.HOLD_RECONCILE_INTERVAL_S, id="hold_reconcile")
    except Exception as e:
        logger.warning(f"Failed to start hold reconcile: {e}")
```

> Use whatever accessor the lifespan already has for the `MQTTService` instance (check how `mqtt_service` is constructed/stored in `main.py` — e.g. `app.state.mqtt_service` — and call `reconcile_alert_holds` on that instance).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest central_server/tests/test_hold_reconcile.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add central_server/services/mqtt_service.py central_server/config.py central_server/main.py central_server/tests/test_hold_reconcile.py
git commit -m "feat(server): periodic alert-hold reconcile (re-assert + unhold-on-resolve)"
```

---

### Task 8: Server — nodes API hold fields (`nodes.py`)

**Files:**
- Modify: `central_server/api/nodes.py` (`NodeStatus` model :128-131; live serialization :376-377; offline serialization :401-404; `get_node` :713-714)
- Test: `central_server/tests/test_node_hold_api.py` (create)

**Interfaces:**
- Consumes (Task 6): `node_states`/`metadata` carry `update_held`, `hold_reason`.
- Produces: `NodeStatus.update_held: Optional[bool]`, `NodeStatus.hold_reason: Optional[str]`, populated in `list_nodes` (live + offline) and `get_node`.

- [ ] **Step 1: Write the failing test**

Create `central_server/tests/test_node_hold_api.py`. Mirror the setup in the existing `test_node_update_api.py` (read its header/fixtures and follow the same client/auth construction). The assertions:

```python
# (Use the same app/test-client + auth fixtures as test_node_update_api.py.)

def test_list_nodes_exposes_hold_fields_live(client_and_state):
    client, mqtt_svc = client_and_state
    mqtt_svc.node_states["glass_node_01"] = {
        "type": "glass", "status": "ONLINE",
        "update_held": True, "hold_reason": "event_capture",
    }
    r = client.get("/api/nodes")  # add auth header exactly as test_node_update_api does
    assert r.status_code == 200
    node = next(n for n in r.json() if n["node_id"] == "glass_node_01")
    assert node["update_held"] is True
    assert node["hold_reason"] == "event_capture"


def test_offline_node_hold_defaults_none(client_and_state):
    client, _ = client_and_state
    # An offline DB-only node with no metadata hold fields -> update_held falsy,
    # hold_reason None (never raises).
    r = client.get("/api/nodes")
    assert r.status_code == 200
```

> If `test_node_update_api.py` uses a different fixture shape, copy that shape verbatim (same `sys.path`/env header, same auth). The load-bearing assertions are the two field names on a live glass node.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest central_server/tests/test_node_hold_api.py -v`
Expected: FAIL — `KeyError`/missing `update_held` in the response.

- [ ] **Step 3: Write minimal implementation**

In `NodeStatus` (after `update_available`, :131):

```python
    update_held: Optional[bool] = None
    hold_reason: Optional[str] = None
```

In the live serialization (after `update_available=...`, :377):

```python
            update_held=state.get("update_held"),
            hold_reason=state.get("hold_reason"),
```

In the offline/DB-only serialization (after `update_available=...`, :404):

```python
            update_held=(row.get("metadata") or {}).get("update_held"),
            hold_reason=(row.get("metadata") or {}).get("hold_reason"),
```

In `get_node` (after `update_available=...`, :714):

```python
        update_held=state.get("update_held"),
        hold_reason=state.get("hold_reason"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest central_server/tests/test_node_hold_api.py central_server/tests/test_node_update_api.py central_server/tests/test_nodes_api.py -v`
Expected: PASS (new + existing node API tests green).

- [ ] **Step 5: Commit**

```bash
git add central_server/api/nodes.py central_server/tests/test_node_hold_api.py
git commit -m "feat(server): expose update_held/hold_reason on the nodes API"
```

---

### Task 9: SPA — stronger held confirm on 「立即更新」(`status.jsx`, `api.jsx`, render_tests.js)

**Files:**
- Modify: `central_server/static/spa/api.jsx` (`mapNode` :472-473)
- Modify: `central_server/static/spa/pages/status.jsx` (`onUpdateNow` :516-530)
- Test: `tools/spa/render_tests.js` (new `TEST_STATUS_UPDATE_HELD` section + register in `SECTIONS` ~line 1682; extend `TEST_API` ~line 1691 for the new mapNode fields)

**Interfaces:**
- Consumes (Task 8): node JSON `update_held`, `hold_reason`.
- Produces: `mapNode` output gains `updateHeld`, `holdReason`; `holdReasonText(code)` maps `"event_capture"`→「進行中的錄製」, `"active_alert"`→「未解除的警報」, else 「監測進行中」; when `updateHeld`, 「立即更新」uses a stronger confirm naming the reason.

- [ ] **Step 1: Write the failing test**

In `tools/spa/render_tests.js`, add a section (model on `TEST_STATUS_UPDATE_NOW`, :577):

```javascript
const TEST_STATUS_UPDATE_HELD = `
window.__TEST_PROMISE = (async () => {
${PRELUDE}
  try {
    const calls = [];
    const confirmMsgs = [];
    window.SDPRS_API = {
      triggerUpdate: (id) => { calls.push(id); return Promise.resolve({ status: 'queued', node_id: id }); },
    };
    window.confirm = (msg) => { confirmMsgs.push(msg); return true; };

    // Held ONLINE glass node (mid-capture) that is also behind the tip.
    const heldNode = { id: 'CAM-1', name: '西灣橋', location: '西灣', type: 'camera', status: 'online', snoozeMin: 0, version: 'abc1234567890', updateAvailable: true, updateHeld: true, holdReason: 'event_capture' };
    // Not-held ONLINE glass node.
    const freeNode = { id: 'CAM-2', name: '大堂', location: '大堂', type: 'camera', status: 'online', snoozeMin: 0, version: 'def4567890abc', updateAvailable: true, updateHeld: false, holdReason: null };
    ReactDOM.flushSync(() => root.render(React.createElement(StatusPage, {
      nodes: [heldNode, freeNode], onSelectNode: () => {}, onRefresh: () => {},
    })));
    await settle();

    const btns = Array.from(container.querySelectorAll('button')).filter(b => b.textContent.indexOf('立即更新') !== -1);
    A('both online glass rows get a 立即更新 button', btns.length === 2, btns.length);

    // Click the HELD row's button: confirm text must name the reason + warn.
    click(btns[0]);
    await settle();
    A('held row triggers a confirm', confirmMsgs.length === 1, JSON.stringify(confirmMsgs));
    A('held confirm names the reason (進行中的錄製)', (confirmMsgs[0] || '').indexOf('進行中的錄製') !== -1, confirmMsgs[0]);
    A('held confirm warns it interrupts monitoring', (confirmMsgs[0] || '').indexOf('中斷監測') !== -1, confirmMsgs[0]);
    A('held row still calls triggerUpdate after confirm (override)', calls.length === 1 && calls[0] === 'CAM-1', JSON.stringify(calls));

    // Click the FREE row: standard confirm, no held wording.
    confirmMsgs.length = 0;
    click(btns[1]);
    await settle();
    A('free row confirm does NOT mention 中斷監測', (confirmMsgs[0] || '').indexOf('中斷監測') === -1, confirmMsgs[0]);
    A('free row calls triggerUpdate', calls.length === 2 && calls[1] === 'CAM-2', JSON.stringify(calls));
  } catch (e) {
    results.push({ name: 'status update-held suite threw', pass: false, detail: e && e.stack ? e.stack.split('\\n').slice(0, 3).join(' | ') : String(e) });
  }
  window.__TEST_RESULT = results;
})();
`;
```

Register it in the `SECTIONS` array (after the `TEST_STATUS_UPDATE_NOW` entry, :1682):

```javascript
  { name: 'Phase 3                     status.jsx (update-held confirm)', deps: ['icons.jsx', 'data.jsx', 'components.jsx'], target: 'pages/status.jsx', test: TEST_STATUS_UPDATE_HELD },
```

In the existing `TEST_API` section (mapNode assertions, ~:1691), add assertions that `mapNode` surfaces the new fields (follow that section's existing assertion style):

```javascript
    // Phase 3: hold state flows through mapNode.
    const hn = api.__mapNodeForTest ? api.__mapNodeForTest({ node_id: 'g1', update_held: true, hold_reason: 'active_alert' }) : null;
```
> If `TEST_API` reaches `mapNode` a different way (it may call the real `mapNode` via a data-layer entry, not a `__mapNodeForTest` shim), use that same access path — assert `updateHeld === true` and `holdReason === 'active_alert'` on the mapped object. Do not invent a new export; match how `version`/`updateAvailable` are already asserted there.

- [ ] **Step 2: Run test to verify it fails**

Run: `NODE_PATH=<main-checkout>/tools/spa/node_modules node tools/spa/run_all.js`
Expected: FAIL — held confirm wording assertions fail (no held-specific confirm yet); `updateHeld`/`holdReason` undefined from `mapNode`.

- [ ] **Step 3: Write minimal implementation**

In `api.jsx` `mapNode` (after `updateAvailable:`, :473):

```javascript
      updateHeld: (n.update_held ?? null),
      holdReason: (n.hold_reason ?? null),
```

In `status.jsx`, add a reason mapper near the top of the component (or module scope) and use a stronger confirm when held. Replace the single confirm line in `onUpdateNow` (:526):

```javascript
    const holdReasonText = (code) =>
      code === 'event_capture' ? '進行中的錄製'
      : code === 'active_alert' ? '未解除的警報'
      : '監測進行中';
    const msg = target.updateHeld
      ? `此節點目前有${holdReasonText(target.holdReason)}，立即更新會中斷監測並中止進行中的作業。\n仍要立即更新節點「${target.name || target.id}」嗎？`
      : `確定要立即更新節點「${target.name || target.id}」？\n節點會在背景更新（快照 → 健康檢查 → 失敗自動回滾），完成後於下次心跳回報新版本。`;
    if (!window.confirm(msg)) return;
```

(Keep the rest of `onUpdateNow` — the `api.triggerUpdate(...)` call and toasts — unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `NODE_PATH=<main-checkout>/tools/spa/node_modules node tools/spa/run_all.js`
Expected: PASS — the new `TEST_STATUS_UPDATE_HELD` section + extended `TEST_API`, and all prior sections (incl. `TEST_STATUS_UPDATE_NOW`) still green.

- [ ] **Step 5: Commit**

```bash
git add central_server/static/spa/api.jsx central_server/static/spa/pages/status.jsx tools/spa/render_tests.js
git commit -m "feat(spa): stronger 立即更新 confirm when node update is held"
```

---

## Final integration verification (after all tasks)

- [ ] Edge suite: from `edge_glass/`, `python -m pytest tests/ -q` — all green.
- [ ] Server suite: from repo root, `python -m pytest central_server/tests/ -q` — all green (each new test file also passes ALONE, per the per-suite isolation trap).
- [ ] Bash: `bash scripts/tests/test_edge_autoupdate.sh` and `bash scripts/tests/test_edge_autoupdate_units.sh` — all pass; `bash -n scripts/edge_autoupdate.sh scripts/edge_autoupdate_install.sh` clean.
- [ ] SPA: `NODE_PATH=<main-checkout>/tools/spa/node_modules node tools/spa/run_all.js` — all sections green.
- [ ] Banned-string scan on the full diff: `git diff ef098be..HEAD | grep -nE 'Msc@2333|MSC-Person|broker\.emqx\.io'` returns nothing in CODE (the spec/plan docs may contain them only as the prohibition-rule text, matching prior shipped docs).
- [ ] Whole-branch review (subagent-driven-development's final review), then finishing-a-development-branch. **No push to `origin/main` / publish to `edge-release` without the user's literal "approved".**

## Self-review notes (author)
- **Spec coverage:** §4 hold file→T1+T4; §5 local capture→T2; §6 server hold (command+sender+reconcile)→T6+T7; §7 heartbeat surface→T1(edge)+T6(server)+T8(api); §8 SPA confirm→T9; §9 readiness→T3(edge)+T5(updater); §10 runtime-dir→T5. All covered.
- **Type consistency:** `hold_reason` is the English code `"event_capture"`/`"active_alert"`/`null` end-to-end (edge emits, server passes through, SPA maps to zh-TW). `set_server_hold(hold, reason)` / `set_local_capture_hold(active, reason)` / `send_hold_command(node_id, hold, reason)` / `reconcile_alert_holds(active_lookup)` names are used identically across tasks.
- **Refinement vs spec §9.2:** the spec described the readiness freshness as "mtime newer than restart"; this plan implements the equivalent-but-simpler **updater deletes `edge_ready` before restart, then health_check waits for it to reappear** (Task 5) — no mtime arithmetic, directly testable in the black-box harness, and strictly at least as safe (a present file after the delete is provably the new process's). Same intent, cleaner mechanism.
