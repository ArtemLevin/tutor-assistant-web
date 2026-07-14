from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from tutor_assistant_web.bootstrap.registry import ModuleDefinition, ModuleRegistry
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.classroom.application import ClassroomService
from tutor_assistant_web.modules.scheduling.application import CreateLesson, SchedulingService
from tutor_assistant_web.modules.students.application import StudentData, StudentService
from tutor_assistant_web.shared.contracts import ConferenceRecording

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "tutor_assistant_web"


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
        enabled_modules="students",
        bbb_demo_mode=True,
    )

    assert settings.enabled_modules == "students"


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
    database.create_schema()
    student = StudentService(database).create(StudentData(full_name="Иван Петров"))
    starts = datetime.now(UTC) + timedelta(hours=1)
    lesson = SchedulingService(database, ZoneInfo("UTC")).create(
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
    classroom = ClassroomService(database, provider, "https://app.test", "secret")

    url = classroom.join_tutor(lesson.id)
    classroom.end(lesson.id)

    assert url.endswith("/MODERATOR")
    assert provider.created[0].meeting_id == lesson.bbb_meeting_id
    assert provider.ended == [lesson.bbb_meeting_id]
