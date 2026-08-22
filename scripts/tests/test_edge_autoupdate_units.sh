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
