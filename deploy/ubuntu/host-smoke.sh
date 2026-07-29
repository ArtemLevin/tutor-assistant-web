#!/bin/sh
# shellcheck disable=SC1090
set -eu

VERIFY_BACKUP=false
case "${1:-}" in
  "") ;;
  --verify-backup) VERIFY_BACKUP=true ;;
  *) echo "Usage: $0 [--verify-backup]" >&2; exit 2 ;;
esac

HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH='' cd -- "$HERE/../.." && pwd)
ENV_FILE="$ROOT/deploy/production/.env.production"
STATE="$ROOT/deploy/production/runtime/deployment.env"

systemctl is-active --quiet tutorboard-stack.service || {
  echo "tutorboard-stack.service is not active." >&2
  exit 1
}

set -a
. "$ENV_FILE"
. "$STATE"
set +a
ACTIVE_SLOT=${ACTIVE_SLOT:-blue}

compose() {
  docker compose -f "$ROOT/compose.production.yml" --env-file "$ENV_FILE" \
    --env-file "$STATE" --profile "$ACTIVE_SLOT" "$@"
}

required_services="caddy web-$ACTIVE_SLOT worker-$ACTIVE_SLOT tutorboard-$ACTIVE_SLOT geometryos postgres redis minio clamav scheduler backup"
running_services=$(compose ps --status running --services)
for service_name in $required_services; do
  printf '%s\n' "$running_services" | grep -qx "$service_name" || {
    echo "Required service is not running: $service_name" >&2
    exit 1
  }
done

"$ROOT/deploy/production/smoke.sh"
compose exec -T geometryos python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=5)"

if [ "$VERIFY_BACKUP" = true ]; then
  backup_id="host-smoke-$(date -u +%Y%m%dT%H%M%SZ)"
  "$ROOT/deploy/production/backup.sh" --backup-id "$backup_id"
  echo "Verified off-host backup upload: $backup_id"
fi

echo "Ubuntu host smoke passed."
