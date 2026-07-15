from __future__ import annotations

import json
import logging
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

import sentry_sdk
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram

from tutor_assistant_web.config import Settings

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")

HTTP_REQUESTS = Counter("tutor_http_requests_total", "HTTP requests", ("method", "route", "status"))
HTTP_DURATION = Histogram(
    "tutor_http_request_duration_seconds", "HTTP request duration", ("method", "route")
)
WORKFLOW_DURATION = Histogram(
    "tutor_workflow_duration_seconds",
    "Workflow stage duration",
    ("stage", "outcome"),
)
LESSON_DURATION = Histogram("tutor_lesson_duration_seconds", "Completed lesson duration")
ARTIFACT_BYTES = Histogram("tutor_artifact_size_bytes", "Stored artifact size", ("kind",))
QUEUE_SIZE = Gauge("tutor_queue_size", "Durable queue size", ("queue", "status"))
QUEUE_AGE = Gauge("tutor_queue_oldest_age_seconds", "Oldest pending item age", ("queue",))
READINESS = Gauge("tutor_readiness_dependency", "Dependency readiness", ("dependency",))
CRITICAL_FAILURES = Counter(
    "tutor_critical_failures_total", "Critical failures suitable for alerting", ("component",)
)

_SENSITIVE_KEYS = re.compile(
    r"(password|secret|token|authorization|cookie|transcript|notes|phone|email|content|"
    r"full_name|guardian|student_name)",
    re.I,
)
_BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~+\-/]+=*")
_EMAIL = re.compile(r"(?<![\w.-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE = re.compile(r"(?<![\w])(?:\+?7|8)[ ()-]?\d{3}[ ()-]?\d{3}[ -]?\d{2}[ -]?\d{2}(?![\w])")
_SECRET_QUERY = re.compile(r"(?i)(token|secret|password|checksum)=([^&\s]+)")
_CORRELATION_ID = re.compile(
    r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|"
    r"[0-9A-HJKMNP-TV-Z]{26})"
)
_configured = False
_tracing_configured = False


def correlation_id() -> str:
    return correlation_id_var.get() or uuid4().hex


def bind_correlation(value: str | None = None) -> Token:
    supplied = str(value or "")
    return correlation_id_var.set(supplied if _CORRELATION_ID.fullmatch(supplied) else uuid4().hex)


def reset_correlation(token: Token) -> None:
    correlation_id_var.reset(token)


def redact(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _BEARER.sub("Bearer [REDACTED]", value)
        value = _EMAIL.sub("[REDACTED_EMAIL]", value)
        value = _PHONE.sub("[REDACTED_PHONE]", value)
        return _SECRET_QUERY.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    return value


def scrub_sentry_event(event: dict[str, Any], _hint=None) -> dict[str, Any]:
    cleaned = redact(event)
    for item in cleaned.get("exception", {}).get("values", []):
        if isinstance(item, dict) and "value" in item:
            item["value"] = "[REDACTED]"
    return cleaned


class JsonFormatter(logging.Formatter):
    _standard = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "correlation_id": correlation_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in self._standard and not key.startswith("_"):
                data[key] = redact(value, key)
        if record.exc_info:
            # Exception messages can contain provider response bodies or lesson
            # text. Keep the actionable type while sending the scrubbed event
            # to the configured error tracker.
            data["exception_type"] = record.exc_info[0].__name__
        return json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":"))


def configure_logging(settings: Settings) -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.log_json else logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())
    _configured = True


def _configure_tracer(settings: Settings) -> None:
    global _tracing_configured
    if _tracing_configured:
        return
    if settings.otel_exporter_otlp_endpoint:
        provider = TracerProvider(
            resource=Resource.create({"service.name": settings.otel_service_name})
        )
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
        )
        trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()
    CeleryInstrumentor().instrument()
    _tracing_configured = True


def configure_telemetry(app, settings: Settings, engine) -> None:
    _configure_tracer(settings)
    if not getattr(app.state, "otel_instrumented", False):
        FastAPIInstrumentor.instrument_app(app)
        app.state.otel_instrumented = True
    if not getattr(engine, "_tutor_otel_instrumented", False):
        SQLAlchemyInstrumentor().instrument(engine=engine)
        engine._tutor_otel_instrumented = True
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment or settings.app_env,
            send_default_pii=False,
            before_send=scrub_sentry_event,
            traces_sample_rate=0.1,
        )


def configure_worker_telemetry(settings: Settings) -> None:
    _configure_tracer(settings)
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment or settings.app_env,
            send_default_pii=False,
            before_send=scrub_sentry_event,
            traces_sample_rate=0.1,
        )


@contextmanager
def workflow_timer(stage: str):
    started = perf_counter()
    outcome = "success"
    try:
        yield
    except Exception:
        outcome = "failure"
        raise
    finally:
        WORKFLOW_DURATION.labels(stage=stage, outcome=outcome).observe(perf_counter() - started)


def record_exception(component: str, exc: Exception) -> None:
    CRITICAL_FAILURES.labels(component=component).inc()
    span = trace.get_current_span()
    span.add_event(
        "exception",
        {"exception.type": type(exc).__name__, "exception.message": "[REDACTED]"},
    )
    sentry_sdk.capture_exception(exc)
