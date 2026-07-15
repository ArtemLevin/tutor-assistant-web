from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from tutor_assistant_web.config import Settings
from tutor_assistant_web.database_copy import copy_sqlite_to_postgres
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.audit.models import AuditEvent
from tutor_assistant_web.modules.automation.application import OutboxService
from tutor_assistant_web.modules.automation.durability import DurableJobService
from tutor_assistant_web.modules.automation.models import OutboxEvent, OutboxStatus
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.modules.identity.models import (
    DEFAULT_ORGANIZATION_ID,
    Membership,
    MembershipRole,
    Organization,
    StudentAccess,
    User,
)
from tutor_assistant_web.modules.materials.models import (
    BuildLog,
    EvidenceBundle,
    GenerationRun,
    GenerationStatus,
    JobStatus,
    ProcessingJob,
)
from tutor_assistant_web.modules.portal.application import PublicationService
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.shared.errors import ValidationError

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture()
def database():
    schema = f"test_{uuid4().hex}"
    base_url = make_url(TEST_DATABASE_URL)
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    schema_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    database = Database(
        schema_url.render_as_string(hide_password=False),
        statement_timeout_ms=30_000,
        lock_timeout_ms=5000,
    )
    database.migrate()
    try:
        yield database
    finally:
        database.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def bootstrap(database: Database):
    identity = IdentityService(database)
    identity.bootstrap(
        Settings(
            database_url=TEST_DATABASE_URL,
            seed_demo_data=False,
            bootstrap_admin_password="admin-password",
        )
    )
    principal = identity.authenticate("admin@localhost", "admin-password")
    assert principal is not None
    return identity, principal


def test_postgres_migration_and_runtime_timeouts(database):
    inspector = inspect(database.engine)
    assert database.dialect_name == "postgresql"
    assert "uq_students_org_id" in {
        item["name"] for item in inspector.get_unique_constraints("students")
    }
    assert "ix_outbox_claim" in {item["name"] for item in inspector.get_indexes("outbox_events")}
    with database.engine.connect() as connection:
        assert connection.scalar(text("SHOW statement_timeout")) == "30s"
        assert connection.scalar(text("SHOW lock_timeout")) == "5s"


def test_sqlite_database_can_be_copied_to_empty_postgres(database, tmp_path):
    source_url = f"sqlite:///{tmp_path / 'source.db'}"
    source = Database(source_url)
    source.migrate()
    identity = IdentityService(source)
    identity.bootstrap(Settings(seed_demo_data=False, bootstrap_admin_password="admin-password"))
    with source.sessions() as session:
        session.add(
            Student(
                organization_id=DEFAULT_ORGANIZATION_ID,
                full_name="Migrated Student",
            )
        )
        session.commit()
    source.dispose()

    counts = copy_sqlite_to_postgres(
        source_url,
        database.engine.url.render_as_string(hide_password=False),
    )

    assert counts["students"] == 1
    assert counts["users"] == 1
    with database.sessions() as session:
        assert session.scalar(select(Student.full_name)) == "Migrated Student"


def test_postgres_rejects_cross_tenant_student_access(database):
    with database.sessions() as session:
        other = Organization(name="Other", slug=f"other-{uuid4().hex}")
        user = User(
            email=f"parent-{uuid4().hex}@example.test",
            full_name="Parent",
            password_hash="test-only-hash",
        )
        student = Student(
            organization_id=DEFAULT_ORGANIZATION_ID,
            full_name="Student",
        )
        session.add_all([other, user, student])
        session.commit()
        session.add(
            StudentAccess(
                organization_id=other.id,
                student_id=student.id,
                user_id=user.id,
                role=MembershipRole.parent.value,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_invitation_can_only_be_accepted_once_concurrently(database):
    identity, admin = bootstrap(database)
    with database.sessions() as session:
        student = Student(
            organization_id=DEFAULT_ORGANIZATION_ID,
            full_name="Concurrent Student",
        )
        session.add(student)
        session.commit()
    created = identity.create_invitation(
        DEFAULT_ORGANIZATION_ID,
        admin.user_id,
        "concurrent-parent@example.test",
        MembershipRole.parent.value,
        24,
        student_id=student.id,
    )
    barrier = threading.Barrier(2)

    def accept() -> str:
        barrier.wait()
        try:
            IdentityService(database).accept_invitation(
                created.token,
                "Concurrent Parent",
                "strong-password",
            )
        except ValidationError:
            return "already-used"
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: accept(), range(2)))

    assert sorted(results) == ["accepted", "already-used"]
    with database.sessions() as session:
        user_id = session.scalar(
            select(User.id).where(User.email == "concurrent-parent@example.test")
        )
        assert user_id is not None
        assert (
            session.scalar(select(func.count(Membership.id)).where(Membership.user_id == user_id))
            == 1
        )
        assert (
            session.scalar(
                select(func.count(StudentAccess.id)).where(StudentAccess.user_id == user_id)
            )
            == 1
        )


def test_publication_is_single_under_concurrency(database):
    _, admin = bootstrap(database)
    with database.sessions() as session:
        student = Student(
            organization_id=DEFAULT_ORGANIZATION_ID,
            full_name="Publication Student",
        )
        session.add(student)
        session.flush()
        lesson = Lesson(
            organization_id=DEFAULT_ORGANIZATION_ID,
            student_id=student.id,
            title="Concurrent lesson",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(hours=1),
            bbb_meeting_id=f"meeting-{uuid4().hex}",
            attendee_password="attendee",
            moderator_password="moderator",
        )
        session.add(lesson)
        session.flush()
        job = ProcessingJob(organization_id=DEFAULT_ORGANIZATION_ID, lesson_id=lesson.id)
        session.add(job)
        bundle = EvidenceBundle(
            organization_id=DEFAULT_ORGANIZATION_ID,
            lesson_id=lesson.id,
            content_hash=uuid4().hex * 2,
            payload={"schema_version": "1.0"},
        )
        session.add(bundle)
        session.flush()
        run = GenerationRun(
            organization_id=DEFAULT_ORGANIZATION_ID,
            lesson_id=lesson.id,
            job_id=job.id,
            evidence_bundle_id=bundle.id,
            idempotency_key=uuid4().hex * 2,
            status=GenerationStatus.approved.value,
        )
        session.add(run)
        session.commit()
    barrier = threading.Barrier(2)

    def publish() -> str:
        barrier.wait()
        return (
            PublicationService(database, DEFAULT_ORGANIZATION_ID)
            .publish(run.id, admin.user_id)
            .status
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: publish(), range(2)))

    assert statuses == [GenerationStatus.published.value] * 2
    with database.sessions() as session:
        assert session.scalar(select(func.count(OutboxEvent.id))) == 1
        assert session.scalar(select(func.count(AuditEvent.id))) == 1
        assert session.scalar(select(func.count(BuildLog.id))) == 1


class RecordingDispatcher:
    name = "recording"

    def __init__(self) -> None:
        self.job_ids: list[str] = []
        self.lock = threading.Lock()

    def enqueue_lesson_processing(self, job_id: str, queue: str = "materials") -> None:
        time.sleep(0.01)
        with self.lock:
            self.job_ids.append(job_id)

    def enqueue_outbox_delivery(self, event_id: str, lease_token: str) -> None:
        raise AssertionError(f"unexpected delivery: {event_id}")


def test_outbox_skip_locked_dispatches_each_event_once(database):
    dispatcher = RecordingDispatcher()
    with database.sessions() as session:
        session.add_all(
            [
                OutboxEvent(
                    organization_id=DEFAULT_ORGANIZATION_ID,
                    topic="post_lesson.requested",
                    dedup_key=f"parallel-{index}-{uuid4().hex}",
                    payload={"job_id": f"job-{index}"},
                )
                for index in range(12)
            ]
        )
        session.commit()

    def dispatch() -> dict[str, int]:
        return OutboxService(
            database,
            dispatcher,
            max_attempts=3,
            retry_base_seconds=1,
        ).dispatch_pending(limit=6)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: dispatch(), range(2)))

    assert sum(item["dispatched"] for item in results) == 12
    assert len(dispatcher.job_ids) == len(set(dispatcher.job_ids)) == 12
    with database.sessions() as session:
        assert (
            session.scalar(
                select(func.count(OutboxEvent.id)).where(
                    OutboxEvent.status == OutboxStatus.completed.value
                )
            )
            == 12
        )


def test_two_workers_cannot_claim_the_same_durable_job(database):
    with database.sessions() as session:
        student = Student(
            organization_id=DEFAULT_ORGANIZATION_ID,
            full_name="Lease Student",
        )
        session.add(student)
        session.flush()
        lesson = Lesson(
            organization_id=DEFAULT_ORGANIZATION_ID,
            student_id=student.id,
            title="Lease lesson",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(hours=1),
            bbb_meeting_id=f"lease-{uuid4().hex}",
            attendee_password="attendee",
            moderator_password="moderator",
        )
        session.add(lesson)
        session.flush()
        job = ProcessingJob(
            organization_id=DEFAULT_ORGANIZATION_ID,
            lesson_id=lesson.id,
        )
        session.add(job)
        session.commit()
    barrier = threading.Barrier(2)

    def claim(index: int) -> bool:
        barrier.wait()
        return (
            DurableJobService(database, lease_seconds=60).claim(job.id, f"worker-{index}").acquired
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, range(2)))

    assert sorted(claims) == [False, True]
    with database.sessions() as session:
        stored = session.get(ProcessingJob, job.id)
        assert stored.status == JobStatus.running.value
        assert stored.attempt_count == 1
