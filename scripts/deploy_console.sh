#!/usr/bin/env bash
# ============================================================
# SDPRS edge_glass — on-Pi self-update (run ON the Raspberry Pi)
# ------------------------------------------------------------
# The fleet is rsync-deployed (NOT a git checkout): code lives at
# /opt/sdprs/edge_glass and the systemd service runs it on-disk. This script
# pulls the latest public main, snapshots the current install for rollback,
# rsyncs the new code while PRESERVING this node's secrets/venv/runtime, and
# restarts the service.
#
# Usage (from the Pi's own console or SSH; needs sudo):
#   curl -fsSL https://raw.githubusercontent.com/Thomas-Tai/sdprs/main/scripts/deploy_console.sh | sudo bash
#
# Safe to re-run. Preserves: config.zeabur.yaml, config.yaml, .env*, venv/,
# events/, buffer/, *.log. Repo is public — no credentials needed.
# ============================================================
set -euo pipefail

REPO="${SDPRS_REPO:-https://github.com/Thomas-Tai/sdprs}"
BRANCH="${SDPRS_BRANCH:-main}"
DEST=/opt/sdprs/edge_glass
SVC=sdprs-edge-cloud
TMP=/tmp/sdprs-new
STAMP="$(date +%Y%m%d-%H%M%S)"

if [ "$(id -u)" -ne 0 ]; then echo "ERROR: run with sudo (needs to write $DEST + restart $SVC)"; exit 1; fi
if [ ! -d "$DEST" ]; then echo "ERROR: $DEST not found — is this a commissioned glass node?"; exit 1; fi

echo "== node $(hostname) — $SVC before: $(systemctl is-active "$SVC" 2>/dev/null || echo unknown) =="

echo "[1/5] clone latest ($BRANCH)"
rm -rf "$TMP"
git clone -q --depth 1 --branch "$BRANCH" "$REPO" "$TMP"
echo "      new HEAD: $(git -C "$TMP" rev-parse --short HEAD)"

echo "[2/5] rollback snapshot -> /opt/sdprs/edge_glass.backup.$STAMP.tgz"
tar czf "/opt/sdprs/edge_glass.backup.$STAMP.tgz" \
  --exclude='edge_glass/venv' --exclude='edge_glass/events' --exclude='edge_glass/buffer' \
  -C /opt/sdprs edge_glass

echo "[3/5] rsync code (secrets/venv/runtime preserved)"
rsync -a \
  --exclude='config.zeabur.yaml' --exclude='config.yaml' --exclude='.env' --exclude='.env.*' \
  --exclude='venv/' --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.log' \
  --exclude='events/' --exclude='buffer/' --exclude='data/' \
  "$TMP/edge_glass/" "$DEST/"
# shared/ lives nested under edge_glass on the node (setup_pi.sh copies it in)
rsync -a --exclude='__pycache__/' --exclude='*.pyc' "$TMP/shared/" "$DEST/shared/"
chown -R sdprs:sdprs "$DEST"

echo "[4/5] restart $SVC"
systemctl restart "$SVC"
sleep 3

echo "[5/5] verify"
STATE="$(systemctl is-active "$SVC" 2>/dev/null || echo inactive)"
echo "== $SVC after: $STATE =="
journalctl -u "$SVC" -n 20 --no-pager || true
rm -rf "$TMP"

if [ "$STATE" != "active" ]; then
  echo ""
  echo "!! $SVC is NOT active. Rollback with:"
  echo "   sudo systemctl stop $SVC && sudo tar xzf /opt/sdprs/edge_glass.backup.$STAMP.tgz -C /opt/sdprs && sudo chown -R sdprs:sdprs $DEST && sudo systemctl start $SVC"
  exit 1
fi
echo ""
echo "DONE — $(hostname) updated and $SVC is active."
echo "(rollback snapshot kept at /opt/sdprs/edge_glass.backup.$STAMP.tgz)"
