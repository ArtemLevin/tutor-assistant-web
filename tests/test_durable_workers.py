from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.automation.application import OutboxService
from tutor_assistant_web.modules.automation.durability import DurableJobService
from tutor_assistant_web.modules.automation.models import OutboxEvent, OutboxStatus
from tutor_assistant_web.modules.identity.models import DEFAULT_ORGANIZATION_ID
from tutor_assistant_web.modules.materials.models import JobStatus, ProcessingJob
from tutor_assistant_web.modules.portal.application import PortalEventHandler
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.providers.resilience import CircuitBreaker, CircuitOpenError


def add_job(database: Database, *, kind: str = "post_lesson") -> ProcessingJob:
    with database.sessions() as session:
        student = Student(
            organization_id=DEFAULT_ORGANIZATION_ID,
            full_name="Durable Student",
        )
        session.add(student)
        session.flush()
        lesson = Lesson(
            organization_id=DEFAULT_ORGANIZATION_ID,
            student_id=student.id,
            title="Durable lesson",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(hours=1),
            bbb_meeting_id="durable-meeting",
            attendee_password="attendee",
            moderator_password="moderator",
        )
        session.add(lesson)
        session.flush()
        job = ProcessingJob(
            organization_id=DEFAULT_ORGANIZATION_ID,
            lesson_id=lesson.id,
            kind=kind,
            queue_name="transcription" if kind == "post_lesson" else "materials",
        )
        session.add(job)
        session.commit()
        return job


def test_only_one_owner_holds_a_live_job_lease(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'lease.db'}")
    database.migrate()
    job = add_job(database)
    service = DurableJobService(database, lease_seconds=60)

    first = service.claim(job.id, "worker-1")
    second = service.claim(job.id, "worker-2")

    assert first.acquired is True
    assert second.acquired is False
    assert second.terminal is False
    assert second.retry_after > 0


def test_expired_lease_is_recovered_through_transactional_outbox(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'recover.db'}")
    database.migrate()
    job = add_job(database)
    service = DurableJobService(database, lease_seconds=60, max_attempts=3)
    assert service.claim(job.id, "lost-worker").acquired
    with database.sessions() as session:
        stored = session.get(ProcessingJob, job.id)
        stored.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    assert service.recover_expired() == 1

    with database.sessions() as session:
        stored = session.get(ProcessingJob, job.id)
        event = session.scalar(select(OutboxEvent))
        assert stored.status == JobStatus.queued.value
        assert stored.lease_owner is None
        assert event is not None and event.payload["job_id"] == job.id
        assert event.topic == "post_lesson.requested"


def test_due_retry_is_recovered_if_redis_was_down_during_task_retry(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'retry-recovery.db'}")
    database.migrate()
    job = add_job(database, kind="materials")
    service = DurableJobService(database, retry_base_seconds=1, jitter=lambda low, high: high)
    assert service.claim(job.id, "worker-with-lost-broker").acquired
    assert service.retry(job.id, "worker-with-lost-broker", ConnectionError("Redis down")) == 1
    with database.sessions() as session:
        stored = session.get(ProcessingJob, job.id)
        stored.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    assert service.recover_expired() == 1

    with database.sessions() as session:
        event = session.scalar(select(OutboxEvent))
        assert event is not None and event.topic == "materials.requested"
        assert event.payload["job_id"] == job.id


def test_dead_letter_can_be_retried_manually(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'dead-letter.db'}")
    database.migrate()
    job = add_job(database)
    service = DurableJobService(
        database,
        max_attempts=2,
        retry_base_seconds=1,
        jitter=lambda low, high: high,
    )

    assert service.claim(job.id, "worker-1").acquired
    assert service.retry(job.id, "worker-1", RuntimeError("HTTP 500")) == 1
    with database.sessions() as session:
        stored = session.get(ProcessingJob, job.id)
        stored.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert service.claim(job.id, "worker-2").acquired
    assert service.retry(job.id, "worker-2", RuntimeError("timeout")) is None
    with database.sessions() as session:
        assert session.get(ProcessingJob, job.id).status == JobStatus.dead_letter.value

    retried = service.retry_manually(DEFAULT_ORGANIZATION_ID, job.id)

    assert retried.status == JobStatus.queued.value
    with database.sessions() as session:
        event = session.scalar(select(OutboxEvent))
        assert event is not None and event.payload["job_id"] == job.id


class UnavailableCeleryDispatcher:
    name = "celery"

    def enqueue_lesson_processing(self, job_id: str, queue: str = "materials") -> None:
        raise ConnectionError("Redis unavailable")

    def enqueue_outbox_delivery(self, event_id: str, lease_token: str) -> None:
        raise ConnectionError("delivery broker unavailable")


class CapturingDeliveryDispatcher:
    name = "celery"

    def __init__(self) -> None:
        self.delivery: tuple[str, str] | None = None

    def enqueue_lesson_processing(self, job_id: str, queue: str = "materials") -> None:
        raise AssertionError(f"unexpected lesson job: {job_id}")

    def enqueue_outbox_delivery(self, event_id: str, lease_token: str) -> None:
        self.delivery = (event_id, lease_token)


class AcceptingHandler:
    def handles(self, topic: str) -> bool:
        return topic == "delivery.test"

    def handle(self, topic: str, organization_id: str, payload: dict) -> None:
        return None


def test_outbox_survives_temporary_redis_failure(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'redis.db'}")
    database.migrate()
    with database.sessions() as session:
        session.add(
            OutboxEvent(
                organization_id=DEFAULT_ORGANIZATION_ID,
                topic="material.published",
                dedup_key="publication:redis-down",
                payload={"generation_run_id": "run-id", "publication_version": 1},
            )
        )
        session.commit()

    result = OutboxService(
        database,
        UnavailableCeleryDispatcher(),
        max_attempts=3,
        retry_base_seconds=1,
        event_handlers=(PortalEventHandler(database),),
        jitter=lambda low, high: high,
    ).dispatch_pending()

    assert result == {"dispatched": 0, "retried": 1, "dead": 0}
    with database.sessions() as session:
        event = session.scalar(select(OutboxEvent))
        assert event.status == OutboxStatus.pending.value
        assert "delivery broker unavailable" in event.last_error


def test_dead_outbox_event_can_be_resent_manually(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'outbox-dead.db'}")
    database.migrate()
    with database.sessions() as session:
        event = OutboxEvent(
            organization_id=DEFAULT_ORGANIZATION_ID,
            topic="unsupported.topic",
            dedup_key="unsupported:dead-letter",
            payload={},
        )
        session.add(event)
        session.commit()

    result = OutboxService(
        database,
        UnavailableCeleryDispatcher(),
        max_attempts=1,
        retry_base_seconds=1,
    ).dispatch_pending()
    assert result == {"dispatched": 0, "retried": 0, "dead": 1}

    resent = DurableJobService(database).resend_outbox(DEFAULT_ORGANIZATION_ID, event.id)

    assert resent.status == OutboxStatus.pending.value
    assert resent.attempts == 0
    assert resent.last_error == ""


def test_stale_delivery_task_cannot_complete_a_newer_outbox_lease(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'outbox-lease.db'}")
    database.migrate()
    with database.sessions() as session:
        event = OutboxEvent(
            organization_id=DEFAULT_ORGANIZATION_ID,
            topic="delivery.test",
            dedup_key="delivery:test",
            payload={},
        )
        session.add(event)
        session.commit()
    dispatcher = CapturingDeliveryDispatcher()
    outbox = OutboxService(
        database,
        dispatcher,
        max_attempts=3,
        retry_base_seconds=1,
        event_handlers=(AcceptingHandler(),),
    )

    assert outbox.dispatch_pending() == {"dispatched": 1, "retried": 0, "dead": 0}
    event_id, lease_token = dispatcher.delivery
    assert outbox._complete(event_id, "stale-token") is False
    assert outbox._complete(event_id, lease_token) is True


def transient_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.example.test")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("provider failed", request=request, response=response)


def test_circuit_breaker_opens_for_429_500_and_timeout():
    for error in (
        transient_status_error(429),
        transient_status_error(500),
        httpx.ReadTimeout("provider timed out"),
    ):
        breaker = CircuitBreaker("provider", failure_threshold=1, recovery_seconds=60)
        try:
            with breaker.guard():
                raise error
        except Exception as caught:
            assert caught is error
        try:
            breaker.before_call()
        except CircuitOpenError:
            pass
        else:
            raise AssertionError("circuit must be open after a transient failure")
