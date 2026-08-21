#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
TEMPLATE="$ROOT_DIR/deploy/board-production/Caddyfile.template"
RUNTIME_DIR="$ROOT_DIR/deploy/board-production/runtime"
OUTPUT="$RUNTIME_DIR/Caddyfile"

slot=${ACTIVE_SLOT:-blue}
case "$slot" in
  blue)
    api_upstream=board-api-blue
    tutorboard_upstream=tutorboard-blue
    ;;
  green)
    api_upstream=board-api-green
    tutorboard_upstream=tutorboard-green
    ;;
  *)
    echo "ACTIVE_SLOT must be blue or green" >&2
    exit 2
    ;;
esac

mkdir -p "$RUNTIME_DIR"
sed \
  -e "s/__BOARD_API_UPSTREAM__/$api_upstream/g" \
  -e "s/__TUTORBOARD_UPSTREAM__/$tutorboard_upstream/g" \
  "$TEMPLATE" > "$OUTPUT"

printf '%s\n' "$OUTPUT"
