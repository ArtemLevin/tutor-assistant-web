#!/bin/sh
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 <backend-release-tag> [tutorboard-release-tag]" >&2
  exit 2
fi
RELEASE=$1
TUTORBOARD_RELEASE=${2:-$1}
case "$RELEASE" in
  latest|""|*[!A-Za-z0-9._-]*) echo "Use an immutable image tag, never 'latest'." >&2; exit 2 ;;
esac
case "$TUTORBOARD_RELEASE" in
  latest|""|*[!A-Za-z0-9._-]*) echo "Use an immutable TutorBoard image tag." >&2; exit 2 ;;
esac

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
ENV_FILE="$HERE/.env.production"
STATE="$HERE/runtime/deployment.env"
COMPOSE_FILE="$ROOT/compose.production.yml"

[ -f "$STATE" ] || "$HERE/init.sh"
set -a
. "$ENV_FILE"
. "$STATE"
set +a
case "${GEOMETRYOS_IMAGE:-}" in
  *@sha256:*)
    geometryos_digest=${GEOMETRYOS_IMAGE##*@sha256:}
    if [ "${#geometryos_digest}" -ne 64 ]; then
      echo "GEOMETRYOS_IMAGE must contain a complete sha256 digest." >&2
      exit 2
    fi
    case "$geometryos_digest" in
      *[!0-9a-fA-F]*)
        echo "GEOMETRYOS_IMAGE digest must be hexadecimal." >&2
        exit 2
        ;;
    esac
    ;;
  *)
    echo "GEOMETRYOS_IMAGE must be pinned with @sha256:, never a mutable tag alone." >&2
    exit 2
    ;;
esac
"$ROOT/deploy/ubuntu/preflight.sh" "$RELEASE" "$TUTORBOARD_RELEASE"

validate_digest_ref() {
  image=$1
  case "$image" in
    *@sha256:*)
      image_digest=${image##*@sha256:}
      [ "${#image_digest}" -eq 64 ] || return 1
      case "$image_digest" in *[!0-9a-fA-F]*) return 1 ;; esac
      ;;
    *) return 1 ;;
  esac
}

resolve_digest_ref() {
  source_image=$1
  source_repository=${source_image%:*}
  docker pull "$source_image" >&2
  for candidate in $(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$source_image"); do
    case "$candidate" in
      "$source_repository"@sha256:*)
        validate_digest_ref "$candidate" || continue
        printf '%s\n' "$candidate"
        return 0
        ;;
    esac
  done
  echo "Docker did not expose an immutable repository digest for $source_image." >&2
  return 1
}

resolve_or_use_digest() {
  source_image=$1
  override=${2:-}
  if [ -n "$override" ]; then
    validate_digest_ref "$override" || {
      echo "Image override must be a complete immutable digest: $override" >&2
      return 1
    }
    printf '%s\n' "$override"
  else
    resolve_digest_ref "$source_image"
  fi
}

echo "Resolving release tags to immutable repository digests..."
WEB_IMAGE=$(resolve_or_use_digest "${IMAGE_REPOSITORY}-web:${RELEASE}" "${WEB_IMAGE_OVERRIDE:-}")
WORKER_IMAGE=$(resolve_or_use_digest "${IMAGE_REPOSITORY}-worker:${RELEASE}" "${WORKER_IMAGE_OVERRIDE:-}")
SCHEDULER_IMAGE_NEXT=$(resolve_or_use_digest "${IMAGE_REPOSITORY}-scheduler:${RELEASE}" "${SCHEDULER_IMAGE_OVERRIDE:-}")
MIGRATION_IMAGE_NEXT=$(resolve_or_use_digest "${IMAGE_REPOSITORY}-migration:${RELEASE}" "${MIGRATION_IMAGE_OVERRIDE:-}")
OPS_IMAGE_NEXT=$(resolve_or_use_digest "${IMAGE_REPOSITORY}-ops:${RELEASE}" "${OPS_IMAGE_OVERRIDE:-}")
TUTORBOARD_IMAGE=$(resolve_or_use_digest "${TUTORBOARD_IMAGE_REPOSITORY}:${TUTORBOARD_RELEASE}" "${TUTORBOARD_IMAGE_OVERRIDE:-}")

TUTORBOARD_BLUE_RELEASE=${TUTORBOARD_BLUE_RELEASE:-${BLUE_RELEASE:-$TUTORBOARD_RELEASE}}
TUTORBOARD_GREEN_RELEASE=${TUTORBOARD_GREEN_RELEASE:-${GREEN_RELEASE:-$TUTORBOARD_RELEASE}}
CURRENT_TUTORBOARD_RELEASE=${CURRENT_TUTORBOARD_RELEASE-$TUTORBOARD_BLUE_RELEASE}
PREVIOUS_TUTORBOARD_RELEASE=${PREVIOUS_TUTORBOARD_RELEASE:-}
BLUE_WEB_IMAGE=${BLUE_WEB_IMAGE:-${IMAGE_REPOSITORY}-web:${BLUE_RELEASE}}
BLUE_WORKER_IMAGE=${BLUE_WORKER_IMAGE:-${IMAGE_REPOSITORY}-worker:${BLUE_RELEASE}}
GREEN_WEB_IMAGE=${GREEN_WEB_IMAGE:-${IMAGE_REPOSITORY}-web:${GREEN_RELEASE}}
GREEN_WORKER_IMAGE=${GREEN_WORKER_IMAGE:-${IMAGE_REPOSITORY}-worker:${GREEN_RELEASE}}
TUTORBOARD_BLUE_IMAGE=${TUTORBOARD_BLUE_IMAGE:-${TUTORBOARD_IMAGE_REPOSITORY}:${TUTORBOARD_BLUE_RELEASE}}
TUTORBOARD_GREEN_IMAGE=${TUTORBOARD_GREEN_IMAGE:-${TUTORBOARD_IMAGE_REPOSITORY}:${TUTORBOARD_GREEN_RELEASE}}
OLD_SCHEDULER_IMAGE=${SCHEDULER_IMAGE:-${IMAGE_REPOSITORY}-scheduler:${SCHEDULER_RELEASE}}
OLD_MIGRATION_IMAGE=${MIGRATION_IMAGE:-${IMAGE_REPOSITORY}-migration:${OPS_RELEASE}}
OLD_OPS_IMAGE=${OPS_IMAGE:-${IMAGE_REPOSITORY}-ops:${OPS_RELEASE}}
OLD_SLOT=${ACTIVE_SLOT:-blue}
if [ "$OLD_SLOT" = blue ]; then NEW_SLOT=green; else NEW_SLOT=blue; fi
OLD_RELEASE=${CURRENT_RELEASE:-}
OLD_TUTORBOARD_RELEASE=${CURRENT_TUTORBOARD_RELEASE:-}

case "$NEW_SLOT" in
  blue)
    BLUE_RELEASE=$RELEASE
    BLUE_WEB_IMAGE=$WEB_IMAGE
    BLUE_WORKER_IMAGE=$WORKER_IMAGE
    TUTORBOARD_BLUE_RELEASE=$TUTORBOARD_RELEASE
    TUTORBOARD_BLUE_IMAGE=$TUTORBOARD_IMAGE
    ;;
  green)
    GREEN_RELEASE=$RELEASE
    GREEN_WEB_IMAGE=$WEB_IMAGE
    GREEN_WORKER_IMAGE=$WORKER_IMAGE
    TUTORBOARD_GREEN_RELEASE=$TUTORBOARD_RELEASE
    TUTORBOARD_GREEN_IMAGE=$TUTORBOARD_IMAGE
    ;;
esac
SCHEDULER_RELEASE=$RELEASE
OPS_RELEASE=$RELEASE
SCHEDULER_IMAGE=$SCHEDULER_IMAGE_NEXT
MIGRATION_IMAGE=$MIGRATION_IMAGE_NEXT
OPS_IMAGE=$OPS_IMAGE_NEXT
PREVIOUS_SCHEDULER_IMAGE=$OLD_SCHEDULER_IMAGE
PREVIOUS_MIGRATION_IMAGE=$OLD_MIGRATION_IMAGE
PREVIOUS_OPS_IMAGE=$OLD_OPS_IMAGE
cp "$STATE" "$STATE.before"
cat > "$STATE" <<EOF
ACTIVE_SLOT=$OLD_SLOT
BLUE_RELEASE=$BLUE_RELEASE
GREEN_RELEASE=$GREEN_RELEASE
CURRENT_RELEASE=$OLD_RELEASE
PREVIOUS_RELEASE=${PREVIOUS_RELEASE:-}
SCHEDULER_RELEASE=$SCHEDULER_RELEASE
OPS_RELEASE=$OPS_RELEASE
TUTORBOARD_BLUE_RELEASE=$TUTORBOARD_BLUE_RELEASE
TUTORBOARD_GREEN_RELEASE=$TUTORBOARD_GREEN_RELEASE
CURRENT_TUTORBOARD_RELEASE=$OLD_TUTORBOARD_RELEASE
PREVIOUS_TUTORBOARD_RELEASE=${PREVIOUS_TUTORBOARD_RELEASE:-}
BLUE_WEB_IMAGE=$BLUE_WEB_IMAGE
BLUE_WORKER_IMAGE=$BLUE_WORKER_IMAGE
GREEN_WEB_IMAGE=$GREEN_WEB_IMAGE
GREEN_WORKER_IMAGE=$GREEN_WORKER_IMAGE
TUTORBOARD_BLUE_IMAGE=$TUTORBOARD_BLUE_IMAGE
TUTORBOARD_GREEN_IMAGE=$TUTORBOARD_GREEN_IMAGE
SCHEDULER_IMAGE=$SCHEDULER_IMAGE
MIGRATION_IMAGE=$MIGRATION_IMAGE
OPS_IMAGE=$OPS_IMAGE
PREVIOUS_SCHEDULER_IMAGE=$PREVIOUS_SCHEDULER_IMAGE
PREVIOUS_MIGRATION_IMAGE=$PREVIOUS_MIGRATION_IMAGE
PREVIOUS_OPS_IMAGE=$PREVIOUS_OPS_IMAGE
EOF

failed=true
cleanup() {
  if [ "$failed" = true ]; then
    mv "$STATE.before" "$STATE"
    sed \
      -e "s/__WEB_UPSTREAM__/web-$OLD_SLOT/g" \
      -e "s/__TUTORBOARD_UPSTREAM__/tutorboard-$OLD_SLOT/g" \
      "$HERE/Caddyfile.template" > "$HERE/runtime/Caddyfile"
    printf '[{"targets":["web-%s:8000"],"labels":{"slot":"%s","release":"%s"}}]\n' \
      "$OLD_SLOT" "$OLD_SLOT" "$OLD_RELEASE" > "$HERE/runtime/prometheus-targets.json"
    compose --profile "$OLD_SLOT" up -d "web-$OLD_SLOT" "worker-$OLD_SLOT" "tutorboard-$OLD_SLOT" scheduler backup || true
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
compose pull geometryos
compose up -d postgres redis minio clamav geometryos tempo otel-collector pushgateway
compose --profile jobs run --rm minio-init

echo "Pulling immutable release $RELEASE..."
compose --profile "$NEW_SLOT" --profile jobs pull \
  "web-$NEW_SLOT" "worker-$NEW_SLOT" "tutorboard-$NEW_SLOT" scheduler migration ops

if [ "${SKIP_PRE_DEPLOY_BACKUP:-false}" != true ]; then
  compose --profile jobs run --rm ops tutor-assistant-backup create
fi
if [ "${SKIP_MIGRATIONS:-false}" != true ]; then
  echo "Running migrations as a one-shot job..."
  compose --profile jobs run --rm migration
fi

echo "Starting inactive $NEW_SLOT slot..."
compose --profile "$NEW_SLOT" up -d "web-$NEW_SLOT" "worker-$NEW_SLOT" "tutorboard-$NEW_SLOT"
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
attempt=0
until compose --profile "$NEW_SLOT" exec -T "tutorboard-$NEW_SLOT" \
  wget -q -O /dev/null http://127.0.0.1:8080/healthz; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    compose --profile "$NEW_SLOT" logs --tail=100 "tutorboard-$NEW_SLOT"
    exit 1
  fi
  sleep 2
done

sed \
  -e "s/__WEB_UPSTREAM__/web-$NEW_SLOT/g" \
  -e "s/__TUTORBOARD_UPSTREAM__/tutorboard-$NEW_SLOT/g" \
  "$HERE/Caddyfile.template" > "$HERE/runtime/Caddyfile.next"
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
TUTORBOARD_BLUE_RELEASE=$TUTORBOARD_BLUE_RELEASE
TUTORBOARD_GREEN_RELEASE=$TUTORBOARD_GREEN_RELEASE
CURRENT_TUTORBOARD_RELEASE=$TUTORBOARD_RELEASE
PREVIOUS_TUTORBOARD_RELEASE=$OLD_TUTORBOARD_RELEASE
BLUE_WEB_IMAGE=$BLUE_WEB_IMAGE
BLUE_WORKER_IMAGE=$BLUE_WORKER_IMAGE
GREEN_WEB_IMAGE=$GREEN_WEB_IMAGE
GREEN_WORKER_IMAGE=$GREEN_WORKER_IMAGE
TUTORBOARD_BLUE_IMAGE=$TUTORBOARD_BLUE_IMAGE
TUTORBOARD_GREEN_IMAGE=$TUTORBOARD_GREEN_IMAGE
SCHEDULER_IMAGE=$SCHEDULER_IMAGE
MIGRATION_IMAGE=$MIGRATION_IMAGE
OPS_IMAGE=$OPS_IMAGE
PREVIOUS_SCHEDULER_IMAGE=$PREVIOUS_SCHEDULER_IMAGE
PREVIOUS_MIGRATION_IMAGE=$PREVIOUS_MIGRATION_IMAGE
PREVIOUS_OPS_IMAGE=$PREVIOUS_OPS_IMAGE
EOF
failed=false

if [ "$OLD_SLOT" != "$NEW_SLOT" ]; then
  compose --profile "$OLD_SLOT" stop -t 90 "web-$OLD_SLOT" "worker-$OLD_SLOT" "tutorboard-$OLD_SLOT" || true
fi
echo "Backend $RELEASE and TutorBoard $TUTORBOARD_RELEASE are active in the $NEW_SLOT slot."
