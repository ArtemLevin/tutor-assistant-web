# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src
ARG INSTALL_TRANSCRIPTION=false
RUN if [ "${INSTALL_TRANSCRIPTION}" = "true" ]; then \
      python -m pip wheel --wheel-dir /wheels ".[transcription]"; \
    else \
      python -m pip wheel --wheel-dir /wheels .; \
    fi


FROM python:3.12-slim-bookworm AS runtime-base

ARG APP_UID=10001
ARG APP_GID=10001
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="Tutor Assistant Web" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/ArtemLevin/tutor-assistant-web"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/home/tutor/.local/bin:${PATH}"

RUN groupadd --gid "${APP_GID}" tutor \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin tutor

WORKDIR /app
COPY --from=builder /wheels /wheels
COPY alembic.ini ./alembic.ini
RUN python -m pip install --no-index --find-links=/wheels tutor-assistant-web==1.0.0 \
    && rm -rf /wheels \
    && mkdir -p /app/data \
    && chown -R tutor:tutor /app /home/tutor

USER tutor:tutor


FROM runtime-base AS web
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=4s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"
CMD ["uvicorn", "tutor_assistant_web.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16", "--no-server-header"]


FROM runtime-base AS worker
STOPSIGNAL SIGTERM
CMD ["celery", "-A", "tutor_assistant_web.worker.celery_app", "worker", "--loglevel=INFO", "--queues=transcription,materials,delivery,maintenance", "--hostname=worker@%h"]


FROM runtime-base AS scheduler
STOPSIGNAL SIGTERM
CMD ["celery", "-A", "tutor_assistant_web.worker.celery_app", "beat", "--loglevel=INFO", "--pidfile=/tmp/celerybeat.pid", "--schedule=/tmp/celerybeat-schedule"]


FROM runtime-base AS migration
CMD ["alembic", "upgrade", "head"]


FROM runtime-base AS ops
USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends postgresql-client curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
USER tutor:tutor
CMD ["tutor-assistant-backup", "--help"]
