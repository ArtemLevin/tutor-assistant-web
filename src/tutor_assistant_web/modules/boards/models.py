from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
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
    from tutor_assistant_web.modules.scheduling.models import Lesson


class BoardSnapshotStatus(StrEnum):
    uploading = "uploading"
    available = "available"
    quarantined = "quarantined"
    deleted = "deleted"


class BoardDocument(Base):
    __tablename__ = "board_documents"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "lesson_id",
            name="uq_board_documents_org_lesson",
        ),
        ForeignKeyConstraint(
            ["organization_id", "student_id", "lesson_id"],
            ["lessons.organization_id", "lessons.student_id", "lessons.id"],
            name="fk_board_documents_org_student_lesson",
            ondelete="CASCADE",
        ),
        CheckConstraint("current_revision >= 0", name="ck_board_documents_current_revision"),
        CheckConstraint(
            "last_snapshot_revision >= 0 AND last_snapshot_revision <= current_revision",
            name="ck_board_documents_snapshot_revision",
        ),
        CheckConstraint(
            "commands_since_snapshot >= 0",
            name="ck_board_documents_commands_since_snapshot",
        ),
        CheckConstraint(
            "bytes_since_snapshot >= 0",
            name="ck_board_documents_bytes_since_snapshot",
        ),
        Index(
            "ix_board_documents_org_student_updated",
            "organization_id",
            "student_id",
            "updated_at",
        ),
        Index(
            "ix_board_documents_purge",
            "deleted_at",
            "purge_after",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    student_id: Mapped[str] = mapped_column(String(36), index=True)
    lesson_id: Mapped[str] = mapped_column(String(36), index=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    current_document_sha256: Mapped[str] = mapped_column(String(64), default="")
    last_snapshot_revision: Mapped[int] = mapped_column(Integer, default=0)
    commands_since_snapshot: Mapped[int] = mapped_column(Integer, default=0)
    bytes_since_snapshot: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    lesson: Mapped[Lesson] = relationship("Lesson")
    command_batches: Mapped[list[BoardCommandBatch]] = relationship(
        "BoardCommandBatch",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="BoardCommandBatch.revision",
    )
    snapshots: Mapped[list[BoardSnapshot]] = relationship(
        "BoardSnapshot",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="BoardSnapshot.revision",
    )
    geometry_imports: Mapped[list[BoardGeometryImport]] = relationship(
        "BoardGeometryImport",
        back_populates="document",
        cascade="all, delete-orphan",
    )


class BoardCommandBatch(Base):
    __tablename__ = "board_command_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "board_document_id"],
            ["board_documents.organization_id", "board_documents.id"],
            name="fk_board_commands_org_document",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "organization_id",
            "board_document_id",
            "revision",
            name="uq_board_commands_org_document_revision",
        ),
        UniqueConstraint(
            "organization_id",
            "board_document_id",
            "idempotency_key",
            name="uq_board_commands_org_document_idempotency",
        ),
        CheckConstraint("revision > 0", name="ck_board_commands_revision"),
        CheckConstraint("base_revision >= 0", name="ck_board_commands_base_revision"),
        CheckConstraint(
            "revision = base_revision + 1",
            name="ck_board_commands_revision_sequence",
        ),
        CheckConstraint("payload_size > 0", name="ck_board_commands_payload_size"),
        Index(
            "ix_board_commands_org_document_created",
            "organization_id",
            "board_document_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), index=True)
    board_document_id: Mapped[str] = mapped_column(String(128), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    base_revision: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contract_actor_id: Mapped[str] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    expected_document_sha256: Mapped[str] = mapped_column(String(64))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    payload_size: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[BoardDocument] = relationship(
        "BoardDocument", back_populates="command_batches"
    )


class BoardSnapshot(Base):
    __tablename__ = "board_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "board_document_id"],
            ["board_documents.organization_id", "board_documents.id"],
            name="fk_board_snapshots_org_document",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "organization_id",
            "board_document_id",
            "revision",
            name="uq_board_snapshots_org_document_revision",
        ),
        UniqueConstraint("storage_key", name="uq_board_snapshots_storage_key"),
        CheckConstraint("revision >= 0", name="ck_board_snapshots_revision"),
        CheckConstraint("size > 0", name="ck_board_snapshots_size"),
        CheckConstraint(
            "storage_status IN ('uploading', 'available', 'quarantined', 'deleted')",
            name="ck_board_snapshots_storage_status",
        ),
        Index(
            "ix_board_snapshots_org_document_created",
            "organization_id",
            "board_document_id",
            "created_at",
        ),
        Index("ix_board_snapshots_purge", "deleted_at", "purge_after"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), index=True)
    board_document_id: Mapped[str] = mapped_column(String(128), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    document_sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    storage_status: Mapped[str] = mapped_column(
        String(24),
        default=BoardSnapshotStatus.uploading.value,
        index=True,
    )
    upload_error: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    document: Mapped[BoardDocument] = relationship("BoardDocument", back_populates="snapshots")


class BoardGeometryImport(Base):
    __tablename__ = "board_geometry_imports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "board_document_id"],
            ["board_documents.organization_id", "board_documents.id"],
            name="fk_board_geometry_imports_org_document",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "organization_id",
            "board_document_id",
            "import_id",
            name="uq_board_geometry_imports_org_document_import",
        ),
        UniqueConstraint(
            "organization_id",
            "request_id",
            name="uq_board_geometry_imports_org_request",
        ),
        CheckConstraint("base_revision >= 0", name="ck_board_geometry_imports_base_revision"),
        Index(
            "ix_board_geometry_imports_org_document_created",
            "organization_id",
            "board_document_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), index=True)
    board_document_id: Mapped[str] = mapped_column(String(128), index=True)
    import_id: Mapped[str] = mapped_column(String(128))
    command_id: Mapped[str] = mapped_column(String(128))
    base_revision: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    request_id: Mapped[str] = mapped_column(String(220))
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    contract_sha256: Mapped[str] = mapped_column(String(64))
    service_version: Mapped[str] = mapped_column(String(32))
    api_version: Mapped[str] = mapped_column(String(32))
    gir_schema_version: Mapped[str] = mapped_column(String(32))
    gir_sha256: Mapped[str] = mapped_column(String(64))
    layout_document_version: Mapped[str] = mapped_column(String(32))
    layout_sha256: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[BoardDocument] = relationship(
        "BoardDocument", back_populates="geometry_imports"
    )
