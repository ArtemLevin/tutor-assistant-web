from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
        UniqueConstraint("organization_id", "id", name="uq_material_deliveries_org_id"),
        ForeignKeyConstraint(
            ["organization_id", "student_id"],
            ["students.organization_id", "students.id"],
            name="fk_material_deliveries_org_student",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "generation_run_id"],
            ["generation_runs.organization_id", "generation_runs.id"],
            name="fk_material_deliveries_org_run",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('pending', 'available', 'revoked')",
            name="ck_material_deliveries_status",
        ),
        Index(
            "ix_deliveries_portal",
            "organization_id",
            "student_id",
            "status",
            "published_at",
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

    generation_run: Mapped[GenerationRun] = relationship(
        "GenerationRun", foreign_keys=[generation_run_id]
    )
    student: Mapped[Student] = relationship("Student", foreign_keys=[student_id])
    created_by: Mapped[User | None] = relationship("User")


class UserNotification(Base):
    __tablename__ = "user_notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "student_id"],
            ["students.organization_id", "students.id"],
            name="fk_user_notifications_org_student",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "delivery_id"],
            ["material_deliveries.organization_id", "material_deliveries.id"],
            name="fk_user_notifications_org_delivery",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "kind IN ('material_available', 'material_replaced', 'material_revoked')",
            name="ck_user_notifications_kind",
        ),
        Index(
            "ix_notifications_inbox",
            "organization_id",
            "user_id",
            "read_at",
            "created_at",
        ),
    )

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

    delivery: Mapped[MaterialDelivery | None] = relationship(
        "MaterialDelivery", foreign_keys=[delivery_id]
    )
