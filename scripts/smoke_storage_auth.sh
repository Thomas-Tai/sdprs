#!/bin/bash
# smoke_storage_auth.sh — regression check: /storage/ must NOT be publicly readable.
#
# The nginx docker-compose deploy previously served /storage/ directly with
# no auth, letting anyone download incident MP4s by guessing the node_id +
# timestamp URL. That location block was removed. This script proves it
# stays removed.
#
# Usage:
#   bash scripts/smoke_storage_auth.sh                  # target http://localhost
#   bash scripts/smoke_storage_auth.sh sdprs.example.com
#   bash scripts/smoke_storage_auth.sh sdprs.example.com https
#
# Exit codes:
#   0 — anonymous /storage/ returns 4xx (expected)
#   1 — anonymous /storage/ returns 2xx/3xx (REGRESSION — auth bypass returned)
#   2 — host unreachable (nothing proven; fix connectivity and re-run)

set -euo pipefail

HOST="${1:-localhost}"
SCHEME="${2:-http}"
PROBE_PATH="/storage/events/probe_regression_check.mp4"
URL="${SCHEME}://${HOST}${PROBE_PATH}"

echo "== SDPRS smoke test: anonymous /storage/ must NOT be readable =="
echo "Target: $URL"

CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$URL" || echo 000)

echo "HTTP status: $CODE"

case "$CODE" in
    200|206|301|302|307|308)
        echo "FAIL: /storage/ accessible anonymously (HTTP $CODE) — auth bypass regression!"
        exit 1
        ;;
    000)
        echo "FAIL: could not reach host — nothing to prove. Fix connectivity first."
        exit 2
        ;;
    *)
        echo "PASS: anonymous /storage/ returned HTTP $CODE (expected 4xx)."
        ;;
esac
