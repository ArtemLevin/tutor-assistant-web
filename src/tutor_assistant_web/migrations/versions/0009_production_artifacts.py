"""S3 artifact lifecycle metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0009_production_artifacts"
down_revision = "0008_durable_workers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("artifact_versions") as batch:
        batch.add_column(
            sa.Column(
                "storage_status",
                sa.String(length=24),
                nullable=False,
                server_default="available",
            )
        )
        batch.add_column(
            sa.Column("quarantine_reason", sa.Text(), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_artifact_versions_storage_status", ["storage_status"])
        batch.create_index("ix_artifact_versions_purge_after", ["purge_after"])


def downgrade() -> None:
    with op.batch_alter_table("artifact_versions") as batch:
        batch.drop_index("ix_artifact_versions_purge_after")
        batch.drop_index("ix_artifact_versions_storage_status")
        batch.drop_column("purge_after")
        batch.drop_column("deleted_at")
        batch.drop_column("verified_at")
        batch.drop_column("quarantine_reason")
        batch.drop_column("storage_status")
