from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.audit.models import AuditEvent
from tutor_assistant_web.modules.automation.models import (
    LessonTranscript,
    OutboxEvent,
    OutboxStatus,
    TranscriptStatus,
    WebhookReceipt,
)
from tutor_assistant_web.modules.classroom.application import ClassroomService
from tutor_assistant_web.modules.classroom.models import RecordingAsset
from tutor_assistant_web.modules.materials.application import MaterialsService
from tutor_assistant_web.modules.materials.models import JobStatus, ProcessingJob
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.providers.transcription import resolve_media_url, segment_payload
from tutor_assistant_web.shared.contracts import (
    JobDispatcher,
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSource,
)
from tutor_assistant_web.shared.errors import GoneError, NotFoundError
from tutor_assistant_web.shared.models import utcnow

logger = logging.getLogger(__name__)


class InvalidWebhookSignature(RuntimeError):
    pass


class RetryableWorkflowError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecordingReadyResult:
    job_id: str
    duplicate: bool


class BigBlueButtonWebhookVerifier:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def decode(self, signed_parameters: str) -> tuple[str, str]:
        if not self.secret or not signed_parameters or len(signed_parameters) > 16_384:
            raise InvalidWebhookSignature("invalid recording callback")
        try:
            payload = jwt.decode(signed_parameters, self.secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise InvalidWebhookSignature("invalid recording callback signature") from exc
        meeting_id = payload.get("meeting_id")
        record_id = payload.get("record_id")
        if not isinstance(meeting_id, str) or not meeting_id:
            raise InvalidWebhookSignature("recording callback has no meeting_id")
        if not isinstance(record_id, str) or not record_id:
            raise InvalidWebhookSignature("recording callback has no record_id")
        return meeting_id[:256], record_id[:256]


class RecordingReadyService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def accept(self, meeting_id: str, record_id: str) -> RecordingReadyResult:
        digest = hashlib.sha256(
            json.dumps(
                {"meeting_id": meeting_id, "record_id": record_id},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        dedup_key = f"bbb.recording.ready:{record_id}"
        try:
            with self.database.sessions() as session:
                lesson = session.scalar(select(Lesson).where(Lesson.bbb_meeting_id == meeting_id))
                if lesson is None:
                    raise GoneError("Занятие для записи больше не существует")
                existing = session.scalar(
                    select(WebhookReceipt).where(
                        WebhookReceipt.provider == "bigbluebutton",
                        WebhookReceipt.external_event_id == record_id,
                    )
                )
                if existing is not None:
                    job_id = session.scalar(
                        select(ProcessingJob.id).where(ProcessingJob.dedup_key == dedup_key)
                    )
                    logger.info("Duplicate BBB callback record_id=%s", record_id)
                    return RecordingReadyResult(job_id=job_id or "", duplicate=True)

                recording = session.scalar(
                    select(RecordingAsset).where(RecordingAsset.record_id == record_id)
                )
                if recording is None:
                    recording = RecordingAsset(
                        organization_id=lesson.organization_id,
                        lesson_id=lesson.id,
                        record_id=record_id,
                        state="published",
                        raw_metadata={"callback_received": True},
                    )
                    session.add(recording)

                job = ProcessingJob(
                    organization_id=lesson.organization_id,
                    lesson_id=lesson.id,
                    kind="post_lesson",
                    trigger="bbb_recording_ready",
                    stage="queued",
                    dedup_key=dedup_key,
                    record_id=record_id,
                    message="Запись готова, ожидаем автоматическую обработку",
                )
                session.add(job)
                session.flush()
                session.add(
                    WebhookReceipt(
                        organization_id=lesson.organization_id,
                        provider="bigbluebutton",
                        external_event_id=record_id,
                        meeting_id=meeting_id,
                        payload_hash=digest,
                    )
                )
                session.add(
                    OutboxEvent(
                        organization_id=lesson.organization_id,
                        topic="post_lesson.requested",
                        dedup_key=dedup_key,
                        payload={"job_id": job.id},
                    )
                )
                session.add(
                    AuditEvent(
                        organization_id=lesson.organization_id,
                        actor_user_id=None,
                        action="recording.ready",
                        entity_type="processing_job",
                        entity_id=job.id,
                        details={"lesson_id": lesson.id, "record_id": record_id},
                    )
                )
                session.commit()
                logger.info("Accepted BBB callback job_id=%s record_id=%s", job.id, record_id)
                return RecordingReadyResult(job_id=job.id, duplicate=False)
        except IntegrityError:
            with self.database.sessions() as session:
                job_id = session.scalar(
                    select(ProcessingJob.id).where(ProcessingJob.dedup_key == dedup_key)
                )
            if job_id:
                logger.info("Concurrent duplicate BBB callback record_id=%s", record_id)
                return RecordingReadyResult(job_id=job_id, duplicate=True)
            raise


class OutboxService:
    def __init__(
        self,
        database: Database,
        dispatcher: JobDispatcher,
        *,
        max_attempts: int,
        retry_base_seconds: int,
    ) -> None:
        self.database = database
        self.dispatcher = dispatcher
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds

    def dispatch_pending(self, limit: int = 20) -> dict[str, int]:
        now = utcnow()
        stale = now - timedelta(minutes=5)
        with self.database.sessions() as session:
            ids = list(
                session.scalars(
                    select(OutboxEvent.id)
                    .where(
                        or_(
                            (
                                (OutboxEvent.status == OutboxStatus.pending.value)
                                & (OutboxEvent.available_at <= now)
                            ),
                            (
                                (OutboxEvent.status == OutboxStatus.dispatching.value)
                                & (OutboxEvent.updated_at <= stale)
                            ),
                        )
                    )
                    .order_by(OutboxEvent.created_at)
                    .limit(limit)
                )
            )
        result = {"dispatched": 0, "retried": 0, "dead": 0}
        for event_id in ids:
            event = self._claim(event_id, now, stale)
            if event is None:
                continue
            try:
                if event.topic != "post_lesson.requested":
                    raise ValueError(f"unsupported outbox topic: {event.topic}")
                job_id = event.payload.get("job_id")
                if not isinstance(job_id, str) or not job_id:
                    raise ValueError("outbox event has no job_id")
                self.dispatcher.enqueue_lesson_processing(job_id)
            except Exception as exc:
                logger.warning("Outbox dispatch failed event_id=%s: %s", event_id, exc)
                outcome = self._release_failed(event_id, exc)
                result[outcome] += 1
            else:
                self._complete(event_id)
                logger.info("Outbox event dispatched event_id=%s", event_id)
                result["dispatched"] += 1
        return result

    def _claim(self, event_id: str, now: datetime, stale: datetime) -> OutboxEvent | None:
        with self.database.sessions() as session:
            event = session.scalar(
                select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
            )
            if event is None or event.status in {
                OutboxStatus.completed.value,
                OutboxStatus.dead.value,
            }:
                return None
            if event.status == OutboxStatus.pending.value and self._utc(event.available_at) > now:
                return None
            if (
                event.status == OutboxStatus.dispatching.value
                and self._utc(event.updated_at) > stale
            ):
                return None
            event.status = OutboxStatus.dispatching.value
            event.updated_at = now
            session.commit()
            return event

    def _complete(self, event_id: str) -> None:
        with self.database.sessions() as session:
            event = session.get(OutboxEvent, event_id)
            if event is None:
                return
            event.status = OutboxStatus.completed.value
            event.processed_at = utcnow()
            event.updated_at = utcnow()
            event.last_error = ""
            session.commit()

    def _release_failed(self, event_id: str, error: Exception) -> str:
        with self.database.sessions() as session:
            event = session.get(OutboxEvent, event_id)
            if event is None:
                return "dead"
            event.attempts += 1
            event.last_error = str(error)[:4000]
            event.updated_at = utcnow()
            if event.attempts >= self.max_attempts:
                event.status = OutboxStatus.dead.value
                outcome = "dead"
            else:
                delay = min(self.retry_base_seconds * (2 ** (event.attempts - 1)), 3600)
                event.status = OutboxStatus.pending.value
                event.available_at = utcnow() + timedelta(seconds=delay)
                outcome = "retried"
            session.commit()
            return outcome

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class PostLessonWorkflowService:
    def __init__(
        self,
        database: Database,
        classroom: ClassroomService,
        materials: MaterialsService,
        transcriber: TranscriptionProvider,
        organization_id: str,
    ) -> None:
        self.database = database
        self.classroom = classroom
        self.materials = materials
        self.transcriber = transcriber
        self.organization_id = organization_id

    def process(self, job_id: str) -> None:
        job = self.materials.status(job_id)
        if job.status == JobStatus.completed.value:
            return
        self.materials.start(
            job_id,
            stage="recording_sync",
            message="Проверяем готовность записи BigBlueButton",
        )
        logger.info("Post-lesson workflow started job_id=%s", job_id)
        try:
            if not self.classroom.conference.is_demo:
                self.classroom.sync_recordings(job.lesson_id)
            recording = self._recording(job.lesson_id, job.record_id)
            media_url = resolve_media_url(recording.playback_url, recording.raw_metadata)
            if not media_url and self.transcriber.name != "demo":
                raise RetryableWorkflowError(
                    "BBB ещё не опубликовал прямой audio/video URL; синхронизация будет повторена"
                )
            self.materials.progress(job_id, 35, "transcribing", "Транскрибируем запись")
            transcript = self._completed_transcript(job.lesson_id, recording.record_id)
            if transcript is None:
                self._mark_transcript_running(job.lesson_id, recording.record_id, media_url)
                result = self.transcriber.transcribe(
                    TranscriptionSource(
                        record_id=recording.record_id,
                        media_url=media_url,
                        metadata=recording.raw_metadata,
                    )
                )
                self._save_transcript(job.lesson_id, recording.record_id, media_url, result)
            self.materials.progress(job_id, 65, "evidence", "Транскрипт готов, собираем материалы")
            self.materials.process(job_id, start=False, sync_recordings=False)
            logger.info("Post-lesson workflow completed job_id=%s", job_id)
        except Exception as exc:
            self._mark_transcript_failed(job.lesson_id, exc)
            self.materials.fail(job_id, exc)
            logger.exception("Post-lesson workflow failed job_id=%s", job_id)
            raise

    def _recording(self, lesson_id: str, record_id: str) -> RecordingAsset:
        with self.database.sessions() as session:
            query = select(RecordingAsset).where(
                RecordingAsset.lesson_id == lesson_id,
                RecordingAsset.organization_id == self.organization_id,
            )
            if record_id:
                query = query.where(RecordingAsset.record_id == record_id)
            recording = session.scalar(query.order_by(RecordingAsset.synced_at.desc()))
            if recording is None:
                raise RetryableWorkflowError("Запись BBB пока не доступна")
            return recording

    def _completed_transcript(self, lesson_id: str, record_id: str) -> LessonTranscript | None:
        with self.database.sessions() as session:
            return session.scalar(
                select(LessonTranscript).where(
                    LessonTranscript.lesson_id == lesson_id,
                    LessonTranscript.organization_id == self.organization_id,
                    LessonTranscript.record_id == record_id,
                    LessonTranscript.status == TranscriptStatus.completed.value,
                )
            )

    def _mark_transcript_running(self, lesson_id: str, record_id: str, source_url: str) -> None:
        with self.database.sessions() as session:
            transcript = session.scalar(
                select(LessonTranscript).where(
                    LessonTranscript.lesson_id == lesson_id,
                    LessonTranscript.organization_id == self.organization_id,
                )
            )
            if transcript is None:
                transcript = LessonTranscript(
                    organization_id=self.organization_id,
                    lesson_id=lesson_id,
                )
                session.add(transcript)
            transcript.record_id = record_id
            transcript.source_url = source_url
            transcript.status = TranscriptStatus.running.value
            transcript.error = ""
            session.commit()

    def _save_transcript(
        self,
        lesson_id: str,
        record_id: str,
        source_url: str,
        result: TranscriptionResult,
    ) -> None:
        with self.database.sessions() as session:
            transcript = session.scalar(
                select(LessonTranscript).where(
                    LessonTranscript.lesson_id == lesson_id,
                    LessonTranscript.organization_id == self.organization_id,
                )
            )
            if transcript is None:
                raise RuntimeError("transcript state disappeared")
            transcript.record_id = record_id
            transcript.source_url = source_url
            transcript.status = TranscriptStatus.completed.value
            transcript.provider = result.provider
            transcript.model = result.model
            transcript.language = result.language
            transcript.text = result.text
            transcript.segments = [segment_payload(item) for item in result.segments]
            transcript.completed_at = datetime.now(UTC)
            transcript.error = ""
            session.commit()

    def _mark_transcript_failed(self, lesson_id: str, error: Exception) -> None:
        with self.database.sessions() as session:
            transcript = session.scalar(
                select(LessonTranscript).where(
                    LessonTranscript.lesson_id == lesson_id,
                    LessonTranscript.organization_id == self.organization_id,
                )
            )
            if transcript is None:
                return
            if transcript.status == TranscriptStatus.running.value:
                transcript.status = TranscriptStatus.failed.value
                transcript.error = str(error)[:4000]
                session.commit()

    def detail(self, lesson_id: str) -> LessonTranscript | None:
        with self.database.sessions() as session:
            return session.scalar(
                select(LessonTranscript)
                .options(selectinload(LessonTranscript.lesson))
                .where(
                    LessonTranscript.lesson_id == lesson_id,
                    LessonTranscript.organization_id == self.organization_id,
                )
            )

    def update_text(self, lesson_id: str, text: str) -> LessonTranscript:
        with self.database.sessions() as session:
            transcript = session.scalar(
                select(LessonTranscript).where(
                    LessonTranscript.lesson_id == lesson_id,
                    LessonTranscript.organization_id == self.organization_id,
                )
            )
            if transcript is None:
                raise NotFoundError("Транскрипт ещё не создан")
            transcript.text = text
            transcript.updated_at = utcnow()
            session.commit()
            return transcript
