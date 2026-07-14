from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.classroom.application import ClassroomService
from tutor_assistant_web.modules.materials.models import (
    JobStatus,
    MaterialArtifact,
    ProcessingJob,
)
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.shared.contracts import JobDispatcher, MaterialGenerator
from tutor_assistant_web.shared.errors import NotFoundError


def evidence_payload(lesson: Lesson) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "organization_id": lesson.organization_id,
        "lesson": {
            "id": lesson.id,
            "title": lesson.title,
            "topic": lesson.topic,
            "started_at": lesson.starts_at.isoformat(),
            "ended_at": lesson.ends_at.isoformat(),
            "tutor_notes": lesson.tutor_notes,
        },
        "student": {
            "id": lesson.student.id,
            "full_name": lesson.student.full_name,
            "grade": lesson.student.grade,
            "subject": lesson.student.subject,
            "goal": lesson.student.goal,
        },
        "recordings": [
            {
                "record_id": recording.record_id,
                "state": recording.state,
                "playback_url": recording.playback_url,
                "metadata": recording.raw_metadata,
            }
            for recording in lesson.recordings
        ],
        "transcript": (
            {
                "status": lesson.transcript.status,
                "language": lesson.transcript.language,
                "text": lesson.transcript.text,
                "segments": lesson.transcript.segments,
            }
            if lesson.transcript is not None
            else None
        ),
        "requested_artifacts": ["lesson_summary", "homework", "parent_report"],
    }


class MaterialsService:
    def __init__(
        self,
        database: Database,
        generator: MaterialGenerator,
        classroom: ClassroomService,
        dispatcher: JobDispatcher | None = None,
        organization_id: str | None = None,
    ) -> None:
        self.database = database
        self.generator = generator
        self.classroom = classroom
        self.dispatcher = dispatcher
        self.organization_id = organization_id or classroom.organization_id
        if self.organization_id is None:
            raise ValueError("organization_id is required")

    def enqueue(self, lesson_id: str) -> ProcessingJob | None:
        if self.dispatcher is None:
            raise RuntimeError("job dispatcher is not configured")
        with self.database.sessions() as session:
            lesson = session.scalar(
                select(Lesson).where(
                    Lesson.id == lesson_id,
                    Lesson.organization_id == self.organization_id,
                )
            )
            if lesson is None:
                raise NotFoundError("Занятие не найдено")
            running = session.scalar(
                select(ProcessingJob.id).where(
                    ProcessingJob.lesson_id == lesson_id,
                    ProcessingJob.organization_id == self.organization_id,
                    ProcessingJob.status.in_(
                        [
                            JobStatus.queued.value,
                            JobStatus.running.value,
                            JobStatus.retrying.value,
                        ]
                    ),
                )
            )
            if running:
                return None
            job = ProcessingJob(
                organization_id=self.organization_id,
                lesson_id=lesson_id,
                kind="materials",
                trigger="manual",
                stage="queued",
            )
            session.add(job)
            session.commit()
        self.dispatcher.enqueue_lesson_processing(job.id)
        return job

    def status(self, job_id: str) -> ProcessingJob:
        with self.database.sessions() as session:
            job = session.scalar(
                select(ProcessingJob).where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.organization_id == self.organization_id,
                )
            )
            if job is None:
                raise NotFoundError("Задание не найдено")
            return job

    def artifact(self, artifact_id: str) -> MaterialArtifact:
        with self.database.sessions() as session:
            artifact = session.scalar(
                select(MaterialArtifact).where(
                    MaterialArtifact.id == artifact_id,
                    MaterialArtifact.organization_id == self.organization_id,
                )
            )
            if artifact is None:
                raise NotFoundError("Материал не найден")
            return artifact

    def process(self, job_id: str, *, start: bool = True, sync_recordings: bool = True) -> None:
        if start:
            self.start(job_id)
        try:
            job = self.status(job_id)
            if sync_recordings and not self.classroom.conference.is_demo:
                self.classroom.sync_recordings(job.lesson_id)
            self.progress(job_id, 72 if not start else 45, "materials", "Формируем пакет данных")
            lesson = self._lesson_with_evidence(job.lesson_id)
            artifacts = self.generator.generate(evidence_payload(lesson))
            self.progress(job_id, 90, "saving", "Сохраняем материалы")
            with self.database.sessions() as session:
                session.execute(delete(MaterialArtifact).where(MaterialArtifact.job_id == job_id))
                for item in artifacts:
                    session.add(
                        MaterialArtifact(
                            organization_id=self.organization_id,
                            lesson_id=lesson.id,
                            job_id=job_id,
                            title=item.title[:200],
                            kind=item.kind[:32],
                            content=item.content,
                            source_url=item.source_url,
                        )
                    )
                job_model = session.scalar(
                    select(ProcessingJob).where(
                        ProcessingJob.id == job_id,
                        ProcessingJob.organization_id == self.organization_id,
                    )
                )
                if job_model is None:
                    raise NotFoundError("Задание не найдено")
                job_model.status = JobStatus.completed.value
                job_model.stage = "completed"
                job_model.progress = 100
                job_model.message = "Материалы готовы к проверке"
                job_model.completed_at = datetime.now(UTC)
                job_model.next_retry_at = None
                session.commit()
        except Exception as exc:
            self.fail(job_id, exc)
            raise

    def _lesson_with_evidence(self, lesson_id: str) -> Lesson:
        with self.database.sessions() as session:
            lesson = session.scalar(
                select(Lesson)
                .options(
                    selectinload(Lesson.student),
                    selectinload(Lesson.recordings),
                    selectinload(Lesson.transcript),
                )
                .where(
                    Lesson.id == lesson_id,
                    Lesson.organization_id == self.organization_id,
                )
            )
            if lesson is None:
                raise NotFoundError("Занятие не найдено")
            return lesson

    def start(
        self, job_id: str, *, stage: str = "collecting", message: str = "Собираем данные занятия"
    ) -> None:
        with self.database.sessions() as session:
            job = session.scalar(
                select(ProcessingJob).where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.organization_id == self.organization_id,
                )
            )
            if job is None:
                raise NotFoundError("Задание не найдено")
            job.status = JobStatus.running.value
            job.stage = stage
            job.started_at = datetime.now(UTC)
            job.completed_at = None
            job.next_retry_at = None
            job.attempt_count += 1
            job.progress = 10
            job.message = message
            job.error = ""
            session.commit()

    def progress(self, job_id: str, progress: int, stage: str, message: str) -> None:
        with self.database.sessions() as session:
            job = session.scalar(
                select(ProcessingJob).where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.organization_id == self.organization_id,
                )
            )
            if job is None:
                raise NotFoundError("Задание не найдено")
            job.progress = progress
            job.stage = stage
            job.message = message
            session.commit()

    def retry(self, job_id: str, error: Exception, next_retry_at: datetime) -> None:
        with self.database.sessions() as session:
            job = session.scalar(
                select(ProcessingJob).where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.organization_id == self.organization_id,
                )
            )
            if job is None:
                return
            job.status = JobStatus.retrying.value
            job.stage = "waiting_retry"
            job.error = str(error)[:4000]
            job.message = "Повторим обработку автоматически"
            job.next_retry_at = next_retry_at
            job.completed_at = None
            session.commit()

    def fail(self, job_id: str, error: Exception) -> None:
        with self.database.sessions() as session:
            job = session.scalar(
                select(ProcessingJob).where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.organization_id == self.organization_id,
                )
            )
            if job is None:
                return
            job.status = JobStatus.failed.value
            job.stage = "failed"
            job.error = str(error)[:4000]
            job.message = "Обработка завершилась ошибкой"
            job.completed_at = datetime.now(UTC)
            job.next_retry_at = None
            session.commit()
