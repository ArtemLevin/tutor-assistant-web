from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.materials.models import JobStatus, MaterialArtifact, ProcessingJob
from tutor_assistant_web.modules.scheduling.models import Lesson, LessonStatus
from tutor_assistant_web.modules.students.models import Student


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
