"""Pilot schema produced by releases up to 0.2."""

import sqlalchemy as sa
from alembic import op

revision = "0001_pilot"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "students",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("grade", sa.String(32), nullable=False, server_default=""),
        sa.Column("subject", sa.String(120), nullable=False, server_default="Математика"),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("guardian_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("guardian_phone", sa.String(80), nullable=False, server_default=""),
        sa.Column("email", sa.String(254), nullable=False, server_default=""),
        sa.Column("social_links", sa.Text(), nullable=False, server_default=""),
        sa.Column("hourly_rate", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_students_full_name", "students", ["full_name"])
    op.create_index("ix_students_active", "students", ["active"])
    op.create_table(
        "lessons",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("student_id", sa.String(36), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default="Занятие"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="scheduled"),
        sa.Column("topic", sa.String(300), nullable=False, server_default=""),
        sa.Column("tutor_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("price_snapshot", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("bbb_meeting_id", sa.String(256), nullable=False, unique=True),
        sa.Column("attendee_password", sa.String(64), nullable=False),
        sa.Column("moderator_password", sa.String(64), nullable=False),
        sa.Column("record_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("student_id", "starts_at", "status", "bbb_meeting_id"):
        op.create_index(f"ix_lessons_{column}", "lessons", [column])
    op.create_table(
        "recording_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lesson_id", sa.String(36), sa.ForeignKey("lessons.id"), nullable=False),
        sa.Column("record_id", sa.String(256), nullable=False, unique=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="processing"),
        sa.Column("playback_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recording_assets_lesson_id", "recording_assets", ["lesson_id"])
    op.create_index("ix_recording_assets_record_id", "recording_assets", ["record_id"])
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lesson_id", sa.String(36), sa.ForeignKey("lessons.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.String(500), nullable=False, server_default="Ожидает обработки"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_processing_jobs_lesson_id", "processing_jobs", ["lesson_id"])
    op.create_index("ix_processing_jobs_status", "processing_jobs", ["status"])
    op.create_table(
        "material_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lesson_id", sa.String(36), sa.ForeignKey("lessons.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="summary"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_material_artifacts_lesson_id", "material_artifacts", ["lesson_id"])


def downgrade() -> None:
    for table in (
        "material_artifacts",
        "processing_jobs",
        "recording_assets",
        "lessons",
        "students",
    ):
        op.drop_table(table)
