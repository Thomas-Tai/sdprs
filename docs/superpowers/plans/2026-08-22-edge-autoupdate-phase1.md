# Edge Glass Auto-Update — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each Raspberry Pi glass node a scheduled, self-contained mechanism that safely brings itself to the `edge-release` tip (snapshot → rsync → health-check → rollback), unattended and staggered, with no dependency on the operator's workstation, LAN, or VPN.

**Architecture:** A single root bash updater (`scripts/edge_autoupdate.sh`) built on the proven `deploy_console.sh` rsync flow, driven by a nightly staggered systemd timer. Every external command (git/rsync/systemctl/tar/chown/sleep) and every path is injectable via env/config so a self-contained bash harness can black-box test the whole flow with stubs — no server, SPA, or `edge_glass` Python change this phase.

**Tech Stack:** bash 5.x, systemd (timer + oneshot service), git/rsync over the node's own internet, GitHub public repo.

**Spec:** `docs/superpowers/specs/2026-08-22-edge-autoupdate-phase1-design.md`

## Global Constraints

- **Push gate:** nothing reaches `origin` (including creating/pushing the `edge-release` branch) without the user typing the literal word **"approved"**. A local commit or local branch is fine; a push is not.
- **Banned strings:** the literals `Msc@2333`, `MSC-Person`, `broker.emqx.io` must NEVER appear in any diff. Scan every commit before making it.
- **No hardcoded credentials** anywhere. The repo is public; the updater needs none (it fetches public code only).
- **Worktree:** all work happens in `C:\Users\sky\AppData\Local\Temp\sdprs-node-mgmt-wt` on branch `design/edge-autoupdate-2026-08-22` (repo root IS the sdprs project here — paths are `scripts/…`, `edge_glass/…`, **no `sdprs/` prefix**).
- **Local test gate:** `bash scripts/tests/test_edge_autoupdate.sh` must be green. It is fully stubbed and must NOT require real `rsync`, `systemctl`, `tar`, `flock`, or network — those are absent on the dev box by design.
- **Linux-only gates** (`shellcheck`, `systemd-analyze verify`) are **bench/CI-gated**, not local blockers; locally use `bash -n <file>` for syntax and the grep-based unit test for the systemd files.
- **User-facing strings** (logs are operator-facing) in zh-TW where a message is a status a human reads on the dashboard; internal `journalctl` logs may stay English (matches `deploy_console.sh`). Keep log copy consistent with `deploy_console.sh`.
- **State files live OUTSIDE `edge_glass/`** (under `/opt/sdprs`, i.e. `STATE_DIR`) so rsync never touches them.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `scripts/edge_autoupdate.sh` (create) | The on-node updater. Root. All flow: lock, guards, ls-remote compare, clone, snapshot, rsync, restart, health-check, rollback, prune. Every command/path injectable. |
| `scripts/tests/test_edge_autoupdate.sh` (create) | Self-contained bash harness. Stubs git/rsync/systemctl/tar/chown; temp sandbox; asserts all §9 behaviors. The local gate. |
| `scripts/tests/test_edge_autoupdate_units.sh` (create) | Lightweight grep gate for the two systemd unit files + conf template (local stand-in for `systemd-analyze verify`). |
| `edge_glass/systemd/sdprs-edge-update.service` (create) | `Type=oneshot` root service that runs the updater. |
| `edge_glass/systemd/sdprs-edge-update.timer` (create) | Nightly `OnCalendar=03:00` + `RandomizedDelaySec=1800` + `Persistent=true`. |
| `edge_glass/systemd/sdprs-edge-update.conf` (create) | Config template sourced by the updater; installer copies it to `/etc/sdprs-edge-update.conf`. |
| `scripts/publish_edge_release.sh` (create) | Dev-workstation release helper: fast-forward `edge-release` to `main`'s tip and push. Run deliberately by a human. |
| `scripts/edge_autoupdate_install.sh` (create) | One-time `curl \| sudo bash` bootstrap: installs updater + units + conf, seeds `.edge_deployed_sha`, enables the timer. Idempotent. |
| `docs/edge-autoupdate-runbook.md` (create) | Ops runbook: publish a release, pause a node, read logs, manual rollback, bootstrap a node. |

---

## Task 1: On-node updater `edge_autoupdate.sh` + bash test harness

**Files:**
- Create: `scripts/edge_autoupdate.sh`
- Test: `scripts/tests/test_edge_autoupdate.sh`

**Interfaces:**
- Consumes: nothing from other tasks. Reads config from `${SDPRS_UPDATE_CONF:-/etc/sdprs-edge-update.conf}`; command hooks and test seams from env.
- Produces (contract other tasks + the harness rely on):
  - **Config keys** (set in conf, defaulted in-script): `REPO`, `BRANCH` (default `edge-release`), `DEST` (default `/opt/sdprs/edge_glass`), `SVC` (default `sdprs-edge-cloud`), `STATE_DIR` (default `/opt/sdprs`), `RUN_DIR` (default `/run/sdprs`), `QUIET_START` (`03:00`), `QUIET_END` (`05:00`), `HEALTH_TIMEOUT` (`60`), `HEALTH_INTERVAL` (`3`), `KEEP_SNAPSHOTS` (`3`), `OWNER` (`sdprs:sdprs`).
  - **Command hooks** (env, `:=` defaults, NOT in conf): `GIT`, `RSYNC`, `SYSTEMCTL`, `TAR`, `CHOWN`, `SLEEP`.
  - **Test seams** (env): `SDPRS_UPDATE_CONF` (conf path), `NOW_OVERRIDE` (`HH:MM` clock), plus stub-controlled vars the *stubs* read (`STUB_REMOTE_SHA`, `STUB_ISACTIVE`, `STUB_NRESTARTS`, `STUB_RSYNC_ITEMIZE`, `CALLS_LOG`).
  - **Derived paths:** `$STATE_DIR/.edge_deployed_sha`, `$STATE_DIR/.edge_update_paused`, `$RUN_DIR/update_hold`, `$STATE_DIR/.edge_update.lock` / `.lock.d`.
  - **CLI:** `--manual` (skip window + hold), `--dry-run` (no mutation). Combinable.
  - **Exit codes:** `0` = up-to-date / applied OK / deferred / paused / dry-run / ls-remote-failed; `1` = health-check failed after rollback.

- [ ] **Step 1: Write the test harness scaffold (stubs + assert helper), assertions still absent**

Create `scripts/tests/test_edge_autoupdate.sh`:

```bash
#!/usr/bin/env bash
# Self-contained black-box harness for scripts/edge_autoupdate.sh.
# Stubs git/rsync/systemctl/tar/chown via command-hook env vars; no real
# rsync/systemctl/tar/flock/network required. Runs on Linux and git-bash.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../edge_autoupdate.sh"
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL %s\n     %s\n' "$1" "${2:-}"; }
A()    { if [ "$2" = "1" ] || [ "$2" = "true" ]; then ok "$1"; else bad "$1" "${3:-}"; fi; }

# --- build a fresh sandbox (temp STATE_DIR/RUN_DIR/DEST, stub bin/, conf) ---
new_sandbox() {
  SB="$(mktemp -d)"
  mkdir -p "$SB/bin" "$SB/state/edge_glass/shared" "$SB/run"
  CALLS_LOG="$SB/calls.log"; : > "$CALLS_LOG"

  cat > "$SB/bin/git" <<'EOF'
#!/usr/bin/env bash
echo "git $*" >> "$CALLS_LOG"
case "$1" in
  ls-remote) printf '%s\trefs/heads/edge-release\n' "${STUB_REMOTE_SHA:-}";;
  clone)     dest="${@: -1}"; mkdir -p "$dest/edge_glass" "$dest/shared"; echo x > "$dest/edge_glass/m";;
  rev-parse) echo "${STUB_REMOTE_SHA:-0000000}";;
esac
exit 0
EOF
  cat > "$SB/bin/rsync" <<'EOF'
#!/usr/bin/env bash
echo "rsync $*" >> "$CALLS_LOG"
if [ -n "${STUB_RSYNC_ITEMIZE:-}" ]; then echo "$STUB_RSYNC_ITEMIZE"; fi
exit 0
EOF
  cat > "$SB/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$CALLS_LOG"
case "$1" in
  is-active) echo "${STUB_ISACTIVE:-active}";;
  show)      echo "${STUB_NRESTARTS:-0}";;
esac
exit 0
EOF
  cat > "$SB/bin/tar" <<'EOF'
#!/usr/bin/env bash
echo "tar $*" >> "$CALLS_LOG"
case "$1" in czf) : > "$2";; esac
exit 0
EOF
  cat > "$SB/bin/chown" <<'EOF'
#!/usr/bin/env bash
echo "chown $*" >> "$CALLS_LOG"; exit 0
EOF
  chmod +x "$SB"/bin/*

  cat > "$SB/conf" <<EOF
REPO="https://example.invalid/repo"
BRANCH="edge-release"
DEST="$SB/state/edge_glass"
SVC="sdprs-edge-cloud"
STATE_DIR="$SB/state"
RUN_DIR="$SB/run"
QUIET_START="03:00"
QUIET_END="05:00"
HEALTH_TIMEOUT="6"
HEALTH_INTERVAL="3"
KEEP_SNAPSHOTS="3"
OWNER="sdprs:sdprs"
EOF
}

# run the updater with all seams pointed at the sandbox; extra "KEY=VAL" env
# pairs may be passed as args, and a trailing set of script flags after "--".
run() {
  local env_pairs=() flags=()
  local seen_dd=0
  for a in "$@"; do
    if [ "$a" = "--" ]; then seen_dd=1; continue; fi
    if [ "$seen_dd" = "1" ]; then flags+=("$a"); else env_pairs+=("$a"); fi
  done
  env CALLS_LOG="$CALLS_LOG" \
      SDPRS_UPDATE_CONF="$SB/conf" \
      GIT="$SB/bin/git" RSYNC="$SB/bin/rsync" SYSTEMCTL="$SB/bin/systemctl" \
      TAR="$SB/bin/tar" CHOWN="$SB/bin/chown" SLEEP=: \
      "${env_pairs[@]}" \
      bash "$SCRIPT" "${flags[@]}"
}

calls() { cat "$CALLS_LOG"; }
sha()   { cat "$SB/state/.edge_deployed_sha" 2>/dev/null || echo "<none>"; }
cleanup(){ rm -rf "$SB"; }

echo "== edge_autoupdate.sh harness =="
# (assertions added in later steps)

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Add the "up-to-date", "paused", "window/hold deferred", and "dry-run" assertions**

Insert before the final `echo "PASS=..."` block:

```bash
# 1) up to date -> no clone, no restart, exit 0
new_sandbox
echo "abc" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=abc NOW_OVERRIDE=04:00
A "up-to-date exits 0" "$([ $? -eq 0 ] && echo 1)"
A "up-to-date does not clone" "$(calls | grep -q 'git clone' && echo 0 || echo 1)" "$(calls)"
A "up-to-date does not restart" "$(calls | grep -q 'systemctl restart' && echo 0 || echo 1)" "$(calls)"
cleanup

# 2) pause file present -> early exit, nothing touched
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
: > "$SB/state/.edge_update_paused"
run STUB_REMOTE_SHA=new NOW_OVERRIDE=04:00
A "paused exits 0" "$([ $? -eq 0 ] && echo 1)"
A "paused does not clone" "$(calls | grep -q 'git clone' && echo 0 || echo 1)" "$(calls)"
cleanup

# 3) outside quiet window -> deferred, no clone
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new NOW_OVERRIDE=12:00
A "outside-window exits 0" "$([ $? -eq 0 ] && echo 1)"
A "outside-window does not clone" "$(calls | grep -q 'git clone' && echo 0 || echo 1)" "$(calls)"
cleanup

# 4) active-alert hold (scheduled) -> deferred; --manual ignores it
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
echo 1 > "$SB/run/update_hold"
run STUB_REMOTE_SHA=new NOW_OVERRIDE=04:00
A "hold(scheduled) does not clone" "$(calls | grep -q 'git clone' && echo 0 || echo 1)" "$(calls)"
cleanup
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
echo 1 > "$SB/run/update_hold"
run STUB_REMOTE_SHA=new STUB_RSYNC_ITEMIZE=">f marker" STUB_ISACTIVE=active -- --manual
A "hold(--manual) proceeds to clone" "$(calls | grep -q 'git clone' && echo 1 || echo 0)" "$(calls)"
cleanup

# 5) dry-run -> update available but no clone/rsync/restart, SHA unchanged
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new NOW_OVERRIDE=04:00 -- --dry-run
A "dry-run exits 0" "$([ $? -eq 0 ] && echo 1)"
A "dry-run does not clone" "$(calls | grep -q 'git clone' && echo 0 || echo 1)" "$(calls)"
A "dry-run leaves SHA old" "$([ "$(sha)" = "old" ] && echo 1)" "$(sha)"
cleanup
```

- [ ] **Step 3: Run the harness — verify it fails because the script does not exist**

Run: `bash scripts/tests/test_edge_autoupdate.sh`
Expected: FAIL — the run() invocations error (`edge_autoupdate.sh: No such file or directory`) and assertions report FAIL; overall exit non-zero.

- [ ] **Step 4: Write `edge_autoupdate.sh` through the guard level (config, lock, guards, ls-remote compare, dry-run)**

Create `scripts/edge_autoupdate.sh`:

```bash
#!/usr/bin/env bash
# ============================================================
# SDPRS edge glass — on-node auto-updater (runs as root via systemd timer)
# ------------------------------------------------------------
# Brings this node to the edge-release tip if (and only if) it changed, with
# snapshot -> rsync -> restart -> health-check -> rollback. No dependency on
# the operator's LAN/VPN: the node fetches public code over its own internet,
# exactly as scripts/deploy_console.sh does.
#
# Modes:  (default) scheduled — honors quiet window + active-alert hold.
#         --manual            — skip window + hold (Phase 2 on-demand trigger).
#         --dry-run           — log intent, mutate nothing.
#
# All external commands + paths are injectable (env/config) so the bash test
# harness can stub them; see scripts/tests/test_edge_autoupdate.sh.
# ============================================================
set -uo pipefail

# --- config (conf sourced first; env command-hooks + defaults after) --------
CONF="${SDPRS_UPDATE_CONF:-/etc/sdprs-edge-update.conf}"
# shellcheck disable=SC1090
[ -r "$CONF" ] && . "$CONF"
: "${REPO:=https://github.com/Thomas-Tai/sdprs}"
: "${BRANCH:=edge-release}"
: "${DEST:=/opt/sdprs/edge_glass}"
: "${SVC:=sdprs-edge-cloud}"
: "${STATE_DIR:=/opt/sdprs}"
: "${RUN_DIR:=/run/sdprs}"
: "${QUIET_START:=03:00}"
: "${QUIET_END:=05:00}"
: "${HEALTH_TIMEOUT:=60}"
: "${HEALTH_INTERVAL:=3}"
: "${KEEP_SNAPSHOTS:=3}"
: "${OWNER:=sdprs:sdprs}"
: "${GIT:=git}"; : "${RSYNC:=rsync}"; : "${SYSTEMCTL:=systemctl}"
: "${TAR:=tar}"; : "${CHOWN:=chown}"; : "${SLEEP:=sleep}"

DEPLOYED_SHA_FILE="$STATE_DIR/.edge_deployed_sha"
PAUSE_FILE="$STATE_DIR/.edge_update_paused"
HOLD_FILE="$RUN_DIR/update_hold"
LOCK_FILE="$STATE_DIR/.edge_update.lock"
LOCK_DIR="$STATE_DIR/.edge_update.lock.d"

MANUAL=""; DRY=""
TMP=""; SNAP=""; CHANGED=""

log() { echo "[$(date '+%F %T')] edge-update: $*"; }

parse_args() {
  for a in "$@"; do
    case "$a" in
      --manual)  MANUAL=1 ;;
      --dry-run) DRY=1 ;;
      *) log "unknown arg: $a" ;;
    esac
  done
}

acquire_lock() {
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    flock -n 9 || { log "another run holds the lock — exiting"; exit 0; }
  else
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
      log "another run holds the lock — exiting"; exit 0
    fi
    trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
  fi
}

current_hhmm() { echo "${NOW_OVERRIDE:-$(date +%H:%M)}"; }

# quiet window assumed non-wrapping (QUIET_START < QUIET_END); zero-padded
# HH:MM compares correctly as strings.
in_window() {
  local now; now="$(current_hhmm)"
  [[ "$now" > "$QUIET_START" || "$now" == "$QUIET_START" ]] && [[ "$now" < "$QUIET_END" ]]
}

held() {
  [ -f "$HOLD_FILE" ] && [ "$(cat "$HOLD_FILE" 2>/dev/null)" = "1" ]
}

cleanup_tmp() { [ -n "$TMP" ] && rm -rf "$TMP" 2>/dev/null || true; }

main() {
  parse_args "$@"
  acquire_lock

  if [ -f "$PAUSE_FILE" ]; then
    log "paused (.edge_update_paused present) — exiting"; exit 0
  fi

  if [ -z "$MANUAL" ]; then
    if ! in_window; then
      log "outside quiet window ($QUIET_START-$QUIET_END, now $(current_hhmm)) — deferred"; exit 0
    fi
    if held; then
      log "active-alert hold set — deferred"; exit 0
    fi
  fi

  local remote local_sha
  remote="$("$GIT" ls-remote "$REPO" "refs/heads/$BRANCH" 2>/dev/null | cut -f1)"
  if [ -z "$remote" ]; then
    log "ls-remote failed (network?) — will retry next window"; exit 0
  fi
  local_sha="$(cat "$DEPLOYED_SHA_FILE" 2>/dev/null || true)"
  if [ "$remote" = "$local_sha" ]; then
    log "up to date ($remote) — no action"; exit 0
  fi
  log "update available: ${local_sha:-<none>} -> $remote"

  if [ -n "$DRY" ]; then
    log "[dry-run] would clone+snapshot+rsync+restart to $remote"; exit 0
  fi

  apply_update "$remote"   # sets CHANGED; populates SNAP/TMP

  if [ -z "$CHANGED" ]; then
    log "edge_glass bytes unchanged — recording SHA, skipping restart"
    echo "$remote" > "$DEPLOYED_SHA_FILE"
    cleanup_tmp; exit 0
  fi

  "$SYSTEMCTL" restart "$SVC"
  if health_check; then
    echo "$remote" > "$DEPLOYED_SHA_FILE"
    prune_snapshots
    cleanup_tmp
    log "DONE — updated to $remote, $SVC active"
    exit 0
  else
    log "!! health-check FAILED — rolling back"
    rollback
    cleanup_tmp
    exit 1
  fi
}

# apply_update / health_check / rollback / prune_snapshots defined in Step 6.

main "$@"
```

At this point `apply_update`, `health_check`, `rollback`, and `prune_snapshots` are still undefined — that is deliberate; Step 5 confirms the guard-level cases already pass and the apply cases fail cleanly.

- [ ] **Step 5: Run the harness — guard/dry-run cases pass; apply cases (not yet asserted) not reached**

Run: `bash scripts/tests/test_edge_autoupdate.sh`
Expected: The 5 assertion groups from Step 2 all PASS **except** the `hold(--manual) proceeds to clone` case, which reaches the undefined `apply_update` and errors (`apply_update: command not found`). That is expected — Step 7 defines it. Do not commit yet.

- [ ] **Step 6: Add the apply / rollback / itemize-empty assertions to the harness**

Insert before the final `echo "PASS=..."` block:

```bash
# 6) update applies -> clone+snapshot+rsync+restart, SHA advances to remote
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new STUB_RSYNC_ITEMIZE=">f marker" STUB_ISACTIVE=active NOW_OVERRIDE=04:00
rc=$?
A "apply exits 0" "$([ $rc -eq 0 ] && echo 1)" "rc=$rc"
A "apply clones" "$(calls | grep -q 'git clone' && echo 1 || echo 0)" "$(calls)"
A "apply snapshots (tar czf)" "$(calls | grep -q 'tar czf' && echo 1 || echo 0)" "$(calls)"
A "apply rsyncs" "$(calls | grep -q 'rsync' && echo 1 || echo 0)" "$(calls)"
A "apply restarts service" "$(calls | grep -q 'systemctl restart' && echo 1 || echo 0)" "$(calls)"
A "apply advances SHA to new" "$([ "$(sha)" = "new" ] && echo 1)" "$(sha)"
cleanup

# 7) health-check fails -> rollback (restore + restart), SHA stays old, exit 1
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new STUB_RSYNC_ITEMIZE=">f marker" STUB_ISACTIVE=failed NOW_OVERRIDE=04:00
rc=$?
A "rollback exits non-zero" "$([ $rc -ne 0 ] && echo 1)" "rc=$rc"
A "rollback restores snapshot (tar xzf)" "$(calls | grep -q 'tar xzf' && echo 1 || echo 0)" "$(calls)"
A "rollback leaves SHA old" "$([ "$(sha)" = "old" ] && echo 1)" "$(sha)"
cleanup

# 8) remote changed but rsync itemize empty -> record SHA, NO restart
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new NOW_OVERRIDE=04:00   # STUB_RSYNC_ITEMIZE unset => empty
rc=$?
A "itemize-empty exits 0" "$([ $rc -eq 0 ] && echo 1)" "rc=$rc"
A "itemize-empty records SHA new" "$([ "$(sha)" = "new" ] && echo 1)" "$(sha)"
A "itemize-empty does NOT restart" "$(calls | grep -q 'systemctl restart' && echo 0 || echo 1)" "$(calls)"
cleanup
```

- [ ] **Step 7: Implement `apply_update`, `health_check`, `rollback`, `prune_snapshots`**

In `scripts/edge_autoupdate.sh`, replace the comment line
`# apply_update / health_check / rollback / prune_snapshots defined in Step 6.`
with:

```bash
apply_update() {
  local remote="$1" stamp
  stamp="$(date +%Y%m%d-%H%M%S)"
  TMP="$(mktemp -d)"; rm -rf "$TMP"
  "$GIT" clone -q --depth 1 --branch "$BRANCH" "$REPO" "$TMP"

  SNAP="$STATE_DIR/edge_glass.backup.$stamp.tgz"
  "$TAR" czf "$SNAP" \
    --exclude='edge_glass/venv' --exclude='edge_glass/events' --exclude='edge_glass/buffer' \
    -C "$STATE_DIR" edge_glass

  local out1 out2
  out1="$("$RSYNC" -ai \
    --exclude='config.zeabur.yaml' --exclude='config.yaml' --exclude='.env' --exclude='.env.*' \
    --exclude='venv/' --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.log' \
    --exclude='events/' --exclude='buffer/' --exclude='data/' \
    "$TMP/edge_glass/" "$DEST/")"
  out2="$("$RSYNC" -ai --exclude='__pycache__/' --exclude='*.pyc' \
    "$TMP/shared/" "$DEST/shared/")"
  "$CHOWN" -R "$OWNER" "$DEST"

  if [ -n "$out1$out2" ]; then CHANGED=1; else CHANGED=""; fi
}

health_check() {
  local polls base i active cur
  polls=$(( HEALTH_TIMEOUT / HEALTH_INTERVAL )); [ "$polls" -lt 1 ] && polls=1
  base="$("$SYSTEMCTL" show -p NRestarts --value "$SVC" 2>/dev/null || echo 0)"
  for ((i=0; i<polls; i++)); do
    "$SLEEP" "$HEALTH_INTERVAL"
    active="$("$SYSTEMCTL" is-active "$SVC" 2>/dev/null || echo inactive)"
    cur="$("$SYSTEMCTL" show -p NRestarts --value "$SVC" 2>/dev/null || echo 0)"
    if [ "$active" != "active" ] || [ "$cur" -gt "$base" ]; then
      return 1
    fi
  done
  return 0
}

rollback() {
  [ -n "$SNAP" ] && [ -f "$SNAP" ] || { log "no snapshot to roll back to ($SNAP)"; return 1; }
  "$TAR" xzf "$SNAP" -C "$STATE_DIR"
  "$CHOWN" -R "$OWNER" "$DEST"
  "$SYSTEMCTL" restart "$SVC"
  # deliberately DO NOT touch $DEPLOYED_SHA_FILE — node retries same target.
  log "rolled back to snapshot $SNAP"
}

prune_snapshots() {
  local keep="$KEEP_SNAPSHOTS" f
  # newest-first; delete everything past the newest $keep
  ls -1t "$STATE_DIR"/edge_glass.backup.*.tgz 2>/dev/null | tail -n +"$((keep+1))" | while read -r f; do
    rm -f "$f"
  done
}
```

- [ ] **Step 8: Run the full harness — all assertions pass**

Run: `bash scripts/tests/test_edge_autoupdate.sh`
Expected: `PASS=<N> FAIL=0`, exit 0. Then syntax-check both files:
Run: `bash -n scripts/edge_autoupdate.sh && bash -n scripts/tests/test_edge_autoupdate.sh`
Expected: no output, exit 0.
(If `shellcheck` happens to be installed: `shellcheck scripts/edge_autoupdate.sh` — clean. It is bench/CI-gated otherwise.)

- [ ] **Step 9: Commit**

```bash
git add scripts/edge_autoupdate.sh scripts/tests/test_edge_autoupdate.sh
git commit -m "feat(edge): on-node auto-updater with snapshot/health-check/rollback + bash harness"
```

---

## Task 2: systemd units + config template (+ local grep gate)

**Files:**
- Create: `edge_glass/systemd/sdprs-edge-update.service`
- Create: `edge_glass/systemd/sdprs-edge-update.timer`
- Create: `edge_glass/systemd/sdprs-edge-update.conf`
- Test: `scripts/tests/test_edge_autoupdate_units.sh`

**Interfaces:**
- Consumes: `scripts/edge_autoupdate.sh` installed at `/opt/sdprs/edge_autoupdate.sh` (the service's `ExecStart`); the config keys defined in Task 1.
- Produces: `sdprs-edge-update.service` (oneshot, root), `sdprs-edge-update.timer` (nightly staggered), `/etc/sdprs-edge-update.conf` template — all consumed by the Task 3 installer.

- [ ] **Step 1: Write the unit-file grep gate (fails — files absent)**

Create `scripts/tests/test_edge_autoupdate_units.sh`:

```bash
#!/usr/bin/env bash
# Local stand-in for `systemd-analyze verify` (Linux-only). Greps the unit
# files + conf template for the directives Phase 1 depends on.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYS="$HERE/../../edge_glass/systemd"
PASS=0; FAIL=0
has() { if grep -qE "$2" "$SYS/$1" 2>/dev/null; then PASS=$((PASS+1)); echo "  ok   $1 :: $2"; else FAIL=$((FAIL+1)); echo "  FAIL $1 :: $2"; fi; }

has sdprs-edge-update.service '^\[Service\]'
has sdprs-edge-update.service '^Type=oneshot'
has sdprs-edge-update.service '^ExecStart=/opt/sdprs/edge_autoupdate\.sh'
has sdprs-edge-update.service '^User=root'
has sdprs-edge-update.service '^SyslogIdentifier=sdprs-edge-update'

has sdprs-edge-update.timer '^\[Timer\]'
has sdprs-edge-update.timer '^OnCalendar=\*-\*-\* 03:00:00'
has sdprs-edge-update.timer '^RandomizedDelaySec=1800'
has sdprs-edge-update.timer '^Persistent=true'
has sdprs-edge-update.timer '^\[Install\]'
has sdprs-edge-update.timer '^WantedBy=timers\.target'

has sdprs-edge-update.conf '^BRANCH="edge-release"'
has sdprs-edge-update.conf '^QUIET_START="03:00"'
has sdprs-edge-update.conf '^KEEP_SNAPSHOTS="3"'

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Run it — verify it fails (unit files not yet created)**

Run: `bash scripts/tests/test_edge_autoupdate_units.sh`
Expected: every assertion FAIL, exit non-zero.

- [ ] **Step 3: Create the service unit**

Create `edge_glass/systemd/sdprs-edge-update.service`:

```ini
# SDPRS edge glass auto-update — oneshot, run by sdprs-edge-update.timer.
# Installed to /etc/systemd/system/ by scripts/edge_autoupdate_install.sh.
#
# 啟用: sudo systemctl enable --now sdprs-edge-update.timer
# 日誌: journalctl -u sdprs-edge-update -f
[Unit]
Description=SDPRS edge glass auto-update (rsync edge-release, health-check, rollback)
Documentation=https://github.com/Thomas-Tai/sdprs
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
ExecStart=/opt/sdprs/edge_autoupdate.sh
StandardOutput=journal
StandardError=journal
SyslogIdentifier=sdprs-edge-update
# clone + rsync + health-check window (60s) can take a couple of minutes.
TimeoutStartSec=600
```

- [ ] **Step 4: Create the timer unit**

Create `edge_glass/systemd/sdprs-edge-update.timer`:

```ini
# Nightly, staggered edge auto-update. RandomizedDelaySec spreads the fleet
# across 03:00-03:30 with no cross-node coordination; Persistent catches up a
# missed run after the node was off (the updater's own window guard prevents a
# daytime catch-up from restarting the camera).
[Unit]
Description=SDPRS edge glass auto-update timer (nightly, staggered)
Documentation=https://github.com/Thomas-Tai/sdprs

[Timer]
OnCalendar=*-*-* 03:00:00
RandomizedDelaySec=1800
Persistent=true
Unit=sdprs-edge-update.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 5: Create the config template**

Create `edge_glass/systemd/sdprs-edge-update.conf`:

```bash
# SDPRS edge auto-update config — sourced by /opt/sdprs/edge_autoupdate.sh.
# Installer copies this to /etc/sdprs-edge-update.conf (only if absent, so
# per-node edits survive re-bootstrap). Tunable without touching the script.
REPO="https://github.com/Thomas-Tai/sdprs"
BRANCH="edge-release"
DEST="/opt/sdprs/edge_glass"
SVC="sdprs-edge-cloud"
STATE_DIR="/opt/sdprs"
RUN_DIR="/run/sdprs"
QUIET_START="03:00"
QUIET_END="05:00"
HEALTH_TIMEOUT="60"
HEALTH_INTERVAL="3"
KEEP_SNAPSHOTS="3"
OWNER="sdprs:sdprs"
```

- [ ] **Step 6: Run the grep gate — passes; syntax-check nothing (ini). Bench note.**

Run: `bash scripts/tests/test_edge_autoupdate_units.sh`
Expected: `PASS=14 FAIL=0`, exit 0.
Bench (Linux, not local): `systemd-analyze verify edge_glass/systemd/sdprs-edge-update.service edge_glass/systemd/sdprs-edge-update.timer` — must be clean. Record this as a deployment-gate step, not a local blocker.

- [ ] **Step 7: Commit**

```bash
git add edge_glass/systemd/sdprs-edge-update.service edge_glass/systemd/sdprs-edge-update.timer edge_glass/systemd/sdprs-edge-update.conf scripts/tests/test_edge_autoupdate_units.sh
git commit -m "feat(edge): systemd timer/service + conf template for auto-update"
```

---

## Task 3: Release-publish helper + bootstrap installer

**Files:**
- Create: `scripts/publish_edge_release.sh`
- Create: `scripts/edge_autoupdate_install.sh`

**Interfaces:**
- Consumes: the units + conf from Task 2 (installer copies them); `scripts/edge_autoupdate.sh` from Task 1 (installer installs it).
- Produces: `publish_edge_release.sh` (dev-side release gate; a human runs it) and `edge_autoupdate_install.sh` (`curl | sudo bash` node bootstrap). Neither runs during implementation.

- [ ] **Step 1: Create the release-publish helper**

Create `scripts/publish_edge_release.sh`:

```bash
#!/usr/bin/env bash
# Publish a release to the fleet: fast-forward the edge-release branch to a
# source ref's tip and push. The fleet's auto-updater tracks edge-release, so
# THIS is the deliberate release gate. Run on the dev workstation.
#
# Usage:  scripts/publish_edge_release.sh [source-ref]   # default: main
# The server enforces fast-forward: a non-FF push is rejected (never rewrites).
set -euo pipefail
REMOTE="${SDPRS_REMOTE:-origin}"
SRC="${1:-main}"

echo "Fetching $REMOTE/$SRC ..."
git fetch "$REMOTE" "$SRC"
TIP="$(git rev-parse "$REMOTE/$SRC")"

echo "Publishing $REMOTE/$SRC ($TIP) -> $REMOTE edge-release (fast-forward only)"
git push "$REMOTE" "$TIP:refs/heads/edge-release"
echo "edge-release now at: $TIP"
echo "Fleet nodes will pick this up in their next quiet window (03:00-03:30)."
```

- [ ] **Step 2: Syntax-check the helper**

Run: `bash -n scripts/publish_edge_release.sh`
Expected: no output, exit 0. (`shellcheck` if available — clean.)

- [ ] **Step 3: Create the bootstrap installer**

Create `scripts/edge_autoupdate_install.sh`:

```bash
#!/usr/bin/env bash
# ============================================================
# SDPRS edge auto-update — one-time bootstrap (run ON the Raspberry Pi).
#   curl -fsSL https://raw.githubusercontent.com/Thomas-Tai/sdprs/edge-release/scripts/edge_autoupdate_install.sh | sudo bash
# Idempotent: safe to re-run (this is also how the updater script itself is
# updated). Seeds .edge_deployed_sha with the current edge-release tip so the
# first scheduled run is a no-op until a newer release is published.
# ============================================================
set -euo pipefail
REPO="${SDPRS_REPO:-https://github.com/Thomas-Tai/sdprs}"
BRANCH="${SDPRS_BRANCH:-edge-release}"

if [ "$(id -u)" -ne 0 ]; then echo "ERROR: run with sudo"; exit 1; fi
if [ ! -d /opt/sdprs/edge_glass ]; then echo "ERROR: /opt/sdprs/edge_glass not found — is this a commissioned glass node?"; exit 1; fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
echo "[1/5] clone $BRANCH"
git clone -q --depth 1 --branch "$BRANCH" "$REPO" "$TMP"

echo "[2/5] install updater + units"
install -m 0755 "$TMP/scripts/edge_autoupdate.sh"                    /opt/sdprs/edge_autoupdate.sh
install -m 0644 "$TMP/edge_glass/systemd/sdprs-edge-update.service"  /etc/systemd/system/sdprs-edge-update.service
install -m 0644 "$TMP/edge_glass/systemd/sdprs-edge-update.timer"    /etc/systemd/system/sdprs-edge-update.timer
if [ ! -f /etc/sdprs-edge-update.conf ]; then
  install -m 0644 "$TMP/edge_glass/systemd/sdprs-edge-update.conf"   /etc/sdprs-edge-update.conf
  echo "      wrote /etc/sdprs-edge-update.conf (default)"
else
  echo "      kept existing /etc/sdprs-edge-update.conf"
fi

echo "[3/5] seed deployed SHA + runtime dir"
mkdir -p /run/sdprs
git -C "$TMP" rev-parse HEAD > /opt/sdprs/.edge_deployed_sha
chown sdprs:sdprs /opt/sdprs/.edge_deployed_sha 2>/dev/null || true

echo "[4/5] enable timer"
systemctl daemon-reload
systemctl enable --now sdprs-edge-update.timer

echo "[5/5] status"
systemctl list-timers sdprs-edge-update.timer --no-pager || true
echo "DONE — auto-update installed on $(hostname); seeded SHA $(cat /opt/sdprs/.edge_deployed_sha)"
```

- [ ] **Step 4: Syntax-check the installer**

Run: `bash -n scripts/edge_autoupdate_install.sh`
Expected: no output, exit 0. (`shellcheck` if available — clean.)

- [ ] **Step 5: Commit**

```bash
git add scripts/publish_edge_release.sh scripts/edge_autoupdate_install.sh
git commit -m "feat(edge): edge-release publish helper + curl|bash bootstrap installer"
```

---

## Task 4: Ops runbook

**Files:**
- Create: `docs/edge-autoupdate-runbook.md`

**Interfaces:**
- Consumes: everything above (documents how to operate it).
- Produces: the operator-facing runbook. No test; the deliverable is the doc.

- [ ] **Step 1: Write the runbook**

Create `docs/edge-autoupdate-runbook.md`:

```markdown
# Edge glass auto-update — operator runbook (Phase 1)

The glass fleet self-updates nightly from the **`edge-release`** branch:
each node, in a staggered 03:00–03:30 window, brings itself to the
`edge-release` tip with snapshot → rsync → health-check → rollback. No
workstation, LAN, or VPN involved — each Pi fetches public code itself.

## Cut a release (dev workstation)
Publishing = fast-forwarding `edge-release` to `main`'s tip:

    scripts/publish_edge_release.sh          # publishes origin/main
    scripts/publish_edge_release.sh <ref>    # publishes another ref

Nodes apply it during their next quiet window. To roll the fleet back to an
earlier good commit, `publish_edge_release.sh` cannot (it is FF-only) — reset
`edge-release` deliberately: `git push origin <good-sha>:refs/heads/edge-release --force-with-lease`
(a considered, manual action).

## Bootstrap a node (once, on the Pi)

    curl -fsSL https://raw.githubusercontent.com/Thomas-Tai/sdprs/edge-release/scripts/edge_autoupdate_install.sh | sudo bash

Idempotent — re-run to update the updater itself.

## Pause / resume a node (maintenance)

    sudo touch /opt/sdprs/.edge_update_paused     # skip all auto-updates
    sudo rm -f /opt/sdprs/.edge_update_paused     # resume

## Observe

    systemctl list-timers sdprs-edge-update.timer      # next run
    journalctl -u sdprs-edge-update -n 50 --no-pager   # last run's log
    cat /opt/sdprs/.edge_deployed_sha                  # what's deployed

## Force an update now (bypasses window + hold)

    sudo /opt/sdprs/edge_autoupdate.sh --manual
    sudo /opt/sdprs/edge_autoupdate.sh --dry-run       # show intent only

## Manual rollback
Automatic rollback runs on a failed health-check. To roll back by hand to a
snapshot (kept at `/opt/sdprs/edge_glass.backup.<stamp>.tgz`, newest
`KEEP_SNAPSHOTS`=3):

    sudo systemctl stop sdprs-edge-cloud
    sudo tar xzf /opt/sdprs/edge_glass.backup.<stamp>.tgz -C /opt/sdprs
    sudo chown -R sdprs:sdprs /opt/sdprs/edge_glass
    sudo systemctl start sdprs-edge-cloud

## Config
Per-node knobs in `/etc/sdprs-edge-update.conf` (quiet window, health timeout,
snapshot retention). `deploy_console.sh` remains the ad-hoc "pull main now"
tool and coexists with this timer.
```

- [ ] **Step 2: Commit**

```bash
git add docs/edge-autoupdate-runbook.md
git commit -m "docs(edge): auto-update operator runbook (publish, pause, observe, rollback)"
```

---

## Deployment gates (NOT implementation — bench/site + push-approval)

These are **out of scope for the code tasks** and happen only under the user's
explicit **"approved"** and on real hardware:

1. **Create + push `edge-release`** at the current `main` tip (first publish):
   `scripts/publish_edge_release.sh` — this is a push (gated on "approved").
2. **`systemd-analyze verify`** the two unit files on a Linux node — clean.
3. **`shellcheck`** all four scripts in CI/bench — clean.
4. **Bench-verify on one node** (per spec §13): bootstrap → confirm timer armed
   + `.edge_deployed_sha` seeded → publish a trivial `edge-release` bump →
   confirm the node updates in-window, service returns active, SHA advances →
   force a bad release → confirm rollback restores service and SHA is NOT
   advanced.
5. Roll the bootstrap to the other two nodes.

---

## Self-Review

**1. Spec coverage** (spec §-by-§):
- §4 state files → Task 1 (`DEPLOYED_SHA_FILE`, `PAUSE_FILE`, `HOLD_FILE`, derived in-script; live outside `edge_glass/`). ✅
- §5 updater flow (lock → pause → window/hold → ls-remote compare → clone → snapshot → rsync → itemize-skip → restart → health → rollback; overridable command vars; `--dry-run`/`--manual`) → Task 1 Steps 4 & 7, asserted in Steps 2 & 6. ✅
- §6 health-check (is-active + NRestarts not climbing, timeout) + rollback (restore, SHA unchanged) → Task 1 Step 7 (`health_check`, `rollback`), asserted Step 6 case 7. ✅
- §7 systemd units + conf → Task 2. ✅
- §8 bootstrap → Task 3 Step 3. ✅
- §9 testing (shellcheck + bash harness w/ all 6 behavior classes + systemd-analyze) → Task 1 harness (up-to-date/apply/rollback/pause/hold+manual/dry-run/itemize) + Task 2 grep gate + deployment gate #2/#3. ✅
- §10 `edge-release` + publish helper → Task 3 Step 1 + deployment gate #1. ✅
- §11 edge cases: network-down (ls-remote empty → exit 0, Task 1 main); Pi-off catch-up (Persistent + window guard, Task 2 timer + Task 1 `in_window`); docs-only bump (itemize-empty, Task 1 case 8); overlap (lock, Task 1 `acquire_lock`); bad-but-healthy release (staging gate, documented). ✅
- §12 files created → all present across Tasks 1–4. ✅
- §13 verification → deployment gates section. ✅
- §14 deferred Phase 2 → explicitly out of scope; no task. ✅

**2. Placeholder scan:** No "TBD/TODO/implement later"; every code step carries full code. ✅

**3. Type/name consistency:** config keys (`REPO/BRANCH/DEST/SVC/STATE_DIR/RUN_DIR/QUIET_START/QUIET_END/HEALTH_TIMEOUT/HEALTH_INTERVAL/KEEP_SNAPSHOTS/OWNER`) identical across Task 1 script, Task 2 conf template, and Task 3 installer. Command hooks (`GIT/RSYNC/SYSTEMCTL/TAR/CHOWN/SLEEP`) consistent between script and harness stubs. Function names (`apply_update/health_check/rollback/prune_snapshots/in_window/held/acquire_lock`) defined and called consistently. `ExecStart=/opt/sdprs/edge_autoupdate.sh` matches the installer's `install` destination. Harness stub for `git rev-parse` returns `STUB_REMOTE_SHA` (used only by the installer path, not the updater) — harmless. ✅

One intentional refinement vs the spec wording: the lock uses **flock when present, atomic `mkdir` fallback otherwise** (flock is absent on the dev git-bash; both give the same mutual-exclusion guarantee). Noted in-script.
