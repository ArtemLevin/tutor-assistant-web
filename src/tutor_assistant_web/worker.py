from __future__ import annotations

import logging
from uuid import uuid4

from celery import Celery
from kombu import Queue
from sqlalchemy import select

from tutor_assistant_web.bootstrap.container import (
    build_artifact_storage,
    build_conference_provider,
    build_document_engine,
    build_material_generator,
    build_transcription_provider,
)
from tutor_assistant_web.config import get_settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.automation.application import (
    OutboxService,
    PostLessonWorkflowService,
)
from tutor_assistant_web.modules.automation.durability import (
    DurableJobService,
    JobCanceled,
    JobTerminal,
    LeaseHeartbeat,
    LeaseUnavailable,
)
from tutor_assistant_web.modules.automation.models import OutboxEvent, OutboxStatus
from tutor_assistant_web.modules.classroom.application import ClassroomService
from tutor_assistant_web.modules.materials.application import MaterialsService
from tutor_assistant_web.modules.materials.models import ProcessingJob
from tutor_assistant_web.modules.portal.application import PortalEventHandler
from tutor_assistant_web.providers.tasks import CeleryJobDispatcher

logger = logging.getLogger(__name__)
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
    task_default_queue="maintenance",
    task_queues=tuple(
        Queue(name, durable=True)
        for name in ("transcription", "materials", "delivery", "maintenance")
    ),
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 3,
        "interval_start": 0.2,
        "interval_step": 0.5,
        "interval_max": 2.0,
    },
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    broker_connection_retry_on_startup=True,
    broker_heartbeat=30,
    broker_transport_options={"visibility_timeout": settings.celery_visibility_timeout},
    result_backend_transport_options={"visibility_timeout": settings.celery_visibility_timeout},
    worker_soft_shutdown_timeout=settings.worker_shutdown_timeout,
    worker_enable_soft_shutdown_on_idle=True,
    task_routes={
        "tutor.process_lesson": {"queue": "materials"},
        "tutor.deliver_outbox": {"queue": "delivery"},
        "tutor.dispatch_outbox": {"queue": "maintenance"},
        "tutor.recover_expired_leases": {"queue": "maintenance"},
        "tutor.verify_artifacts": {"queue": "maintenance"},
        "tutor.purge_artifacts": {"queue": "maintenance"},
    },
    beat_schedule={
        "dispatch-transactional-outbox": {
            "task": "tutor.dispatch_outbox",
            "schedule": float(settings.outbox_poll_seconds),
        },
        "recover-expired-job-leases": {
            "task": "tutor.recover_expired_leases",
            "schedule": float(settings.job_recovery_poll_seconds),
        },
        "verify-artifact-integrity": {
            "task": "tutor.verify_artifacts",
            "schedule": float(settings.artifact_maintenance_poll_seconds),
        },
        "purge-soft-deleted-artifacts": {
            "task": "tutor.purge_artifacts",
            "schedule": float(settings.artifact_maintenance_poll_seconds),
        },
    },
)


def _database() -> Database:
    database = Database.from_settings(settings)
    if settings.auto_migrate:
        database.migrate()
    return database


def _durability(database: Database) -> DurableJobService:
    return DurableJobService(
        database,
        lease_seconds=settings.job_lease_seconds,
        max_attempts=settings.workflow_max_attempts,
        retry_base_seconds=settings.workflow_retry_base_seconds,
        retry_max_seconds=settings.workflow_retry_max_seconds,
    )


def _outbox(database: Database) -> OutboxService:
    return OutboxService(
        database,
        CeleryJobDispatcher(),
        max_attempts=settings.outbox_max_attempts,
        retry_base_seconds=settings.workflow_retry_base_seconds,
        event_handlers=(PortalEventHandler(database),),
        dispatch_lease_seconds=settings.outbox_dispatch_lease_seconds,
    )


@celery_app.task(
    name="tutor.process_lesson",
    bind=True,
    max_retries=None,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=settings.workflow_soft_time_limit,
    time_limit=settings.workflow_hard_time_limit,
)
def process_lesson_task(self, job_id: str, phase: str = "materials") -> None:
    database = _database()
    durability = _durability(database)
    owner = str(self.request.id or uuid4())
    try:
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
            document_engine=build_document_engine(settings),
            artifact_storage=build_artifact_storage(settings),
        )
        try:
            with LeaseHeartbeat(durability, job_id, owner):
                if kind == "post_lesson" and phase == "transcription":
                    PostLessonWorkflowService(
                        database,
                        classroom,
                        materials,
                        build_transcription_provider(settings),
                        organization_id,
                    ).transcribe(job_id)
                else:
                    materials.process(
                        job_id,
                        sync_recordings=kind != "post_lesson",
                    )
        except JobTerminal:
            logger.info("Skipping terminal durable job job_id=%s", job_id)
            return
        except JobCanceled:
            logger.info("Durable job canceled job_id=%s", job_id)
            return
        except LeaseUnavailable as exc:
            raise self.retry(countdown=exc.retry_after, max_retries=None) from exc
        except Exception as exc:
            delay = durability.retry(job_id, owner, exc)
            if delay is None:
                logger.exception("Durable job moved to dead-letter job_id=%s", job_id)
                raise
            logger.warning("Durable job retry job_id=%s countdown=%s", job_id, delay)
            raise self.retry(exc=exc, countdown=delay, max_retries=None) from exc
    finally:
        database.dispose()


@celery_app.task(
    name="tutor.deliver_outbox",
    bind=True,
    max_retries=None,
    acks_late=True,
    reject_on_worker_lost=True,
)
def deliver_outbox_task(self, event_id: str, lease_token: str) -> None:
    database = _database()
    outbox = _outbox(database)
    try:
        with database.sessions() as session:
            event = session.get(OutboxEvent, event_id)
        if (
            event is None
            or event.lease_token != lease_token
            or event.status
            in {
                OutboxStatus.completed.value,
                OutboxStatus.dead.value,
            }
        ):
            return
        handler = PortalEventHandler(database)
        if not handler.handles(event.topic):
            raise ValueError(f"unsupported delivery topic: {event.topic}")
        try:
            handler.handle(event.topic, event.organization_id, event.payload)
        except Exception as exc:
            outcome = outbox._release_failed(event.id, exc, lease_token)
            if outcome == "stale":
                return
            if outcome == "dead":
                raise
            with database.sessions() as session:
                current = session.get(OutboxEvent, event.id)
                countdown = (
                    max(
                        1,
                        int(
                            (
                                outbox._utc(current.available_at) - outbox._utc(current.updated_at)
                            ).total_seconds()
                        ),
                    )
                    if current
                    else settings.workflow_retry_base_seconds
                )
            raise self.retry(exc=exc, countdown=countdown, max_retries=None) from exc
        outbox._complete(event.id, lease_token)
    finally:
        database.dispose()


@celery_app.task(name="tutor.dispatch_outbox")
def dispatch_outbox_task() -> dict[str, int]:
    database = _database()
    try:
        return _outbox(database).dispatch_pending(settings.outbox_batch_size)
    finally:
        database.dispose()


@celery_app.task(name="tutor.recover_expired_leases")
def recover_expired_leases_task() -> int:
    database = _database()
    try:
        return _durability(database).recover_expired(settings.job_recovery_batch_size)
    finally:
        database.dispose()


@celery_app.task(name="tutor.verify_artifacts")
def verify_artifacts_task() -> dict[str, int]:
    from tutor_assistant_web.modules.materials.retention import ArtifactLifecycleService

    database = _database()
    try:
        return ArtifactLifecycleService(
            database,
            build_artifact_storage(settings),
            delete_grace_days=settings.artifact_delete_grace_days,
        ).verify_integrity(settings.artifact_integrity_batch_size)
    finally:
        database.dispose()


@celery_app.task(name="tutor.purge_artifacts")
def purge_artifacts_task() -> int:
    from tutor_assistant_web.modules.materials.retention import ArtifactLifecycleService

    database = _database()
    try:
        lifecycle = ArtifactLifecycleService(
            database,
            build_artifact_storage(settings),
            delete_grace_days=settings.artifact_delete_grace_days,
        )
        lifecycle.expire_retention(
            settings.artifact_retention_days, settings.artifact_integrity_batch_size
        )
        return lifecycle.purge_due(settings.artifact_integrity_batch_size)
    finally:
        database.dispose()


def enqueue_processing(job_id: str) -> None:
    """Compatibility helper retained for integrations using the pilot API."""
    database = _database()
    try:
        with database.sessions() as session:
            queue = session.scalar(
                select(ProcessingJob.queue_name).where(ProcessingJob.id == job_id)
            )
    finally:
        database.dispose()
    if queue not in {"transcription", "materials"}:
        queue = "materials"
    process_lesson_task.apply_async(args=(job_id, queue), queue=queue)


def run() -> None:
    celery_app.worker_main(
        [
            "worker",
            "--loglevel=INFO",
            "--queues=transcription,materials,delivery,maintenance",
        ]
    )
