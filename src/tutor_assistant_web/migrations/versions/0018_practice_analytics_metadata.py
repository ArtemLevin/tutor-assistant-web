"""Add versioned competency metadata used by practice analytics."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_practice_analytics_metadata"
down_revision = "0017_practice_sync"
branch_labels = None
depends_on = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "practice_analytics_metadata",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("source_revision", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("metadata_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id", "student_id"],
            ["students.organization_id", "students.id"],
            name="fk_practice_analytics_metadata_org_student",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "student_id",
            name="uq_practice_analytics_metadata_org_student",
        ),
    )
    op.create_index(
        "ix_practice_analytics_metadata_organization_id",
        "practice_analytics_metadata",
        ["organization_id"],
    )
    op.create_index(
        "ix_practice_analytics_metadata_student_id",
        "practice_analytics_metadata",
        ["student_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    count = int(
        connection.execute(sa.text("SELECT COUNT(*) FROM practice_analytics_metadata")).scalar_one()
    )
    if count:
        raise RuntimeError(
            "Cannot downgrade practice analytics metadata while snapshots exist. "
            "Export or remove analytics metadata first."
        )
    op.drop_index(
        "ix_practice_analytics_metadata_student_id",
        table_name="practice_analytics_metadata",
    )
    op.drop_index(
        "ix_practice_analytics_metadata_organization_id",
        table_name="practice_analytics_metadata",
    )
    op.drop_table("practice_analytics_metadata")
