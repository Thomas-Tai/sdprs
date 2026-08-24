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
: "${HOLD_MAX_AGE:=300}"
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
      *) log "unknown arg: $a — aborting"; exit 2 ;;
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

  if ! apply_update "$remote"; then   # sets CHANGED; populates SNAP/TMP
    log "!! update apply failed — SHA not advanced, retry next window"
    cleanup_tmp
    exit 1
  fi

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

apply_update() {
  local remote="$1" stamp
  stamp="$(date +%Y%m%d-%H%M%S)"
  TMP="$(mktemp -d)"
  if ! "$GIT" clone -q --depth 1 --branch "$BRANCH" "$REPO" "$TMP"; then
    log "!! git clone failed — aborting update, SHA not advanced"
    cleanup_tmp
    return 1
  fi

  SNAP="$STATE_DIR/edge_glass.backup.$stamp.tgz"
  if ! "$TAR" czf "$SNAP" \
    --exclude='edge_glass/venv' --exclude='edge_glass/events' --exclude='edge_glass/buffer' \
    -C "$STATE_DIR" edge_glass; then
    log "!! snapshot (tar czf) failed — aborting update, SHA not advanced"
    cleanup_tmp
    return 1
  fi

  local out1 out2 rc1 rc2
  out1="$("$RSYNC" -aic \
    --exclude='config.zeabur.yaml' --exclude='config.yaml' --exclude='.env' --exclude='.env.*' \
    --exclude='venv/' --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.log' \
    --exclude='events/' --exclude='buffer/' --exclude='data/' \
    "$TMP/edge_glass/" "$DEST/")"; rc1=$?
  out2="$("$RSYNC" -aic --exclude='__pycache__/' --exclude='*.pyc' \
    "$TMP/shared/" "$DEST/shared/")"; rc2=$?

  if [ "$rc1" -ne 0 ] || [ "$rc2" -ne 0 ]; then
    log "!! rsync failed (rc1=$rc1 rc2=$rc2) — restoring snapshot to keep DEST consistent, SHA not advanced"
    restore_snapshot
    cleanup_tmp
    return 1
  fi

  "$CHOWN" -R "$OWNER" "$DEST"

  if [ -n "$out1$out2" ]; then CHANGED=1; else CHANGED=""; fi
  return 0
}

health_check() {
  local polls base i active cur
  [ "${HEALTH_INTERVAL:-0}" -lt 1 ] && HEALTH_INTERVAL=1
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

restore_snapshot() {
  [ -n "$SNAP" ] && [ -f "$SNAP" ] || { log "no snapshot to restore ($SNAP)"; return 1; }
  "$TAR" xzf "$SNAP" -C "$STATE_DIR"
  "$CHOWN" -R "$OWNER" "$DEST"
}

rollback() {
  restore_snapshot || return 1
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

main "$@"
