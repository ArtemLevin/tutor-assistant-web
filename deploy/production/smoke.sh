#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE="$HERE/.env.production"
BASE_URL=${BASE_URL:-$(sed -n 's/^PUBLIC_BASE_URL=//p' "$ENV_FILE")}
[ -n "$BASE_URL" ] || { echo "PUBLIC_BASE_URL is missing." >&2; exit 1; }

retry() {
  url=$1
  attempt=0
  until curl --fail --silent --show-error --max-time 10 "$url" >/tmp/tutor-smoke.json; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 15 ] || return 1
    sleep 4
  done
}

retry "$BASE_URL/health/live"
grep -q '"status":"ok"' /tmp/tutor-smoke.json
retry "$BASE_URL/health/ready"
grep -q '"status":"ready"' /tmp/tutor-smoke.json
curl --fail --silent --show-error --max-time 10 "$BASE_URL/" >/dev/null
headers=$(curl --silent --show-error --head --max-time 10 "$BASE_URL/")
printf '%s' "$headers" | grep -qi '^strict-transport-security:'
printf '%s' "$headers" | grep -qi '^content-security-policy:'
echo "Smoke tests passed for $BASE_URL"
