"""Users, organizations, memberships and tenant isolation."""

import sqlalchemy as sa
from alembic import op

revision = "0002_identity_tenancy"
down_revision = "0001_pilot"
branch_labels = None
depends_on = None

DEFAULT_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"])
    op.create_index("ix_organizations_active", "organizations", ["active"])
    op.execute(
        sa.text(
            "INSERT INTO organizations (id, name, slug, active, created_at) "
            "VALUES (:id, :name, :slug, :active, CURRENT_TIMESTAMP)"
        ).bindparams(
            id=DEFAULT_ORGANIZATION_ID, name="Tutor Workspace", slug="default", active=True
        )
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_active", "users", ["active"])
    op.create_table(
        "memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("role", sa.String(24), nullable=False, server_default="tutor"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
    )
    for column in ("organization_id", "user_id", "role", "active"):
        op.create_index(f"ix_memberships_{column}", "memberships", [column])

    for table in (
        "students",
        "lessons",
        "recording_assets",
        "processing_jobs",
        "material_artifacts",
    ):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    "organization_id",
                    sa.String(36),
                    nullable=False,
                    server_default=DEFAULT_ORGANIZATION_ID,
                )
            )
            batch.create_foreign_key(
                f"fk_{table}_organization_id",
                "organizations",
                ["organization_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch.create_index(f"ix_{table}_organization_id", ["organization_id"])


def downgrade() -> None:
    for table in (
        "material_artifacts",
        "processing_jobs",
        "recording_assets",
        "lessons",
        "students",
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_organization_id")
            batch.drop_constraint(f"fk_{table}_organization_id", type_="foreignkey")
            batch.drop_column("organization_id")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("organizations")
