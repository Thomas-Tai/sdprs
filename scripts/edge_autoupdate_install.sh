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
if [ ! -f /opt/sdprs/.edge_deployed_sha ]; then
  git -C "$TMP" rev-parse HEAD > /opt/sdprs/.edge_deployed_sha
  chown sdprs:sdprs /opt/sdprs/.edge_deployed_sha 2>/dev/null || true
  echo "      seeded /opt/sdprs/.edge_deployed_sha (first bootstrap)"
else
  echo "      kept existing /opt/sdprs/.edge_deployed_sha"
fi

echo "[4/5] enable timer"
systemctl daemon-reload
systemctl enable --now sdprs-edge-update.timer

echo "[5/5] status"
systemctl list-timers sdprs-edge-update.timer --no-pager || true
echo "DONE — auto-update installed on $(hostname); seeded SHA $(cat /opt/sdprs/.edge_deployed_sha)"
