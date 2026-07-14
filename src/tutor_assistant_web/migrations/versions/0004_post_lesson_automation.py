"""Reliable post-lesson automation and transcripts."""

import sqlalchemy as sa
from alembic import op

revision = "0004_post_lesson_automation"
down_revision = "0003_workspace_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("processing_jobs") as batch:
        batch.add_column(
            sa.Column("kind", sa.String(32), nullable=False, server_default="materials")
        )
        batch.add_column(
            sa.Column("trigger", sa.String(32), nullable=False, server_default="manual")
        )
        batch.add_column(sa.Column("stage", sa.String(64), nullable=False, server_default="queued"))
        batch.add_column(sa.Column("dedup_key", sa.String(320)))
        batch.add_column(sa.Column("record_id", sa.String(256), nullable=False, server_default=""))
        batch.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("next_retry_at", sa.DateTime(timezone=True)))
        batch.create_index("ix_processing_jobs_kind", ["kind"])
        batch.create_index("ix_processing_jobs_trigger", ["trigger"])
        batch.create_index("ix_processing_jobs_stage", ["stage"])
        batch.create_index("ix_processing_jobs_next_retry_at", ["next_retry_at"])
        batch.create_index("ix_processing_jobs_record_id", ["record_id"])
        batch.create_unique_constraint("uq_processing_jobs_dedup_key", ["dedup_key"])

    with op.batch_alter_table("material_artifacts") as batch:
        batch.add_column(
            sa.Column(
                "job_id",
                sa.String(36),
                sa.ForeignKey(
                    "processing_jobs.id",
                    name="fk_material_artifacts_job_id",
                    ondelete="SET NULL",
                ),
            )
        )
        batch.create_index("ix_material_artifacts_job_id", ["job_id"])

    op.create_table(
        "webhook_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("external_event_id", sa.String(256), nullable=False),
        sa.Column("meeting_id", sa.String(256), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "external_event_id", name="uq_webhook_provider_event"),
    )
    for column in ("organization_id", "provider", "external_event_id", "meeting_id"):
        op.create_index(f"ix_webhook_receipts_{column}", "webhook_receipts", [column])

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("dedup_key", sa.String(320), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
    )
    for column in ("organization_id", "topic", "status", "available_at"):
        op.create_index(f"ix_outbox_events_{column}", "outbox_events", [column])

    op.create_table(
        "lesson_transcripts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lesson_id", sa.String(36), sa.ForeignKey("lessons.id"), nullable=False),
        sa.Column("record_id", sa.String(256), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("provider", sa.String(64), nullable=False, server_default=""),
        sa.Column("model", sa.String(120), nullable=False, server_default=""),
        sa.Column("language", sa.String(16), nullable=False, server_default=""),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("segments", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    for column in ("organization_id", "lesson_id", "record_id", "status"):
        op.create_index(
            f"ix_lesson_transcripts_{column}",
            "lesson_transcripts",
            [column],
            unique=column == "lesson_id",
        )


def downgrade() -> None:
    op.drop_table("lesson_transcripts")
    op.drop_table("outbox_events")
    op.drop_table("webhook_receipts")
    with op.batch_alter_table("material_artifacts") as batch:
        batch.drop_index("ix_material_artifacts_job_id")
        batch.drop_column("job_id")
    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_constraint("uq_processing_jobs_dedup_key", type_="unique")
        batch.drop_index("ix_processing_jobs_next_retry_at")
        batch.drop_index("ix_processing_jobs_record_id")
        batch.drop_index("ix_processing_jobs_stage")
        batch.drop_index("ix_processing_jobs_trigger")
        batch.drop_index("ix_processing_jobs_kind")
        batch.drop_column("next_retry_at")
        batch.drop_column("attempt_count")
        batch.drop_column("dedup_key")
        batch.drop_column("record_id")
        batch.drop_column("stage")
        batch.drop_column("trigger")
        batch.drop_column("kind")
