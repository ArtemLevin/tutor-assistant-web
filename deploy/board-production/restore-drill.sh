#!/bin/sh
set -eu

BACKUP_ID=${1:-}
[ -n "$BACKUP_ID" ] || { echo "Usage: $0 <backup-id>" >&2; exit 2; }
printf '%s\n' "$BACKUP_ID" | grep -Eq '^[0-9]{8}T[0-9]{6}Z$' || {
  echo "Backup id must use YYYYMMDDTHHMMSSZ." >&2
  exit 2
}
HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH='' cd -- "$HERE/../.." && pwd)
DRILL_DB="tutor_restore_${BACKUP_ID%%T*}"
DRILL_BUCKET="tutor-restore-${BACKUP_ID%%T*}"

compose() {
  docker compose -f "$ROOT/compose.board.production.yml" \
    --env-file "$HERE/.env.production" --env-file "$HERE/runtime/deployment.env" "$@"
}

compose exec -T postgres psql -U tutorboard -d postgres \
  -c "DROP DATABASE IF EXISTS $DRILL_DB WITH (FORCE)" -c "CREATE DATABASE $DRILL_DB"
result=$(compose --profile jobs run --rm \
  -e ALLOW_RESTORE=true \
  -e RESTORE_BACKUP_ID="$BACKUP_ID" \
  -e RESTORE_DATABASE="$DRILL_DB" \
  -e RESTORE_BUCKET="$DRILL_BUCKET" \
  ops /bin/sh -c '
    url=$(sed "s|/[^/]*$|/$RESTORE_DATABASE|" /run/secrets/database_url)
    exec tutor-assistant-backup restore "$RESTORE_BACKUP_ID" \
      --database-url "$url" \
      --artifact-bucket "$RESTORE_BUCKET"
  ')
printf '%s\n' "$result"
printf '%s' "$result" | grep -q '"verified_artifacts"'
compose exec -T postgres psql -U tutorboard -d "$DRILL_DB" \
  -c "SELECT count(*) FROM alembic_version"
compose exec -T postgres psql -U tutorboard -d postgres \
  -c "DROP DATABASE $DRILL_DB WITH (FORCE)"
compose --profile jobs run --rm ops tutor-assistant-backup delete-drill "$DRILL_BUCKET"
echo "Isolated board restore drill passed."
