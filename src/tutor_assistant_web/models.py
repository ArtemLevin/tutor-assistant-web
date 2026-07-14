from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tutor_assistant_web.db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class LessonStatus(StrEnum):
    scheduled = "scheduled"
    live = "live"
    completed = "completed"
    cancelled = "cancelled"
    missed = "missed"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class Student(Base):
    __tablename__ = "students"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    full_name: Mapped[str] = mapped_column(String(160), index=True)
    grade: Mapped[str] = mapped_column(String(32), default="")
    subject: Mapped[str] = mapped_column(String(120), default="Математика")
    goal: Mapped[str] = mapped_column(Text, default="")
    guardian_name: Mapped[str] = mapped_column(String(160), default="")
    guardian_phone: Mapped[str] = mapped_column(String(80), default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    social_links: Mapped[str] = mapped_column(Text, default="")
    hourly_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    lessons: Mapped[list[Lesson]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Занятие")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(24), default=LessonStatus.scheduled.value, index=True
    )
    topic: Mapped[str] = mapped_column(String(300), default="")
    tutor_notes: Mapped[str] = mapped_column(Text, default="")
    price_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    bbb_meeting_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    attendee_password: Mapped[str] = mapped_column(String(64))
    moderator_password: Mapped[str] = mapped_column(String(64))
    record_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    student: Mapped[Student] = relationship(back_populates="lessons")
    recordings: Mapped[list[RecordingAsset]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[ProcessingJob]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[MaterialArtifact]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )


class RecordingAsset(Base):
    __tablename__ = "recording_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), index=True)
    record_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    state: Mapped[str] = mapped_column(String(32), default="processing")
    playback_url: Mapped[str] = mapped_column(Text, default="")
    raw_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lesson: Mapped[Lesson] = relationship(back_populates="recordings")


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default=JobStatus.queued.value, index=True)
    progress: Mapped[int] = mapped_column(default=0)
    message: Mapped[str] = mapped_column(String(500), default="Ожидает обработки")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lesson: Mapped[Lesson] = relationship(back_populates="jobs")


class MaterialArtifact(Base):
    __tablename__ = "material_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="summary")
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lesson: Mapped[Lesson] = relationship(back_populates="artifacts")
