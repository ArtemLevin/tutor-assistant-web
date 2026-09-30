#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE="$HERE/.env.production"
RUNTIME="$HERE/runtime"
SECRETS="$HERE/secrets"

[ -f "$ENV_FILE" ] || cp "$HERE/.env.production.example" "$ENV_FILE"
mkdir -p "$RUNTIME" "$SECRETS"
chmod 700 "$RUNTIME" "$SECRETS"
umask 077

secret() {
  path="$SECRETS/$1"
  [ -s "$path" ] || openssl rand -hex "$2" > "$path"
}

secret postgres_password 24
secret redis_password 24
secret app_secret_key 32
secret bootstrap_admin_password 18
secret metrics_bearer_token 24
secret minio_root_user 12
secret minio_root_password 24
secret artifact_s3_secret_key 24
secret backup_s3_secret_key 24

postgres_password=$(cat "$SECRETS/postgres_password")
redis_password=$(cat "$SECRETS/redis_password")
minio_user=$(cat "$SECRETS/minio_root_user")
printf '%s\n' "postgresql+psycopg://tutorboard:${postgres_password}@postgres:5432/tutorboard" \
  > "$SECRETS/database_url"
printf '%s\n' "redis://:${redis_password}@redis:6379/0" > "$SECRETS/redis_url"

if [ ! -s "$RUNTIME/deployment.env" ]; then
  set -a
  . "$ENV_FILE"
  set +a
  {
    printf 'ACTIVE_SLOT=blue\n'
    printf 'CURRENT_RELEASE=\nPREVIOUS_RELEASE=\n'
    printf 'BOARD_API_BLUE_IMAGE=%s\n' "$BOARD_API_BLUE_IMAGE"
    printf 'BOARD_API_GREEN_IMAGE=%s\n' "$BOARD_API_GREEN_IMAGE"
    printf 'TUTORBOARD_BLUE_IMAGE=%s\n' "$TUTORBOARD_BLUE_IMAGE"
    printf 'TUTORBOARD_GREEN_IMAGE=%s\n' "$TUTORBOARD_GREEN_IMAGE"
    printf 'MIGRATION_IMAGE=%s\n' "$MIGRATION_IMAGE"
    printf 'OPS_IMAGE=%s\n' "$OPS_IMAGE"
  } > "$RUNTIME/deployment.env"
fi

echo "Board production files initialized. Review $ENV_FILE and off-host backup credentials."
