"""Add teacher-owned standalone board persistence fields.

This is an expand migration. Legacy lesson-bound rows keep nullable standalone
metadata while standalone rows are required by a check constraint to have an
owner and title. Application rollback may keep this schema in place. A schema
downgrade is intentionally blocked once standalone rows exist to avoid data
loss when restoring the legacy NOT NULL lesson/student linkage.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_standalone_board_persistence"
down_revision = "0014_board_origins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("board_documents") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("title", sa.String(length=200), nullable=True))
        batch.add_column(
            sa.Column(
                "guest_writes_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "access_version",
                sa.BigInteger(),
                nullable=False,
                server_default="1",
            )
        )
        batch.alter_column(
            "student_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
        batch.alter_column(
            "lesson_id",
            existing_type=sa.String(length=36),
            nullable=True,
        )
        batch.create_foreign_key(
            "fk_board_documents_org_owner_membership",
            "memberships",
            ["organization_id", "owner_user_id"],
            ["organization_id", "user_id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_board_documents_linkage",
            "((lesson_id IS NOT NULL AND student_id IS NOT NULL) OR "
            "(lesson_id IS NULL AND student_id IS NULL))",
        )
        batch.create_check_constraint(
            "ck_board_documents_standalone_owner",
            "lesson_id IS NOT NULL OR "
            "(owner_user_id IS NOT NULL AND title IS NOT NULL "
            "AND length(trim(title)) > 0)",
        )
        batch.create_check_constraint(
            "ck_board_documents_access_version",
            "access_version > 0",
        )

    op.create_index(
        "ix_board_documents_org_owner_updated",
        "board_documents",
        ["organization_id", "owner_user_id", "updated_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    standalone_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM board_documents WHERE lesson_id IS NULL OR student_id IS NULL"
        )
    ).scalar_one()
    if int(standalone_count) > 0:
        raise RuntimeError(
            "Cannot downgrade standalone board persistence while standalone rows exist. "
            "Roll back the application while keeping migration 0015, or migrate/export "
            "standalone boards explicitly first."
        )

    op.drop_index("ix_board_documents_org_owner_updated", table_name="board_documents")
    with op.batch_alter_table("board_documents") as batch:
        batch.drop_constraint("ck_board_documents_access_version", type_="check")
        batch.drop_constraint("ck_board_documents_standalone_owner", type_="check")
        batch.drop_constraint("ck_board_documents_linkage", type_="check")
        batch.drop_constraint(
            "fk_board_documents_org_owner_membership",
            type_="foreignkey",
        )
        batch.alter_column(
            "lesson_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
        batch.alter_column(
            "student_id",
            existing_type=sa.String(length=36),
            nullable=False,
        )
        batch.drop_column("access_version")
        batch.drop_column("guest_writes_enabled")
        batch.drop_column("title")
        batch.drop_column("owner_user_id")
