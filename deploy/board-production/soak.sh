#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
STATE="$HERE/runtime/soak.env"
DURATION=${SOAK_DURATION_SECONDS:-86400}
INTERVAL=${SOAK_CHECK_INTERVAL_SECONDS:-300}

set -a
. "$HERE/.env.production"
. "$HERE/runtime/deployment.env"
set +a

compose() {
  docker compose -f "$ROOT/compose.board.production.yml" \
    --env-file "$HERE/.env.production" --env-file "$HERE/runtime/deployment.env" "$@"
}

check() {
  BASE_URL=$PUBLIC_BASE_URL "$HERE/smoke.sh"
  for service in "board-api-$ACTIVE_SLOT" "tutorboard-$ACTIVE_SLOT" postgres redis minio; do
    running=$(compose ps --status running --services | grep -Fx "$service" || true)
    [ -n "$running" ] || { echo "$service is not running" >&2; return 1; }
    container_id=$(compose ps -q "$service")
    restarts=$(docker inspect "$container_id" --format '{{.RestartCount}}')
    [ "$restarts" -le "${SOAK_MAX_RESTARTS:-0}" ] || {
      echo "$service restart count is $restarts" >&2
      return 1
    }
  done
}

case "${1:-check}" in
  start)
    printf 'SOAK_STARTED_AT=%s\n' "$(date +%s)" > "$STATE"
    check
    ;;
  check)
    [ -s "$STATE" ] || { echo "Soak has not been started." >&2; exit 1; }
    . "$STATE"
    check
    elapsed=$(($(date +%s) - SOAK_STARTED_AT))
    [ "$elapsed" -ge "$DURATION" ] || {
      echo "Soak has run for ${elapsed}s; ${DURATION}s required." >&2
      exit 1
    }
    echo "Soak gate passed after ${elapsed}s."
    ;;
  wait)
    "$0" start
    while :; do
      sleep "$INTERVAL"
      check
      . "$STATE"
      elapsed=$(($(date +%s) - SOAK_STARTED_AT))
      [ "$elapsed" -lt "$DURATION" ] || break
    done
    "$0" check
    ;;
  *) echo "Usage: $0 [start|check|wait]" >&2; exit 2 ;;
esac
