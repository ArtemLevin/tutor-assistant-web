#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
TMP_DIR=$(mktemp -d)
CONTAINER=tutorboard-caddy-contract-$$
trap 'docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; rm -rf "$TMP_DIR"' EXIT INT TERM

sed \
  -e 's/__BOARD_API_UPSTREAM__/127.0.0.1/g' \
  -e 's/__TUTORBOARD_UPSTREAM__/127.0.0.1/g' \
  "$ROOT_DIR/deploy/board-production/Caddyfile.template" \
  > "$TMP_DIR/Caddyfile"

docker run --rm \
  -e APP_DOMAIN=:8080 \
  -e ACME_EMAIL=ci@example.invalid \
  -v "$TMP_DIR/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2.10.0-alpine \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

docker run -d --name "$CONTAINER" \
  -e APP_DOMAIN=:8080 \
  -e ACME_EMAIL=ci@example.invalid \
  -p 127.0.0.1:18080:8080 \
  -v "$TMP_DIR/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2.10.0-alpine >/dev/null

for attempt in $(seq 1 30); do
  if curl --silent --output /dev/null http://127.0.0.1:18080/forbidden; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    docker logs "$CONTAINER" >&2
    exit 1
  fi
  sleep 1
done

INVITATION_SECRET_SENTINEL=INVITATION_SECRET_SENTINEL_7f16c1
WS_TICKET_SECRET_SENTINEL=WS_TICKET_SECRET_SENTINEL_4a95d2
PUBLIC_SENTINEL=PUBLIC_SENTINEL_12ab90

curl --silent --output /dev/null \
  "http://127.0.0.1:18080/j/$INVITATION_SECRET_SENTINEL" || true
curl --silent --output /dev/null \
  "http://127.0.0.1:18080/api/v1/boards/board-test/collaboration?ticket=$WS_TICKET_SECRET_SENTINEL" || true
curl --silent --output /dev/null \
  "http://127.0.0.1:18080/not-published?marker=$PUBLIC_SENTINEL" || true

logs=$(docker logs "$CONTAINER" 2>&1)
if printf '%s' "$logs" | grep -F "$INVITATION_SECRET_SENTINEL" >/dev/null; then
  echo "raw invitation secret leaked to Caddy logs" >&2
  exit 1
fi
if printf '%s' "$logs" | grep -F "$WS_TICKET_SECRET_SENTINEL" >/dev/null; then
  echo "WebSocket ticket leaked to Caddy logs" >&2
  exit 1
fi
if ! printf '%s' "$logs" | grep -F "$PUBLIC_SENTINEL" >/dev/null; then
  echo "control request was not logged; redaction assertion is not meaningful" >&2
  exit 1
fi

status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  http://127.0.0.1:18080/not-published)
if [ "$status" != "404" ]; then
  echo "default-deny route returned $status instead of 404" >&2
  exit 1
fi
