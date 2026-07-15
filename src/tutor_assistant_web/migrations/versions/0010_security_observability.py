"""Correlation identifiers for durable work."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0010_security_observability"
down_revision = "0009_production_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("processing_jobs") as batch:
        batch.add_column(
            sa.Column("correlation_id", sa.String(length=128), nullable=False, server_default="")
        )
        batch.create_index("ix_processing_jobs_correlation_id", ["correlation_id"])
    with op.batch_alter_table("outbox_events") as batch:
        batch.add_column(
            sa.Column("correlation_id", sa.String(length=128), nullable=False, server_default="")
        )
        batch.create_index("ix_outbox_events_correlation_id", ["correlation_id"])
    connection = op.get_bind()
    for table_name in ("processing_jobs", "outbox_events"):
        table = sa.table(
            table_name,
            sa.column("id", sa.String()),
            sa.column("correlation_id", sa.String()),
        )
        rows = connection.execute(sa.select(table.c.id).where(table.c.correlation_id == ""))
        assignments = [{"row_id": row.id, "new_correlation_id": uuid4().hex} for row in rows]
        if assignments:
            connection.execute(
                sa.update(table)
                .where(table.c.id == sa.bindparam("row_id"))
                .values(correlation_id=sa.bindparam("new_correlation_id")),
                assignments,
            )
    with op.batch_alter_table("processing_jobs") as batch:
        batch.alter_column("correlation_id", server_default=None)
        batch.create_check_constraint(
            "ck_processing_jobs_correlation_id", "length(correlation_id) > 0"
        )
    with op.batch_alter_table("outbox_events") as batch:
        batch.alter_column("correlation_id", server_default=None)
        batch.create_check_constraint(
            "ck_outbox_events_correlation_id", "length(correlation_id) > 0"
        )


def downgrade() -> None:
    with op.batch_alter_table("outbox_events") as batch:
        batch.drop_constraint("ck_outbox_events_correlation_id", type_="check")
        batch.drop_index("ix_outbox_events_correlation_id")
        batch.drop_column("correlation_id")
    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_constraint("ck_processing_jobs_correlation_id", type_="check")
        batch.drop_index("ix_processing_jobs_correlation_id")
        batch.drop_column("correlation_id")
