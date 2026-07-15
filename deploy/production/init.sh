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
[ -s "$SECRETS/backup_s3_secret_key" ] || cp "$SECRETS/artifact_s3_secret_key" "$SECRETS/backup_s3_secret_key"

ALERT_WEBHOOK_OVERRIDE=${ALERT_WEBHOOK_URL:-}
. "$ENV_FILE"
[ -z "$ALERT_WEBHOOK_OVERRIDE" ] || ALERT_WEBHOOK_URL=$ALERT_WEBHOOK_OVERRIDE
POSTGRES_PASSWORD=$(cat "$SECRETS/postgres_password")
REDIS_PASSWORD=$(cat "$SECRETS/redis_password")
printf 'postgresql+psycopg://%s:%s@postgres:5432/%s' \
  "$POSTGRES_USER" "$POSTGRES_PASSWORD" "$POSTGRES_DB" > "$SECRETS/database_url"
printf 'redis://:%s@redis:6379/0' "$REDIS_PASSWORD" > "$SECRETS/redis_url"

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
  cat > "$RUNTIME/deployment.env" <<EOF
ACTIVE_SLOT=${ACTIVE_SLOT:-blue}
BLUE_RELEASE=${BLUE_RELEASE:-v1.0.0}
GREEN_RELEASE=${GREEN_RELEASE:-v1.0.0}
CURRENT_RELEASE=${BLUE_RELEASE:-v1.0.0}
PREVIOUS_RELEASE=
SCHEDULER_RELEASE=${SCHEDULER_RELEASE:-v1.0.0}
OPS_RELEASE=${OPS_RELEASE:-v1.0.0}
EOF
fi
. "$RUNTIME/deployment.env"
slot=${ACTIVE_SLOT:-blue}
sed "s/__WEB_UPSTREAM__/web-$slot/g" "$HERE/Caddyfile.template" > "$RUNTIME/Caddyfile"
printf '[{"targets":["web-%s:8000"],"labels":{"slot":"%s","release":"%s"}}]\n' \
  "$slot" "$slot" "$CURRENT_RELEASE" > "$RUNTIME/prometheus-targets.json"
chmod 600 "$SECRETS"/* "$RUNTIME/deployment.env"
chmod 644 "$RUNTIME/Caddyfile" "$RUNTIME/prometheus-targets.json" "$RUNTIME/alertmanager.yml"
echo "Production files initialized. Fill non-empty provider secrets in $SECRETS before deploy."
