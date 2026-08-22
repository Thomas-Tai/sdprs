# Edge auto-update Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each Pi's deployed software version on the dashboard (with an "update available" badge) and add a per-node "Update now" button that triggers an immediate `--manual` update.

**Architecture:** Version rides the existing heartbeat (edge → server `node_states`/`metadata` → nodes API → SPA). "Update now" rides the existing (currently-stubbed) `update` MQTT command: SPA button → `POST /api/nodes/{id}/update` → server publish to `topic_cmd_update` → edge `handle_update` launches a new `sdprs-edge-update-manual.service` oneshot via a narrow sudoers rule. A server-side poller learns the `edge-release` tip from the GitHub API to compute "update available".

**Tech Stack:** Python 3 (edge firmware + FastAPI server), bash + systemd (Pi), React (no-build-step JSX SPA), MQTT (paho), httpx, apscheduler, pytest, jsdom.

**Spec:** `docs/superpowers/specs/2026-08-22-edge-autoupdate-phase2-design.md`

## Global Constraints

- No hardcoded credentials. The literal strings `Msc@2333`, `MSC-Person`, `broker.emqx.io` must NEVER appear in any diff (banned-string scan before every commit).
- User-facing SPA strings are **zh-TW Traditional Chinese**; on-node/journal/log strings stay English (matching `deploy_console.sh` and existing edge logs).
- All server timestamps are **naive UTC** via `central_server.timeutil.utcnow()` — never `datetime.utcnow()` or tz-aware.
- `version` on the wire is the **full 40-char SHA string** (or `null`); the SPA displays the first 7. "update available" compares full SHA to full SHA.
- The sudoers rule and the edge's `sudo systemctl` invocation MUST reference the **same absolute `systemctl` path** (default `/usr/bin/systemctl`), or sudo denies and the trigger silently fails.
- Edge tests import as `from comms.mqtt_client import ...` and run with `edge_glass/` on `sys.path` (pytest is invoked from `edge_glass/`). Server tests `sys.path.insert(0, <repo root>)` and import `central_server.services...`, setting `DASHBOARD_USER`/`DASHBOARD_PASS`/`EDGE_API_KEY`/`SECRET_KEY` env (see `tests/test_cmd_topics.py` header).
- SPA has **no build step**; the jsdom test suite needs `NODE_PATH` → the main checkout's `tools/spa/node_modules` (this worktree has none).
- This phase adds **no** active-alert hold source and **no** readiness check (Phase 3).

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `edge_glass/comms/mqtt_client.py` (modify) | Read `.edge_deployed_sha`; add `version` to heartbeat | 1 |
| `edge_glass/tests/test_mqtt_version.py` (create) | Version read + heartbeat-inclusion tests | 1 |
| `edge_glass/systemd/sdprs-edge-update-manual.service` (create) | On-demand `--manual` oneshot unit | 2 |
| `scripts/edge_autoupdate_install.sh` (modify) | Install manual unit + visudo-gated sudoers | 2 |
| `scripts/tests/test_edge_autoupdate_units.sh` (modify) | Grep gate for manual unit + installer sudoers logic | 2 |
| `edge_glass/edge_glass_main.py` (modify) | Fill `handle_update` → launch manual unit (`--no-block`) | 3 |
| `edge_glass/tests/test_update_trigger.py` (create) | `trigger_manual_update` argv + exception-swallow tests | 3 |
| `central_server/services/mqtt_service.py` (modify) | Persist `version`; `send_update_command` | 4 |
| `central_server/tests/test_update_command.py` (create) | version persisted + `send_update_command` topic | 4 |
| `central_server/services/release_check.py` (create) | edge-release tip poller + `compute_update_available` | 5 |
| `central_server/config.py` (modify) | `UPDATE_CHECK_*` settings | 5 |
| `central_server/main.py` (modify) | Start/stop the poller in lifespan | 5 |
| `central_server/tests/test_release_check.py` (create) | Pure compare fn + stubbed-fetch poller tests | 5 |
| `central_server/api/nodes.py` (modify) | `NodeStatus` fields; serialize; `POST /nodes/{id}/update` | 6 |
| `central_server/tests/test_node_update_api.py` (create) | Endpoint guards + serialization | 6 |
| `central_server/static/spa/api.jsx` (modify) | `mapNode` version fields; `triggerUpdate` action | 7 |
| `central_server/static/spa/pages/status.jsx` (modify) | Version badge + "Update now" button + confirm/toast | 7 |
| `central_server/static/spa/__tests__/node_update.test.jsx` (create) | jsdom render + action tests | 7 |

---

### Task 1: Edge — report `version` in the heartbeat

**Files:**
- Modify: `edge_glass/comms/mqtt_client.py` (heartbeat build at `_publish_heartbeat`, ~line 289; constructor `__init__`)
- Test: `edge_glass/tests/test_mqtt_version.py`

**Interfaces:**
- Produces: heartbeat JSON now carries `"version": <full-sha-str-or-None>`. Module fn `_read_deployed_version(path: str) -> Optional[str]`.

- [ ] **Step 1: Write the failing test**

Create `edge_glass/tests/test_mqtt_version.py`:

```python
"""版本回報（deployed SHA）單元測試：讀取 marker + 心跳帶入 version。
不連線 broker、不相依 paho —— 建構時跳過 _init_client 並注入假 client。"""
import json
from unittest import mock

from comms.mqtt_client import MQTTClient, _read_deployed_version


class _FakeClient:
    def __init__(self):
        self.last_payload = None

    def publish(self, topic, payload, qos=0):
        self.last_payload = payload


def _make_client():
    config = {"node_id": "glass_node_01",
              "server": {"mqtt_broker": "localhost", "mqtt_port": 1883}}
    with mock.patch("comms.mqtt_client.PAHO_AVAILABLE", True), \
            mock.patch.object(MQTTClient, "_init_client", lambda self: None):
        client = MQTTClient(config)
    client._client = _FakeClient()
    return client


def test_read_deployed_version_present(tmp_path):
    f = tmp_path / "sha"
    f.write_text("723456f76fec578f9af85d6ecc460896cba38254\n")
    assert _read_deployed_version(str(f)) == "723456f76fec578f9af85d6ecc460896cba38254"


def test_read_deployed_version_missing(tmp_path):
    assert _read_deployed_version(str(tmp_path / "nope")) is None


def test_read_deployed_version_blank(tmp_path):
    f = tmp_path / "sha"
    f.write_text("   \n")
    assert _read_deployed_version(str(f)) is None


def test_heartbeat_includes_version_key():
    client = _make_client()
    client._publish_heartbeat()
    payload = json.loads(client._client.last_payload)
    assert "version" in payload  # key always present (value may be None)


def test_heartbeat_reports_set_version():
    client = _make_client()
    client._version = "deadbeefcafe"
    client._publish_heartbeat()
    payload = json.loads(client._client.last_payload)
    assert payload["version"] == "deadbeefcafe"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd edge_glass && python -m pytest tests/test_mqtt_version.py -v`
Expected: FAIL — `ImportError: cannot import name '_read_deployed_version'`.

- [ ] **Step 3: Implement**

In `edge_glass/comms/mqtt_client.py`, add near the top (after imports):

```python
import os


def _read_deployed_version(path: str):
    """Return the deployed commit SHA from the marker file, or None.

    Must never raise: a missing/unreadable marker (a node bootstrapped before
    Phase 2, or a dev run) simply reports no version rather than breaking the
    heartbeat.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            sha = fh.read().strip()
        return sha or None
    except Exception:
        return None
```

In `MQTTClient.__init__` (where other instance attrs are set), add:

```python
        # Deployed software version (full edge-release SHA) for the dashboard.
        # Read once at startup — it only changes on an update, which restarts
        # this process. Path overridable for tests.
        self._version = _read_deployed_version(
            os.environ.get("EDGE_DEPLOYED_SHA_FILE", "/opt/sdprs/.edge_deployed_sha")
        )
```

In `_publish_heartbeat`, add to the `heartbeat_data` dict (alongside `mac`):

```python
            # Deployed edge-release SHA so the dashboard can show each Pi's
            # version + whether an update is available (Phase 2). None until a
            # Phase-1+ node has a marker file.
            "version": self._version,
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd edge_glass && python -m pytest tests/test_mqtt_version.py tests/test_mqtt_heartbeat.py -v`
Expected: PASS (new file + existing heartbeat tests still green).

- [ ] **Step 5: Commit**

```bash
git add edge_glass/comms/mqtt_client.py edge_glass/tests/test_mqtt_version.py
git commit -m "feat(edge): report deployed version in heartbeat (Phase 2)"
```

---

### Task 2: Edge — manual update unit + sudoers + installer

**Files:**
- Create: `edge_glass/systemd/sdprs-edge-update-manual.service`
- Modify: `scripts/edge_autoupdate_install.sh`
- Test: `scripts/tests/test_edge_autoupdate_units.sh` (extend the grep gate)

**Interfaces:**
- Produces: unit name `sdprs-edge-update-manual.service` (started by Task 3's edge handler); sudoers file `/etc/sudoers.d/sdprs-edge-update` granting `sdprs` exactly `<systemctl> start --no-block sdprs-edge-update-manual.service`.

- [ ] **Step 1: Write the failing test (extend the grep gate)**

Append to `scripts/tests/test_edge_autoupdate_units.sh` (before its final PASS/FAIL summary; reuse its existing `A`/assert helper and `FAIL`/`PASS` counters — match the file's current style exactly):

```bash
# --- Phase 2: manual on-demand unit ---
MANUAL="$REPO_ROOT/edge_glass/systemd/sdprs-edge-update-manual.service"
A "manual unit is oneshot"        "$(grep -c '^Type=oneshot' "$MANUAL")"        1
A "manual unit runs --manual"     "$(grep -c 'edge_autoupdate.sh --manual' "$MANUAL")" 1
A "manual unit has NO [Install]"  "$(grep -c '^\[Install\]' "$MANUAL")"          0

# --- Phase 2: installer wires sudoers (visudo-gated) + installs manual unit ---
INST="$REPO_ROOT/scripts/edge_autoupdate_install.sh"
A "installer installs manual unit"  "$(grep -c 'sdprs-edge-update-manual.service' "$INST")" 1
A "installer writes sudoers drop-in" "$(grep -c '/etc/sudoers.d/sdprs-edge-update' "$INST")" 1
A "installer validates with visudo"  "$(grep -c 'visudo -cf' "$INST")"           1
```

(Adjust `REPO_ROOT`/assert-helper names to whatever the file already defines; if it uses literal counts differently, match that. The point: 6 new non-vacuous assertions.)

- [ ] **Step 2: Run to verify it fails**

Run: `bash scripts/tests/test_edge_autoupdate_units.sh`
Expected: FAIL — new assertions fail (manual unit + installer lines don't exist yet).

- [ ] **Step 3a: Create the manual unit**

Create `edge_glass/systemd/sdprs-edge-update-manual.service`:

```ini
[Unit]
Description=SDPRS edge glass on-demand update (dashboard "Update now")
# On-demand only: started by edge_glass via sudo, or by an operator. NO timer,
# NO [Install] — the nightly cadence is sdprs-edge-update.timer.

[Service]
Type=oneshot
User=root
ExecStart=/opt/sdprs/edge_autoupdate.sh --manual
SyslogIdentifier=sdprs-edge-update
TimeoutStartSec=600
```

- [ ] **Step 3b: Extend the installer**

In `scripts/edge_autoupdate_install.sh`, in the unit-install section (where `sdprs-edge-update.service`/`.timer` are installed), add the manual unit install:

```bash
install -m 0644 "$TMP/edge_glass/systemd/sdprs-edge-update-manual.service" /etc/systemd/system/sdprs-edge-update-manual.service
```

Then add a sudoers step (before `daemon-reload`), resolving the systemctl path once and validating before install:

```bash
echo "[*] install narrow sudoers for dashboard 'Update now'"
SYSTEMCTL_BIN="$(command -v systemctl || echo /usr/bin/systemctl)"
SUDOERS_TMP="$(mktemp)"
# Grant the sdprs user exactly one command: start the on-demand update unit.
printf 'sdprs ALL=(root) NOPASSWD: %s start --no-block sdprs-edge-update-manual.service\n' "$SYSTEMCTL_BIN" > "$SUDOERS_TMP"
if visudo -cf "$SUDOERS_TMP" >/dev/null 2>&1; then
  install -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/sdprs-edge-update
  echo "      wrote /etc/sudoers.d/sdprs-edge-update ($SYSTEMCTL_BIN)"
else
  echo "!! sudoers validation FAILED — NOT installing (dashboard Update-now will be unavailable)" >&2
fi
rm -f "$SUDOERS_TMP"
```

(The installer already runs `systemctl daemon-reload` after unit installs — the new oneshot needs no enable.)

- [ ] **Step 4: Run to verify it passes**

Run: `bash scripts/tests/test_edge_autoupdate_units.sh && bash -n scripts/edge_autoupdate_install.sh`
Expected: grep gate PASS (all assertions), `bash -n` clean. (If WSL is available: `wsl.exe -d Ubuntu -u root -- bash -lc 'systemd-analyze verify …'` on the manual unit, and `visudo -cf` on a rendered sudoers line — both clean.)

- [ ] **Step 5: Commit**

```bash
git add edge_glass/systemd/sdprs-edge-update-manual.service scripts/edge_autoupdate_install.sh scripts/tests/test_edge_autoupdate_units.sh
git commit -m "feat(edge): on-demand manual update unit + narrow sudoers (Phase 2)"
```

---

### Task 3: Edge — fill the `handle_update` stub

**Files:**
- Modify: `edge_glass/edge_glass_main.py` (`handle_update`, ~line 394)
- Test: `edge_glass/tests/test_update_trigger.py`

**Interfaces:**
- Consumes: unit name `sdprs-edge-update-manual.service` (Task 2).
- Produces: module fn `trigger_manual_update(runner=subprocess.run) -> bool` in `edge_glass_main`; constants `SYSTEMCTL_BIN`, `MANUAL_UPDATE_UNIT`.

- [ ] **Step 1: Write the failing test**

Create `edge_glass/tests/test_update_trigger.py`:

```python
"""Update-now trigger: launches the manual systemd unit with --no-block and
never lets a launch error escape (it runs on the MQTT callback thread)."""
import edge_glass_main as m


def test_trigger_builds_correct_argv():
    seen = {}

    def fake_runner(argv, **kwargs):
        seen["argv"] = argv
        class R:  # minimal CompletedProcess stand-in
            returncode = 0
        return R()

    assert m.trigger_manual_update(runner=fake_runner) is True
    argv = seen["argv"]
    assert argv[0] == "sudo"
    assert m.SYSTEMCTL_BIN in argv
    assert "start" in argv and "--no-block" in argv
    assert m.MANUAL_UPDATE_UNIT == "sdprs-edge-update-manual.service"
    assert argv[-1] == m.MANUAL_UPDATE_UNIT


def test_trigger_swallows_exception():
    def boom(argv, **kwargs):
        raise OSError("sudo missing")

    # Must return False, not raise — the MQTT dispatch thread must survive.
    assert m.trigger_manual_update(runner=boom) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd edge_glass && python -m pytest tests/test_update_trigger.py -v`
Expected: FAIL — `AttributeError: module 'edge_glass_main' has no attribute 'trigger_manual_update'`.

- [ ] **Step 3: Implement**

In `edge_glass/edge_glass_main.py`, add near the top (module scope, after imports):

```python
import shutil
import subprocess

# Absolute systemctl path MUST match the path in the installer's sudoers rule
# (scripts/edge_autoupdate_install.sh) or sudo denies the call.
SYSTEMCTL_BIN = shutil.which("systemctl") or "/usr/bin/systemctl"
MANUAL_UPDATE_UNIT = "sdprs-edge-update-manual.service"


def trigger_manual_update(runner=subprocess.run) -> bool:
    """Launch the on-demand update unit. Fire-and-forget: --no-block returns
    immediately so the 60s health-check never blocks the caller (the MQTT
    dispatch thread). Never raises — returns False on any launch failure."""
    argv = ["sudo", SYSTEMCTL_BIN, "start", "--no-block", MANUAL_UPDATE_UNIT]
    try:
        result = runner(argv, capture_output=True, text=True, timeout=15)
        ok = getattr(result, "returncode", 1) == 0
        if not ok:
            logger.error("manual update launch failed rc=%s stderr=%s",
                         getattr(result, "returncode", "?"),
                         getattr(result, "stderr", ""))
        return ok
    except Exception as e:  # sudo missing, timeout, etc.
        logger.error("manual update launch error: %s", e)
        return False
```

Replace the `handle_update` body (currently the `# TODO` stub) with:

```python
        def handle_update(payload):
            """Dashboard 'Update now': launch the on-demand updater."""
            logger.info(f"Update command received: {payload}")
            trigger_manual_update()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd edge_glass && python -m pytest tests/test_update_trigger.py tests/test_main_helpers.py -v`
Expected: PASS (new tests + existing main helpers green).

- [ ] **Step 5: Commit**

```bash
git add edge_glass/edge_glass_main.py edge_glass/tests/test_update_trigger.py
git commit -m "feat(edge): handle_update launches manual update unit (Phase 2)"
```

---

### Task 4: Server — persist `version` + `send_update_command`

**Files:**
- Modify: `central_server/services/mqtt_service.py` (`_handle_heartbeat` node_states ~261 + metadata ~282; new sender near `send_stream_command` ~535)
- Test: `central_server/tests/test_update_command.py`

**Interfaces:**
- Consumes: heartbeat payload `version` (Task 1); `topic_cmd` (already imported).
- Produces: `node_states[nid]["version"]` + persisted metadata `version`; `MQTTService.send_update_command(node_id: str) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `central_server/tests/test_update_command.py`:

```python
import os
import sys
import json
import threading
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


def test_heartbeat_persists_version(monkeypatch):
    svc = make_service()
    captured = {}
    monkeypatch.setattr("central_server.services.mqtt_service.upsert_node",
                        lambda nid, ntype, status, meta: captured.update(meta=meta))
    payload = json.dumps({"node_id": "glass_node_01", "status": "online",
                          "version": "723456fdeadbeef"})
    svc._handle_heartbeat("glass_node_01", payload)
    assert svc.node_states["glass_node_01"]["version"] == "723456fdeadbeef"
    assert captured["meta"]["version"] == "723456fdeadbeef"


def test_heartbeat_missing_version_is_none(monkeypatch):
    svc = make_service()
    monkeypatch.setattr("central_server.services.mqtt_service.upsert_node",
                        lambda *a, **k: None)
    svc._handle_heartbeat("glass_node_01", json.dumps({"node_id": "glass_node_01"}))
    assert svc.node_states["glass_node_01"]["version"] is None


def test_send_update_command_uses_canonical_topic():
    svc = make_service()
    calls = []
    svc.publish = lambda topic, payload, qos=1: calls.append((topic, payload, qos)) or True
    assert svc.send_update_command("glass_node_01") is True
    assert calls[0][0] == topic_cmd("glass_node_01", "update")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest central_server/tests/test_update_command.py -v`
Expected: FAIL — `send_update_command` missing; `version` not in node_states/metadata.

- [ ] **Step 3: Implement**

In `_handle_heartbeat`, add to the `node_states[node_id]` dict (~line 278, alongside `mac`):

```python
                    "version": data.get("version"),
```

and to the persisted `metadata` dict (~line 288, after `uptime_seconds`):

```python
                "version": data.get("version"),
```

Add the sender after `send_stream_command`:

```python
    def send_update_command(self, node_id: str) -> bool:
        """Trigger an immediate --manual update on a glass edge node.

        Publishes to sdprs/edge/{node_id}/cmd/update; the edge's handle_update
        launches sdprs-edge-update-manual.service. Fire-and-forget — the node
        reports its new version on the next heartbeat once the update finishes.
        """
        topic = topic_cmd(node_id, "update")
        payload = {"timestamp": utcnow().isoformat()}
        logger.info(f"Sending update command to {node_id}")
        return self.publish(topic, payload, qos=1)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest central_server/tests/test_update_command.py central_server/tests/test_cmd_topics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add central_server/services/mqtt_service.py central_server/tests/test_update_command.py
git commit -m "feat(server): persist node version + send_update_command (Phase 2)"
```

---

### Task 5: Server — edge-release tip poller + `update_available`

**Files:**
- Create: `central_server/services/release_check.py`
- Modify: `central_server/config.py` (Settings: add `UPDATE_CHECK_*`), `central_server/main.py` (lifespan)
- Test: `central_server/tests/test_release_check.py`

**Interfaces:**
- Produces: `compute_update_available(node_version, tip_sha) -> Optional[bool]`; `ReleaseCheckService` with `async refresh()`, `tip_sha` attr; `init_release_check_service(settings)`, `get_release_check_service()`.

- [ ] **Step 1: Write the failing test**

Create `central_server/tests/test_release_check.py`:

```python
import sys
import asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from central_server.services.release_check import (
    compute_update_available, ReleaseCheckService,
)


def test_compute_unknown_when_version_missing():
    assert compute_update_available(None, "abc") is None

def test_compute_unknown_when_tip_missing():
    assert compute_update_available("abc", None) is None

def test_compute_up_to_date():
    assert compute_update_available("abc123", "abc123") is False

def test_compute_update_available():
    assert compute_update_available("old111", "new222") is True


class _FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
    def json(self):
        return self._body
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc = resp, exc
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, url, **kw):
        if self._exc:
            raise self._exc
        return self._resp


def test_refresh_caches_tip(monkeypatch):
    svc = ReleaseCheckService(owner="o", repo="r", branch="edge-release")
    resp = _FakeResp(200, {"object": {"sha": "cafef00d"}})
    monkeypatch.setattr("central_server.services.release_check.httpx.AsyncClient",
                        lambda *a, **k: _FakeClient(resp=resp))
    asyncio.run(svc.refresh())
    assert svc.tip_sha == "cafef00d"


def test_refresh_keeps_last_on_failure(monkeypatch):
    svc = ReleaseCheckService(owner="o", repo="r", branch="edge-release")
    svc.tip_sha = "known"
    monkeypatch.setattr("central_server.services.release_check.httpx.AsyncClient",
                        lambda *a, **k: _FakeClient(exc=RuntimeError("network")))
    asyncio.run(svc.refresh())  # must not raise
    assert svc.tip_sha == "known"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest central_server/tests/test_release_check.py -v`
Expected: FAIL — module `release_check` does not exist.

- [ ] **Step 3a: Create the service**

Create `central_server/services/release_check.py`:

```python
"""Poll the edge-release branch tip (GitHub API) so the dashboard can show
whether each node has an update available. Best-effort: any fetch failure
keeps the last-known tip (or None) and never raises into a request."""
import logging
from typing import Optional

import httpx

logger = logging.getLogger("release_check")

_HTTP_TIMEOUT_S = 10.0
_service: Optional["ReleaseCheckService"] = None


def compute_update_available(node_version: Optional[str], tip_sha: Optional[str]) -> Optional[bool]:
    """None = unknown (either side missing); False = up to date; True = behind."""
    if not node_version or not tip_sha:
        return None
    return node_version != tip_sha


class ReleaseCheckService:
    def __init__(self, owner: str, repo: str, branch: str = "edge-release",
                 enabled: bool = True):
        self.owner, self.repo, self.branch = owner, repo, branch
        self.enabled = enabled
        self.tip_sha: Optional[str] = None

    @property
    def _url(self) -> str:
        return (f"https://api.github.com/repos/{self.owner}/{self.repo}"
                f"/git/refs/heads/{self.branch}")

    async def refresh(self) -> None:
        if not self.enabled:
            return
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(self._url, timeout=_HTTP_TIMEOUT_S,
                                     headers={"Accept": "application/vnd.github+json"})
                r.raise_for_status()
                sha = (r.json() or {}).get("object", {}).get("sha")
                if sha:
                    self.tip_sha = sha
                    logger.debug("edge-release tip: %s", sha)
        except Exception as e:  # network, rate-limit, malformed — keep last-known
            logger.warning("release-check refresh failed (keeping last-known): %s", e)


def init_release_check_service(settings) -> ReleaseCheckService:
    global _service
    owner, _, repo = getattr(settings, "UPDATE_RELEASE_REPO", "Thomas-Tai/sdprs").partition("/")
    _service = ReleaseCheckService(
        owner=owner, repo=repo,
        branch=getattr(settings, "UPDATE_RELEASE_BRANCH", "edge-release"),
        enabled=getattr(settings, "UPDATE_CHECK_ENABLED", True),
    )
    return _service


def get_release_check_service() -> Optional["ReleaseCheckService"]:
    return _service
```

- [ ] **Step 3b: Settings**

In `central_server/config.py` Settings (follow the `CWA_API_KEY` field pattern, ~line 115):

```python
    UPDATE_CHECK_ENABLED: bool = True
    UPDATE_CHECK_INTERVAL_S: int = 300   # ~12 req/hr, under GitHub's 60/hr
    UPDATE_RELEASE_REPO: str = "Thomas-Tai/sdprs"
    UPDATE_RELEASE_BRANCH: str = "edge-release"
```

- [ ] **Step 3c: Lifespan wiring**

In `central_server/main.py` lifespan (after the weather/lightning blocks, ~line 138), start the poller on the existing scheduler:

```python
    # Release-check poller (Phase 2 "update available"). Self-degrading.
    try:
        from .services.release_check import init_release_check_service, get_release_check_service
        rc_svc = init_release_check_service(settings)
        if rc_svc.enabled and getattr(app.state, "scheduler", None):
            await rc_svc.refresh()  # prime once at startup
            app.state.scheduler.add_job(rc_svc.refresh, "interval",
                                        seconds=settings.UPDATE_CHECK_INTERVAL_S,
                                        id="release_check")
        app.state.release_check = rc_svc
    except Exception as e:
        logger.warning(f"Failed to start release-check poller: {e}")
        app.state.release_check = None
```

(No explicit shutdown needed — the scheduler is stopped in the existing shutdown block.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest central_server/tests/test_release_check.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add central_server/services/release_check.py central_server/config.py central_server/main.py central_server/tests/test_release_check.py
git commit -m "feat(server): edge-release tip poller + update_available (Phase 2)"
```

---

### Task 6: Server API — `NodeStatus` fields + serialize + `POST /nodes/{id}/update`

**Files:**
- Modify: `central_server/api/nodes.py` (`NodeStatus` model ~47; `list_nodes` loop ~310 + DB-fallback ~373; `get_node` ~581; new endpoint near `pump_command` ~969)
- Test: `central_server/tests/test_node_update_api.py`

**Interfaces:**
- Consumes: `state.get("version")` (Task 4), `db_row["metadata"]["version"]`, `get_release_check_service()` + `compute_update_available` (Task 5), `mqtt_service.send_update_command` (Task 4).
- Produces: `NodeStatus.version`, `NodeStatus.update_available`; `POST /api/nodes/{node_id}/update` → 202.

- [ ] **Step 1: Write the failing test**

Create `central_server/tests/test_node_update_api.py` (mirror `tests/test_nodes_api.py`'s app/client + auth-override fixture; the essential new assertions):

```python
# Follows tests/test_nodes_api.py for app construction + get_current_user override.
# Asserts: version + update_available serialized; POST /update guards + publishes.
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "testpass123")
os.environ.setdefault("EDGE_API_KEY", "test-api-key-12345")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

# --- Pure serialization intent (documents the required behavior) ---
from central_server.services.release_check import compute_update_available

def test_update_available_helper_contract():
    assert compute_update_available("a", "a") is False
    assert compute_update_available("a", "b") is True
    assert compute_update_available(None, "b") is None

# --- Endpoint tests (build the app with an online glass node in a fake mqtt
# service and get_current_user overridden; see test_nodes_api.py). Assert:
#   * POST /api/nodes/{id}/update for an ONLINE glass node -> 202 and
#     send_update_command called with that node_id
#   * offline node -> 409
#   * pump/unknown node -> 400/404
# Implement using the same TestClient + dependency_overrides pattern as
# test_nodes_api.py; the reviewer verifies the guards + the send call.
```

Implement the endpoint tests concretely against `tests/test_nodes_api.py`'s harness (that file's `client` fixture + `app.dependency_overrides[get_current_user]`). The three endpoint cases (202 + send called; 409 offline; 400/404 wrong-type) are required, not optional.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest central_server/tests/test_node_update_api.py -v`
Expected: FAIL — endpoint 404 (route missing); fields absent.

- [ ] **Step 3a: Add model fields**

In `NodeStatus` (after `last_pump_command`, ~line 126):

```python
    # Phase 2: deployed edge-release SHA (glass nodes) and whether the node is
    # behind the edge-release tip. update_available is None ("unknown") when
    # either the node's version or the release tip is unknown.
    version: Optional[str] = None
    update_available: Optional[bool] = None
```

- [ ] **Step 3b: Serialize in `list_nodes`**

At the top of `list_nodes` (after `node_states = ...`), resolve the tip once:

```python
    from ..services.release_check import get_release_check_service, compute_update_available
    _rc = get_release_check_service()
    _tip = _rc.tip_sha if _rc else None
```

In the `node_states` loop's `NodeStatus(...)` (add kwargs):

```python
            version=state.get("version"),
            update_available=compute_update_available(state.get("version"), _tip),
```

In the DB-only fallback `NodeStatus(...)` (offline nodes; `row["metadata"]` is a parsed dict — see `database.get_all_nodes`):

```python
            version=(row.get("metadata") or {}).get("version"),
            update_available=compute_update_available((row.get("metadata") or {}).get("version"), _tip),
```

Apply the same two kwargs in `get_node` (~line 581).

- [ ] **Step 3c: Add the endpoint**

After `pump_command` (~line 1018), mirroring its auth/guards:

```python
@router.post("/nodes/{node_id}/update", status_code=202)
async def trigger_node_update(
    node_id: str,
    user: str = Depends(get_current_user),
) -> Dict[str, Any]:
    """Dashboard 'Update now': trigger an immediate --manual update on a glass
    node. Fire-and-forget over MQTT; the node reports its new version on the
    next heartbeat. Refused for offline nodes (can't receive the command) and
    non-glass nodes."""
    node = db_get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    if (node.get("node_type") or "").lower() != "glass":
        raise HTTPException(status_code=400,
                            detail=f"Node {node_id} is not a glass node (type={node.get('node_type')!r})")
    mqtt_service = get_mqtt_service()
    if not mqtt_service:
        raise HTTPException(status_code=503, detail="MQTT service not available")
    state = mqtt_service.get_node_state(node_id)
    if not state or state.get("status") != "ONLINE":
        raise HTTPException(status_code=409, detail=f"Node {node_id} is offline")
    ok = mqtt_service.send_update_command(node_id)
    if not ok:
        raise HTTPException(status_code=502, detail="Failed to publish update command")
    return {"status": "queued", "node_id": node_id}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest central_server/tests/test_node_update_api.py central_server/tests/test_nodes_api.py -v`
Expected: PASS (new + existing node API tests).

- [ ] **Step 5: Commit**

```bash
git add central_server/api/nodes.py central_server/tests/test_node_update_api.py
git commit -m "feat(server): node version/update_available + POST /nodes/{id}/update (Phase 2)"
```

---

### Task 7: SPA — version badge + "Update now" button

**Files:**
- Modify: `central_server/static/spa/api.jsx` (`mapNode` ~290; actions object ~1468)
- Modify: `central_server/static/spa/pages/status.jsx` (node row: version badge + button, near the delete control ~847-868 + handler ~489-507)
- Test: `central_server/static/spa/__tests__/node_update.test.jsx` (match the existing jsdom suite location/pattern)

**Interfaces:**
- Consumes: API `version`, `update_available`; `POST /api/nodes/{id}/update`.
- Produces: SPA node fields `version`, `updateAvailable`; action `triggerUpdate(nodeId)`.

- [ ] **Step 1: Write the failing test**

Create `central_server/static/spa/__tests__/node_update.test.jsx` (mirror the render-assertion style of the existing SPA tests — same jsdom + Babel setup the suite already uses):

```jsx
// Version badge + Update-now trigger. Uses the repo's existing jsdom/babel
// harness (NODE_PATH -> main checkout tools/spa/node_modules).
const { mapNode } = require('../api.jsx'); // if not exported, test via window.SDPRS_API shim per existing tests

describe('mapNode version fields', () => {
  test('carries version + updateAvailable', () => {
    const n = mapNode({ node_id: 'glass_1', node_type: 'glass', status: 'ONLINE',
                        version: '723456fabc', update_available: true });
    expect(n.version).toBe('723456fabc');
    expect(n.updateAvailable).toBe(true);
  });
  test('null update_available is unknown', () => {
    const n = mapNode({ node_id: 'glass_1', node_type: 'glass', status: 'ONLINE',
                        version: null, update_available: null });
    expect(n.version).toBeNull();
    expect(n.updateAvailable).toBeNull();
  });
});
```

Add a render test for `status.jsx` asserting: an online glass node with `updateAvailable === true` shows the "Update now" button and the "update available" badge; clicking the button (after confirm) calls `window.SDPRS_API.triggerUpdate`. Follow the exact render/act pattern the existing `status.jsx` tests use (the delete-button tests are the closest template).

- [ ] **Step 2: Run to verify it fails**

Run (from the main checkout, or with `NODE_PATH` set): `npm test -- node_update` (or the repo's SPA test command).
Expected: FAIL — `mapNode` lacks version fields; button/badge absent.

- [ ] **Step 3a: `mapNode` + action (`api.jsx`)**

In `mapNode` (~line 290), add to the returned node object (alongside existing fields):

```javascript
      version: n.version || null,
      updateAvailable: (n.update_available === undefined ? null : n.update_available),
```

Add the action near `deleteNode` (~line 1230):

```javascript
  const triggerUpdate = (nodeId) => apiFetch('/api/nodes/' + encodeURIComponent(nodeId) + '/update',
    { method: 'POST' });
```

Add `triggerUpdate` to the returned actions object (~line 1468, alongside `deleteNode`).

- [ ] **Step 3b: Version badge + button (`status.jsx`)**

In the node-management row (near the delete control, ~line 847), add a version badge and, for online glass nodes, an "Update now" button. zh-TW strings; disabled/absent for offline or non-glass. Follow the delete button's exact class/handler/confirm/toast pattern (~lines 489-507, 847-868):

```jsx
{/* Phase 2: deployed version + update-available badge */}
<span className="text-xs text-slate-400" title={n.version || ''}>
  {n.version ? n.version.slice(0, 7) : '—'}
  {n.updateAvailable === true && (
    <span className="ml-1 px-1 rounded bg-sev-warn/20 text-sev-warn">有更新</span>
  )}
  {n.updateAvailable === false && (
    <span className="ml-1 text-emerald-400">最新</span>
  )}
</span>
{n.type === 'camera' && n.status !== 'offline' && (
  <button type="button"
    onClick={() => onUpdateNow(n)}
    className="px-2 h-8 rounded text-sm bg-sky-600 text-white hover:bg-sky-700"
    title="立即更新此節點軟體（背景執行，含健康檢查與自動回滾）"
    aria-label="立即更新此節點">立即更新</button>
)}
```

Add the handler alongside the delete handler (~line 489), reusing the confirm/toast idiom:

```jsx
  const onUpdateNow = (target) => {
    const api = window.SDPRS_API;
    if (!(api && api.triggerUpdate)) return;
    if (!window.confirm(`確定要立即更新節點「${target.name || target.id}」？\n節點會在背景更新（快照 → 健康檢查 → 失敗自動回滾），完成後於下次心跳回報新版本。`)) return;
    Promise.resolve(api.triggerUpdate(target.id))
      .then(() => { if (mountedRef.current) setToast({ tone: 'success', msg: `已要求節點「${target.name || target.id}」更新` }); })
      .catch(err => { if (mountedRef.current) setToast({ tone: 'error', msg: '更新要求失敗: ' + window.actionErrorText(err) }); });
  };
```

- [ ] **Step 4: Run to verify it passes**

Run the SPA suite (with `NODE_PATH` → main-checkout `tools/spa/node_modules`): `npm test`
Expected: new tests PASS; full SPA suite green.

- [ ] **Step 5: Commit**

```bash
git add central_server/static/spa/api.jsx central_server/static/spa/pages/status.jsx central_server/static/spa/__tests__/node_update.test.jsx
git commit -m "feat(spa): node version badge + Update-now button (Phase 2)"
```

---

## Deployment gates (NOT implementation — bench/site + push-approval)

Out of scope for the code tasks; happen only under the user's explicit **"approved"** and on real hardware:

1. Publish Phase 2 to `edge-release` (`scripts/publish_edge_release.sh`) so the fleet self-updates to the new edge code (version-in-heartbeat + filled `handle_update` + manual unit + sudoers). Until a node runs Phase-2 code its version shows "unknown" and "Update now" hits the old stub (no-op).
2. Deploy the server (Zeabur) so the API/poller/endpoint are live.
3. Bench-verify one node: dashboard shows version + "最新"; publish a bump → shows "有更新"; click 立即更新 → node updates within seconds, version advances, badge returns to "最新".
4. Confirm the sudoers rule works on the Pi (`sudo -n <systemctl> start --no-block sdprs-edge-update-manual.service` as `sdprs` succeeds).

---

## Self-Review

**1. Spec coverage** (spec §-by-§):
- §4.1 edge version in heartbeat → Task 1. ✅
- §4.2 server persist version (node_states + metadata) → Task 4. ✅
- §4.3 API version + update_available → Task 6. ✅
- §5.1 tip poller (GitHub, 300s, failure keeps last-known) → Task 5. ✅
- §5.2 compute_update_available (None/False/True) → Task 5 (pure fn) + Task 6 (applied). ✅
- §6.1 POST /nodes/{id}/update (guards online glass, 202) → Task 6. ✅
- §6.2 send_update_command → Task 4. ✅
- §6.3 edge handle_update → --no-block manual unit → Task 3. ✅
- §7.1 manual unit → Task 2. ✅
- §7.2 sudoers (visudo-gated, same systemctl path) → Task 2 (installer) + Task 3 (edge SYSTEMCTL_BIN). ✅
- §8 installer changes → Task 2. ✅
- §10 testing → each task's tests + §Deployment gates. ✅
- §11 old-firmware null version, offline 409, GitHub-down unknown → Tasks 1/6/5. ✅
- §13 Phase-3 deferrals → no task (out of scope). ✅

**2. Placeholder scan:** No "TBD/implement later". Every code step carries real code. Task 6/7 endpoint- and render-test harnesses reference the exact existing template file (`test_nodes_api.py`, the `status.jsx` delete-button tests) rather than reinventing app/jsdom setup — the required assertions are spelled out explicitly (not "write tests for the above").

**3. Type/name consistency:** `version` is a full-SHA `str|None` end-to-end (edge `self._version` → heartbeat `version` → server `node_states["version"]`/metadata → API `NodeStatus.version` → SPA `n.version`). `update_available`: server `bool|None` → SPA `updateAvailable` (`n.update_available` → camelCase in mapNode). `compute_update_available(node_version, tip_sha)` signature identical in Task 5 def and Task 6 call. Unit name `sdprs-edge-update-manual.service` identical in Task 2 (file + installer + grep), Task 3 (`MANUAL_UPDATE_UNIT`). `SYSTEMCTL_BIN` (edge, Task 3) and `SYSTEMCTL_BIN` (installer sudoers, Task 2) both default `/usr/bin/systemctl`. `send_update_command(node_id)` defined Task 4, called Task 6. `triggerUpdate(nodeId)` defined + exposed Task 7. ✅

One intentional scoping note: offline nodes' version is read back from persisted DB `metadata` (Task 6 fallback) — this is why Task 4 persists to metadata, not just live state; the two are consistent.
