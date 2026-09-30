#!/bin/sh
set -eu

if [ "$#" -ne 4 ]; then
  echo "Usage: $0 <board-api-digest> <tutorboard-digest> <migration-digest> <ops-digest>" >&2
  exit 2
fi

validate_digest() {
  case "$1" in
    *@sha256:*)
      digest=${1##*@sha256:}
      [ "${#digest}" -eq 64 ] || return 1
      case "$digest" in *[!0-9a-fA-F]*) return 1 ;; esac
      ;;
    *) return 1 ;;
  esac
}

for image in "$@"; do
  validate_digest "$image" || {
    echo "Every release image must be pinned by a complete sha256 digest: $image" >&2
    exit 2
  }
done

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
ENV_FILE="$HERE/.env.production"
STATE="$HERE/runtime/deployment.env"
COMPOSE_FILE="$ROOT/compose.board.production.yml"
[ -s "$STATE" ] || "$HERE/init.sh"

set -a
. "$ENV_FILE"
. "$STATE"
set +a

old_slot=${ACTIVE_SLOT:-blue}
if [ "$old_slot" = blue ]; then new_slot=green; else new_slot=blue; fi
old_release=${CURRENT_RELEASE:-}
api_image=$1
ui_image=$2
migration_image=$3
ops_image=$4

case "$new_slot" in
  blue)
    BOARD_API_BLUE_IMAGE=$api_image
    TUTORBOARD_BLUE_IMAGE=$ui_image
    ;;
  green)
    BOARD_API_GREEN_IMAGE=$api_image
    TUTORBOARD_GREEN_IMAGE=$ui_image
    ;;
esac
MIGRATION_IMAGE=$migration_image
OPS_IMAGE=$ops_image
export BOARD_API_BLUE_IMAGE BOARD_API_GREEN_IMAGE TUTORBOARD_BLUE_IMAGE
export TUTORBOARD_GREEN_IMAGE MIGRATION_IMAGE OPS_IMAGE

compose() {
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --env-file "$STATE" "$@"
}

state_before=$(mktemp "$HERE/runtime/deployment.before.XXXXXX")
cp "$STATE" "$state_before"
failed=true
cleanup() {
  if [ "$failed" = true ]; then
    cp "$state_before" "$STATE"
    rm -f "$state_before"
    if [ -n "$old_release" ]; then
      sed -e "s/__BOARD_API_UPSTREAM__/board-api-$old_slot/g" \
        -e "s/__TUTORBOARD_UPSTREAM__/tutorboard-$old_slot/g" \
        "$HERE/Caddyfile.template" > "$HERE/runtime/Caddyfile"
      compose --profile "$old_slot" up -d "board-api-$old_slot" "tutorboard-$old_slot" || true
      compose up -d caddy || true
      compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile || true
    fi
    echo "Deployment failed; traffic remains on $old_slot." >&2
  else
    rm -f "$state_before"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

compose up -d postgres redis minio
compose --profile jobs run --rm minio-init
compose --profile "$new_slot" --profile jobs pull \
  "board-api-$new_slot" "tutorboard-$new_slot" migration ops

if [ -n "$old_release" ] && [ "${SKIP_PRE_DEPLOY_BACKUP:-false}" != true ]; then
  compose --profile jobs run --rm ops tutor-assistant-backup create
fi
if [ "${SKIP_MIGRATIONS:-false}" != true ]; then
  compose --profile jobs run --rm migration
fi

compose --profile "$new_slot" up -d "board-api-$new_slot" "tutorboard-$new_slot"
attempt=0
until compose --profile "$new_slot" exec -T "board-api-$new_slot" python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)"; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 30 ] || exit 1
  sleep 4
done
attempt=0
until compose --profile "$new_slot" exec -T "tutorboard-$new_slot" \
  wget -q -O /dev/null http://127.0.0.1:8080/healthz; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 30 ] || exit 1
  sleep 2
done

sed -e "s/__BOARD_API_UPSTREAM__/board-api-$new_slot/g" \
  -e "s/__TUTORBOARD_UPSTREAM__/tutorboard-$new_slot/g" \
  "$HERE/Caddyfile.template" > "$HERE/runtime/Caddyfile.next"
mv "$HERE/runtime/Caddyfile.next" "$HERE/runtime/Caddyfile"
compose up -d caddy backup
compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile
compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile

BASE_URL=$PUBLIC_BASE_URL "$HERE/smoke.sh"

release=$(printf '%s' "$api_image" | sed 's/.*@sha256://')
{
  printf 'ACTIVE_SLOT=%s\n' "$new_slot"
  printf 'CURRENT_RELEASE=%s\n' "$release"
  printf 'PREVIOUS_RELEASE=%s\n' "$old_release"
  printf 'BOARD_API_BLUE_IMAGE=%s\n' "$BOARD_API_BLUE_IMAGE"
  printf 'BOARD_API_GREEN_IMAGE=%s\n' "$BOARD_API_GREEN_IMAGE"
  printf 'TUTORBOARD_BLUE_IMAGE=%s\n' "$TUTORBOARD_BLUE_IMAGE"
  printf 'TUTORBOARD_GREEN_IMAGE=%s\n' "$TUTORBOARD_GREEN_IMAGE"
  printf 'MIGRATION_IMAGE=%s\n' "$MIGRATION_IMAGE"
  printf 'OPS_IMAGE=%s\n' "$OPS_IMAGE"
} > "$STATE"

commit=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || printf unknown)
cat > "$HERE/runtime/release-manifest.json" <<EOF
{"releasedAt":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","commit":"$commit","slot":"$new_slot","boardApiImage":"$api_image","tutorboardImage":"$ui_image","migrationImage":"$migration_image","opsImage":"$ops_image"}
EOF

failed=false
if [ -n "$old_release" ]; then
  compose --profile "$old_slot" stop -t 45 "board-api-$old_slot" "tutorboard-$old_slot" || true
fi
echo "Board release $release is active in $new_slot."
