#!/bin/sh
set -eu

BACKUP_ID=${1:-}
[ -n "$BACKUP_ID" ] || { echo "Usage: $0 <backup-id>" >&2; exit 2; }
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
DRILL_DB="tutor_restore_${BACKUP_ID%%T*}"
DRILL_BUCKET="tutor-restore-${BACKUP_ID%%T*}"

compose() {
  docker compose -f "$ROOT/compose.board.production.yml" \
    --env-file "$HERE/.env.production" --env-file "$HERE/runtime/deployment.env" "$@"
}

compose exec -T postgres psql -U tutorboard -d postgres \
  -c "DROP DATABASE IF EXISTS $DRILL_DB WITH (FORCE)" -c "CREATE DATABASE $DRILL_DB"
result=$(compose --profile jobs run --rm -e ALLOW_RESTORE=true ops /bin/sh -c \
  'url=$(sed "s|/[^/]*$|/'"$DRILL_DB"'|" /run/secrets/database_url); tutor-assistant-backup restore '"$BACKUP_ID"' --database-url "$url" --artifact-bucket '"$DRILL_BUCKET"'")
printf '%s\n' "$result"
printf '%s' "$result" | grep -q '"verified_artifacts"'
compose exec -T postgres psql -U tutorboard -d "$DRILL_DB" \
  -c "SELECT count(*) FROM alembic_version"
compose exec -T postgres psql -U tutorboard -d postgres \
  -c "DROP DATABASE $DRILL_DB WITH (FORCE)"
compose --profile jobs run --rm ops tutor-assistant-backup delete-drill "$DRILL_BUCKET"
echo "Isolated board restore drill passed."
