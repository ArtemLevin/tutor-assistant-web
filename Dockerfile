FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

ARG INSTALL_TRANSCRIPTION=false

RUN addgroup --system tutor && adduser --system --ingroup tutor tutor

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
RUN pip install --upgrade pip && \
    if [ "$INSTALL_TRANSCRIPTION" = "true" ]; then pip install ".[transcription]"; else pip install .; fi

RUN mkdir -p /app/data && chown -R tutor:tutor /app
USER tutor

EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"

CMD ["uvicorn", "tutor_assistant_web.app:app", "--host", "0.0.0.0", "--port", "8000"]
