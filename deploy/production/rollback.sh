#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE="$HERE/runtime/deployment.env"
[ -f "$STATE" ] || { echo "No deployment state." >&2; exit 1; }

case "${1:-app}" in
  app)
    set -a
    . "$STATE"
    set +a
    [ -n "${PREVIOUS_RELEASE:-}" ] || { echo "No previous release recorded." >&2; exit 1; }
    [ -n "${PREVIOUS_TUTORBOARD_RELEASE:-}" ] || PREVIOUS_TUTORBOARD_RELEASE=$PREVIOUS_RELEASE
    if [ "${ACTIVE_SLOT:-blue}" = blue ]; then
      WEB_IMAGE_OVERRIDE=${GREEN_WEB_IMAGE:-}
      WORKER_IMAGE_OVERRIDE=${GREEN_WORKER_IMAGE:-}
      TUTORBOARD_IMAGE_OVERRIDE=${TUTORBOARD_GREEN_IMAGE:-}
    else
      WEB_IMAGE_OVERRIDE=${BLUE_WEB_IMAGE:-}
      WORKER_IMAGE_OVERRIDE=${BLUE_WORKER_IMAGE:-}
      TUTORBOARD_IMAGE_OVERRIDE=${TUTORBOARD_BLUE_IMAGE:-}
    fi
    for rollback_image in \
      "$WEB_IMAGE_OVERRIDE" \
      "$WORKER_IMAGE_OVERRIDE" \
      "$TUTORBOARD_IMAGE_OVERRIDE" \
      "${PREVIOUS_SCHEDULER_IMAGE:-}" \
      "${PREVIOUS_MIGRATION_IMAGE:-}" \
      "${PREVIOUS_OPS_IMAGE:-}"; do
      case "$rollback_image" in
        *@sha256:*) ;;
        *)
          echo "Exact rollback digests are unavailable; refusing a tag-based rollback." >&2
          exit 1
          ;;
      esac
    done
    WEB_IMAGE_OVERRIDE=$WEB_IMAGE_OVERRIDE \
      WORKER_IMAGE_OVERRIDE=$WORKER_IMAGE_OVERRIDE \
      SCHEDULER_IMAGE_OVERRIDE=$PREVIOUS_SCHEDULER_IMAGE \
      MIGRATION_IMAGE_OVERRIDE=$PREVIOUS_MIGRATION_IMAGE \
      OPS_IMAGE_OVERRIDE=$PREVIOUS_OPS_IMAGE \
      TUTORBOARD_IMAGE_OVERRIDE=$TUTORBOARD_IMAGE_OVERRIDE \
      SKIP_MIGRATIONS=true SKIP_PRE_DEPLOY_BACKUP=true \
      "$HERE/deploy.sh" "$PREVIOUS_RELEASE" "$PREVIOUS_TUTORBOARD_RELEASE"
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
