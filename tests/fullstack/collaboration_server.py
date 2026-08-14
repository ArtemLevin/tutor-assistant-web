from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn

from tutor_assistant_web.bootstrap.app_factory import create_app
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.modules.identity.models import (
    DEFAULT_ORGANIZATION_ID,
    Membership,
    MembershipRole,
    StudentAccess,
    User,
)
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.modules.students.models import Student

LESSON_ID = "20000000-0000-4000-8000-000000000001"
STUDENT_ID = "20000000-0000-4000-8000-000000000002"
STUDENT_USER_ID = "20000000-0000-4000-8000-000000000003"
PASSWORD = "collaboration-e2e-password"


def settings(database_path: Path, artifact_path: Path) -> Settings:
    return Settings(
        app_env="development",
        app_secret_key="collaboration-e2e-secret-key-32-characters",
        artifact_storage_provider="local",
        artifact_storage_root=str(artifact_path),
        auto_migrate=False,
        bootstrap_admin_email="collaboration-tutor@example.test",
        bootstrap_admin_name="E2E Преподаватель",
        bootstrap_admin_password=PASSWORD,
        database_url=f"sqlite:///{database_path}",
        metrics_enabled=False,
        otel_exporter_otlp_endpoint="",
        public_base_url="http://127.0.0.1:4173",
        rate_limit_board_reads=10_000,
        rate_limit_board_writes=10_000,
        rate_limit_login=1_000,
        seed_demo_data=False,
        session_cookie_secure=False,
        task_eager=True,
        transcription_provider="demo",
        trusted_hosts="127.0.0.1,localhost",
    )


def seed(database: Database, configured: Settings) -> None:
    identity = IdentityService(database)
    identity.bootstrap(configured)
    with database.sessions() as session:
        student = Student(
            id=STUDENT_ID,
            organization_id=DEFAULT_ORGANIZATION_ID,
            full_name="E2E Ученик",
            grade="8 класс",
            subject="Математика",
        )
        session.add(student)
        session.add(
            Lesson(
                id=LESSON_ID,
                organization_id=DEFAULT_ORGANIZATION_ID,
                student_id=STUDENT_ID,
                title="E2E совместная доска",
                starts_at=datetime.now(UTC),
                ends_at=datetime.now(UTC) + timedelta(hours=1),
                bbb_meeting_id="collaboration-e2e",
                attendee_password="attendee",
                moderator_password="moderator",
            )
        )
        student_user = User(
            id=STUDENT_USER_ID,
            email="collaboration-student@example.test",
            full_name="E2E Ученик",
            password_hash=identity.passwords.hash(PASSWORD),
        )
        session.add(student_user)
        session.add(
            Membership(
                organization_id=DEFAULT_ORGANIZATION_ID,
                user_id=STUDENT_USER_ID,
                role=MembershipRole.student.value,
            )
        )
        session.add(
            StudentAccess(
                organization_id=DEFAULT_ORGANIZATION_ID,
                student_id=STUDENT_ID,
                user_id=STUDENT_USER_ID,
                role=MembershipRole.student.value,
            )
        )
        session.commit()


def main() -> None:
    database_path = Path(
        os.getenv("COLLABORATION_E2E_DATABASE", "/tmp/tutorboard-collaboration-e2e.db")
    ).resolve()
    artifact_path = Path(
        os.getenv("COLLABORATION_E2E_ARTIFACTS", "/tmp/tutorboard-collaboration-e2e-artifacts")
    ).resolve()
    database_path.unlink(missing_ok=True)
    artifact_path.mkdir(parents=True, exist_ok=True)
    configured = settings(database_path, artifact_path)
    database = Database.from_settings(configured)
    database.migrate()
    seed(database, configured)
    uvicorn.run(
        create_app(configured, database),
        host="127.0.0.1",
        port=4181,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
