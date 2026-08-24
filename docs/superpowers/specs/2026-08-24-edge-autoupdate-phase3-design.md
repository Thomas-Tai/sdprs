# Edge glass auto-update — Phase 3: active-alert update-hold + readiness gate

**Date:** 2026-08-24
**Status:** Draft design (brainstorming) — **awaiting user approval** before an
implementation plan is written.
**Builds on:** `2026-08-22-edge-autoupdate-phase1-design.md` (`723456f`),
`2026-08-22-edge-autoupdate-phase2-design.md` (`fe7dcd2`), and Phase 2.1
(`ef098be`, heartbeat re-reads the SHA marker). All shipped to `origin/main` +
`edge-release` @ `ef098be`; fleet 3/3 bootstrapped.

## 1. Context & motivation

Phase 1 built the updater's *reader* side of an "active-alert hold" and a 60 s
health-check, but left both half-wired — Phase 2 explicitly deferred the rest
here (Phase 2 spec §13). Two concrete gaps remain:

1. **The hold has no writer.** The updater's scheduled path defers when
   `/run/sdprs/update_hold` exists and contains `1`
   (`scripts/edge_autoupdate.sh:80-82`, `held()`; gated at :98-100), and
   `--manual` bypasses it. But **nothing ever writes that file**, so the hold is
   inert — a nightly auto-update can restart the service in the middle of a
   crack recording, or while an operator is working an unresolved alert.
2. **Health-check is shallow.** `health_check()`
   (`scripts/edge_autoupdate.sh:186-200`) only confirms the service is
   `is-active` and not crash-looping for 60 s. A process that comes up but never
   opens the camera / wedges its detection loop still reads "active" → a broken
   update is marked healthy and the deployed SHA advances.

Phase 3 closes both, with the three product decisions locked in brainstorming
(2026-08-24):

- **(D1) Hold from BOTH sources** — the node raises the hold locally while
  mid-capture (protects an in-progress recording even when offline), AND the
  server pushes hold/unhold while the dashboard has an unresolved alert for that
  node.
- **(D2) Manual "Update now" = respect + override with confirm** — the edge
  `--manual` path keeps bypassing the hold file (unchanged). The "respect" lives
  at the dashboard: it surfaces hold-state + reason and, when held, shows a
  stronger zh-TW confirm naming the reason. The operator can still force; on
  confirm the update proceeds exactly as today.
- **(D3) Readiness = a positive `edge_ready` signal** — the edge writes
  `/run/sdprs/edge_ready` once it is genuinely functional; the updater's
  health-check waits for that (in addition to `is-active`) before advancing the
  SHA, else rolls back.

## 2. Scope

**In scope:**
- A real writer for `/run/sdprs/update_hold`, aggregating the two hold sources.
- Edge-local hold while an event is mid-capture / cooldown.
- Server-driven hold while a node has an unresolved (active) alert, delivered
  over a new `hold` MQTT command, reconciled server-side.
- Hold-state surfaced on the heartbeat → dashboard, driving the stronger
  "Update now" confirm (D2).
- A positive `/run/sdprs/edge_ready` readiness signal + the updater's
  health-check gate on it (D3).
- The runtime-dir ownership fix so the `sdprs`-user edge process can write
  `/run/sdprs`.

**Explicitly NOT in scope:**
- Changing `--manual`/Update-now bypass semantics (they stay; D2).
- Any new inbound network surface on the Pi (reuse MQTT + heartbeat, as Phase 2).
- A fleet-wide "update all" / update scheduling UI (possible later phase).

## 3. Approach (and the ones not taken)

**One writer for the hold file.** The **edge process is the sole writer** of
`/run/sdprs/update_hold`. It aggregates both sources —
`held = local_capture_active OR server_hold_active` — and writes `1`/`0` on
every heartbeat (co-located with `_publish_heartbeat`). *Not taken:* letting the
updater or a second daemon also write it — two writers racing on one file is a
correctness hazard, and the edge already has both inputs in-process.

**Freshness by mtime, not by a liveness protocol.** Because the edge rewrites
the file every heartbeat, its mtime is always fresh while the edge lives. The
updater's `held()` gains a **staleness guard**: held iff content is `1` *and*
mtime is within `HOLD_MAX_AGE` (default 300 s ≈ 10 heartbeats). A dead edge
writer's stale `1` self-expires → a genuinely dead node becomes updatable (which
is what we want), and `/run` is tmpfs so a reboot clears it regardless. *Not
taken:* a heartbeat/handshake between updater and edge — unnecessary given the
file already carries a fresh mtime.

**Reuse the heartbeat + command channels** (as Phase 2). Hold-state rides the
heartbeat; server→edge hold rides a new `hold` command on the existing
`cmd/+` subscription (`mqtt_client.py:210` already subscribes wildcard, so only
a handler is added). *Not taken:* an SSH/control-socket trigger — a second
transport and inbound surface we deliberately avoided in Phase 2.

## 4. The hold file — single writer, TTL-fresh

### 4.1 Edge writes it each heartbeat
In `edge_glass/comms/mqtt_client.py`, at heartbeat time, compute the aggregate
and write the file:

- `held = local_capture_active OR server_hold_active` (§5, §6).
- Write `1` or `0` to `/run/sdprs/update_hold` (path overridable via env for
  tests, e.g. `EDGE_UPDATE_HOLD_FILE`). Write defensively — a write failure
  (dir missing, perms) is logged at WARNING and **must never break the
  heartbeat** (same discipline as the existing `version`/IP reads).
- The write is small and idempotent; a plain truncating write is fine (the
  updater tolerates reading during a rewrite because it re-checks content ==
  `1`; a torn read yields "not held", which is the safe default).

### 4.2 Updater `held()` gains a freshness guard
In `scripts/edge_autoupdate.sh`, extend `held()` (currently :80-82):

- Held iff `update_hold` exists **and** content is `1` **and** file mtime is
  within `HOLD_MAX_AGE` seconds of now. New conf var `HOLD_MAX_AGE` (default
  `300`), sourced from `/etc/sdprs-edge-update.conf` like the others.
- Backward-compatible in both directions: a Phase-1 updater (no TTL) + Phase-3
  edge still works (content `1`/`0` semantics unchanged); a Phase-3 updater +
  Phase-1 edge (no writer) → file absent → not held (unchanged). Only the
  scheduled path consults `held()`; `--manual` still bypasses.

## 5. Edge-local hold (event capture in progress)

The edge raises `local_capture_active` while an event is being captured, so a
scheduled update can't restart the service mid-recording — even if the node is
offline and the server can't help.

- Signal (main loop, `edge_glass/edge_glass_main.py`): capture is "in progress"
  from event fire (:553) through the end of the post-roll encode window. The
  existing **cooldown** already spans that sensitive window
  (`cooldown_until = timestamp + cooldown_seconds`, :622), and the async path
  tracks undrained events in `event_tracker`. So:
  `local_capture_active = (now <= cooldown_until) OR event_tracker has undrained events`.
- Plumbing: mirror the existing health setters — the main loop calls
  `mqtt_client.set_local_capture_hold(active: bool, reason: str)` (like
  `set_buffer_health` / `set_detector_health`), and `mqtt_client` folds the
  stored value into the heartbeat-time write (§4.1). Reason e.g.
  `"event capture in progress"` (English on-node string).

## 6. Server-driven hold (unresolved alert)

### 6.1 New `hold` MQTT command (server → edge)
- Topic: `topic_cmd(node_id, "hold")` (`shared/mqtt_topics.py:156`, reusing the
  generic helper — no new topic function needed).
- Payload: `{"hold": bool, "reason": str, "timestamp": <utc iso>}`.
- Server sender `send_hold_command(node_id, hold, reason)` in
  `central_server/services/mqtt_service.py`, mirroring `send_stream_command`
  (:537) / `send_update_command` (:554): publish JSON at `QOS_CMD`, return bool.

### 6.2 Edge handler
- Register a `"hold"` command handler (`edge_glass_main.py`, alongside `update`
  / `simulate_trigger`, :431-432). It stores `server_hold = payload.hold`,
  `server_hold_reason = payload.reason`, and `server_hold_ts = now` on the mqtt
  client (via a setter). Never raises (runs on the MQTT dispatch thread).
- **Edge server-hold TTL:** the edge treats `server_hold_active` as true only if
  `hold` is true *and* the last hold command is within `SERVER_HOLD_TTL`
  (default 900 s). So a dead/unreachable server can never pin a node held
  forever. (Local-capture needs no TTL — it's recomputed live each heartbeat.)

### 6.3 Server reconcile — hold iff node has an unresolved alert
"Unresolved" = an event whose status is active
(`PENDING_VIDEO`, `PENDING`, `ACKNOWLEDGED`); `RESOLVED` clears it. The query
already exists: `event_service.list_events(status_filter=<active set>,
node_filter=node_id)` (or a light count).

- **Periodic re-assert (self-healing):** piggyback the existing ~300 s poller
  loop (the one that runs `release_check.refresh()`; see `main.py:142-149`). Each
  tick, for every **online** node: recompute has-active-alert; **send
  `hold=true`** (re-assert — refreshes the edge's `SERVER_HOLD_TTL`) for nodes
  with an active alert, and **send `hold=false` once** when a node transitions
  from held → none. Re-asserting each tick (300 s) comfortably inside the 900 s
  edge TTL keeps held nodes held; sending unhold on the transition clears it
  promptly rather than waiting out the TTL.
- **Opportunistic (low latency):** also send `hold=true` when an alert is
  created for a node, and `hold=false` when its last active alert is resolved
  (hook near `event_service` create / resolve / bulk-resolve, best-effort). This
  is an optimization on top of the periodic reconcile; if it's dropped, the next
  tick fixes it.
- Idempotent + best-effort: repeated `hold=true` is harmless; a publish failure
  is logged, never raised into the request/alert path. Snooze does **not** clear
  the hold — a snoozed-but-unresolved alert still means an operator may be
  working the node, so it stays held until actually resolved.

## 7. Heartbeat surfaces hold-state (edge → server → SPA)

- **Edge:** add `"update_held": bool` and `"hold_reason": str|None` to
  `heartbeat_data` (`mqtt_client.py:_publish_heartbeat`, :316-342). Reason
  precedence: local-capture reason if locally held, else the server reason, else
  `None`.
- **Server:** in `_handle_heartbeat` (`mqtt_service.py:231`), add
  `update_held` + `hold_reason` to the in-memory `node_states` entry (:261) and
  the persisted `metadata` dict (:283), mirroring `version`. `None`/`False` are
  valid defaults.
- **API:** add `update_held: Optional[bool]` and `hold_reason: Optional[str]`
  to `NodeStatus` (`central_server/api/nodes.py:130-131`, next to `version`),
  and populate them in `list_nodes` (live state, ~:376; offline metadata, ~:401)
  and `get_node` (~:713).

## 8. SPA — stronger "Update now" confirm when held (D2)

In the existing 「立即更新」(Update-Now) flow (Phase 2, `pages/status.jsx` +
`data.jsx`):

- When the node's `update_held` is true, clicking 「立即更新」shows a **stronger
  zh-TW confirm** that names the reason, e.g.
  「此節點目前有進行中的錄製／未解除警報（<hold_reason>），立即更新會中斷監測。仍要立即更新嗎？」
  On confirm, the existing `POST /api/nodes/{id}/update` proceeds unchanged (the
  server → edge `--manual` path bypasses the hold file by design).
- When `update_held` is false, the existing lighter confirm is unchanged.
- Optional surface-only touch: a small 「暫緩更新」indicator beside the version
  badge when held. Keep it minimal; no new controls.
- zh-TW Traditional Chinese for all operator-facing strings.

## 9. Readiness gate (D3)

### 9.1 Edge writes `/run/sdprs/edge_ready`
- **At startup, before the main loop, delete any stale `edge_ready`.** `/run`
  is tmpfs and a file from the *previous* process survives a restart; the
  updater's post-restart health-check must observe the **new** process assert
  readiness, not a leftover file.
- Create `edge_ready` once the node is genuinely functional: a successful
  `camera.read()` in the main loop **and** the MQTT client started (so the
  detection loop is iterating and heartbeats flow). Practically: set it the
  first time the loop completes a successful camera read with `mqtt_client`
  present. Write defensively (never crash the loop on a write failure).
- Path overridable via env (`EDGE_READY_FILE`, default `/run/sdprs/edge_ready`)
  for tests.

### 9.2 Updater health-check waits for it
Extend `health_check()` (`scripts/edge_autoupdate.sh:186-200`): after the
existing `is-active` + no-crash-loop poll passes, **also require `edge_ready` to
exist with an mtime newer than the restart** (the pre-restart delete guarantees
a present file is the new process's) within `HEALTH_TIMEOUT`; otherwise fail →
rollback (SHA not advanced), exactly like a crash-loop today.

- New conf var `EDGE_READY_FILE` (default `/run/sdprs/edge_ready`) and
  `REQUIRE_EDGE_READY` (default `1`).
- **Ordering invariant makes default-on safe:** edge_glass code and the updater
  script rsync together in one update, and the health-check that gates update
  *N→N+1* is run by the updater version present *before* it — so the first time
  the Phase-3 health-check ever runs, the edge already ships the `edge_ready`
  writer (both landed in the Phase-3 release). `REQUIRE_EDGE_READY=0` is the
  documented bench/rollout valve for the unusual "new updater + pre-writer edge"
  case (mirrors Phase 1's conf-if-absent discipline).

## 10. Runtime-dir ownership fix

The edge now **writes** `/run/sdprs` (hold + ready), but the installer creates
it as root (`edge_autoupdate_install.sh:33`, `mkdir -p /run/sdprs` → root:root
0755) while the edge runs `User=sdprs` (`sdprs-edge-cloud.service:30`) → it can't
write there. Fix in the installer:

- Install `/etc/tmpfiles.d/sdprs.conf` containing `d /run/sdprs 0755 sdprs sdprs -`
  and run `systemd-tmpfiles --create /etc/tmpfiles.d/sdprs.conf` so `/run/sdprs`
  is owned `sdprs:sdprs` immediately **and** recreated with correct ownership on
  every boot (tmpfs clears on reboot; tmpfiles recreates before services start).
- The updater reads the hold file as root — unaffected.
- *Alternative considered:* `RuntimeDirectory=sdprs` on `sdprs-edge-cloud.service`
  is more idiomatic, but that unit is provisioned by `setup_pi.sh`, **not**
  installed/updated by the OTA path, so it wouldn't reach the fleet via update.
  tmpfiles.d rides `edge_autoupdate_install.sh`, which is already re-run per node
  for Phase 2 — the rollout-friendly choice.

## 11. Files touched
- `edge_glass/comms/mqtt_client.py` — `set_local_capture_hold` + server-hold
  setter/TTL; write `update_hold` each heartbeat; `update_held` + `hold_reason`
  in the heartbeat.
- `edge_glass/edge_glass_main.py` — compute local-capture hold → setter; register
  `hold` command handler; `edge_ready` startup-delete + ready-create.
- `scripts/edge_autoupdate.sh` — `held()` TTL guard; `health_check()` edge_ready
  gate; new conf vars.
- `scripts/edge_autoupdate_install.sh` — tmpfiles.d for `/run/sdprs` ownership.
- `edge_glass/systemd/sdprs-edge-update.conf` — document `HOLD_MAX_AGE`,
  `EDGE_READY_FILE`, `REQUIRE_EDGE_READY`.
- `central_server/services/mqtt_service.py` — persist `update_held`/`hold_reason`;
  `send_hold_command`.
- `central_server/services/release_check.py` (+ `main.py` loop) and/or
  `event_service.py` — per-node hold reconcile (periodic re-assert +
  opportunistic on alert create/resolve).
- `central_server/api/nodes.py` — `NodeStatus` fields; serialize in
  `list_nodes` + `get_node`.
- `central_server/static/spa/` — stronger held-confirm; optional held indicator.
- Tests across all of the above (§12).

## 12. Testing
- **Edge (pytest):** local-capture hold true during cooldown / with undrained
  events, false otherwise; server-hold stored from a `hold` command and expiring
  after `SERVER_HOLD_TTL`; aggregate write emits `1`/`0`; heartbeat carries
  `update_held` + `hold_reason` with correct reason precedence; `edge_ready`
  deleted at startup then created after a successful read; a write failure never
  breaks the heartbeat/loop.
- **Bash harness (`scripts/tests/test_edge_autoupdate.sh`, stubbed):** `held()`
  — fresh `1` → held, stale `1` (mtime > `HOLD_MAX_AGE`) → not held, `0` → not
  held, absent → not held; `health_check()` — edge_ready present-and-fresh →
  pass, absent within timeout → fail → rollback, `REQUIRE_EDGE_READY=0` → skips
  the gate.
- **Server (pytest):** `send_hold_command` publishes the right topic/payload;
  reconcile computes hold from active alerts (has-active → true; all resolved →
  false); `update_held`/`hold_reason` persisted into metadata + node_states; API
  serializes them (live + offline).
- **SPA (jsdom):** held node → 「立即更新」triggers the stronger confirm naming
  the reason; confirm proceeds to the POST, cancel does not; not-held → existing
  confirm. Suite needs `NODE_PATH` → the main checkout's `tools/spa/node_modules`
  (this worktree has none).
- **units/tmpfiles:** grep gate that the installer writes the tmpfiles.d line;
  conf documents the new vars.

## 13. Edge cases & security
- **Two-writers race** avoided — the edge is the sole writer of `update_hold`.
- **Dead edge process** → stale `1` self-expires via `HOLD_MAX_AGE` → node
  becomes updatable (desired); tmpfs reboot clears it too.
- **Dead/unreachable server** → edge `SERVER_HOLD_TTL` expires → node not pinned
  held; local-capture hold still protects real recordings.
- **`--manual` / Update-now** always bypass the hold (unchanged) — the operator
  override path (D2), gated at the dashboard by the stronger confirm.
- **`edge_ready` ordering invariant** (co-ships with the Phase-3 updater) makes
  `REQUIRE_EDGE_READY=1` safe by default; the conf flag is the bench valve.
- **Quiet window (03:00–05:00):** a rare 3 a.m. alert correctly defers the
  scheduled run (`"active-alert hold set — deferred"`); the operator can still
  force via Update-now (with confirm) or `--manual`.
- **No new privilege:** no sudoers change (reuses the Phase-2 manual-unit path);
  the `hold` MQTT command carries no shell/payload the edge interprets beyond a
  bool + reason string.
- **No hardcoded credentials**; the banned strings `Msc@2333`, `MSC-Person`,
  `broker.emqx.io` must not appear in any diff. Operator-facing SPA strings are
  zh-TW Traditional Chinese; on-node / journal / log strings stay English.

## 14. Verification
- Full pytest (edge + server) green; bash updater harness green; SPA suite green
  (with `NODE_PATH`).
- Bench (one node, after Phase-3 edge lands): raise a real/sim alert → dashboard
  shows the held indicator + reason; a scheduled updater run defers
  (`"active-alert hold set — deferred"`); resolve the alert → hold clears within
  a reconcile tick; 「立即更新」while held → stronger confirm → proceeds and
  updates. Separately, force a "camera never opens" failure on an update →
  `edge_ready` never appears → health-check fails → rollback restores the
  service, SHA not advanced. (Bench/site-gated, like all fleet changes.)
