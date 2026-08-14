#!/bin/sh
set -eu

INSTALL_DIR=${INSTALL_DIR:-/opt/tutorboard-stack}
DEPLOY_USER=${DEPLOY_USER:-tutor-deploy}
SECRET_ID=${1:-}
SECRETS_DIR="$INSTALL_DIR/deploy/production/secrets"
ENV_FILE="$INSTALL_DIR/deploy/production/.env.production"
METADATA_TOKEN_URL=http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token

[ "$(id -u)" -eq 0 ] || {
  echo "Run materialize-lockbox.sh as root." >&2
  exit 1
}
case "$INSTALL_DIR" in
  ""|/|/opt) echo "Unsafe installation directory: $INSTALL_DIR" >&2; exit 2 ;;
  /*) ;;
  *) echo "INSTALL_DIR must be absolute." >&2; exit 2 ;;
esac
if [ -z "$SECRET_ID" ] && [ -s /etc/tutorboard/lockbox-secret-id ]; then
  SECRET_ID=$(sed -n '1p' /etc/tutorboard/lockbox-secret-id)
fi
case "$SECRET_ID" in
  *[!A-Za-z0-9_-]*|"") echo "A valid Lockbox secret ID is required." >&2; exit 2 ;;
esac
[ -f "$ENV_FILE" ] || {
  echo "Production environment file is missing: $ENV_FILE" >&2
  exit 1
}
id "$DEPLOY_USER" >/dev/null 2>&1 || {
  echo "Deployment user does not exist: $DEPLOY_USER" >&2
  exit 1
}

umask 077
payload_file=$(mktemp)
staging_dir=$(mktemp -d)
cleanup() {
  rm -f "$payload_file"
  rm -rf "${staging_dir:?}"
}
trap cleanup EXIT INT TERM

token=$(
  curl -fsS --connect-timeout 3 --max-time 10 \
    -H 'Metadata-Flavor: Google' \
    "$METADATA_TOKEN_URL" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
)
[ -n "$token" ] || {
  echo "Compute metadata did not return a service-account IAM token." >&2
  exit 1
}
curl -fsS --connect-timeout 5 --max-time 20 \
  -H "Authorization: Bearer $token" \
  "https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/$SECRET_ID/payload" \
  > "$payload_file"
unset token

python3 - "$payload_file" "$staging_dir" <<'PY'
import base64
import json
import os
import sys
from pathlib import Path

payload_path = Path(sys.argv[1])
destination = Path(sys.argv[2])
required = {
    "app_secret_key",
    "artifact_s3_secret_key",
    "backup_s3_secret_key",
    "bbb_secret",
    "bootstrap_admin_password",
    "document_engine_token",
    "ghcr_token",
    "grafana_admin_password",
    "materials_webhook_token",
    "metrics_bearer_token",
    "minio_root_password",
    "postgres_password",
    "redis_password",
    "sentry_dsn",
    "transcription_webhook_token",
}
document = json.loads(payload_path.read_text(encoding="utf-8"))
values: dict[str, bytes] = {}
for entry in document.get("entries", []):
    key = entry.get("key")
    if key not in required or key in values:
        continue
    if "textValue" in entry:
        value = str(entry["textValue"]).encode()
    elif "binaryValue" in entry:
        value = base64.b64decode(entry["binaryValue"], validate=True)
    else:
        continue
    if not value or b"\0" in value or b"\n" in value or b"\r" in value:
        raise SystemExit(f"Lockbox entry {key!r} must be a non-empty single-line value")
    values[key] = value
missing = sorted(required - values.keys())
if missing:
    raise SystemExit("Lockbox payload is missing keys: " + ", ".join(missing))
for key, value in values.items():
    path = destination / key
    path.write_bytes(value)
    os.chmod(path, 0o600)
PY

install -d -m 0700 -o "$DEPLOY_USER" -g docker "$SECRETS_DIR"
secret_names="app_secret_key artifact_s3_secret_key backup_s3_secret_key bbb_secret bootstrap_admin_password document_engine_token grafana_admin_password materials_webhook_token metrics_bearer_token minio_root_password postgres_password redis_password sentry_dsn transcription_webhook_token"
for secret_name in $secret_names; do
  install -m 0600 -o "$DEPLOY_USER" -g docker \
    "$staging_dir/$secret_name" "$SECRETS_DIR/$secret_name"
done
install -d -m 0700 -o "$DEPLOY_USER" -g docker /etc/tutorboard
install -m 0600 -o "$DEPLOY_USER" -g docker \
  "$staging_dir/ghcr_token" /etc/tutorboard/ghcr_token

set -a
# The deploy operator selects the runtime environment file.
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
export POSTGRES_DB=${POSTGRES_DB:-tutor}
export POSTGRES_USER=${POSTGRES_USER:-tutor}
python3 - "$SECRETS_DIR" <<'PY'
import os
import sys
from pathlib import Path
from urllib.parse import quote

destination = Path(sys.argv[1])
postgres_password = (destination / "postgres_password").read_text(encoding="utf-8")
redis_password = (destination / "redis_password").read_text(encoding="utf-8")
postgres_user = os.environ["POSTGRES_USER"]
postgres_database = os.environ["POSTGRES_DB"]
(destination / "database_url").write_text(
    "postgresql+psycopg://"
    + quote(postgres_user, safe="")
    + ":"
    + quote(postgres_password, safe="")
    + "@postgres:5432/"
    + quote(postgres_database, safe=""),
    encoding="utf-8",
)
(destination / "redis_url").write_text(
    "redis://:" + quote(redis_password, safe="") + "@redis:6379/0",
    encoding="utf-8",
)
os.chmod(destination / "database_url", 0o600)
os.chmod(destination / "redis_url", 0o600)
PY
chown "$DEPLOY_USER:docker" "$SECRETS_DIR/database_url" "$SECRETS_DIR/redis_url"
date -u +%Y-%m-%dT%H:%M:%SZ > /etc/tutorboard/lockbox-materialized-at
chmod 0644 /etc/tutorboard/lockbox-materialized-at

echo "Lockbox payload materialized without exposing secret values."
