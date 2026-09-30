#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
ENV_FILE="$HERE/.env.production"
STATE="$HERE/runtime/deployment.env"
[ -s "$STATE" ] || { echo "No deployment state." >&2; exit 1; }

set -a
. "$ENV_FILE"
. "$STATE"
set +a
[ -n "${PREVIOUS_RELEASE:-}" ] || { echo "No previous release is recorded." >&2; exit 1; }

current_slot=$ACTIVE_SLOT
if [ "$current_slot" = blue ]; then target_slot=green; else target_slot=blue; fi
eval "target_api=\$BOARD_API_$(printf '%s' "$target_slot" | tr '[:lower:]' '[:upper:]')_IMAGE"
eval "target_ui=\$TUTORBOARD_$(printf '%s' "$target_slot" | tr '[:lower:]' '[:upper:]')_IMAGE"
for image in "$target_api" "$target_ui"; do
  case "$image" in *@sha256:*) ;; *) echo "Rollback digest is unavailable." >&2; exit 1 ;; esac
done

compose() {
  docker compose -f "$ROOT/compose.board.production.yml" \
    --env-file "$ENV_FILE" --env-file "$STATE" "$@"
}

compose --profile "$target_slot" up -d "board-api-$target_slot" "tutorboard-$target_slot"
sed -e "s/__BOARD_API_UPSTREAM__/board-api-$target_slot/g" \
  -e "s/__TUTORBOARD_UPSTREAM__/tutorboard-$target_slot/g" \
  "$HERE/Caddyfile.template" > "$HERE/runtime/Caddyfile.next"
mv "$HERE/runtime/Caddyfile.next" "$HERE/runtime/Caddyfile"
compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile
compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile
BASE_URL=$PUBLIC_BASE_URL "$HERE/smoke.sh"

old_current=$CURRENT_RELEASE
CURRENT_RELEASE=$PREVIOUS_RELEASE
PREVIOUS_RELEASE=$old_current
ACTIVE_SLOT=$target_slot
export CURRENT_RELEASE PREVIOUS_RELEASE ACTIVE_SLOT
env | grep -E '^(ACTIVE_SLOT|CURRENT_RELEASE|PREVIOUS_RELEASE|BOARD_API_(BLUE|GREEN)_IMAGE|TUTORBOARD_(BLUE|GREEN)_IMAGE|MIGRATION_IMAGE|OPS_IMAGE)=' \
  | sort > "$STATE"
compose --profile "$current_slot" stop -t 45 "board-api-$current_slot" "tutorboard-$current_slot" || true
echo "Traffic rolled back to $target_slot ($CURRENT_RELEASE)."
