from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tutor_assistant_web.db import Base
from tutor_assistant_web.shared.models import new_id, utcnow

if TYPE_CHECKING:
    from tutor_assistant_web.modules.automation.models import LessonTranscript
    from tutor_assistant_web.modules.classroom.models import RecordingAsset
    from tutor_assistant_web.modules.materials.models import (
        GenerationRun,
        MaterialArtifact,
        ProcessingJob,
    )
    from tutor_assistant_web.modules.students.models import Student


class LessonStatus(StrEnum):
    scheduled = "scheduled"
    live = "live"
    completed = "completed"
    cancelled = "cancelled"
    missed = "missed"


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (Index("ix_lessons_org_starts_at", "organization_id", "starts_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
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

    student: Mapped[Student] = relationship("Student", back_populates="lessons")
    recordings: Mapped[list[RecordingAsset]] = relationship(
        "RecordingAsset", back_populates="lesson", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[ProcessingJob]] = relationship(
        "ProcessingJob", back_populates="lesson", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[MaterialArtifact]] = relationship(
        "MaterialArtifact", back_populates="lesson", cascade="all, delete-orphan"
    )
    generation_runs: Mapped[list[GenerationRun]] = relationship(
        "GenerationRun", back_populates="lesson", cascade="all, delete-orphan"
    )
    transcript: Mapped[LessonTranscript | None] = relationship(
        "LessonTranscript", back_populates="lesson", cascade="all, delete-orphan", uselist=False
    )
