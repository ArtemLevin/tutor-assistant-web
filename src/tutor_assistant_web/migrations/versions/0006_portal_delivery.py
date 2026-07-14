"""Recipient access, material deliveries and portal notifications."""

import sqlalchemy as sa
from alembic import op

revision = "0006_portal_delivery"
down_revision = "0005_materials_factory"
branch_labels = None
depends_on = None


def _tenant_column() -> sa.Column:
    return sa.Column(
        "organization_id",
        sa.String(36),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    with op.batch_alter_table("invitations") as batch:
        batch.add_column(
            sa.Column(
                "student_id",
                sa.String(36),
                sa.ForeignKey(
                    "students.id",
                    name="fk_invitations_student_id_students",
                    ondelete="CASCADE",
                ),
                nullable=True,
            )
        )
        batch.create_index("ix_invitations_student_id", ["student_id"])

    op.create_table(
        "student_access",
        sa.Column("id", sa.String(36), primary_key=True),
        _tenant_column(),
        sa.Column(
            "student_id",
            sa.String(36),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "organization_id",
            "student_id",
            "user_id",
            name="uq_student_access_org_student_user",
        ),
    )
    for column in ("organization_id", "student_id", "user_id", "role", "active"):
        op.create_index(f"ix_student_access_{column}", "student_access", [column])

    op.create_table(
        "material_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        _tenant_column(),
        sa.Column(
            "generation_run_id",
            sa.String(36),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            sa.String(36),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("publication_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "organization_id",
            "generation_run_id",
            "student_id",
            name="uq_delivery_org_run_student",
        ),
    )
    for column in ("organization_id", "generation_run_id", "student_id", "status"):
        op.create_index(f"ix_material_deliveries_{column}", "material_deliveries", [column])

    op.create_table(
        "user_notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        _tenant_column(),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            sa.String(36),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "delivery_id",
            sa.String(36),
            sa.ForeignKey("material_deliveries.id", ondelete="CASCADE"),
        ),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("dedup_key", sa.String(320), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
    )
    for column in (
        "organization_id",
        "user_id",
        "student_id",
        "delivery_id",
        "kind",
        "read_at",
    ):
        op.create_index(f"ix_user_notifications_{column}", "user_notifications", [column])


def downgrade() -> None:
    op.drop_table("user_notifications")
    op.drop_table("material_deliveries")
    op.drop_table("student_access")
    with op.batch_alter_table("invitations") as batch:
        batch.drop_index("ix_invitations_student_id")
        batch.drop_column("student_id")
