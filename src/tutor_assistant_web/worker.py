from __future__ import annotations

from celery import Celery

from tutor_assistant_web.config import get_settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.services import process_job

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
    database.create_schema()
    with database.sessions() as session:
        process_job(session, job_id, settings)


def enqueue_processing(job_id: str) -> None:
    process_lesson_task.delay(job_id)


def run() -> None:
    celery_app.worker_main(["worker", "--loglevel=INFO"])
