#!/bin/sh
set -eu

DATABASE_URL=${DATABASE_URL:?set DATABASE_URL to an isolated PostgreSQL database}
export DATABASE_URL AUTO_MIGRATE=false
alembic upgrade head
current=$(alembic current | sed -n '1{s/ .*//;p;}')
[ -n "$current" ] || { echo "No Alembic head after upgrade." >&2; exit 1; }
alembic downgrade -1
alembic upgrade head
alembic check
echo "Upgrade, one-step downgrade, re-upgrade and model drift check passed."
