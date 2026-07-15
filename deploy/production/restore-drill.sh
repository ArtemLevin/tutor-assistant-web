#!/bin/sh
set -eu

BACKUP_ID=${1:-}
[ -n "$BACKUP_ID" ] || { echo "Usage: $0 <backup-id>" >&2; exit 2; }
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
ENV_FILE="$HERE/.env.production"
STATE="$HERE/runtime/deployment.env"
DRILL_DB="tutor_restore_${BACKUP_ID%%T*}"
DRILL_BUCKET="tutor-restore-${BACKUP_ID%%T*}"
DB_USER=$(sed -n 's/^POSTGRES_USER=//p' "$ENV_FILE" | tr -d '"')
DB_USER=${DB_USER:-tutor}

compose() {
  docker compose -f "$ROOT/compose.production.yml" --env-file "$ENV_FILE" --env-file "$STATE" "$@"
}

compose exec -T postgres psql -U "$DB_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS $DRILL_DB WITH (FORCE)" \
  -c "CREATE DATABASE $DRILL_DB"
result=$(compose --profile jobs run --rm -e ALLOW_RESTORE=true ops /bin/sh -c \
  'url=$(sed "s|/[^/]*$|/'"$DRILL_DB"'|" /run/secrets/database_url); tutor-assistant-backup restore '"$BACKUP_ID"' --database-url "$url" --artifact-bucket '"$DRILL_BUCKET"'')
printf '%s\n' "$result"
printf '%s' "$result" | grep -q '"verified_artifacts"'
compose exec -T postgres psql -U "$DB_USER" -d "$DRILL_DB" \
  -c "SELECT count(*) AS alembic_version_rows FROM alembic_version"
compose exec -T postgres psql -U "$DB_USER" -d postgres \
  -c "DROP DATABASE $DRILL_DB WITH (FORCE)"
if [ "${KEEP_RESTORE_DRILL:-false}" != true ]; then
  compose --profile jobs run --rm ops tutor-assistant-backup delete-drill "$DRILL_BUCKET"
  echo "Restore drill passed; isolated database and bucket were cleaned up."
else
  echo "Restore drill passed; private bucket $DRILL_BUCKET was retained for approved inspection."
fi
