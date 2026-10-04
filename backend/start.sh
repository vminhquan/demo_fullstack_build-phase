#!/bin/sh
set -e
# RUN_MIGRATIONS=1 applies Alembic migrations before serving (used on hosts without a separate
# migrate step, e.g. Render's free plan). docker compose runs migrations in its own service.
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  alembic upgrade head
  python -m app.seed_catalog
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
