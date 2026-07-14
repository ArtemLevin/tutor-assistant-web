"""Workspace invitations and audit log."""

import sqlalchemy as sa
from alembic import op

revision = "0003_workspace_admin"
down_revision = "0002_identity_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Align legacy explicit indexes with the model-level unique indexes. The
    # underlying UNIQUE constraints remain intact during replacement.
    for table, column in (
        ("lessons", "bbb_meeting_id"),
        ("organizations", "slug"),
        ("recording_assets", "record_id"),
        ("users", "email"),
    ):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.create_index(f"ix_{table}_{column}", table, [column], unique=True)

    op.create_table(
        "invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("role", sa.String(24), nullable=False, server_default="tutor"),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "invited_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("organization_id", "email", "role", "expires_at"):
        op.create_index(f"ix_invitations_{column}", "invitations", [column])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in (
        "organization_id",
        "actor_user_id",
        "action",
        "entity_type",
        "entity_id",
        "created_at",
    ):
        op.create_index(f"ix_audit_events_{column}", "audit_events", [column])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("invitations")
    for table, column in (
        ("lessons", "bbb_meeting_id"),
        ("organizations", "slug"),
        ("recording_assets", "record_id"),
        ("users", "email"),
    ):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.create_index(f"ix_{table}_{column}", table, [column], unique=False)
