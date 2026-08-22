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
  clone)
    if [ "${STUB_CLONE_FAIL:-}" = "1" ]; then exit 1; fi
    dest="${@: -1}"; mkdir -p "$dest/edge_glass" "$dest/shared"; echo x > "$dest/edge_glass/m";;
  rev-parse) echo "${STUB_REMOTE_SHA:-0000000}";;
esac
exit 0
EOF
  cat > "$SB/bin/rsync" <<'EOF'
#!/usr/bin/env bash
echo "rsync $*" >> "$CALLS_LOG"
if [ -n "${STUB_RSYNC_ITEMIZE:-}" ]; then echo "$STUB_RSYNC_ITEMIZE"; fi
if [ "${STUB_RSYNC_FAIL:-}" = "1" ]; then exit 1; fi
exit 0
EOF
  cat > "$SB/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$CALLS_LOG"
case "$1" in
  is-active) echo "${STUB_ISACTIVE:-active}";;
  show)
    if [ "${STUB_NRESTARTS_CLIMB:-}" = "1" ]; then
      f="${STUB_NRESTARTS_FILE:-/tmp/.stub_nr_default}"
      n="$(cat "$f" 2>/dev/null || echo 0)"
      n=$((n+1))
      echo "$n" > "$f"
      echo "$n"
    else
      echo "${STUB_NRESTARTS:-0}"
    fi
    ;;
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

# 9) clone fails -> apply_update aborts, SHA unchanged, no restart, exit non-zero
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new STUB_CLONE_FAIL=1 NOW_OVERRIDE=04:00
rc=$?
A "clone-fail exits non-zero" "$([ $rc -ne 0 ] && echo 1)" "rc=$rc"
A "clone-fail leaves SHA old" "$([ "$(sha)" = "old" ] && echo 1)" "$(sha)"
A "clone-fail does not restart" "$(calls | grep -q 'systemctl restart' && echo 0 || echo 1)" "$(calls)"
cleanup

# 10) rsync fails -> restore snapshot to keep DEST consistent, SHA unchanged, no restart
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new STUB_RSYNC_FAIL=1 NOW_OVERRIDE=04:00
rc=$?
A "rsync-fail exits non-zero" "$([ $rc -ne 0 ] && echo 1)" "rc=$rc"
A "rsync-fail leaves SHA old" "$([ "$(sha)" = "old" ] && echo 1)" "$(sha)"
A "rsync-fail restores snapshot (tar xzf)" "$(calls | grep -q 'tar xzf' && echo 1 || echo 0)" "$(calls)"
A "rsync-fail does not restart" "$(calls | grep -q 'systemctl restart' && echo 0 || echo 1)" "$(calls)"
cleanup

# 11) ls-remote fails (empty remote sha / network down) -> exit 0, no clone, SHA unchanged
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA= NOW_OVERRIDE=04:00
rc=$?
A "ls-remote-fail exits 0" "$([ $rc -eq 0 ] && echo 1)" "rc=$rc"
A "ls-remote-fail does not clone" "$(calls | grep -q 'git clone' && echo 0 || echo 1)" "$(calls)"
A "ls-remote-fail leaves SHA old" "$([ "$(sha)" = "old" ] && echo 1)" "$(sha)"
cleanup

# 12) health-check fails via climbing NRestarts (crash-loop) -> rollback, SHA stays old, exit non-zero
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new STUB_RSYNC_ITEMIZE=">f marker" STUB_ISACTIVE=active \
    STUB_NRESTARTS_CLIMB=1 STUB_NRESTARTS_FILE="$SB/nr" NOW_OVERRIDE=04:00
rc=$?
A "climb-rollback exits non-zero" "$([ $rc -ne 0 ] && echo 1)" "rc=$rc"
A "climb-rollback restores snapshot (tar xzf)" "$(calls | grep -q 'tar xzf' && echo 1 || echo 0)" "$(calls)"
A "climb-rollback leaves SHA old" "$([ "$(sha)" = "old" ] && echo 1)" "$(sha)"
cleanup

# 13) prune_snapshots keeps only the newest KEEP_SNAPSHOTS (3) after a successful apply
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
for i in 1 2 3 4; do
  f="$SB/state/edge_glass.backup.2026010${i}-000000.tgz"
  : > "$f"
  touch -d "2026-01-0${i}T00:00:00" "$f"
done
run STUB_REMOTE_SHA=new STUB_RSYNC_ITEMIZE=">f marker" STUB_ISACTIVE=active NOW_OVERRIDE=04:00
remaining=$(ls "$SB/state"/edge_glass.backup.*.tgz 2>/dev/null | wc -l | tr -d ' ')
A "prune keeps only KEEP_SNAPSHOTS(3) files" "$([ "$remaining" = "3" ] && echo 1)" \
  "remaining=$remaining $(ls "$SB/state"/edge_glass.backup.*.tgz 2>/dev/null)"
cleanup

# 14) in_window boundary: NOW_OVERRIDE=03:00 (inclusive start) -> guard passes, reaches ls-remote
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new NOW_OVERRIDE=03:00 -- --dry-run
rc=$?
A "window start (03:00) exits 0" "$([ $rc -eq 0 ] && echo 1)" "rc=$rc"
A "window start (03:00) passes guard (reaches ls-remote)" "$(calls | grep -q 'git ls-remote' && echo 1 || echo 0)" "$(calls)"
cleanup

# 15) in_window boundary: NOW_OVERRIDE=05:00 (exclusive end) -> deferred, never reaches ls-remote
new_sandbox
echo "old" > "$SB/state/.edge_deployed_sha"
run STUB_REMOTE_SHA=new NOW_OVERRIDE=05:00
rc=$?
A "window end (05:00) exits 0" "$([ $rc -eq 0 ] && echo 1)" "rc=$rc"
A "window end (05:00) does not reach ls-remote" "$(calls | grep -q 'git ls-remote' && echo 0 || echo 1)" "$(calls)"
cleanup

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
