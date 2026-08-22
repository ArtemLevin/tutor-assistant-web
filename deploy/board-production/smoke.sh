#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE_URL=${BASE_URL:-$(sed -n 's/^PUBLIC_BASE_URL=//p' "$HERE/.env.production")}
[ -n "$BASE_URL" ] || { echo "PUBLIC_BASE_URL is missing." >&2; exit 1; }

retry() {
  url=$1
  attempt=0
  until curl --fail --silent --show-error --max-time 10 "$url" > /tmp/board-smoke.json; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 15 ] || return 1
    sleep 4
  done
}

retry "$BASE_URL/health/live"
grep -q '"profile":"board"' /tmp/board-smoke.json
retry "$BASE_URL/health/ready"
grep -q '"status":"ok"' /tmp/board-smoke.json
curl --fail --silent --show-error --max-time 10 "$BASE_URL/login" | grep -q 'TutorBoard'
curl --fail --silent --show-error --max-time 10 "$BASE_URL/board/" | grep -q '<div id="root">'

for forbidden in /students /schedule /portal /api/v1/students /api/v1/lessons/unknown/boards; do
  code=$(curl --silent --output /dev/null --write-out '%{http_code}' "$BASE_URL$forbidden")
  [ "$code" = 404 ] || { echo "Forbidden route $forbidden returned $code" >&2; exit 1; }
done

headers=$(curl --silent --show-error --head --max-time 10 "$BASE_URL/login")
printf '%s' "$headers" | grep -qi '^strict-transport-security:'
printf '%s' "$headers" | grep -qi '^x-content-type-options:'

if [ "${CHECK_LOG_REDACTION:-false}" = true ]; then
  sentinel="join-sentinel-$(date +%s)-secret"
  ticket="ticket-sentinel-$(date +%s)-secret"
  curl --silent --output /dev/null "$BASE_URL/j/$sentinel" || true
  curl --silent --output /dev/null \
    "$BASE_URL/api/v1/boards/example/collaboration?ticket=$ticket" || true
  logs=$(docker compose -f "$HERE/../../compose.board.production.yml" \
    --env-file "$HERE/.env.production" --env-file "$HERE/runtime/deployment.env" logs caddy)
  ! printf '%s' "$logs" | grep -q "$sentinel"
  ! printf '%s' "$logs" | grep -q "$ticket"
  printf '%s' "$logs" | grep -q '\[REDACTED\]'
fi

echo "Board smoke tests passed for $BASE_URL"
