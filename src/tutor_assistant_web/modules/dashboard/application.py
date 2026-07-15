from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import redis
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.automation.models import OutboxEvent, OutboxStatus
from tutor_assistant_web.modules.materials.models import JobStatus, MaterialArtifact, ProcessingJob
from tutor_assistant_web.modules.scheduling.models import Lesson, LessonStatus
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.observability import QUEUE_AGE, QUEUE_SIZE, READINESS
from tutor_assistant_web.shared.contracts import ArtifactStorage, ConferenceProvider


@dataclass(frozen=True)
class DashboardData:
    upcoming: list[Lesson]
    students_count: int
    pending_jobs: int
    artifacts_count: int
    now: datetime


class DashboardService:
    def __init__(self, database: Database, organization_id: str) -> None:
        self.database = database
        self.organization_id = organization_id

    def load(self) -> DashboardData:
        now = datetime.now(UTC)
        with self.database.sessions() as session:
            students_count = session.scalar(
                select(func.count())
                .select_from(Student)
                .where(
                    Student.organization_id == self.organization_id,
                    Student.active.is_(True),
                )
            )
            upcoming = list(
                session.scalars(
                    select(Lesson)
                    .options(selectinload(Lesson.student))
                    .where(
                        Lesson.ends_at >= now,
                        Lesson.organization_id == self.organization_id,
                        Lesson.status.in_(
                            [
                                LessonStatus.scheduled.value,
                                LessonStatus.live.value,
                            ]
                        ),
                    )
                    .order_by(Lesson.starts_at)
                    .limit(6)
                )
            )
            pending_jobs = session.scalar(
                select(func.count())
                .select_from(ProcessingJob)
                .where(
                    ProcessingJob.status.in_(
                        [
                            JobStatus.queued.value,
                            JobStatus.running.value,
                            JobStatus.retrying.value,
                        ]
                    )
                )
                .where(ProcessingJob.organization_id == self.organization_id)
            )
            artifacts_count = session.scalar(
                select(func.count())
                .select_from(MaterialArtifact)
                .where(MaterialArtifact.organization_id == self.organization_id)
            )
        return DashboardData(
            upcoming=upcoming,
            students_count=students_count or 0,
            pending_jobs=pending_jobs or 0,
            artifacts_count=artifacts_count or 0,
            now=now,
        )


class ReadinessService:
    """Checks mandatory runtime dependencies without leaking exception details."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        conference: ConferenceProvider,
        artifact_storage: ArtifactStorage,
        materials_name: str,
    ) -> None:
        self.database = database
        self.settings = settings
        self.conference = conference
        self.artifact_storage = artifact_storage
        self.materials_name = materials_name

    def check(self) -> tuple[bool, dict[str, str]]:
        checks: dict[str, str] = {}
        dependencies = {
            "postgresql": self.database.healthcheck,
            "redis": self._redis,
            "s3": self.artifact_storage.healthcheck,
            "bigbluebutton": self.conference.healthcheck,
        }
        labels = {
            "redis": "eager" if self.settings.task_eager else "ok",
            "s3": "local" if self.settings.artifact_storage_provider == "local" else "ok",
            "bigbluebutton": "demo" if self.conference.is_demo else "ok",
        }
        mandatory = {
            "postgresql",
            *(set() if self.settings.task_eager else {"redis"}),
            *(set() if self.settings.artifact_storage_provider == "local" else {"s3"}),
            *(set() if self.conference.is_demo else {"bigbluebutton"}),
        }
        for name, check in dependencies.items():
            if name not in mandatory:
                checks[name] = labels[name]
                READINESS.labels(dependency=name).set(1)
                continue
            try:
                check()
            except Exception:
                checks[name] = "error"
                READINESS.labels(dependency=name).set(0)
            else:
                checks[name] = labels.get(name, "ok")
                READINESS.labels(dependency=name).set(1)
        # Kept as a provider diagnostic for operators and compatibility with
        # existing health consumers; it is not a network dependency itself.
        checks["materials"] = self.materials_name
        return all(checks[name] != "error" for name in mandatory), checks

    def _redis(self) -> None:
        client = redis.Redis.from_url(
            self.settings.redis_url,
            socket_connect_timeout=self.settings.readiness_timeout_seconds,
            socket_timeout=self.settings.readiness_timeout_seconds,
        )
        try:
            client.ping()
        finally:
            client.close()


class QueueMetricsService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def refresh(self) -> None:
        now = datetime.now(UTC)
        with self.database.sessions() as session:
            for queue in ("transcription", "materials", "delivery", "maintenance"):
                for status in (
                    JobStatus.queued.value,
                    JobStatus.running.value,
                    JobStatus.retrying.value,
                ):
                    count = (
                        session.scalar(
                            select(func.count(ProcessingJob.id)).where(
                                ProcessingJob.queue_name == queue,
                                ProcessingJob.status == status,
                            )
                        )
                        or 0
                    )
                    QUEUE_SIZE.labels(queue=queue, status=status).set(count)
                oldest = session.scalar(
                    select(func.min(ProcessingJob.created_at)).where(
                        ProcessingJob.queue_name == queue,
                        ProcessingJob.status.in_(
                            [JobStatus.queued.value, JobStatus.retrying.value]
                        ),
                    )
                )
                age = (
                    (
                        now
                        - (
                            oldest.replace(tzinfo=UTC)
                            if oldest and oldest.tzinfo is None
                            else oldest
                        )
                    ).total_seconds()
                    if oldest
                    else 0
                )
                QUEUE_AGE.labels(queue=queue).set(max(age, 0))
            outbox_count = (
                session.scalar(
                    select(func.count(OutboxEvent.id)).where(
                        OutboxEvent.status.in_(
                            [OutboxStatus.pending.value, OutboxStatus.dispatching.value]
                        )
                    )
                )
                or 0
            )
            QUEUE_SIZE.labels(queue="outbox", status="pending").set(outbox_count)
        self._refresh_broker_depth()

    def _refresh_broker_depth(self) -> None:
        client = redis.Redis.from_url(
            self.settings.redis_url,
            socket_connect_timeout=self.settings.readiness_timeout_seconds,
            socket_timeout=self.settings.readiness_timeout_seconds,
        )
        try:
            for queue in ("transcription", "materials", "delivery", "maintenance"):
                QUEUE_SIZE.labels(queue=queue, status="broker").set(client.llen(queue))
        except redis.RedisError:
            return
        finally:
            client.close()
