#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SECRETS="$HERE/secrets"
RUNTIME="$HERE/runtime"
ENV_FILE="$HERE/.env.production"

if [ ! -f "$ENV_FILE" ]; then
  cp "$HERE/.env.production.example" "$ENV_FILE"
  echo "Created $ENV_FILE; set APP_DOMAIN, public URLs and provider endpoints."
fi
umask 077
mkdir -p "$SECRETS" "$RUNTIME"
chmod 700 "$SECRETS" "$RUNTIME"

random_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    python3 -c "import secrets; print(secrets.token_hex($1))"
  fi
}

write_random() {
  [ -s "$SECRETS/$1" ] || random_hex "$2" > "$SECRETS/$1"
}

write_external() {
  name=$1
  variable=$2
  eval "value=\${$variable:-}"
  if [ -n "$value" ]; then
    printf '%s' "$value" > "$SECRETS/$name"
  elif [ ! -e "$SECRETS/$name" ]; then
    : > "$SECRETS/$name"
  fi
}

write_random app_secret_key 32
write_random bootstrap_admin_password 18
write_random postgres_password 24
write_random redis_password 24
write_random minio_root_password 24
write_random artifact_s3_secret_key 24
write_random metrics_bearer_token 24
write_random grafana_admin_password 18
write_external bbb_secret BBB_SECRET
write_external transcription_webhook_token TRANSCRIPTION_WEBHOOK_TOKEN
write_external document_engine_token DOCUMENT_ENGINE_TOKEN
write_external materials_webhook_token MATERIALS_WEBHOOK_TOKEN
write_external sentry_dsn SENTRY_DSN
write_external backup_s3_secret_key BACKUP_S3_SECRET_KEY

ALERT_WEBHOOK_OVERRIDE=${ALERT_WEBHOOK_URL:-}
. "$ENV_FILE"
[ -z "$ALERT_WEBHOOK_OVERRIDE" ] || ALERT_WEBHOOK_URL=$ALERT_WEBHOOK_OVERRIDE
case "${GEOMETRYOS_IMAGE:-}" in
  *@sha256:*) ;;
  *)
    echo "Set GEOMETRYOS_IMAGE to the published GeometryOS digest before deploy." >&2
    ;;
esac
export POSTGRES_USER POSTGRES_DB SECRETS
python3 <<'PY'
import os
from pathlib import Path
from urllib.parse import quote

secrets = Path(os.environ["SECRETS"])
postgres_password = (secrets / "postgres_password").read_text(encoding="utf-8")
redis_password = (secrets / "redis_password").read_text(encoding="utf-8")
(secrets / "database_url").write_text(
    "postgresql+psycopg://"
    + quote(os.environ["POSTGRES_USER"], safe="")
    + ":"
    + quote(postgres_password, safe="")
    + "@postgres:5432/"
    + quote(os.environ["POSTGRES_DB"], safe=""),
    encoding="utf-8",
)
(secrets / "redis_url").write_text(
    "redis://:" + quote(redis_password, safe="") + "@redis:6379/0",
    encoding="utf-8",
)
PY

case "${ALERT_WEBHOOK_URL:-}" in
  https://* )
    case "$ALERT_WEBHOOK_URL" in *'"'*) echo "ALERT_WEBHOOK_URL contains an invalid quote" >&2; exit 2;; esac
    cat > "$RUNTIME/alertmanager.yml" <<EOF
route:
  receiver: on-call-webhook
  group_by: [alertname, slo]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
receivers:
  - name: on-call-webhook
    webhook_configs:
      - url: "$ALERT_WEBHOOK_URL"
        send_resolved: true
EOF
    ;;
  "" )
    cat > "$RUNTIME/alertmanager.yml" <<'EOF'
route:
  receiver: unconfigured
receivers:
  - name: unconfigured
EOF
    echo "Warning: ALERT_WEBHOOK_URL is empty; configure notifications before production." >&2
    ;;
  * ) echo "ALERT_WEBHOOK_URL must be empty or use https://" >&2; exit 2 ;;
esac

if [ ! -s "$RUNTIME/deployment.env" ]; then
  initial_backend_release=${BLUE_RELEASE:-v1.0.0}
  initial_green_release=${GREEN_RELEASE:-$initial_backend_release}
  initial_tutorboard_release=${TUTORBOARD_BLUE_RELEASE:-v1.0.0}
  initial_tutorboard_green_release=${TUTORBOARD_GREEN_RELEASE:-$initial_tutorboard_release}
  cat > "$RUNTIME/deployment.env" <<EOF
ACTIVE_SLOT=${ACTIVE_SLOT:-blue}
BLUE_RELEASE=$initial_backend_release
GREEN_RELEASE=$initial_green_release
CURRENT_RELEASE=
PREVIOUS_RELEASE=
SCHEDULER_RELEASE=${SCHEDULER_RELEASE:-v1.0.0}
OPS_RELEASE=${OPS_RELEASE:-v1.0.0}
TUTORBOARD_BLUE_RELEASE=$initial_tutorboard_release
TUTORBOARD_GREEN_RELEASE=$initial_tutorboard_green_release
CURRENT_TUTORBOARD_RELEASE=
PREVIOUS_TUTORBOARD_RELEASE=
BLUE_WEB_IMAGE=${IMAGE_REPOSITORY}-web:$initial_backend_release
BLUE_WORKER_IMAGE=${IMAGE_REPOSITORY}-worker:$initial_backend_release
GREEN_WEB_IMAGE=${IMAGE_REPOSITORY}-web:$initial_green_release
GREEN_WORKER_IMAGE=${IMAGE_REPOSITORY}-worker:$initial_green_release
TUTORBOARD_BLUE_IMAGE=${TUTORBOARD_IMAGE_REPOSITORY}:$initial_tutorboard_release
TUTORBOARD_GREEN_IMAGE=${TUTORBOARD_IMAGE_REPOSITORY}:$initial_tutorboard_green_release
SCHEDULER_IMAGE=${IMAGE_REPOSITORY}-scheduler:${SCHEDULER_RELEASE:-v1.0.0}
MIGRATION_IMAGE=${IMAGE_REPOSITORY}-migration:${OPS_RELEASE:-v1.0.0}
OPS_IMAGE=${IMAGE_REPOSITORY}-ops:${OPS_RELEASE:-v1.0.0}
PREVIOUS_SCHEDULER_IMAGE=
PREVIOUS_MIGRATION_IMAGE=
PREVIOUS_OPS_IMAGE=
EOF
fi
. "$RUNTIME/deployment.env"
slot=${ACTIVE_SLOT:-blue}
sed \
  -e "s/__WEB_UPSTREAM__/web-$slot/g" \
  -e "s/__TUTORBOARD_UPSTREAM__/tutorboard-$slot/g" \
  "$HERE/Caddyfile.template" > "$RUNTIME/Caddyfile"
printf '[{"targets":["web-%s:8000"],"labels":{"slot":"%s","release":"%s"}}]\n' \
  "$slot" "$slot" "$CURRENT_RELEASE" > "$RUNTIME/prometheus-targets.json"
chmod 600 "$SECRETS"/* "$RUNTIME/deployment.env"
chmod 644 "$RUNTIME/Caddyfile" "$RUNTIME/prometheus-targets.json" "$RUNTIME/alertmanager.yml"
echo "Production files initialized. Fill non-empty provider secrets in $SECRETS before deploy."
