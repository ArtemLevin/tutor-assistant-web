"""Durable job leases and operator state."""

import sqlalchemy as sa
from alembic import op

revision = "0008_durable_workers"
down_revision = "0007_production_postgres"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("outbox_events") as batch:
        batch.add_column(sa.Column("lease_token", sa.String(length=36), nullable=True))

    with op.batch_alter_table("processing_jobs") as batch:
        batch.add_column(
            sa.Column(
                "queue_name",
                sa.String(length=32),
                nullable=False,
                server_default="materials",
            )
        )
        batch.add_column(sa.Column("lease_owner", sa.String(length=160), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_processing_jobs_lease_owner", ["lease_owner"])
        batch.create_index("ix_processing_jobs_queue_name", ["queue_name"])
        batch.create_index("ix_processing_jobs_lease_expires_at", ["lease_expires_at"])
        batch.create_index("ix_processing_jobs_lease", ["status", "lease_expires_at"])
        batch.create_index(
            "ix_processing_jobs_operations",
            ["organization_id", "status", "created_at"],
        )
    op.execute("UPDATE processing_jobs SET updated_at = created_at WHERE updated_at IS NULL")
    op.execute("UPDATE processing_jobs SET queue_name = 'transcription' WHERE kind = 'post_lesson'")
    with op.batch_alter_table("processing_jobs") as batch:
        batch.alter_column("updated_at", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_index("ix_processing_jobs_operations")
        batch.drop_index("ix_processing_jobs_lease")
        batch.drop_index("ix_processing_jobs_lease_expires_at")
        batch.drop_index("ix_processing_jobs_lease_owner")
        batch.drop_index("ix_processing_jobs_queue_name")
        batch.drop_column("updated_at")
        batch.drop_column("dead_lettered_at")
        batch.drop_column("cancel_requested_at")
        batch.drop_column("heartbeat_at")
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_owner")
        batch.drop_column("queue_name")
    with op.batch_alter_table("outbox_events") as batch:
        batch.drop_column("lease_token")
