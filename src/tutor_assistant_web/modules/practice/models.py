from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tutor_assistant_web.db import Base
from tutor_assistant_web.shared.models import new_id, utcnow

JSON_DOCUMENT = JSON().with_variant(JSONB, "postgresql")


class PracticeProfile(Base):
    __tablename__ = "practice_profiles"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "student_id", name="uq_practice_profiles_org_student"
        ),
        ForeignKeyConstraint(
            ["organization_id", "student_id"],
            ["students.organization_id", "students.id"],
            name="fk_practice_profiles_org_student",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[str] = mapped_column(String(36), index=True)
    schema_version: Mapped[int] = mapped_column(BigInteger, default=2)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    state_jsonb: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PracticeEvent(Base):
    __tablename__ = "practice_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_practice_events_event_id"),
        ForeignKeyConstraint(
            ["organization_id", "student_id"],
            ["students.organization_id", "students.id"],
            name="fk_practice_events_org_student",
            ondelete="CASCADE",
        ),
        Index(
            "ix_practice_events_student_competency_occurred",
            "student_id",
            "competency_id",
            "occurred_at",
        ),
        Index(
            "ix_practice_events_student_outcome_occurred",
            "student_id",
            "outcome",
            "occurred_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[str] = mapped_column(String(36), index=True)
    event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_version: Mapped[int] = mapped_column(BigInteger, default=2)
    client_instance_id: Mapped[str] = mapped_column(String(160), default="")
    competency_id: Mapped[str] = mapped_column(String(160), index=True)
    outcome: Mapped[str] = mapped_column(String(24), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event_jsonb: Mapped[dict] = mapped_column(JSON_DOCUMENT)
