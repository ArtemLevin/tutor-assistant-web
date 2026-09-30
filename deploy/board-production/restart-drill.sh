#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
set -a
. "$HERE/.env.production"
. "$HERE/runtime/deployment.env"
set +a

compose() {
  docker compose -f "$ROOT/compose.board.production.yml" \
    --env-file "$HERE/.env.production" --env-file "$HERE/runtime/deployment.env" "$@"
}

slot=$ACTIVE_SLOT
compose restart redis
compose --profile "$slot" restart "board-api-$slot"
BASE_URL=$PUBLIC_BASE_URL "$HERE/smoke.sh"
compose --profile "$slot" restart "tutorboard-$slot"
BASE_URL=$PUBLIC_BASE_URL "$HERE/smoke.sh"
echo "Redis, API, and UI restart drill passed in $slot."
