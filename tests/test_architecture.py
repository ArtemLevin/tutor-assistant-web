from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import APIRouter

from tutor_assistant_web.bootstrap.registry import ModuleDefinition, ModuleRegistry
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.classroom.application import ClassroomService
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.modules.identity.models import DEFAULT_ORGANIZATION_ID
from tutor_assistant_web.modules.scheduling.application import CreateLesson, SchedulingService
from tutor_assistant_web.modules.students.application import StudentData, StudentService
from tutor_assistant_web.shared.contracts import ConferenceRecording

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "tutor_assistant_web"
PRODUCTION_DATABASE_URL = "postgresql+psycopg://tutor:secret@db:5432/tutor"


def _router(_):
    return APIRouter()


def test_module_registry_resolves_dependencies_in_order():
    registry = ModuleRegistry(
        [
            ModuleDefinition("materials", _router, ("classroom",)),
            ModuleDefinition("identity", _router),
            ModuleDefinition("classroom", _router, ("identity",)),
        ]
    )

    assert [module.name for module in registry.ordered({"materials"})] == [
        "identity",
        "classroom",
        "materials",
    ]


def test_http_routes_do_not_depend_on_sqlalchemy_or_bbb_adapter():
    for route_file in (SOURCE_ROOT / "modules").glob("*/routes.py"):
        tree = ast.parse(route_file.read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not any(name.startswith("sqlalchemy") for name in imports), route_file
        assert "tutor_assistant_web.bbb" not in imports, route_file


def test_composition_entrypoint_stays_small():
    app_file = SOURCE_ROOT / "app.py"
    assert len(app_file.read_text(encoding="utf-8").splitlines()) <= 15


def test_crm_only_production_configuration_does_not_require_bbb():
    settings = Settings(
        app_env="production",
        app_secret_key="production-secret",
        app_access_token="access-token",
        bootstrap_admin_password="a-secure-production-password",
        database_url=PRODUCTION_DATABASE_URL,
        auto_migrate=False,
        enabled_modules="students",
        bbb_demo_mode=True,
    )

    assert settings.enabled_modules == "students"


def test_automation_production_configuration_requires_real_bbb():
    try:
        Settings(
            app_env="production",
            app_secret_key="production-secret",
            bootstrap_admin_password="a-secure-production-password",
            database_url=PRODUCTION_DATABASE_URL,
            auto_migrate=False,
            enabled_modules="automation",
            bbb_demo_mode=True,
        )
    except ValueError as exc:
        assert "BBB_DEMO_MODE" in str(exc)
    else:
        raise AssertionError("automation must require real BBB in production")


def test_materials_production_configuration_requires_real_document_engine():
    try:
        Settings(
            app_env="production",
            app_secret_key="production-secret",
            bootstrap_admin_password="a-secure-production-password",
            database_url=PRODUCTION_DATABASE_URL,
            auto_migrate=False,
            enabled_modules="materials",
            bbb_demo_mode=False,
            bbb_base_url="https://bbb.example.test",
            bbb_secret="bbb-secret",
            public_base_url="https://tutor.example.test",
        )
    except ValueError as exc:
        assert "DOCUMENT_ENGINE_PROVIDER" in str(exc)
    else:
        raise AssertionError("materials must require a real document engine in production")


def test_production_configuration_requires_postgresql_and_migration_job():
    common = {
        "app_env": "production",
        "app_secret_key": "production-secret",
        "bootstrap_admin_password": "a-secure-production-password",
        "enabled_modules": "students",
    }
    with pytest.raises(ValueError, match="PostgreSQL"):
        Settings(**common, database_url="sqlite:///production.db", auto_migrate=False)
    with pytest.raises(ValueError, match="AUTO_MIGRATE"):
        Settings(**common, database_url=PRODUCTION_DATABASE_URL, auto_migrate=True)


def test_production_automation_requires_celery_dispatcher():
    with pytest.raises(ValueError, match="TASK_EAGER"):
        Settings(
            app_env="production",
            app_secret_key="production-secret",
            bootstrap_admin_password="a-secure-production-password",
            database_url=PRODUCTION_DATABASE_URL,
            auto_migrate=False,
            task_eager=True,
            enabled_modules="automation",
            bbb_demo_mode=False,
            bbb_base_url="https://bbb.example.test",
            bbb_secret="bbb-secret",
            public_base_url="https://tutor.example.test",
            transcription_provider="webhook",
            transcription_webhook_url="https://speech.example.test",
            document_engine_provider="latex-for-everyone",
            document_engine_url="https://latex.example.test",
            document_engine_token="service-token",
        )


class FakeConference:
    name = "fake"
    is_demo = False

    def __init__(self) -> None:
        self.created = []
        self.ended = []

    def create_room(self, command) -> None:
        self.created.append(command)

    def join_url(self, command) -> str:
        return f"https://conference.test/{command.meeting_id}/{command.role}"

    def end_room(self, meeting_id: str) -> None:
        self.ended.append(meeting_id)

    def recordings(self, meeting_id: str) -> list[ConferenceRecording]:
        return []


def test_classroom_uses_replaceable_conference_provider(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'provider.db'}")
    database.migrate()
    IdentityService(database).bootstrap(Settings(seed_demo_data=False))
    student = StudentService(database, DEFAULT_ORGANIZATION_ID).create(
        StudentData(full_name="Иван Петров")
    )
    starts = datetime.now(UTC) + timedelta(hours=1)
    lesson = SchedulingService(database, ZoneInfo("UTC"), DEFAULT_ORGANIZATION_ID).create(
        CreateLesson(
            student_id=student.id,
            title="Алгебра",
            topic="Функции",
            starts_at=starts,
            ends_at=starts + timedelta(hours=1),
            record_enabled=True,
        )
    )
    provider = FakeConference()
    classroom = ClassroomService(
        database, provider, "https://app.test", "secret", DEFAULT_ORGANIZATION_ID
    )

    url = classroom.join_tutor(lesson.id)
    classroom.end(lesson.id)

    assert url.endswith("/MODERATOR")
    assert provider.created[0].meeting_id == lesson.bbb_meeting_id
    assert provider.created[0].recording_ready_url == (
        "https://app.test/webhooks/bigbluebutton/recording-ready"
    )
    assert provider.ended == [lesson.bbb_meeting_id]
