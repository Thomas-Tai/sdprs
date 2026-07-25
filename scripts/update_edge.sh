#!/usr/bin/env bash
# ============================================================
# SDPRS Edge Update Script
# 將一台或多台邊緣端 Pi 更新到 origin/main 並重啟服務，且「絕不」動到節點本地的
# config 秘鑰。
#
# 為什麼安全：偵測節流／async 編碼／心跳等調校值放在程式預設（config_loader
# .DEFAULTS），透過 load_config 的 deep-merge 送達每個節點；config.zeabur.yaml
# 只保留「該節點的秘鑰」，上游不再更動它，因此 fast-forward 拉取不會覆寫秘鑰、
# 也不會衝突。
#
# Usage:
#   scripts/update_edge.sh <ssh-target> [<ssh-target> ...]
#     e.g. scripts/update_edge.sh pi@glass01.local pi@192.168.1.52 pi@192.168.1.53
#
# Env overrides:
#   SDPRS_EDGE_SERVICE  systemd unit to restart   (default: sdprs-edge-cloud)
#   SDPRS_EDGE_REPO     git checkout path on Pi   (default: /opt/sdprs)
#   SDPRS_EDGE_BRANCH   branch to fast-forward to (default: main)
# ============================================================
set -euo pipefail

SERVICE="${SDPRS_EDGE_SERVICE:-sdprs-edge-cloud}"
REPO="${SDPRS_EDGE_REPO:-/opt/sdprs}"
BRANCH="${SDPRS_EDGE_BRANCH:-main}"

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <ssh-target> [<ssh-target> ...]" >&2
    echo "  Fast-forwards each edge Pi to origin/$BRANCH and restarts $SERVICE." >&2
    echo "  Node config secrets (config.zeabur.yaml) are never touched." >&2
    echo "  Env: SDPRS_EDGE_SERVICE=$SERVICE  SDPRS_EDGE_REPO=$REPO  SDPRS_EDGE_BRANCH=$BRANCH" >&2
    exit 64
fi

update_one() {
    local target="$1"
    echo "==================== ${target} ===================="
    # Pass config via positional args to the remote shell; the heredoc body is
    # single-quoted so nothing expands locally.
    ssh "$target" bash -s -- "$REPO" "$SERVICE" "$BRANCH" <<'REMOTE'
set -euo pipefail
REPO="$1"; SERVICE="$2"; BRANCH="$3"
echo "[$(hostname)] repo=$REPO  service=$SERVICE  branch=$BRANCH"

if [ ! -d "$REPO/.git" ]; then
    echo "  ERROR: $REPO is not a git checkout (this node was set up via rsync)." >&2
    echo "         Use scripts/deploy_sync.sh from the dev machine instead." >&2
    exit 2
fi

before="$(git -C "$REPO" rev-parse --short HEAD)"
git -C "$REPO" fetch --quiet origin

# --ff-only never creates a merge commit and never overwrites a locally-modified
# file that ALSO changed upstream. The node's config.zeabur.yaml (its secrets) is
# unchanged upstream, so a clean fast-forward leaves it byte-for-byte as-is. If it
# cannot fast-forward, we STOP rather than risk the node's working tree.
if ! git -C "$REPO" merge --ff-only "origin/$BRANCH"; then
    echo "  ERROR: cannot fast-forward $REPO." >&2
    echo "         Likely a local commit, or a tracked file was edited on the node" >&2
    echo "         that also changed upstream. Resolve on the node, then re-run." >&2
    exit 3
fi

after="$(git -C "$REPO" rev-parse --short HEAD)"
if [ "$before" = "$after" ]; then
    echo "  already up to date (${after})"
else
    echo "  updated ${before} -> ${after}"
fi

echo "  restarting ${SERVICE} ..."
sudo systemctl restart "$SERVICE"
sleep 3

if systemctl is-active --quiet "$SERVICE"; then
    echo "  ${SERVICE} is active"
else
    echo "  ERROR: ${SERVICE} did not come back up:" >&2
    journalctl -u "$SERVICE" -n 25 --no-pager 2>/dev/null >&2 || true
    exit 4
fi

# Best-effort confirmation that the throttled path is live (journalctl may need
# no sudo on Pi OS; if it does, this quietly shows nothing — non-fatal).
echo "  --- recent log (expect the CV-throttle line) ---"
journalctl -u "$SERVICE" -n 60 --no-pager 2>/dev/null \
    | grep -iE "throttled|detect_fps|async encode|Entering main loop|Camera:" \
    | tail -6 \
    || echo "  (no matching lines yet — give it a few seconds and check the dashboard)"
REMOTE
    echo "  done: ${target}"
}

rc=0
for t in "$@"; do
    update_one "$t" || { echo "!! update FAILED for ${t}" >&2; rc=1; }
done

echo ""
if [ "$rc" -eq 0 ]; then
    echo "All targets updated + ${SERVICE} restarted."
else
    echo "One or more targets failed — see errors above." >&2
fi
exit "$rc"
