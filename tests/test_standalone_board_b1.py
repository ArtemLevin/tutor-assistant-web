from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

import tutor_assistant_web.db as db_module
from tutor_assistant_web.app import create_app
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.models import BoardDocument
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.modules.identity.models import (
    DEFAULT_ORGANIZATION_ID,
    Membership,
    MembershipRole,
    User,
)
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.modules.students.models import Student

PASSWORD = "test-password"


def _settings(tmp_path) -> Settings:
    return Settings(
        app_secret_key="test-secret-for-standalone-board-b1",
        database_url=f"sqlite:///{tmp_path / 'standalone-b1.db'}",
        artifact_storage_root=str(tmp_path / "artifacts"),
        seed_demo_data=False,
        bootstrap_admin_password=PASSWORD,
        otel_exporter_otlp_endpoint="",
        rate_limit_board_reads=1000,
        rate_limit_board_writes=1000,
    )


def _csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def _login(client: TestClient, email: str = "admin@localhost") -> None:
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": _csrf_from(page.text),
            "email": email,
            "password": PASSWORD,
            "next": "/",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def _context(client: TestClient) -> dict:
    response = client.get("/api/v1/boards/context")
    assert response.status_code == 200
    return response.json()


def _add_tutor(database: Database, email: str = "other-tutor@example.test") -> str:
    identity = IdentityService(database)
    with database.sessions() as session:
        user = User(
            email=email,
            full_name="Other Tutor",
            password_hash=identity.passwords.hash(PASSWORD),
        )
        session.add(user)
        session.flush()
        session.add(
            Membership(
                organization_id=DEFAULT_ORGANIZATION_ID,
                user_id=user.id,
                role=MembershipRole.tutor.value,
            )
        )
        session.commit()
        return user.id


def _seed_lesson(database: Database) -> tuple[str, str]:
    with database.sessions() as session:
        student = Student(
            organization_id=DEFAULT_ORGANIZATION_ID,
            full_name="Legacy Board Student",
        )
        session.add(student)
        session.flush()
        lesson = Lesson(
            organization_id=DEFAULT_ORGANIZATION_ID,
            student_id=student.id,
            title="Legacy board lesson",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(hours=1),
            bbb_meeting_id=f"standalone-b1-{student.id}",
            attendee_password="attendee",
            moderator_password="moderator",
        )
        session.add(lesson)
        session.commit()
        return student.id, lesson.id


@pytest.fixture()
def standalone_api(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings.database_url)
    with TestClient(create_app(settings, database)) as client:
        _login(client)
        yield client, database, _context(client)
    database.dispose()


def test_standalone_create_list_update_archive_and_delete(standalone_api):
    client, database, context = standalone_api
    csrf = context["csrfToken"]

    created = client.post(
        "/api/v1/boards",
        json={},
        headers={"x-csrf-token": csrf},
    )
    assert created.status_code == 201
    payload = created.json()
    assert set(payload) == {
        "schemaVersion",
        "boardId",
        "title",
        "currentRevision",
        "guestWritesEnabled",
        "archivedAt",
        "deletedAt",
        "createdAt",
        "updatedAt",
    }
    assert payload["schemaVersion"] == "1.0"
    assert payload["title"] == "Новая доска"
    assert payload["currentRevision"] == 0
    assert payload["guestWritesEnabled"] is True
    assert payload["archivedAt"] is None
    assert payload["deletedAt"] is None
    board_id = payload["boardId"]

    with database.sessions() as session:
        document = session.scalar(
            select(BoardDocument).where(
                BoardDocument.organization_id == DEFAULT_ORGANIZATION_ID,
                BoardDocument.id == board_id,
            )
        )
        assert document is not None
        assert document.owner_user_id == context["userId"]
        assert document.lesson_id is None
        assert document.student_id is None
        assert document.access_version == 1

    listed = client.get("/api/v1/boards")
    assert listed.status_code == 200
    assert [item["boardId"] for item in listed.json()["items"]] == [board_id]

    renamed = client.patch(
        f"/api/v1/boards/{board_id}",
        json={"title": "Алгебра — 16 августа"},
        headers={"x-csrf-token": csrf},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Алгебра — 16 августа"
    with database.sessions() as session:
        assert session.get(BoardDocument, (board_id, DEFAULT_ORGANIZATION_ID)).access_version == 1

    read_only = client.patch(
        f"/api/v1/boards/{board_id}",
        json={"guestWritesEnabled": False},
        headers={"x-csrf-token": csrf},
    )
    assert read_only.status_code == 200
    assert read_only.json()["guestWritesEnabled"] is False
    with database.sessions() as session:
        assert session.get(BoardDocument, (board_id, DEFAULT_ORGANIZATION_ID)).access_version == 2

    unchanged = client.patch(
        f"/api/v1/boards/{board_id}",
        json={"guestWritesEnabled": False},
        headers={"x-csrf-token": csrf},
    )
    assert unchanged.status_code == 200
    with database.sessions() as session:
        assert session.get(BoardDocument, (board_id, DEFAULT_ORGANIZATION_ID)).access_version == 2

    archived = client.post(
        f"/api/v1/boards/{board_id}/archive",
        headers={"x-csrf-token": csrf},
    )
    assert archived.status_code == 200
    assert archived.json()["boardId"] == board_id
    assert archived.json()["archivedAt"] is not None
    assert client.get("/api/v1/boards").json()["items"] == []
    assert client.get("/api/v1/boards?includeArchived=true").json()["items"][0]["boardId"] == board_id

    restored = client.post(
        f"/api/v1/boards/{board_id}/unarchive",
        headers={"x-csrf-token": csrf},
    )
    assert restored.status_code == 200
    assert restored.json()["archivedAt"] is None

    deleted = client.delete(
        f"/api/v1/boards/{board_id}",
        headers={"x-csrf-token": csrf},
    )
    assert deleted.status_code == 204
    assert client.get("/api/v1/boards").json()["items"] == []
    with database.sessions() as session:
        document = session.get(BoardDocument, (board_id, DEFAULT_ORGANIZATION_ID))
        assert document is not None
        assert document.deleted_at is not None
        assert document.access_version == 5


def test_tutor_cannot_enumerate_or_read_another_tutors_standalone_board(standalone_api):
    client, database, context = standalone_api
    created = client.post(
        "/api/v1/boards",
        json={"title": "Admin owned"},
        headers={"x-csrf-token": context["csrfToken"]},
    )
    assert created.status_code == 201
    admin_board_id = created.json()["boardId"]

    _add_tutor(database)
    client.cookies.clear()
    _login(client, "other-tutor@example.test")
    tutor_context = _context(client)

    assert client.get("/api/v1/boards").json()["items"] == []
    hidden = client.get(f"/api/v1/boards/{admin_board_id}")
    assert hidden.status_code == 404

    own = client.post(
        "/api/v1/boards",
        json={"title": "Tutor owned"},
        headers={"x-csrf-token": tutor_context["csrfToken"]},
    )
    assert own.status_code == 201
    own_id = own.json()["boardId"]
    listed = client.get("/api/v1/boards").json()["items"]
    assert [item["boardId"] for item in listed] == [own_id]


def test_legacy_lesson_bound_creation_keeps_legacy_descriptor(standalone_api):
    client, database, context = standalone_api
    student_id, lesson_id = _seed_lesson(database)
    response = client.post(
        f"/api/v1/lessons/{lesson_id}/board",
        json={"documentId": "document:legacy-b1"},
        headers={"x-csrf-token": context["csrfToken"]},
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["documentId"] == "document:legacy-b1"
    assert payload["lessonId"] == lesson_id
    assert payload["studentId"] == student_id
    assert "boardId" not in payload


def _alembic_config(database: Database) -> Config:
    config = Config()
    migrations = Path(db_module.__file__).with_name("migrations")
    config.set_main_option("script_location", str(migrations))
    url = database.engine.url.render_as_string(False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migration_0015_can_downgrade_before_standalone_rows_exist(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'migration-b1.db'}")
    config = _alembic_config(database)
    command.upgrade(config, "0014_board_command_origins")
    command.upgrade(config, "0015_standalone_board_persistence")
    columns = {column["name"]: column for column in inspect(database.engine).get_columns("board_documents")}
    assert columns["lesson_id"]["nullable"] is True
    assert columns["student_id"]["nullable"] is True
    assert {"owner_user_id", "title", "guest_writes_enabled", "access_version"} <= set(columns)

    command.downgrade(config, "0014_board_command_origins")
    downgraded = {column["name"]: column for column in inspect(database.engine).get_columns("board_documents")}
    assert downgraded["lesson_id"]["nullable"] is False
    assert downgraded["student_id"]["nullable"] is False
    assert "owner_user_id" not in downgraded
    database.dispose()


def test_migration_0015_refuses_schema_downgrade_with_standalone_data(standalone_api):
    client, database, context = standalone_api
    created = client.post(
        "/api/v1/boards",
        json={"title": "Must survive rollback"},
        headers={"x-csrf-token": context["csrfToken"]},
    )
    assert created.status_code == 201

    with pytest.raises(RuntimeError, match="Cannot downgrade standalone board persistence"):
        command.downgrade(_alembic_config(database), "0014_board_command_origins")
