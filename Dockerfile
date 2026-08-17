FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=ldrbrd.settings

WORKDIR /app

# curl backs the compose healthcheck.  git is only needed while pip resolves
# the django-allauth fork from Codeberg, so it is purged in the same layer
# rather than shipped in the image.
COPY requirements.txt requirements-docker.txt ./
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl git \
    && pip install -r requirements-docker.txt \
    && apt-get purge -y git \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN useradd --create-home --uid 10001 app

# The SQLite file lives on a volume rather than in the image.  Creating the
# directory here with the right owner means the named volume inherits that
# ownership the first time Docker populates it, so the unprivileged user can
# actually write to it.
RUN mkdir -p /data && chown app:app /data

# collectstatic runs at build time so the image is self-contained and the
# container needs no writable static directory at runtime.  The manifest
# storage backend needs a settings module that imports cleanly, hence the
# throwaway key.
RUN DJANGO_SECRET_KEY=build-only DJANGO_DB_PATH=/tmp/build.sqlite3 \
    python manage.py collectstatic --noinput \
    && rm -f /tmp/build.sqlite3 \
    && chown -R app:app /app/staticfiles

USER app

EXPOSE 8000

VOLUME ["/data"]

# Checks the body, not just the status: a redirect or an error page would
# otherwise satisfy a bare `curl -f`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz | grep -q '"status": "ok"' || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Two workers, not three: the database is SQLite, so writers serialise anyway
# and more processes just means more lock contention. Threads carry the
# concurrency instead, since these requests are I/O bound.
CMD ["gunicorn", "ldrbrd.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
