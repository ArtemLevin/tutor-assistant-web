#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE="$HERE/runtime/deployment.env"
[ -f "$STATE" ] || { echo "No deployment state." >&2; exit 1; }

case "${1:-app}" in
  app)
    PREVIOUS_RELEASE=$(sed -n 's/^PREVIOUS_RELEASE=//p' "$STATE")
    [ -n "$PREVIOUS_RELEASE" ] || { echo "No previous release recorded." >&2; exit 1; }
    SKIP_MIGRATIONS=true SKIP_PRE_DEPLOY_BACKUP=true "$HERE/deploy.sh" "$PREVIOUS_RELEASE"
    ;;
  migration)
    revision=${2:-}
    [ -n "$revision" ] || { echo "Usage: $0 migration <verified-alembic-revision>" >&2; exit 2; }
    [ "${CONFIRM_MIGRATION_ROLLBACK:-}" = "yes" ] || {
      echo "Set CONFIRM_MIGRATION_ROLLBACK=yes after checking downgrade compatibility." >&2
      exit 2
    }
    ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
    ENV_FILE="$HERE/.env.production"
    docker compose -f "$ROOT/compose.production.yml" --env-file "$ENV_FILE" --env-file "$STATE" \
      --profile jobs run --rm ops tutor-assistant-backup create
    docker compose -f "$ROOT/compose.production.yml" --env-file "$ENV_FILE" --env-file "$STATE" \
      --profile jobs run --rm migration alembic downgrade "$revision"
    ;;
  *) echo "Usage: $0 [app|migration <revision>]" >&2; exit 2 ;;
esac
