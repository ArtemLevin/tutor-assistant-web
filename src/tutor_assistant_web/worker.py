from __future__ import annotations

from datetime import timedelta

from celery import Celery
from sqlalchemy import select

from tutor_assistant_web.bootstrap.container import (
    build_conference_provider,
    build_material_generator,
    build_transcription_provider,
)
from tutor_assistant_web.config import get_settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.automation.application import (
    OutboxService,
    PostLessonWorkflowService,
)
from tutor_assistant_web.modules.classroom.application import ClassroomService
from tutor_assistant_web.modules.materials.application import MaterialsService
from tutor_assistant_web.modules.materials.models import ProcessingJob
from tutor_assistant_web.providers.tasks import CeleryJobDispatcher
from tutor_assistant_web.providers.transcription import TranscriptionProviderError
from tutor_assistant_web.shared.models import utcnow

settings = get_settings()
celery_app = Celery("tutor_assistant_web", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_always_eager=settings.task_eager,
    task_eager_propagates=False,
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    beat_schedule={
        "dispatch-transactional-outbox": {
            "task": "tutor.dispatch_outbox",
            "schedule": float(settings.outbox_poll_seconds),
        }
    },
)


@celery_app.task(
    name="tutor.process_lesson",
    bind=True,
    max_retries=settings.workflow_max_retries,
)
def process_lesson_task(self, job_id: str) -> None:
    database = Database(settings.database_url)
    if settings.auto_migrate:
        database.migrate()
    with database.sessions() as session:
        row = session.execute(
            select(ProcessingJob.organization_id, ProcessingJob.kind).where(
                ProcessingJob.id == job_id
            )
        ).one_or_none()
    if row is None:
        return
    organization_id, kind = row
    conference = build_conference_provider(settings)
    classroom = ClassroomService(
        database,
        conference,
        settings.public_base_url,
        settings.app_secret_key,
        organization_id,
    )
    materials = MaterialsService(
        database,
        build_material_generator(settings),
        classroom,
        organization_id=organization_id,
    )
    try:
        if kind == "post_lesson":
            PostLessonWorkflowService(
                database,
                classroom,
                materials,
                build_transcription_provider(settings),
                organization_id,
            ).process(job_id)
        else:
            materials.process(job_id)
    except TranscriptionProviderError as exc:
        materials.fail(job_id, exc)
        raise
    except Exception as exc:
        if self.request.retries >= settings.workflow_max_retries:
            materials.fail(job_id, exc)
            raise
        delay = min(
            settings.workflow_retry_base_seconds * (2**self.request.retries),
            3600,
        )
        materials.retry(job_id, exc, utcnow() + timedelta(seconds=delay))
        raise self.retry(exc=exc, countdown=delay) from exc


@celery_app.task(name="tutor.dispatch_outbox")
def dispatch_outbox_task() -> dict[str, int]:
    database = Database(settings.database_url)
    if settings.auto_migrate:
        database.migrate()
    return OutboxService(
        database,
        CeleryJobDispatcher(),
        max_attempts=settings.outbox_max_attempts,
        retry_base_seconds=settings.workflow_retry_base_seconds,
    ).dispatch_pending(settings.outbox_batch_size)


def enqueue_processing(job_id: str) -> None:
    """Compatibility helper retained for integrations using the pilot API."""
    process_lesson_task.delay(job_id)


def run() -> None:
    celery_app.worker_main(["worker", "--loglevel=INFO"])
