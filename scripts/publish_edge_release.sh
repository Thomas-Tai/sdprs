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
