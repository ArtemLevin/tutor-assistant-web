from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.automation.models import OutboxEvent
from tutor_assistant_web.modules.classroom.application import ClassroomService
from tutor_assistant_web.modules.materials.evidence import build_evidence_bundle
from tutor_assistant_web.modules.materials.models import (
    ArtifactStatus,
    ArtifactStorageStatus,
    ArtifactVersion,
    BuildLog,
    EvidenceBundle,
    GenerationRun,
    GenerationStatus,
    JobStatus,
    MaterialArtifact,
    ProcessingJob,
)
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.providers.artifacts import ArtifactStorageError
from tutor_assistant_web.shared.contracts import (
    ArtifactStorage,
    DocumentBuildRequest,
    DocumentEngine,
    JobDispatcher,
    MaterialGenerator,
)
from tutor_assistant_web.shared.errors import ConflictError, NotFoundError


def evidence_payload(lesson: Lesson) -> dict[str, Any]:
    return build_evidence_bundle(lesson).model_dump(mode="json")


class MaterialsService:
    def __init__(
        self,
        database: Database,
        generator: MaterialGenerator,
        classroom: ClassroomService,
        dispatcher: JobDispatcher | None = None,
        organization_id: str | None = None,
        document_engine: DocumentEngine | None = None,
        artifact_storage: ArtifactStorage | None = None,
    ) -> None:
        self.database = database
        self.generator = generator
        self.classroom = classroom
        self.dispatcher = dispatcher
        self.organization_id = organization_id or classroom.organization_id
        self.document_engine = document_engine
        self.artifact_storage = artifact_storage
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
            session.flush()
            if self.dispatcher.name == "celery":
                session.add(
                    OutboxEvent(
                        organization_id=self.organization_id,
                        topic="materials.requested",
                        dedup_key=f"materials.requested:{job.id}",
                        payload={"job_id": job.id},
                    )
                )
            session.commit()
        if self.dispatcher.name != "celery":
            self.dispatcher.enqueue_lesson_processing(job.id, queue="materials")
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

    def artifact_version(self, artifact_id: str) -> ArtifactVersion:
        with self.database.sessions() as session:
            artifact = session.scalar(
                select(ArtifactVersion).where(
                    ArtifactVersion.id == artifact_id,
                    ArtifactVersion.organization_id == self.organization_id,
                )
            )
            if artifact is None:
                raise NotFoundError("Версия материала не найдена")
            return artifact

    def read_artifact_version(self, artifact_id: str) -> tuple[ArtifactVersion, bytes]:
        if self.artifact_storage is None:
            raise RuntimeError("artifact storage is not configured")
        artifact = self.artifact_version(artifact_id)
        if artifact.storage_status != ArtifactStorageStatus.available.value:
            raise NotFoundError("Файл недоступен")
        return artifact, self.artifact_storage.read(artifact.storage_key)

    def stream_artifact_version(self, artifact_id: str):
        if self.artifact_storage is None:
            raise RuntimeError("artifact storage is not configured")
        artifact = self.artifact_version(artifact_id)
        if artifact.storage_status != ArtifactStorageStatus.available.value:
            raise NotFoundError("Файл недоступен")
        return artifact, self.artifact_storage.iter_bytes(artifact.storage_key)

    def approve(self, run_id: str, user_id: str) -> GenerationRun:
        with self.database.sessions() as session:
            run = self._run_for_update(session, run_id)
            if run.status not in {
                GenerationStatus.review_required.value,
                GenerationStatus.approved.value,
            }:
                raise ConflictError("Сборка ещё не готова к согласованию")
            now = datetime.now(UTC)
            run.status = GenerationStatus.approved.value
            run.approved_at = now
            run.approved_by = user_id
            for version in run.versions:
                version.status = ArtifactStatus.approved.value
                version.approved_at = now
            session.add(
                BuildLog(
                    organization_id=self.organization_id,
                    generation_run_id=run.id,
                    stage="review",
                    status="approved",
                    message="Комплект согласован преподавателем",
                )
            )
            session.commit()
            return run

    def process(self, job_id: str, *, start: bool = True, sync_recordings: bool = True) -> None:
        if start:
            self.start(job_id)
        try:
            job = self.status(job_id)
            if sync_recordings and not self.classroom.conference.is_demo:
                self.classroom.sync_recordings(job.lesson_id)
            self.progress(job_id, 72 if not start else 45, "materials", "Формируем пакет данных")
            lesson = self._lesson_with_evidence(job.lesson_id)
            bundle = build_evidence_bundle(lesson)
            run = self._prepare_run(
                job, lesson, bundle.model_dump(mode="json"), bundle.content_hash
            )
            if run.status in {
                GenerationStatus.review_required.value,
                GenerationStatus.approved.value,
                GenerationStatus.published.value,
                GenerationStatus.revoked.value,
            } and self._run_has_versions(run.id):
                self._complete_job(job_id)
                return
            artifacts = self.generator.generate(bundle.model_dump(mode="json"))
            build_result = None
            if self.document_engine is not None and self.artifact_storage is not None:
                self.progress(job_id, 82, "building", "Собираем TEX, PDF и HTML")
                build_result = self.document_engine.build(
                    DocumentBuildRequest(
                        title=f"{lesson.student.full_name}: {lesson.topic or lesson.title}",
                        evidence=bundle.model_dump(mode="json"),
                        materials=artifacts,
                    )
                )
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
                run_model = session.scalar(
                    select(GenerationRun)
                    .options(selectinload(GenerationRun.versions))
                    .where(
                        GenerationRun.id == run.id,
                        GenerationRun.organization_id == self.organization_id,
                    )
                )
                if run_model is None:
                    raise NotFoundError("Сборка не найдена")
                if build_result is not None:
                    version_number = (
                        session.scalar(
                            select(func.max(ArtifactVersion.version)).where(
                                ArtifactVersion.lesson_id == lesson.id,
                                ArtifactVersion.organization_id == self.organization_id,
                            )
                        )
                        or 0
                    ) + 1
                    existing = {item.kind: item for item in run_model.versions}
                    for output in build_result.outputs:
                        key = (
                            f"{self.organization_id}/{lesson.id}/{run.id}/"
                            f"v{version_number}/{output.filename}"
                        )
                        version = existing.get(output.kind)
                        if version is None:
                            version = ArtifactVersion(
                                organization_id=self.organization_id,
                                lesson_id=lesson.id,
                                generation_run_id=run.id,
                                kind=output.kind,
                                filename=output.filename,
                            )
                            session.add(version)
                        version.storage_status = ArtifactStorageStatus.uploading.value
                        version.media_type = output.media_type
                        version.storage_key = key
                        version.sha256 = hashlib.sha256(output.content).hexdigest()
                        version.size = len(output.content)
                        version.version = version_number
                        session.commit()
                        try:
                            stored = self.artifact_storage.put(
                                key, output.content, output.media_type
                            )
                        except ArtifactStorageError as exc:
                            version.storage_status = ArtifactStorageStatus.quarantined.value
                            version.quarantine_reason = str(exc)[:2000]
                            session.commit()
                            raise
                        version.media_type = stored.media_type
                        version.storage_key = stored.key
                        version.sha256 = stored.sha256
                        version.size = stored.size
                        version.version = version_number
                        version.status = ArtifactStatus.review_required.value
                        version.storage_status = ArtifactStorageStatus.available.value
                        version.quarantine_reason = ""
                    run_model.engine = build_result.engine
                    session.add(
                        BuildLog(
                            organization_id=self.organization_id,
                            generation_run_id=run.id,
                            stage="build",
                            status="success",
                            message=build_result.log[:4000],
                            details={"outputs": [item.kind for item in build_result.outputs]},
                        )
                    )
                run_model.status = GenerationStatus.review_required.value
                run_model.completed_at = datetime.now(UTC)
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
            self._fail_run(job_id, exc)
            self.fail(job_id, exc)
            raise

    def _prepare_run(
        self, job: ProcessingJob, lesson: Lesson, payload: dict[str, Any], content_hash: str
    ) -> GenerationRun:
        idempotency_key = hashlib.sha256(f"materials-factory-v1:{job.id}".encode()).hexdigest()
        with self.database.sessions() as session:
            run = session.scalar(
                select(GenerationRun).where(
                    GenerationRun.idempotency_key == idempotency_key,
                    GenerationRun.organization_id == self.organization_id,
                )
            )
            if run is not None:
                return run
            evidence = session.scalar(
                select(EvidenceBundle).where(
                    EvidenceBundle.organization_id == self.organization_id,
                    EvidenceBundle.content_hash == content_hash,
                )
            )
            if evidence is None:
                evidence = EvidenceBundle(
                    organization_id=self.organization_id,
                    lesson_id=lesson.id,
                    schema_version="1.0",
                    content_hash=content_hash,
                    payload=payload,
                )
                session.add(evidence)
                session.flush()
            run = GenerationRun(
                organization_id=self.organization_id,
                lesson_id=lesson.id,
                job_id=job.id,
                evidence_bundle_id=evidence.id,
                idempotency_key=idempotency_key,
                status=GenerationStatus.building.value,
                generator=self.generator.name,
                engine=self.document_engine.name if self.document_engine else "disabled",
            )
            session.add(run)
            session.flush()
            session.add(
                BuildLog(
                    organization_id=self.organization_id,
                    generation_run_id=run.id,
                    stage="evidence",
                    status="success",
                    message="LessonEvidenceBundle v1 зафиксирован",
                    details={"content_hash": content_hash},
                )
            )
            session.commit()
            return run

    def _run_has_versions(self, run_id: str) -> bool:
        with self.database.sessions() as session:
            return bool(
                session.scalar(
                    select(func.count(ArtifactVersion.id)).where(
                        ArtifactVersion.generation_run_id == run_id
                    )
                )
            )

    def _complete_job(self, job_id: str) -> None:
        with self.database.sessions() as session:
            job = session.get(ProcessingJob, job_id)
            if job is None or job.organization_id != self.organization_id:
                raise NotFoundError("Задание не найдено")
            job.status = JobStatus.completed.value
            job.stage = "completed"
            job.progress = 100
            job.message = "Материалы готовы к проверке"
            job.completed_at = datetime.now(UTC)
            job.next_retry_at = None
            session.commit()

    def _fail_run(self, job_id: str, error: Exception) -> None:
        with self.database.sessions() as session:
            run = session.scalar(
                select(GenerationRun).where(
                    GenerationRun.job_id == job_id,
                    GenerationRun.organization_id == self.organization_id,
                )
            )
            if run is None:
                return
            run.status = GenerationStatus.failed.value
            run.error = str(error)[:4000]
            session.add(
                BuildLog(
                    organization_id=self.organization_id,
                    generation_run_id=run.id,
                    stage="build",
                    status="failed",
                    message=run.error,
                )
            )
            session.commit()

    def _run_for_update(self, session, run_id: str) -> GenerationRun:
        run = session.scalar(
            select(GenerationRun)
            .options(selectinload(GenerationRun.versions))
            .where(
                GenerationRun.id == run_id,
                GenerationRun.organization_id == self.organization_id,
            )
        )
        if run is None:
            raise NotFoundError("Сборка не найдена")
        return run

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
            if job.lease_owner is None:
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
