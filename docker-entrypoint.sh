#!/usr/bin/env bash
# Prepares the database, then hands off to the process in CMD.
set -euo pipefail

DB_PATH="${DJANGO_DB_PATH:-/data/db.sqlite3}"
DB_DIR="$(dirname "$DB_PATH")"

if [ ! -w "$DB_DIR" ]; then
  echo "database directory $DB_DIR is not writable by $(id -un)" >&2
  echo "if you mounted a host path, chown it to uid 10001" >&2
  exit 1
fi

echo "applying migrations..."
python manage.py migrate --noinput

# A Ldrbrd with no superuser has no way to promote anyone to staff, and so no
# way to create a course. Seed one when credentials are supplied; skip quietly
# when they are not, so restarts stay idempotent.
if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  echo "ensuring superuser ${DJANGO_SUPERUSER_USERNAME} exists..."
  python manage.py createsuperuser --noinput --skip-checks 2>/dev/null \
    || echo "superuser already present, leaving it alone"
fi

exec "$@"
