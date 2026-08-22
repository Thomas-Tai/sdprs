# Edge glass auto-update — Phase 2: dashboard version + one-click update

**Date:** 2026-08-22
**Status:** Approved design (brainstorming) — implementation plan to follow
**Builds on:** `2026-08-22-edge-autoupdate-phase1-design.md` (shipped to
`origin/main` `723456f`; fleet 3/3 bootstrapped).

## 1. Context & motivation

Phase 1 gave each glass Pi a self-updating mechanism (nightly timer →
snapshot → rsync from `edge-release` → health-check → rollback), plus a
per-node deployed-SHA marker at `/opt/sdprs/.edge_deployed_sha`. But the fleet
is invisible from the dashboard: an operator cannot see **what version each Pi
runs** or **trigger an update on demand** — they must wait for the nightly
window or SSH in. Phase 1 deliberately left two hooks for this:

- the heartbeat already carries per-node telemetry to the server
  (`edge_glass/comms/mqtt_client.py:_publish_heartbeat`, ~line 289), and the
  server already persists a `metadata` dict per node
  (`central_server/services/mqtt_service.py:_handle_heartbeat`, ~line 282);
- the edge already subscribes to an `update` command topic
  (`shared/mqtt_topics.py:topic_cmd_update`) and dispatches it to a handler
  that is currently a **stub** (`edge_glass/edge_glass_main.py:394`,
  `# TODO: 實作自動更新邏輯`).

Phase 2 fills those hooks in.

## 2. Scope

**In scope (this phase):**
- Each node reports its deployed version to the dashboard.
- The dashboard shows, per node, the version and whether an update is
  available (compared to the `edge-release` tip).
- A per-node **"Update now"** button that triggers an immediate `--manual`
  update on that node.

**Deferred to Phase 3 (own spec) — NOT built here:**
- Server-side **active-alert hold** (server tells a node to defer auto-updates
  while it has an active/critical alert; edge writes `/run/sdprs/update_hold`).
  The Phase-1 updater already honors that file if present; nothing writes it
  yet, and `--manual` overrides it regardless.
- End-to-end **readiness** health-check (`/run/sdprs/edge_ready`: the updater
  requires a fresh heartbeat after the restart, not just `is-active`).

## 3. Approach (and the one not taken)

Reuse the existing heartbeat and command channels rather than adding a new
transport. Version rides the heartbeat (already flowing Pi→server every
interval); the trigger rides the existing `update` MQTT command (topic +
subscription + dispatch already exist — only the edge handler body and a
server sender are missing). The **not-taken** alternative — a dedicated
control socket or an SSH-based trigger from the server — is rejected: it would
add a second transport and a new inbound attack surface on each Pi, when the
MQTT command path already exists and is authenticated at the broker.

The one genuinely new outbound dependency is a small server-side poller that
learns the `edge-release` tip from the GitHub API so "update available" is
meaningful.

## 4. Version reporting (edge → server → SPA)

### 4.1 Edge — add `version` to the heartbeat
In `edge_glass/comms/mqtt_client.py`, read the deployed-SHA marker **once at
client init** (it only changes on an update, which restarts the process) and
include it in every `heartbeat_data`:

- New config/const: the marker path, default `/opt/sdprs/.edge_deployed_sha`,
  overridable (env `EDGE_DEPLOYED_SHA_FILE`) for tests.
- Read defensively: file missing/unreadable → `version = None`. **A missing
  marker must never break the heartbeat** (a node bootstrapped before Phase 2,
  or a dev run, simply reports `null`).
- Add `"version": <full-sha-or-None>` to `heartbeat_data`.

### 4.2 Server — persist `version`
In `central_server/services/mqtt_service.py:_handle_heartbeat`, add
`"version": data.get("version")` to the persisted `metadata` dict (~line 282)
so it survives a server restart, and to the in-memory `node_states` entry
(~line 261) so the live list has it without a DB read. `None` is a valid,
expected value.

### 4.3 API — expose `version` + `update_available`
In `central_server/api/nodes.py`:
- Add `version: Optional[str]` and `update_available: Optional[bool]` to the
  `NodeStatus` response model.
- In `list_nodes` (the `node_states` serialization loop, ~line 310) and
  `get_node` (~line 581), populate `version` from the node state/metadata and
  compute `update_available` (§5.2).

## 5. "Update available" — the edge-release tip poller

### 5.1 Poller
A background task in the server (started alongside the other background loops)
fetches the current `edge-release` tip and caches it in memory:

- Source: GitHub REST, unauthenticated —
  `GET https://api.github.com/repos/{owner}/{repo}/git/refs/heads/edge-release`
  → `.object.sha`. Owner/repo/branch configurable (default
  `Thomas-Tai`/`sdprs`/`edge-release`).
- Interval: **300 s** (≈12 requests/hour, well under the unauthenticated
  60/hour limit). Configurable.
- Cache: `{ "sha": <full-sha>|None, "fetched_at": <utcnow>|None }`.
- Failure handling (network error, non-200, rate-limited, malformed): keep the
  last-known value and log at WARNING; never raise into the request path. If
  the tip has **never** been fetched, `sha` stays `None`.
- The poller is opt-outable via config (`UPDATE_CHECK_ENABLED`, default true);
  when disabled, tip is `None` and `update_available` is always `null`.

### 5.2 `update_available` computation (pure function, unit-tested)
Given `(node_version, tip_sha)`:
- either is `None` → `None` ("unknown")
- equal (compare full SHAs) → `False` ("up to date")
- else → `True` ("update available")

Computed at request time in the nodes API; **not** stored.

## 6. "Update now" (SPA → server → edge)

### 6.1 API endpoint
`POST /api/nodes/{node_id}/update` in `central_server/api/nodes.py`, modeled on
the existing `POST /nodes/{node_id}/pump` (~line 969) and `/snooze` (~line
1049) endpoints (same auth — an authenticated dashboard session, NOT a node
API key):
- Guard: node must exist, be `type == "glass"`, and be **ONLINE** (offline →
  409; wrong type / unknown → 404/400). An offline node can't receive the
  command; refuse rather than silently drop.
- On success: call `mqtt_service.send_update_command(node_id)` and return
  **202 Accepted** with `{"status": "queued", "node_id": ...}`.

### 6.2 Server MQTT sender
Add `send_update_command(node_id)` to `mqtt_service.py`, mirroring
`send_stream_command` (~line 535): publish an empty/minimal JSON payload to
`topic_cmd_update(node_id)` at `QOS_CMD`. Return bool success.

### 6.3 Edge handler
Replace the `handle_update` stub (`edge_glass_main.py:394`) body with a
launch of the manual update unit, and **return immediately**:

- Run `sudo systemctl start --no-block sdprs-edge-update-manual.service` via
  `subprocess`. `--no-block` is required: a oneshot `systemctl start` blocks
  until `ExecStart` finishes (up to the 60 s health-check), which would freeze
  the MQTT callback thread.
- Wrap in try/except; log receipt and any launch error. **Never** let an
  exception escape the handler (it runs on the MQTT dispatch thread).
- The subprocess argv (incl. the `sudo`/`systemctl` binaries and the unit
  name) is built from overridable module constants so the handler is testable
  with a stubbed runner.
- Concurrency: the manual unit and the scheduled unit invoke the same updater,
  which already serializes via `flock` (Phase 1 §5) — a manual run while the
  nightly run holds the lock exits cleanly.

## 7. systemd manual unit + sudoers

### 7.1 `edge_glass/systemd/sdprs-edge-update-manual.service`
Oneshot, root, `ExecStart=/opt/sdprs/edge_autoupdate.sh --manual`,
`SyslogIdentifier=sdprs-edge-update`. **No `[Install]`/timer** — it is started
on demand only (by the edge via sudo, or by an operator).

### 7.2 `/etc/sudoers.d/sdprs-edge-update`
Grants the `sdprs` user exactly one command, nothing else:

```
sdprs ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block sdprs-edge-update-manual.service
```

- File mode `0440`, validated with `visudo -cf` before install.
- **Path consistency (correctness):** sudoers matches the command by absolute
  path, and `sudo` uses its own `secure_path`. The `systemctl` absolute path in
  the rule MUST equal what the edge invokes. Resolve it once at install time
  (`command -v systemctl`, e.g. `/usr/bin/systemctl` on RPi OS), write that
  exact path into the sudoers rule, and have the edge call that same absolute
  path — otherwise sudo denies and "Update now" silently fails.
- Blast radius = starting that one unit, which is itself safe (snapshot →
  health-check → rollback; SHA never advances on failure). No shell, no
  wildcards, no other unit.

## 8. Installer changes (`scripts/edge_autoupdate_install.sh`)
- Install `sdprs-edge-update-manual.service` to `/etc/systemd/system/`
  alongside the existing units (no enable — it's on-demand).
- Install the sudoers drop-in to `/etc/sudoers.d/sdprs-edge-update`, mode
  `0440`, **only after `visudo -cf` validates it** (a bad sudoers file must
  never be installed). Idempotent (overwrite-in-place is fine; content is
  fixed).
- `daemon-reload` already runs. Remains idempotent + safe to re-run.

## 9. Files touched
- `edge_glass/comms/mqtt_client.py` — version read + heartbeat field.
- `edge_glass/edge_glass_main.py` — fill `handle_update`; module constants for
  the update-launch argv.
- `edge_glass/systemd/sdprs-edge-update-manual.service` — new.
- `scripts/edge_autoupdate_install.sh` — install unit + sudoers (visudo-gated).
- `central_server/services/mqtt_service.py` — persist `version`;
  `send_update_command`.
- `central_server/api/nodes.py` — `NodeStatus` fields; serialize version +
  `update_available`; `POST /nodes/{id}/update`; the tip poller + cache (poller
  may live in its own small module, e.g. `services/release_check.py`, and be
  started from server lifespan).
- `central_server/static/spa/` — version badge + "Update now" button + the
  data-layer call (`data.jsx`), matching the existing no-build-step pages/*
  structure and `window.*` plumbing.
- Tests across all of the above (§10).

## 10. Testing
- **Edge (pytest):** version read (present / missing-file → None / included in
  heartbeat dict); `handle_update` invokes the exact argv incl. `--no-block`
  with a stubbed runner, and swallows a runner exception.
- **Server (pytest):** `version` persisted into metadata + node_states;
  `update_available` pure function (all three cases); `POST /nodes/{id}/update`
  → publishes the correct command, returns 202, and guards offline/wrong-type;
  poller caches a stubbed GitHub 200, and on failure keeps last-known / stays
  None (no raise).
- **SPA (jsdom):** version badge renders each state (up to date / update
  available / unknown); "Update now" button shows for online glass nodes,
  confirm dialog gates the `POST`. SPA suite needs `NODE_PATH` → the main
  checkout's `tools/spa/node_modules` (this worktree has none).
- **systemd/sudoers:** grep gate for the manual unit (as in Phase 1's
  `test_edge_autoupdate_units.sh`); `visudo -cf` on the sudoers file (bench/CI;
  also runnable in WSL).

## 11. Edge cases & security
- **Old-firmware node** (Phase-1 code, no `version` in heartbeat) → API
  reports `version: null`, `update_available: null` → SPA shows "unknown".
  Self-heals once the node runs Phase-2 code.
- **Bootstrapping Phase 2 is self-hosting:** the fleet runs Phase-1 edge code
  today, so it picks up Phase-2 edge code via the nightly auto-update (or a
  re-bootstrap). Until then "Update now" reaches the stub (logs, no-op) — so
  publish Phase 2 to `edge-release` and let one nightly cycle (or a manual
  `deploy_console.sh` + installer re-run) land it before relying on the button.
- **Offline node**: "Update now" is refused (409) rather than dropped.
- **GitHub unreachable / rate-limited**: `update_available` degrades to
  "unknown"; the button still works (it doesn't depend on the tip).
- **Privilege**: the only new privilege is the single sudoers line (§7.2);
  the endpoint requires an authenticated dashboard session; the MQTT command
  carries no shell/payload that the edge interprets beyond "start the unit".
- **No hardcoded credentials**; the banned strings `Msc@2333`, `MSC-Person`,
  `broker.emqx.io` must not appear in any diff. User-facing SPA strings are
  zh-TW Traditional Chinese; on-node/journal logs stay English.

## 12. Verification
- Full pytest (edge + server) green; SPA suite green (with `NODE_PATH`).
- `visudo -cf` clean on the sudoers file; unit grep gate green.
- Bench (one node, after Phase-2 edge lands): dashboard shows the node's
  version + "up to date"; publish a bump → dashboard shows "update available";
  click "Update now" → node updates within seconds, version advances, badge
  returns to "up to date". (Bench/site-gated, like all fleet changes.)

## 13. Deferred → Phase 3 (separate spec)
- Server active-alert hold source + `/run/sdprs/update_hold` writer.
- End-to-end readiness gate (`/run/sdprs/edge_ready`).
- Optional: surface `trigger_source` / last-update result and timestamp in the
  dashboard; a fleet-wide "update all" action.
