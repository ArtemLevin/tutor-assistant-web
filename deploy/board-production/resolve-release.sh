#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE="$HERE/.env.production"
[ -s "$ENV_FILE" ] || {
  echo "Missing $ENV_FILE. Run $HERE/init.sh and configure the deployment first." >&2
  exit 2
}
set -a
. "$ENV_FILE"
set +a

tag=${1:-}
[ -n "$tag" ] || { echo "Usage: $0 <registry-image-tag>" >&2; exit 2; }
echo "Diagnostic only: automated board promotion uses board-release-manifest digests." >&2
case "$tag" in latest|*[!A-Za-z0-9._-]*) echo "Invalid release tag." >&2; exit 2 ;; esac
: "${BACKEND_IMAGE_REPOSITORY:?set BACKEND_IMAGE_REPOSITORY}"
: "${TUTORBOARD_IMAGE_REPOSITORY:?set TUTORBOARD_IMAGE_REPOSITORY}"

resolve() {
  tagged=$1
  repository=${tagged%:*}
  docker pull "$tagged" >/dev/null
  docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$tagged" \
    | awk -v prefix="$repository@sha256:" 'index($0,prefix)==1 && length($0)==length(prefix)+64 {print; exit}'
}

BOARD_API_DIGEST=$(resolve "$BACKEND_IMAGE_REPOSITORY-board-api:$tag")
MIGRATION_DIGEST=$(resolve "$BACKEND_IMAGE_REPOSITORY-migration:$tag")
OPS_DIGEST=$(resolve "$BACKEND_IMAGE_REPOSITORY-ops:$tag")
TUTORBOARD_DIGEST=$(resolve "$TUTORBOARD_IMAGE_REPOSITORY:$tag")
for value in "$BOARD_API_DIGEST" "$MIGRATION_DIGEST" "$OPS_DIGEST" "$TUTORBOARD_DIGEST"; do
  [ -n "$value" ] || { echo "Registry did not return an immutable digest." >&2; exit 1; }
done

printf "BOARD_API_DIGEST='%s'\n" "$BOARD_API_DIGEST"
printf "MIGRATION_DIGEST='%s'\n" "$MIGRATION_DIGEST"
printf "OPS_DIGEST='%s'\n" "$OPS_DIGEST"
printf "TUTORBOARD_DIGEST='%s'\n" "$TUTORBOARD_DIGEST"
