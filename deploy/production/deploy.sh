#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <immutable-release-tag>" >&2
  exit 2
fi
RELEASE=$1
case "$RELEASE" in
  latest|""|*[!A-Za-z0-9._-]*) echo "Use an immutable image tag, never 'latest'." >&2; exit 2 ;;
esac

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
ENV_FILE="$HERE/.env.production"
STATE="$HERE/runtime/deployment.env"
COMPOSE_FILE="$ROOT/compose.production.yml"

[ -f "$STATE" ] || "$HERE/init.sh"
cp "$STATE" "$STATE.before"
set -a
. "$STATE"
set +a
OLD_SLOT=${ACTIVE_SLOT:-blue}
if [ "$OLD_SLOT" = blue ]; then NEW_SLOT=green; else NEW_SLOT=blue; fi
OLD_RELEASE=${CURRENT_RELEASE:-}

case "$NEW_SLOT" in
  blue) BLUE_RELEASE=$RELEASE ;;
  green) GREEN_RELEASE=$RELEASE ;;
esac
SCHEDULER_RELEASE=$RELEASE
OPS_RELEASE=$RELEASE
cat > "$STATE" <<EOF
ACTIVE_SLOT=$OLD_SLOT
BLUE_RELEASE=$BLUE_RELEASE
GREEN_RELEASE=$GREEN_RELEASE
CURRENT_RELEASE=$OLD_RELEASE
PREVIOUS_RELEASE=${PREVIOUS_RELEASE:-}
SCHEDULER_RELEASE=$SCHEDULER_RELEASE
OPS_RELEASE=$OPS_RELEASE
EOF

failed=true
cleanup() {
  if [ "$failed" = true ]; then
    mv "$STATE.before" "$STATE"
    sed "s/__WEB_UPSTREAM__/web-$OLD_SLOT/g" "$HERE/Caddyfile.template" > "$HERE/runtime/Caddyfile"
    printf '[{"targets":["web-%s:8000"],"labels":{"slot":"%s","release":"%s"}}]\n' \
      "$OLD_SLOT" "$OLD_SLOT" "$OLD_RELEASE" > "$HERE/runtime/prometheus-targets.json"
    compose --profile "$OLD_SLOT" up -d "web-$OLD_SLOT" "worker-$OLD_SLOT" scheduler backup || true
    compose up -d caddy prometheus || true
    compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile || true
    echo "Deployment failed; traffic and deployment state were returned to $OLD_SLOT." >&2
  else
    rm -f "$STATE.before"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

compose() {
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --env-file "$STATE" "$@"
}

echo "Starting stable infrastructure..."
compose up -d postgres redis minio clamav tempo otel-collector pushgateway
compose --profile jobs run --rm minio-init

echo "Pulling immutable release $RELEASE..."
compose --profile "$NEW_SLOT" --profile jobs pull \
  "web-$NEW_SLOT" "worker-$NEW_SLOT" scheduler migration ops

if [ "${SKIP_PRE_DEPLOY_BACKUP:-false}" != true ]; then
  compose --profile jobs run --rm ops tutor-assistant-backup create
fi
if [ "${SKIP_MIGRATIONS:-false}" != true ]; then
  echo "Running migrations as a one-shot job..."
  compose --profile jobs run --rm migration
fi

echo "Starting inactive $NEW_SLOT slot..."
compose --profile "$NEW_SLOT" up -d "web-$NEW_SLOT" "worker-$NEW_SLOT"
attempt=0
until compose --profile "$NEW_SLOT" exec -T "web-$NEW_SLOT" python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)"; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    compose --profile "$NEW_SLOT" logs --tail=100 "web-$NEW_SLOT"
    exit 1
  fi
  sleep 4
done

sed "s/__WEB_UPSTREAM__/web-$NEW_SLOT/g" "$HERE/Caddyfile.template" > "$HERE/runtime/Caddyfile.next"
mv "$HERE/runtime/Caddyfile.next" "$HERE/runtime/Caddyfile"
printf '[{"targets":["web-%s:8000"],"labels":{"slot":"%s","release":"%s"}}]\n' \
  "$NEW_SLOT" "$NEW_SLOT" "$RELEASE" > "$HERE/runtime/prometheus-targets.json"

compose up -d caddy prometheus alertmanager grafana scheduler backup
compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile

"$HERE/smoke.sh"

cat > "$STATE" <<EOF
ACTIVE_SLOT=$NEW_SLOT
BLUE_RELEASE=$BLUE_RELEASE
GREEN_RELEASE=$GREEN_RELEASE
CURRENT_RELEASE=$RELEASE
PREVIOUS_RELEASE=$OLD_RELEASE
SCHEDULER_RELEASE=$SCHEDULER_RELEASE
OPS_RELEASE=$OPS_RELEASE
EOF
failed=false

if [ "$OLD_SLOT" != "$NEW_SLOT" ]; then
  compose --profile "$OLD_SLOT" stop -t 90 "web-$OLD_SLOT" "worker-$OLD_SLOT" || true
fi
echo "Release $RELEASE is active in the $NEW_SLOT slot."
