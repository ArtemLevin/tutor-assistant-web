from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tutor_assistant_web.db import Base
from tutor_assistant_web.shared.models import new_id, utcnow

if TYPE_CHECKING:
    from tutor_assistant_web.modules.scheduling.models import Lesson


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_students_org_id"),
        Index("ix_students_org_active_name", "organization_id", "active", "full_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
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
        "Lesson", back_populates="student", cascade="all, delete-orphan"
    )
