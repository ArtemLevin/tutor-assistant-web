#!/bin/sh
# shellcheck disable=SC1090
set -eu

HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH='' cd -- "$HERE/../.." && pwd)
ENV_FILE="$ROOT/deploy/production/.env.production"
STATE="$ROOT/deploy/production/runtime/deployment.env"
COMPOSE_FILE="$ROOT/compose.production.yml"

[ -s "$ENV_FILE" ] || { echo "Missing $ENV_FILE" >&2; exit 1; }
[ -s "$STATE" ] || { echo "Missing $STATE" >&2; exit 1; }
set -a
. "$ENV_FILE"
. "$STATE"
set +a
ACTIVE_SLOT=${ACTIVE_SLOT:-blue}

compose() {
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --env-file "$STATE" \
    --profile "$ACTIVE_SLOT" "$@"
}

case "${1:-}" in
  start)
    "$HERE/preflight.sh" --runtime
    compose up -d --remove-orphans
    ;;
  stop)
    compose down --remove-orphans --timeout 120
    ;;
  restart)
    compose down --remove-orphans --timeout 120
    "$HERE/preflight.sh" --runtime
    compose up -d --remove-orphans
    ;;
  status)
    compose ps
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
