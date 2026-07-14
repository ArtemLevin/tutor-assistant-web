from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tutor_assistant_web.db import Base
from tutor_assistant_web.shared.models import new_id, utcnow

if TYPE_CHECKING:
    from tutor_assistant_web.modules.scheduling.models import Lesson


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    retrying = "retrying"
    completed = "completed"
    failed = "failed"


class GenerationStatus(StrEnum):
    building = "building"
    review_required = "review_required"
    approved = "approved"
    published = "published"
    revoked = "revoked"
    failed = "failed"


class ArtifactStatus(StrEnum):
    review_required = "review_required"
    approved = "approved"
    published = "published"
    revoked = "revoked"


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="materials", index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    stage: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    dedup_key: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    record_id: Mapped[str] = mapped_column(String(256), default="", index=True)
    status: Mapped[str] = mapped_column(String(24), default=JobStatus.queued.value, index=True)
    attempt_count: Mapped[int] = mapped_column(default=0)
    progress: Mapped[int] = mapped_column(default=0)
    message: Mapped[str] = mapped_column(String(500), default="Ожидает обработки")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    lesson: Mapped[Lesson] = relationship("Lesson", back_populates="jobs")


class MaterialArtifact(Base):
    __tablename__ = "material_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="summary")
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lesson: Mapped[Lesson] = relationship("Lesson", back_populates="artifacts")


class EvidenceBundle(Base):
    __tablename__ = "lesson_evidence_bundles"
    __table_args__ = (
        UniqueConstraint("organization_id", "content_hash", name="uq_evidence_org_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), index=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GenerationRun(Base):
    __tablename__ = "generation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), index=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), unique=True, index=True
    )
    evidence_bundle_id: Mapped[str] = mapped_column(
        ForeignKey("lesson_evidence_bundles.id", ondelete="RESTRICT"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(
        String(24), default=GenerationStatus.building.value, index=True
    )
    generator: Mapped[str] = mapped_column(String(100), default="")
    engine: Mapped[str] = mapped_column(String(100), default="")
    prompt_version: Mapped[str] = mapped_column(String(40), default="materials-v1")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    lesson: Mapped[Lesson] = relationship("Lesson", back_populates="generation_runs")
    evidence_bundle: Mapped[EvidenceBundle] = relationship("EvidenceBundle")
    versions: Mapped[list[ArtifactVersion]] = relationship(
        "ArtifactVersion", back_populates="generation_run", cascade="all, delete-orphan"
    )
    logs: Mapped[list[BuildLog]] = relationship(
        "BuildLog", back_populates="generation_run", cascade="all, delete-orphan"
    )


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"
    __table_args__ = (
        UniqueConstraint("generation_run_id", "kind", name="uq_artifact_version_run_kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), index=True)
    generation_run_id: Mapped[str] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(120))
    storage_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(24), default=ArtifactStatus.review_required.value, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    generation_run: Mapped[GenerationRun] = relationship("GenerationRun", back_populates="versions")


class BuildLog(Base):
    __tablename__ = "build_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    generation_run_id: Mapped[str] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    generation_run: Mapped[GenerationRun] = relationship("GenerationRun", back_populates="logs")
