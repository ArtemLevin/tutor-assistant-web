from __future__ import annotations

import logging
import random
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import or_, select

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.automation.models import OutboxEvent
from tutor_assistant_web.modules.materials.models import JobStatus, ProcessingJob
from tutor_assistant_web.shared.errors import ConflictError, NotFoundError
from tutor_assistant_web.shared.models import new_id, utcnow

logger = logging.getLogger(__name__)


class LeaseUnavailable(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(f"lease is busy for {retry_after} seconds")


class JobTerminal(RuntimeError):
    pass


class JobCanceled(RuntimeError):
    pass


@dataclass(frozen=True)
class LeaseClaim:
    acquired: bool
    terminal: bool = False
    retry_after: int = 1


class DurableJobService:
    def __init__(
        self,
        database: Database,
        *,
        lease_seconds: int = 300,
        max_attempts: int = 5,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 3600,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.database = database
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.jitter = jitter

    def claim(self, job_id: str, owner: str) -> LeaseClaim:
        now = utcnow()
        with self.database.sessions() as session:
            job = session.scalar(
                select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()
            )
            if job is None:
                raise NotFoundError("Задание не найдено")
            if job.status in {
                JobStatus.completed.value,
                JobStatus.canceled.value,
                JobStatus.dead_letter.value,
            }:
                return LeaseClaim(acquired=False, terminal=True)
            if job.cancel_requested_at is not None:
                self._cancel_locked(job, now)
                session.commit()
                return LeaseClaim(acquired=False, terminal=True)
            if (
                job.status == JobStatus.retrying.value
                and job.next_retry_at
                and self._utc(job.next_retry_at) > now
            ):
                seconds = max(1, int((self._utc(job.next_retry_at) - now).total_seconds()))
                return LeaseClaim(acquired=False, retry_after=seconds)
            if (
                job.lease_owner
                and job.lease_owner != owner
                and job.lease_expires_at
                and self._utc(job.lease_expires_at) > now
            ):
                seconds = max(1, int((self._utc(job.lease_expires_at) - now).total_seconds()))
                return LeaseClaim(acquired=False, retry_after=seconds)
            job.lease_owner = owner[:160]
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            job.status = JobStatus.running.value
            job.started_at = job.started_at or now
            job.attempt_count += 1
            job.updated_at = now
            session.commit()
            return LeaseClaim(acquired=True)

    def heartbeat(self, job_id: str, owner: str) -> bool:
        now = utcnow()
        with self.database.sessions() as session:
            job = session.scalar(
                select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()
            )
            if job is None or job.lease_owner != owner:
                return False
            if job.cancel_requested_at is not None:
                self._cancel_locked(job, now)
                session.commit()
                raise JobCanceled("Задание отменено")
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            job.updated_at = now
            session.commit()
            return True

    def release(self, job_id: str, owner: str) -> None:
        with self.database.sessions() as session:
            job = session.scalar(
                select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()
            )
            if job is None or job.lease_owner != owner:
                return
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            job.updated_at = utcnow()
            session.commit()

    def retry(self, job_id: str, owner: str, error: Exception) -> int | None:
        now = utcnow()
        with self.database.sessions() as session:
            job = session.scalar(
                select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()
            )
            if job is None:
                return None
            if job.lease_owner and job.lease_owner != owner:
                return max(1, self.lease_seconds)
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            job.error = str(error)[:4000]
            job.updated_at = now
            if job.cancel_requested_at is not None:
                self._cancel_locked(job, now)
                session.commit()
                return None
            if job.attempt_count >= self.max_attempts:
                job.status = JobStatus.dead_letter.value
                job.stage = "dead_letter"
                job.message = "Требуется ручное вмешательство"
                job.dead_lettered_at = now
                job.completed_at = now
                job.next_retry_at = None
                session.commit()
                return None
            ceiling = min(
                self.retry_base_seconds * (2 ** max(job.attempt_count - 1, 0)),
                self.retry_max_seconds,
            )
            delay = max(1, int(self.jitter(ceiling * 0.5, float(ceiling))))
            job.status = JobStatus.retrying.value
            job.stage = "waiting_retry"
            job.message = "Повторим обработку автоматически"
            job.next_retry_at = now + timedelta(seconds=delay)
            session.commit()
            return delay

    def retry_manually(self, organization_id: str, job_id: str) -> ProcessingJob:
        with self.database.sessions() as session:
            job = session.scalar(
                select(ProcessingJob)
                .where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.organization_id == organization_id,
                )
                .with_for_update()
            )
            if job is None:
                raise NotFoundError("Задание не найдено")
            if job.status not in {
                JobStatus.failed.value,
                JobStatus.dead_letter.value,
                JobStatus.canceled.value,
                JobStatus.retrying.value,
            }:
                raise ConflictError("Задание сейчас нельзя повторить")
            job.status = JobStatus.queued.value
            job.stage = "queued"
            job.message = "Поставлено в очередь вручную"
            job.error = ""
            job.next_retry_at = None
            job.dead_lettered_at = None
            job.cancel_requested_at = None
            job.completed_at = None
            job.lease_owner = None
            job.lease_expires_at = None
            topic = self._topic(job)
            session.add(
                OutboxEvent(
                    organization_id=organization_id,
                    topic=topic,
                    dedup_key=f"job.manual_retry:{job.id}:{new_id()}",
                    payload={"job_id": job.id},
                )
            )
            session.commit()
            return job

    def cancel(self, organization_id: str, job_id: str) -> ProcessingJob:
        now = utcnow()
        with self.database.sessions() as session:
            job = session.scalar(
                select(ProcessingJob)
                .where(
                    ProcessingJob.id == job_id,
                    ProcessingJob.organization_id == organization_id,
                )
                .with_for_update()
            )
            if job is None:
                raise NotFoundError("Задание не найдено")
            if job.status in {JobStatus.completed.value, JobStatus.dead_letter.value}:
                raise ConflictError("Завершённое задание нельзя отменить")
            job.cancel_requested_at = now
            if not job.lease_owner:
                self._cancel_locked(job, now)
            session.commit()
            return job

    def resend_outbox(self, organization_id: str, event_id: str) -> OutboxEvent:
        with self.database.sessions() as session:
            event = session.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.organization_id == organization_id,
                )
                .with_for_update()
            )
            if event is None:
                raise NotFoundError("Событие outbox не найдено")
            event.status = "pending"
            event.attempts = 0
            event.last_error = ""
            event.available_at = utcnow()
            event.processed_at = None
            event.lease_token = None
            event.updated_at = utcnow()
            session.commit()
            return event

    def recover_expired(self, limit: int = 100) -> int:
        now = utcnow()
        recovered = 0
        with self.database.sessions() as session:
            jobs = list(
                session.scalars(
                    select(ProcessingJob)
                    .where(
                        or_(
                            (
                                (ProcessingJob.status == JobStatus.running.value)
                                & (ProcessingJob.lease_expires_at <= now)
                            ),
                            (
                                (ProcessingJob.status == JobStatus.retrying.value)
                                & (ProcessingJob.next_retry_at <= now)
                            ),
                        )
                    )
                    .order_by(ProcessingJob.updated_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for job in jobs:
                job.lease_owner = None
                job.lease_expires_at = None
                job.heartbeat_at = None
                if job.cancel_requested_at is not None:
                    self._cancel_locked(job, now)
                    continue
                if job.attempt_count >= self.max_attempts:
                    job.status = JobStatus.dead_letter.value
                    job.stage = "dead_letter"
                    job.message = "Lease истёк; требуется ручное вмешательство"
                    job.dead_lettered_at = now
                    job.completed_at = now
                    continue
                job.status = JobStatus.queued.value
                job.stage = "recovered"
                job.message = "Задача восстановлена maintenance worker"
                topic = self._topic(job)
                session.add(
                    OutboxEvent(
                        organization_id=job.organization_id,
                        topic=topic,
                        dedup_key=f"job.recovered:{job.id}:{job.attempt_count}",
                        payload={"job_id": job.id},
                    )
                )
                recovered += 1
            session.commit()
        return recovered

    def operations(self, organization_id: str, limit: int = 200) -> tuple[list, list]:
        now = utcnow()
        with self.database.sessions() as session:
            jobs = list(
                session.scalars(
                    select(ProcessingJob)
                    .where(
                        ProcessingJob.organization_id == organization_id,
                        or_(
                            ProcessingJob.status.in_(
                                [
                                    JobStatus.failed.value,
                                    JobStatus.dead_letter.value,
                                    JobStatus.retrying.value,
                                    JobStatus.canceled.value,
                                ]
                            ),
                            (
                                (ProcessingJob.status == JobStatus.running.value)
                                & (ProcessingJob.lease_expires_at <= now)
                            ),
                        ),
                    )
                    .order_by(ProcessingJob.created_at.desc())
                    .limit(limit)
                )
            )
            events = list(
                session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.organization_id == organization_id,
                        OutboxEvent.status == "dead",
                    )
                    .order_by(OutboxEvent.created_at.desc())
                    .limit(limit)
                )
            )
            return jobs, events

    @staticmethod
    def _cancel_locked(job: ProcessingJob, now) -> None:
        job.status = JobStatus.canceled.value
        job.stage = "canceled"
        job.message = "Задание отменено"
        job.completed_at = now
        job.next_retry_at = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = None

    @staticmethod
    def _utc(value):
        return value if value.tzinfo is not None else value.replace(tzinfo=utcnow().tzinfo)

    @staticmethod
    def _topic(job: ProcessingJob) -> str:
        return (
            "post_lesson.requested" if job.queue_name == "transcription" else "materials.requested"
        )


class LeaseHeartbeat(AbstractContextManager):
    def __init__(self, service: DurableJobService, job_id: str, owner: str) -> None:
        self.service = service
        self.job_id = job_id
        self.owner = owner
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None

    def __enter__(self):
        claim = self.service.claim(self.job_id, self.owner)
        if claim.terminal:
            raise JobTerminal
        if not claim.acquired:
            raise LeaseUnavailable(claim.retry_after)
        interval = max(1, self.service.lease_seconds // 3)
        self._thread = threading.Thread(
            target=self._run,
            args=(interval,),
            daemon=True,
            name=f"job-heartbeat-{self.job_id[:8]}",
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if exc is None and self._error is not None:
            raise self._error
        if exc is None:
            self.service.release(self.job_id, self.owner)
        return False

    def _run(self, interval: int) -> None:
        while not self._stop.wait(interval):
            try:
                if not self.service.heartbeat(self.job_id, self.owner):
                    return
            except JobCanceled as exc:
                self._error = exc
                return
            except Exception:
                logger.exception("Job heartbeat failed job_id=%s", self.job_id)
                return
