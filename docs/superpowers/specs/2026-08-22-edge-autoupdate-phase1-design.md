# Edge glass auto-update — Phase 1: on-node auto-updater

**Date:** 2026-08-22
**Status:** Approved design (brainstorming) — implementation plan to follow
**Scope:** Edge node only (bash + systemd). No server, SPA, or `edge_glass`
Python change in this phase.

## 1. Context & motivation

The glass fleet is **rsync-deployed, not a git checkout**: code lives at
`/opt/sdprs/edge_glass`, the `sdprs-edge-cloud` systemd service runs it
on-disk, and there is no automatic update path. Today a node is updated by a
human running the `scripts/deploy_console.sh` one-liner on it
(`curl … | sudo bash`). That is reliable but manual — every release requires
someone to touch every Pi.

This phase makes each node keep **itself** current, unattended and safely,
against a curated release pointer — without any dependency on the operator's
workstation, LAN, or VPN (the node fetches over its own internet, exactly as
`deploy_console.sh` already does).

Decisions locked during brainstorming (2026-08-22):

- **Rollout:** automatic, **staggered** across nodes, with **per-node
  rollback** on a failed update.
- **Release model:** the fleet follows a dedicated **`edge-release` branch**.
  `main` stays a working branch; a release reaches the fleet only when
  `edge-release` is fast-forwarded. This is the staging gate.
- **Timing:** apply during **quiet hours only**, and (Phase 2) defer while an
  active-alert hold is set. Phase 1 makes the hold path forward-compatible
  (absent hold file = not held).
- **Manual override:** a per-node dashboard "Update now" that bypasses the
  window and hold — **Phase 2** (needs server + SPA). Phase 1 leaves the
  updater invokable on demand so Phase 2 can wire to it.

## 2. Approach (and the one not taken)

Build on the proven **rsync** updater (`deploy_console.sh`), tracking the
`edge-release` tip. **Not** the alternative of converting each `/opt/sdprs`
into a git checkout — that needs risky one-time surgery (gitignoring the
tracked `config.zeabur.yaml` template, reconciling the nested
`edge_glass/shared`, a `reset --hard`) for no benefit here, since rollback is
already covered by local snapshots.

## 3. Goals / non-goals

**Goals**
- Each node, on a quiet-hours timer, brings itself to the `edge-release` tip
  if (and only if) it has changed, with snapshot → health-check → rollback.
- Nodes never restart together (stagger), and a node under maintenance can be
  held (pause file).
- One-time `curl | sudo bash` bootstrap installs the mechanism.

**Non-goals (Phase 2 or later)**
- No server change, no SPA change, no `edge_glass` Python change this phase.
- No dashboard version column, no active-alert hold *source*, no manual
  "Update now" trigger — all Phase 2.
- No self-update of the updater script itself (re-run the bootstrap to update
  it; noted as a future nicety).

## 4. State files (node-local, OUTSIDE `edge_glass/` so rsync never touches them)

- **`/opt/sdprs/.edge_deployed_sha`** — full SHA of the `edge-release` commit
  currently deployed. Written only after a successful, health-checked update.
  (Phase 2's version column reads this.)
- **`/opt/sdprs/.edge_update_paused`** — presence = hold this node; the updater
  exits early. For maintenance.
- **`/run/sdprs/update_hold`** — active-alert hold. Written by `edge_glass` in
  Phase 2; **absent in Phase 1**, which the updater treats as "not held". The
  code path exists now so Phase 2 only has to start writing the file.

## 5. Updater — `scripts/edge_autoupdate.sh`

Runs as root (installed to `/opt/sdprs/edge_autoupdate.sh`). Modes: default
**scheduled**; `--manual` (Phase 2 on-demand) skips the window + hold checks;
`--dry-run` performs no mutation.

Flow (scheduled):

1. `flock` a lock file so overlapping runs cannot collide.
2. If `.edge_update_paused` exists → log + exit 0.
3. Scheduled only: if the current time is outside the configured quiet window
   (guards a `Persistent=true` catch-up firing mid-day), or `/run/sdprs/update_hold`
   contains `1` → log "deferred" + exit 0.
4. `REMOTE=$(git ls-remote "$REPO" refs/heads/edge-release | cut -f1)`.
   - `ls-remote` failure (network) → log + exit 0 (retry next window).
5. `LOCAL=$(cat /opt/sdprs/.edge_deployed_sha 2>/dev/null)`.
   If `REMOTE == LOCAL` → "up to date" + exit 0 (**no clone, no restart**).
6. Changed → `git clone --depth 1 --branch edge-release "$REPO" "$TMP"`.
7. **Snapshot**: `tar czf /opt/sdprs/edge_glass.backup.<STAMP>.tgz`
   (exclude `edge_glass/venv|events|buffer`).
8. **rsync** `"$TMP/edge_glass/"` → `/opt/sdprs/edge_glass/` with the exact
   excludes from `deploy_console.sh`
   (`config.zeabur.yaml`, `config.yaml`, `.env`, `.env.*`, `venv/`,
   `__pycache__/`, `*.pyc`, `*.log`, `events/`, `buffer/`, `data/`), then
   rsync `"$TMP/shared/"` → `/opt/sdprs/edge_glass/shared/`, then
   `chown -R sdprs:sdprs /opt/sdprs/edge_glass`.
9. If the rsync changed nothing on disk (`rsync --itemize-changes` empty)
   → record `REMOTE` to `.edge_deployed_sha`, **skip restart**, exit 0.
10. Else `systemctl restart sdprs-edge-cloud` → **health-check** (§6).
11. Success → write `REMOTE` to `.edge_deployed_sha`; prune backups to the
    newest `KEEP_SNAPSHOTS`; `rm -rf "$TMP"`; log DONE.

**Testability requirement:** every external command is called through an
overridable variable — `: "${GIT:=git}" "${RSYNC:=rsync}" "${SYSTEMCTL:=systemctl}" "${TAR:=tar}"`
and invoked as `"$GIT" …`, `"$SYSTEMCTL" …`, etc. — so the test harness (§9)
can stub them. `--dry-run` short-circuits every mutating step.

## 6. Health-check + rollback

After the restart, poll for up to `HEALTH_TIMEOUT` (default 60 s):
- require `systemctl is-active sdprs-edge-cloud` = `active`, **and**
- `systemctl show -p NRestarts sdprs-edge-cloud` not climbing across the
  window (catches a crash-loop that flaps active/failed).

The service's existing `ExecStartPre` cloud-reachability gate means "active"
is a genuine signal here, not just "the process spawned".

**On failure:** restore the snapshot over `/opt/sdprs/edge_glass`,
`chown` back to `sdprs:sdprs`, `systemctl restart`, **leave
`.edge_deployed_sha` unchanged** (so the node retries the same target next
window and never records a bad SHA as deployed), and log the rollback loudly.

*(Phase 2 upgrades this to an end-to-end readiness check: `edge_glass` touches
`/run/sdprs/edge_ready` on each successful heartbeat and the updater requires a
fresh timestamp after the restart — added when `edge_glass` is being modified
anyway. Phase 1's check is pure systemd, no Python change.)*

## 7. systemd units (`edge_glass/systemd/`, installed to `/etc/systemd/system/`)

- **`sdprs-edge-update.service`** — `Type=oneshot`, runs as root,
  `ExecStart=/opt/sdprs/edge_autoupdate.sh`.
- **`sdprs-edge-update.timer`** — `OnCalendar=*-*-* 03:00:00`,
  `RandomizedDelaySec=1800` (spreads the three nodes across 03:00–03:30 with
  **no cross-node coordination**), `Persistent=true` (catch up if the Pi was
  off during the window). A daily check suffices because updates only apply in
  the quiet window regardless.
- Config in **`/etc/sdprs-edge-update.conf`** (sourced by the script):
  `REPO`, `BRANCH=edge-release`, `QUIET_START=03:00`, `QUIET_END=05:00`,
  `HEALTH_TIMEOUT=60`, `KEEP_SNAPSHOTS=3`. Tunable per node without editing
  the script.

## 8. Bootstrap — `scripts/edge_autoupdate_install.sh` (`curl … | sudo bash`)

One-time per node: clone `edge-release`; install `/opt/sdprs/edge_autoupdate.sh`
+ the two unit files + default `/etc/sdprs-edge-update.conf`; seed
`/opt/sdprs/.edge_deployed_sha` with the current `edge-release` tip (the fleet
is already on it as of 2026-08-22, so the first scheduled run is a no-op until
a newer release is published); `systemctl daemon-reload`,
`enable --now sdprs-edge-update.timer`; print status. Idempotent (safe to
re-run — this is also how the updater script itself gets updated).
`deploy_console.sh` stays as the ad-hoc "pull `main` now" tool and coexists.

## 9. Testing

- **`shellcheck`** on `edge_autoupdate.sh`, `edge_autoupdate_install.sh`,
  `publish_edge_release.sh`.
- **`scripts/tests/test_edge_autoupdate.sh`** — a self-contained bash harness
  that puts stub `git`/`rsync`/`systemctl`/`tar` on the injectable hooks and a
  temp `DEST`, asserting:
  - `REMOTE == LOCAL` → no clone, no restart, exit 0.
  - `REMOTE != LOCAL` → clone + snapshot + rsync + restart, and
    `.edge_deployed_sha` becomes `REMOTE`.
  - health-check failure → snapshot restored, `.edge_deployed_sha` unchanged,
    non-zero exit.
  - `.edge_update_paused` present → early exit, nothing touched.
  - scheduled + `update_hold=1` → deferred; `--manual` ignores the hold.
  - `--dry-run` → no mutation.
- **`systemd-analyze verify`** on the two unit files.

## 10. The `edge-release` branch + publishing

Create `edge-release` once at the current `main` tip (fleet starts "up to
date"). Publishing a release is a fast-forward + push, wrapped in a small
**`scripts/publish_edge_release.sh`** helper (ff-only `main`→`edge-release`,
push, print the new tip). Cutting a release stays a deliberate human action.

## 11. Edge cases
- **Network down at check time:** `ls-remote` fails → clean no-op, retry next
  window. Never leaves a half-applied state.
- **Pi off during the window:** `Persistent=true` runs the timer at next boot.
  If that catch-up fires outside the quiet window, the in-script window guard
  defers it (logs "outside window") and the next 03:00 run applies it — so a
  boot mid-day never triggers a daytime camera restart. (Pause/hold are honored
  regardless.)
- **Release published but `edge_glass` bytes unchanged** (e.g. a docs-only
  bump on the branch): rsync itemize empty → record SHA, no restart.
- **Two runs overlap** (timer + a future manual): `flock` serializes them.
- **Bad release that still passes health-check:** out of scope for an
  automated gate; mitigated by the staging gate (`edge-release` is curated,
  gate-green before publish) and by rollback catching the common failures.

## 12. Files created
- `scripts/edge_autoupdate.sh`
- `scripts/edge_autoupdate_install.sh`
- `scripts/publish_edge_release.sh`
- `edge_glass/systemd/sdprs-edge-update.service`
- `edge_glass/systemd/sdprs-edge-update.timer`
- `scripts/tests/test_edge_autoupdate.sh`
- `docs/…` short runbook (how to publish a release, pause a node, read logs,
  manual rollback)
- `edge-release` branch (ops, not a file)

## 13. Verification
- `shellcheck` clean + `bash scripts/tests/test_edge_autoupdate.sh` green +
  `systemd-analyze verify` clean.
- Bench check on one node: bootstrap → confirm timer armed
  (`systemctl list-timers`), `.edge_deployed_sha` seeded; publish a trivial
  `edge-release` bump → confirm the node updates in the window, service comes
  back active, SHA advances; force a bad release → confirm rollback restores
  service and SHA is not advanced. (Bench/site-gated, like all fleet changes.)

## 14. Deferred → Phase 2 (separate spec)
- Heartbeat `version` field (reads `.edge_deployed_sha`) → server persist →
  SPA **version column** ("up to date / update available").
- Server **update-gate** retained MQTT publish (`hold` while an active/critical
  alert exists) + `edge_glass` writes `/run/sdprs/update_hold`.
- Per-node dashboard **"Update now"** (`POST /api/nodes/{id}/update` → MQTT
  command → `edge_glass` starts `sdprs-edge-update.service --manual` via a
  narrow sudoers rule) with a confirm dialog.
- End-to-end **readiness** health-check (`/run/sdprs/edge_ready`).
