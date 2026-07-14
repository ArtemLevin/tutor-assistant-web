from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tutor_assistant_web.db import Base
from tutor_assistant_web.shared.models import new_id, utcnow

if TYPE_CHECKING:
    from tutor_assistant_web.modules.identity.models import User
    from tutor_assistant_web.modules.materials.models import GenerationRun
    from tutor_assistant_web.modules.students.models import Student


class DeliveryStatus(StrEnum):
    pending = "pending"
    available = "available"
    revoked = "revoked"


class NotificationKind(StrEnum):
    material_available = "material_available"
    material_replaced = "material_replaced"
    material_revoked = "material_revoked"


class MaterialDelivery(Base):
    __tablename__ = "material_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "generation_run_id",
            "student_id",
            name="uq_delivery_org_run_student",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    generation_run_id: Mapped[str] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(24), default=DeliveryStatus.pending.value, index=True
    )
    publication_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    generation_run: Mapped[GenerationRun] = relationship("GenerationRun")
    student: Mapped[Student] = relationship("Student")
    created_by: Mapped[User | None] = relationship("User")


class UserNotification(Base):
    __tablename__ = "user_notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    delivery_id: Mapped[str | None] = mapped_column(
        ForeignKey("material_deliveries.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), index=True)
    dedup_key: Mapped[str] = mapped_column(String(320), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    delivery: Mapped[MaterialDelivery | None] = relationship("MaterialDelivery")
