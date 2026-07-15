from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from tutor_assistant_web.app import create_app
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.automation.application import OutboxService
from tutor_assistant_web.modules.automation.models import OutboxEvent
from tutor_assistant_web.modules.dashboard.application import ReadinessService
from tutor_assistant_web.modules.identity.application import Principal
from tutor_assistant_web.modules.identity.models import DEFAULT_ORGANIZATION_ID
from tutor_assistant_web.modules.materials.models import ProcessingJob
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.observability import (
    JsonFormatter,
    bind_correlation,
    reset_correlation,
    scrub_sentry_event,
)
from tutor_assistant_web.providers.tasks import CeleryJobDispatcher
from tutor_assistant_web.shared.web import WebSupport


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "app_secret_key": "test-secret-for-security-observability",
        "database_url": f"sqlite:///{tmp_path / 'security.db'}",
        "seed_demo_data": False,
        "bootstrap_admin_password": "test-password",
        "otel_exporter_otlp_endpoint": "",
    }
    values.update(overrides)
    return Settings(**values)


def _login(client: TestClient) -> None:
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": _csrf(page.text),
            "email": "admin@localhost",
            "password": "test-password",
            "next": "/",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_security_headers_secure_cookie_csrf_and_trusted_host(tmp_path):
    settings = _settings(tmp_path, session_cookie_secure=True)
    database = Database(settings.database_url)
    with TestClient(create_app(settings, database), base_url="https://testserver") as client:
        page = client.get("/login")
        cookie = page.headers["set-cookie"].lower()
        assert f"{settings.session_cookie_name}=" in cookie
        assert "secure" in cookie
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        assert "unsafe-inline" not in page.headers["content-security-policy"]
        assert page.headers["x-content-type-options"] == "nosniff"
        assert page.headers["x-frame-options"] == "DENY"
        assert page.headers["strict-transport-security"].startswith("max-age=")
        assert page.headers["x-request-id"]

        rejected = client.post(
            "/login",
            data={"email": "admin@localhost", "password": "test-password"},
        )
        assert rejected.status_code == 403
        assert client.get("/health/live", headers={"host": "evil.example"}).status_code == 400


def test_login_rate_limit_uses_bounded_response(tmp_path):
    settings = _settings(tmp_path, rate_limit_login=2)
    database = Database(settings.database_url)
    with TestClient(create_app(settings, database)) as client:
        csrf = _csrf(client.get("/login").text)
        for _ in range(2):
            response = client.post(
                "/login",
                data={
                    "csrf_token": csrf,
                    "email": "admin@localhost",
                    "password": "wrong-password",
                },
            )
            assert response.status_code == 401
        limited = client.post(
            "/login",
            data={
                "csrf_token": csrf,
                "email": "admin@localhost",
                "password": "wrong-password",
            },
        )
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == str(settings.rate_limit_window_seconds)


class _IdentityStub:
    def __init__(self, principal: Principal) -> None:
        self.value = principal

    def switch_workspace(self, _user_id: str, _organization_id: str) -> Principal:
        return self.value


def test_session_idle_expiry_and_rotation(monkeypatch):
    now = 100_000
    monkeypatch.setattr("tutor_assistant_web.shared.web.time.time", lambda: now)
    principal = Principal("user", "org", "Org", "tutor", "user@example.test", "Tutor")
    settings = Settings(
        session_max_age=3600,
        session_idle_timeout=600,
        session_rotation_seconds=300,
    )
    web = WebSupport(settings, None, None, _IdentityStub(principal))  # type: ignore[arg-type]

    expired = Request({"type": "http", "headers": [], "session": {"session_seen": now - 601}})
    assert web.principal(expired) is None
    assert expired.session == {}

    session = {
        "user_id": "user",
        "organization_id": "org",
        "role": "tutor",
        "email": "user@example.test",
        "full_name": "Tutor",
        "session_created": now - 500,
        "session_seen": now - 5,
        "session_rotated": now - 301,
        "session_id": "old-session-id",
    }
    request = Request({"type": "http", "headers": [], "session": session})
    assert web.principal(request) == principal
    assert request.session["session_id"] != "old-session-id"
    assert request.session["session_rotated"] == now


def test_json_logs_redact_pii_secrets_transcripts_and_exception_text():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        "security-test",
        logging.ERROR,
        __file__,
        1,
        "request for tutor@example.test with Bearer top-secret-token",
        (),
        None,
    )
    record.transcript = "UNIQUE_TRANSCRIPT_TEXT"
    record.guardian_phone = "+7 999 123-45-67"
    record.full_name = "Private Student Name"
    rendered = formatter.format(record)
    assert "tutor@example.test" not in rendered
    assert "top-secret-token" not in rendered
    assert "UNIQUE_TRANSCRIPT_TEXT" not in rendered
    assert "+7 999 123-45-67" not in rendered
    assert "Private Student Name" not in rendered

    try:
        raise ValueError("UNIQUE_EXCEPTION_TRANSCRIPT")
    except ValueError:
        record.exc_info = __import__("sys").exc_info()
    parsed = json.loads(formatter.format(record))
    assert parsed["exception_type"] == "ValueError"
    assert "UNIQUE_EXCEPTION_TRANSCRIPT" not in json.dumps(parsed)
    sentry = scrub_sentry_event(
        {
            "exception": {"values": [{"type": "ValueError", "value": "PRIVATE LESSON"}]},
            "request": {"headers": {"Authorization": "Bearer secret"}},
        }
    )
    assert sentry["exception"]["values"][0]["value"] == "[REDACTED]"
    assert sentry["request"]["headers"]["Authorization"] == "[REDACTED]"


def test_http_outbox_and_worker_keep_the_same_correlation_id(tmp_path, monkeypatch):
    settings = _settings(tmp_path, task_eager=False)
    database = Database(settings.database_url)
    correlation = "0123456789abcdef0123456789abcdef"
    with TestClient(create_app(settings, database), follow_redirects=False) as client:
        _login(client)
        with database.sessions() as session:
            student = Student(organization_id=DEFAULT_ORGANIZATION_ID, full_name="Trace Student")
            session.add(student)
            session.flush()
            lesson = Lesson(
                organization_id=DEFAULT_ORGANIZATION_ID,
                student_id=student.id,
                title="Tracing",
                starts_at=datetime.now(UTC),
                ends_at=datetime.now(UTC) + timedelta(hours=1),
                bbb_meeting_id="trace-meeting",
                attendee_password="attendee",
                moderator_password="moderator",
            )
            session.add(lesson)
            session.commit()
            lesson_id = lesson.id
        detail = client.get(f"/lessons/{lesson_id}")
        response = client.post(
            f"/lessons/{lesson_id}/process",
            headers={"x-request-id": correlation},
            data={"csrf_token": _csrf(detail.text)},
        )
        assert response.headers["x-request-id"] == correlation

    with database.sessions() as session:
        event = session.scalar(select(OutboxEvent))
        job = session.scalar(select(ProcessingJob))
        assert event is not None and job is not None
        assert event.correlation_id == correlation
        assert job.correlation_id == correlation

    captured: dict[str, object] = {}

    class _Task:
        @staticmethod
        def apply_async(**kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("tutor_assistant_web.worker.process_lesson_task", _Task())
    token = bind_correlation(event.correlation_id)
    try:
        CeleryJobDispatcher().enqueue_lesson_processing(job.id, queue="materials")
    finally:
        reset_correlation(token)
    assert captured["headers"] == {"x-correlation-id": correlation}


class _HealthDependency:
    name = "test"
    is_demo = False

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def healthcheck(self) -> None:
        if self.fail:
            raise OSError("dependency unavailable")


class _RedisClient:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    def ping(self):
        if self.fail:
            raise OSError("redis unavailable")
        return True

    def close(self):
        return None


@pytest.mark.parametrize("failed", ["postgresql", "redis", "s3", "bigbluebutton"])
def test_readiness_fails_for_each_mandatory_dependency(monkeypatch, failed):
    settings = Settings(task_eager=False, artifact_storage_provider="s3")
    database = _HealthDependency(failed == "postgresql")
    storage = _HealthDependency(failed == "s3")
    conference = _HealthDependency(failed == "bigbluebutton")
    monkeypatch.setattr(
        "tutor_assistant_web.modules.dashboard.application.redis.Redis.from_url",
        lambda *_args, **_kwargs: _RedisClient(failed == "redis"),
    )
    ready, checks = ReadinessService(
        database,
        settings,
        conference,
        storage,
        "local-template",  # type: ignore[arg-type]
    ).check()
    assert ready is False
    assert checks[failed] == "error"


def test_metrics_require_configured_bearer_token(tmp_path):
    token = "metrics-test-token-which-is-private"
    settings = _settings(tmp_path, metrics_bearer_token=token)
    database = Database(settings.database_url)
    with TestClient(create_app(settings, database)) as client:
        assert client.get("/metrics").status_code == 401
        response = client.get("/metrics", headers={"authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "tutor_http_requests_total" in response.text


def test_production_rejects_demo_passwords_and_wildcard_proxy():
    common = {
        "app_env": "production",
        "app_secret_key": "a-unique-application-secret-over-32-characters",
        "database_url": "postgresql+psycopg://tutor:secret@db:5432/tutor",
        "auto_migrate": False,
        "enabled_modules": "students",
        "public_base_url": "https://tutor.example.test",
        "session_cookie_secure": True,
        "seed_demo_data": False,
        "bootstrap_admin_email": "admin@example.test",
        "metrics_bearer_token": "metrics-token-with-24-characters",
    }
    with pytest.raises(ValueError, match="demo or placeholder"):
        Settings(**common, bootstrap_admin_password="change-this-password")
    with pytest.raises(ValueError, match="TRUSTED_PROXY_IPS"):
        Settings(
            **common,
            bootstrap_admin_password="truly-unique-admin-passphrase",
            trusted_proxy_ips="*",
        )


def test_mounted_secret_file_takes_precedence(tmp_path):
    secret = tmp_path / "app-secret"
    secret.write_text("file-backed-secret-value", encoding="utf-8")
    settings = Settings(app_secret_key="environment-value", app_secret_key_file=str(secret))
    assert settings.app_secret_key == "file-backed-secret-value"


def test_outbox_dispatch_restores_callers_correlation_context(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings.database_url)
    database.migrate()

    class _Dispatcher:
        name = "celery"

        def enqueue_lesson_processing(self, *_args, **_kwargs):
            return None

        def enqueue_outbox_delivery(self, *_args, **_kwargs):
            return None

    with database.sessions() as session:
        session.add(
            OutboxEvent(
                organization_id=DEFAULT_ORGANIZATION_ID,
                topic="materials.requested",
                dedup_key="correlation-reset",
                correlation_id="event-correlation",
                payload={"job_id": "missing-but-valid-id"},
            )
        )
        session.commit()
    caller_correlation = "abcdef0123456789abcdef0123456789"
    token = bind_correlation(caller_correlation)
    try:
        OutboxService(
            database,
            _Dispatcher(),  # type: ignore[arg-type]
            max_attempts=2,
            retry_base_seconds=1,
        ).dispatch_pending()
        from tutor_assistant_web.observability import correlation_id_var

        assert correlation_id_var.get() == caller_correlation
    finally:
        reset_correlation(token)
