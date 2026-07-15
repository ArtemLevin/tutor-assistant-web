#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
docker compose -f "$ROOT/compose.production.yml" \
  --env-file "$HERE/.env.production" --env-file "$HERE/runtime/deployment.env" \
  --profile jobs run --rm ops tutor-assistant-backup create "$@"
