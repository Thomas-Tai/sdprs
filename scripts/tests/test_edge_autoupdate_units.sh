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

# --- Phase 2: manual on-demand unit ---
has sdprs-edge-update-manual.service '^\[Service\]'
has sdprs-edge-update-manual.service '^Type=oneshot'
has sdprs-edge-update-manual.service '^ExecStart=/opt/sdprs/edge_autoupdate\.sh --manual'
has sdprs-edge-update-manual.service '^User=root'
has sdprs-edge-update-manual.service '^SyslogIdentifier=sdprs-edge-update'

absent() { if ! grep -qE "$2" "$SYS/$1" 2>/dev/null; then PASS=$((PASS+1)); echo "  ok   $1 :: absent $2"; else FAIL=$((FAIL+1)); echo "  FAIL $1 :: unexpected $2"; fi; }
absent sdprs-edge-update-manual.service '^\[Install\]'

# --- Phase 2: installer wires sudoers (visudo-gated) + installs manual unit ---
INST="$HERE/../edge_autoupdate_install.sh"
hasf() { if grep -qE "$2" "$1" 2>/dev/null; then PASS=$((PASS+1)); echo "  ok   $(basename "$1") :: $2"; else FAIL=$((FAIL+1)); echo "  FAIL $(basename "$1") :: $2"; fi; }
hasf "$INST" '/etc/systemd/system/sdprs-edge-update-manual\.service'
hasf "$INST" 'install -m 0440'
hasf "$INST" 'visudo -cf'

# --- Phase 3: installer writes tmpfiles.d so /run/sdprs is owned by sdprs ---
ok() { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1"; }
grep -q 'tmpfiles.d/sdprs.conf' scripts/edge_autoupdate_install.sh \
  && ok "installer writes tmpfiles.d for /run/sdprs" \
  || bad "installer writes tmpfiles.d for /run/sdprs"
grep -q 'd /run/sdprs 0755 sdprs sdprs' scripts/edge_autoupdate_install.sh \
  && ok "tmpfiles line owns /run/sdprs as sdprs" \
  || bad "tmpfiles line owns /run/sdprs as sdprs"

echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
