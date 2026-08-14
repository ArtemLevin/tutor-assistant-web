#!/bin/sh
# shellcheck disable=SC1090
set -eu

MODE=deploy
if [ "${1:-}" = "--runtime" ]; then
  MODE=runtime
  shift
fi

if [ "$MODE" = deploy ]; then
  [ "$#" -ge 1 ] && [ "$#" -le 2 ] || {
    echo "Usage: $0 [--runtime] <backend-release-tag> [tutorboard-release-tag]" >&2
    exit 2
  }
  RELEASE=$1
  TUTORBOARD_RELEASE=${2:-$1}
else
  [ "$#" -eq 0 ] || { echo "Usage: $0 --runtime" >&2; exit 2; }
fi

HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH='' cd -- "$HERE/../.." && pwd)
ENV_FILE="$ROOT/deploy/production/.env.production"
STATE="$ROOT/deploy/production/runtime/deployment.env"
SECRETS="$ROOT/deploy/production/secrets"
COMPOSE_FILE="$ROOT/compose.production.yml"

[ "$(uname -s)" = Linux ] || { echo "Production deploy is supported on Linux only." >&2; exit 1; }
[ -r /etc/os-release ] || { echo "/etc/os-release is unavailable." >&2; exit 1; }
. /etc/os-release
[ "${ID:-}" = ubuntu ] || { echo "Production host must run Ubuntu." >&2; exit 1; }
case "${VERSION_ID:-}" in
  22.04|24.04) ;;
  *) echo "Ubuntu 22.04 or 24.04 is required; found ${VERSION_ID:-unknown}." >&2; exit 1 ;;
esac
[ "$(uname -m)" = x86_64 ] || {
  echo "Published production images currently require x86_64/amd64." >&2
  exit 1
}

for command_name in docker curl getent ss systemctl timedatectl; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command is missing: $command_name" >&2
    exit 1
  }
done
docker info >/dev/null 2>&1 || {
  echo "Docker daemon is unavailable to user $(id -un)." >&2
  exit 1
}
docker compose version >/dev/null 2>&1 || {
  echo "Docker Compose v2 is unavailable." >&2
  exit 1
}
systemctl is-enabled --quiet tutorboard-stack.service || {
  echo "tutorboard-stack.service is not installed and enabled." >&2
  exit 1
}
systemctl is-enabled --quiet tutorboard-firewall.service || {
  echo "tutorboard-firewall.service is not installed and enabled." >&2
  exit 1
}

[ -s "$ENV_FILE" ] || { echo "Run deploy/production/init.sh and configure $ENV_FILE." >&2; exit 1; }
[ -s "$STATE" ] || { echo "Deployment state is missing: $STATE" >&2; exit 1; }
set -a
. "$ENV_FILE"
. "$STATE"
set +a

[ "${APP_ENV:-}" = production ] || { echo "APP_ENV must be production." >&2; exit 1; }
[ "${PUBLIC_BASE_URL:-}" = "https://${APP_DOMAIN:-}" ] || {
  echo "PUBLIC_BASE_URL must equal https://APP_DOMAIN." >&2
  exit 1
}
case ",${TRUSTED_HOSTS:-}," in
  *,"${APP_DOMAIN}",*) ;;
  *) echo "TRUSTED_HOSTS must include APP_DOMAIN." >&2; exit 1 ;;
esac
case "${BACKUP_S3_ENDPOINT_URL:-}" in
  https://*) ;;
  *)
    echo "BACKUP_S3_ENDPOINT_URL must use off-host HTTPS S3; local MinIO is not a disaster-recovery backup." >&2
    exit 1
    ;;
esac
case "$BACKUP_S3_ENDPOINT_URL" in
  *localhost*|*127.0.0.1*|*minio:9000*)
    echo "BACKUP_S3_ENDPOINT_URL must not point to this host or the Compose MinIO service." >&2
    exit 1
    ;;
esac
[ "${BACKUP_S3_BUCKET:-}" != "${ARTIFACT_S3_BUCKET:-}" ] || {
  echo "Use a dedicated backup bucket, separate from the artifact bucket." >&2
  exit 1
}
case "${ALERT_WEBHOOK_URL:-}" in
  https://*) ;;
  *) echo "ALERT_WEBHOOK_URL must be a configured HTTPS endpoint." >&2; exit 1 ;;
esac
for provider_url in "${BBB_BASE_URL:-}" "${TRANSCRIPTION_WEBHOOK_URL:-}" "${DOCUMENT_ENGINE_URL:-}"; do
  case "$provider_url" in
    https://*) ;;
    *) echo "Production provider URLs must use HTTPS: $provider_url" >&2; exit 1 ;;
  esac
done

case "${GEOMETRYOS_IMAGE:-}" in
  *@sha256:*)
    digest=${GEOMETRYOS_IMAGE##*@sha256:}
    case "$digest" in
      *[!0-9a-fA-F]*|"") echo "GEOMETRYOS_IMAGE digest must be hexadecimal." >&2; exit 1 ;;
    esac
    [ "${#digest}" -eq 64 ] || {
      echo "GEOMETRYOS_IMAGE must contain a complete 64-character digest." >&2
      exit 1
    }
    ;;
  *) echo "GEOMETRYOS_IMAGE must be pinned with @sha256:." >&2; exit 1 ;;
esac

required_secrets="database_url redis_url app_secret_key bootstrap_admin_password postgres_password redis_password minio_root_password artifact_s3_secret_key metrics_bearer_token backup_s3_secret_key grafana_admin_password sentry_dsn"
[ "${BBB_DEMO_MODE:-false}" = true ] || required_secrets="$required_secrets bbb_secret"
[ "${TRANSCRIPTION_PROVIDER:-webhook}" != webhook ] || required_secrets="$required_secrets transcription_webhook_token"
[ "${DOCUMENT_ENGINE_PROVIDER:-latex-for-everyone}" != latex-for-everyone ] || required_secrets="$required_secrets document_engine_token"
required_secrets="$required_secrets materials_webhook_token"
for secret_name in $required_secrets; do
  [ -s "$SECRETS/$secret_name" ] || {
    echo "Required production secret is empty: $SECRETS/$secret_name" >&2
    exit 1
  }
done

case "${APP_DOMAIN:-} ${ACME_EMAIL:-} ${BOOTSTRAP_ADMIN_EMAIL:-} ${BACKUP_S3_ENDPOINT_URL:-} ${BACKUP_S3_ACCESS_KEY:-} ${GEOMETRYOS_IMAGE:-}" in
  *example.com*|*.invalid*|*REPLACE_WITH*|*replace-with*)
    echo "Production configuration still contains placeholder values." >&2
    exit 1
    ;;
esac

memory_mb=$(awk '/MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)
minimum_memory_mb=${MINIMUM_HOST_MEMORY_MB:-16384}
[ "$memory_mb" -ge "$minimum_memory_mb" ] || {
  echo "At least ${minimum_memory_mb} MiB RAM is required; found ${memory_mb} MiB." >&2
  exit 1
}
available_kb=$(df -Pk "$ROOT" | awk 'NR == 2 {print $4}')
minimum_disk_gb=${MINIMUM_HOST_DISK_GB:-40}
[ "$available_kb" -ge "$((minimum_disk_gb * 1024 * 1024))" ] || {
  echo "At least ${minimum_disk_gb} GiB free disk is required for images and volumes." >&2
  exit 1
}

attempt=0
while :; do
  clock_ready=false
  dns_ready=false
  [ "$(timedatectl show --property=NTPSynchronized --value 2>/dev/null)" = yes ] \
    && clock_ready=true
  getent ahosts "$APP_DOMAIN" >/dev/null 2>&1 && dns_ready=true
  [ "$clock_ready" = true ] && [ "$dns_ready" = true ] && break
  attempt=$((attempt + 1))
  if [ "$MODE" != runtime ] || [ "$attempt" -ge 30 ]; then
    [ "$clock_ready" = true ] || echo "Host clock is not NTP-synchronized." >&2
    [ "$dns_ready" = true ] || echo "APP_DOMAIN does not resolve: $APP_DOMAIN" >&2
    exit 1
  fi
  sleep 4
done

compose() {
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --env-file "$STATE" "$@"
}
compose config --quiet

if [ "$MODE" = runtime ]; then
  if [ "${ACTIVE_SLOT:-blue}" = blue ]; then
    runtime_web_image=${BLUE_WEB_IMAGE:-}
    runtime_worker_image=${BLUE_WORKER_IMAGE:-}
    runtime_tutorboard_image=${TUTORBOARD_BLUE_IMAGE:-}
  else
    runtime_web_image=${GREEN_WEB_IMAGE:-}
    runtime_worker_image=${GREEN_WORKER_IMAGE:-}
    runtime_tutorboard_image=${TUTORBOARD_GREEN_IMAGE:-}
  fi
  for runtime_image in \
    "$runtime_web_image" \
    "$runtime_worker_image" \
    "$runtime_tutorboard_image" \
    "${SCHEDULER_IMAGE:-}" \
    "${MIGRATION_IMAGE:-}" \
    "${OPS_IMAGE:-}"; do
    case "$runtime_image" in
      *@sha256:*)
        runtime_digest=${runtime_image##*@sha256:}
        [ "${#runtime_digest}" -eq 64 ] || {
          echo "Runtime image has an incomplete digest: $runtime_image" >&2
          exit 1
        }
        case "$runtime_digest" in
          *[!0-9a-fA-F]*)
            echo "Runtime image digest is not hexadecimal: $runtime_image" >&2
            exit 1
            ;;
        esac
        ;;
      *)
        echo "Runtime image is not pinned by digest: $runtime_image" >&2
        exit 1
        ;;
    esac
  done
fi

if ss -H -ltn | awk '{print $4}' | grep -Eq '(:|\])80$|(:|\])443$'; then
  caddy_id=$(compose ps -q caddy 2>/dev/null || true)
  [ -n "$caddy_id" ] || {
    echo "Ports 80/443 are already occupied by a process outside this Compose stack." >&2
    exit 1
  }
fi

if [ "$MODE" = deploy ]; then
  case "$RELEASE" in
    latest|""|*[!A-Za-z0-9._-]*) echo "Backend release must be an immutable tag." >&2; exit 1 ;;
  esac
  case "$TUTORBOARD_RELEASE" in
    latest|""|*[!A-Za-z0-9._-]*) echo "TutorBoard release must be an immutable tag." >&2; exit 1 ;;
  esac

  check_amd64_image() {
    image=$1
    docker manifest inspect --verbose "$image" 2>/dev/null \
      | grep -Eq '"architecture"[[:space:]]*:[[:space:]]*"amd64"' || {
        echo "Image is inaccessible or has no linux/amd64 manifest: $image" >&2
        exit 1
      }
  }

  check_amd64_image "${IMAGE_REPOSITORY}-web:${RELEASE}"
  check_amd64_image "${IMAGE_REPOSITORY}-worker:${RELEASE}"
  check_amd64_image "${IMAGE_REPOSITORY}-scheduler:${RELEASE}"
  check_amd64_image "${IMAGE_REPOSITORY}-migration:${RELEASE}"
  check_amd64_image "${IMAGE_REPOSITORY}-ops:${RELEASE}"
  check_amd64_image "${TUTORBOARD_IMAGE_REPOSITORY}:${TUTORBOARD_RELEASE}"
  check_amd64_image "$GEOMETRYOS_IMAGE"
fi

echo "Ubuntu production preflight passed (${MODE} mode)."
