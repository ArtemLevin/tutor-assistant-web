#!/bin/sh
set -eu

[ "${CONFIRM_STAGING_CHAOS:-}" = yes ] || {
  echo "Run only on staging: CONFIRM_STAGING_CHAOS=yes $0" >&2
  exit 2
}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
STATE="$HERE/runtime/deployment.env"
ACTIVE_SLOT=$(sed -n 's/^ACTIVE_SLOT=//p' "$STATE")
compose() {
  docker compose -f "$ROOT/compose.production.yml" --env-file "$HERE/.env.production" \
    --env-file "$STATE" --profile "$ACTIVE_SLOT" "$@"
}
disconnected=""
network=""
cleanup() {
  compose up -d redis minio >/dev/null 2>&1 || true
  if [ -n "$network" ]; then
    for container in $disconnected; do
      docker network connect "$network" "$container" >/dev/null 2>&1 || true
    done
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

for service in redis minio; do
  echo "Interrupting $service for 20 seconds..."
  compose stop "$service"
  sleep 20
  compose up -d "$service"
  sleep 15
done
project=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$HERE/.env.production" | tr -d '"')
network="${project:-tutor-production}_egress"
echo "Disconnecting web/workers from external providers, including BBB, for 20 seconds..."
containers="$(compose ps -q "web-$ACTIVE_SLOT") $(compose ps -q "worker-$ACTIVE_SLOT")"
disconnected=$containers
for container in $containers; do docker network disconnect "$network" "$container"; done
sleep 20
for container in $containers; do docker network connect "$network" "$container"; done
disconnected=""
sleep 15
echo "Restarting active web and worker during queue processing..."
compose restart "web-$ACTIVE_SLOT" "worker-$ACTIVE_SLOT"
compose exec -T "web-$ACTIVE_SLOT" python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=10)"
echo "Redis, S3, external provider/BBB and process restart drill passed."
