from __future__ import annotations

from celery import Celery
from sqlalchemy import select

from tutor_assistant_web.bootstrap.container import (
    build_conference_provider,
    build_material_generator,
)
from tutor_assistant_web.config import get_settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.classroom.application import ClassroomService
from tutor_assistant_web.modules.materials.application import MaterialsService
from tutor_assistant_web.modules.materials.models import ProcessingJob

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
)


@celery_app.task(
    name="tutor.process_lesson",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=2,
)
def process_lesson_task(job_id: str) -> None:
    database = Database(settings.database_url)
    if settings.auto_migrate:
        database.migrate()
    with database.sessions() as session:
        organization_id = session.scalar(
            select(ProcessingJob.organization_id).where(ProcessingJob.id == job_id)
        )
    if organization_id is None:
        return
    conference = build_conference_provider(settings)
    classroom = ClassroomService(
        database,
        conference,
        settings.public_base_url,
        settings.app_secret_key,
        organization_id,
    )
    MaterialsService(
        database,
        build_material_generator(settings),
        classroom,
        organization_id=organization_id,
    ).process(job_id)


def enqueue_processing(job_id: str) -> None:
    """Compatibility helper retained for integrations using the pilot API."""
    process_lesson_task.delay(job_id)


def run() -> None:
    celery_app.worker_main(["worker", "--loglevel=INFO"])
