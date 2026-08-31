"""Add canonical PracticeState snapshots and immutable event ingestion."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_practice_sync"
down_revision = "0016_board_guest_invites"
branch_labels = None
depends_on = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "practice_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.BigInteger(), nullable=False, server_default="2"),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("state_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id", "student_id"],
            ["students.organization_id", "students.id"],
            name="fk_practice_profiles_org_student",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "student_id", name="uq_practice_profiles_org_student"
        ),
    )
    op.create_index(
        "ix_practice_profiles_organization_id", "practice_profiles", ["organization_id"]
    )
    op.create_index("ix_practice_profiles_student_id", "practice_profiles", ["student_id"])

    op.create_table(
        "practice_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=160), nullable=False),
        sa.Column("event_version", sa.BigInteger(), nullable=False, server_default="2"),
        sa.Column("client_instance_id", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("competency_id", sa.String(length=160), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_jsonb", JSON_DOCUMENT, nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id", "student_id"],
            ["students.organization_id", "students.id"],
            name="fk_practice_events_org_student",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_practice_events_event_id"),
    )
    op.create_index("ix_practice_events_organization_id", "practice_events", ["organization_id"])
    op.create_index("ix_practice_events_student_id", "practice_events", ["student_id"])
    op.create_index("ix_practice_events_competency_id", "practice_events", ["competency_id"])
    op.create_index("ix_practice_events_outcome", "practice_events", ["outcome"])
    op.create_index("ix_practice_events_occurred_at", "practice_events", ["occurred_at"])
    op.create_index(
        "ix_practice_events_student_competency_occurred",
        "practice_events",
        ["student_id", "competency_id", "occurred_at"],
    )
    op.create_index(
        "ix_practice_events_student_outcome_occurred",
        "practice_events",
        ["student_id", "outcome", "occurred_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    event_count = int(
        connection.execute(sa.text("SELECT COUNT(*) FROM practice_events")).scalar_one()
    )
    profile_count = int(
        connection.execute(sa.text("SELECT COUNT(*) FROM practice_profiles")).scalar_one()
    )
    if event_count or profile_count:
        raise RuntimeError(
            "Cannot downgrade practice sync while canonical PracticeState data exists. "
            "Keep migration 0017 during application rollback or export/remove practice data explicitly."
        )
    op.drop_index("ix_practice_events_student_outcome_occurred", table_name="practice_events")
    op.drop_index("ix_practice_events_student_competency_occurred", table_name="practice_events")
    op.drop_index("ix_practice_events_occurred_at", table_name="practice_events")
    op.drop_index("ix_practice_events_outcome", table_name="practice_events")
    op.drop_index("ix_practice_events_competency_id", table_name="practice_events")
    op.drop_index("ix_practice_events_student_id", table_name="practice_events")
    op.drop_index("ix_practice_events_organization_id", table_name="practice_events")
    op.drop_table("practice_events")
    op.drop_index("ix_practice_profiles_student_id", table_name="practice_profiles")
    op.drop_index("ix_practice_profiles_organization_id", table_name="practice_profiles")
    op.drop_table("practice_profiles")
