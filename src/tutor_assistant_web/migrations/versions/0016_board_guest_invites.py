"""Add standalone board guest invitations.

This is an expand migration. Invitation secrets are persisted only as keyed
HMAC-SHA-256 digests; raw join secrets never enter the database. Application
rollback may keep this table in place. A physical downgrade is blocked once an
invitation exists so an operational rollback cannot silently destroy guest
access metadata.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_board_guest_invites"
down_revision = "0015_standalone_boards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "board_invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("board_document_id", sa.String(length=128), nullable=False),
        sa.Column("secret_digest", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column(
            "write_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "credential_version",
            sa.BigInteger(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "access_version",
            sa.BigInteger(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "use_count",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(display_name)) > 0",
            name="ck_board_invitations_display_name",
        ),
        sa.CheckConstraint(
            "credential_version > 0",
            name="ck_board_invitations_credential_version",
        ),
        sa.CheckConstraint(
            "access_version > 0",
            name="ck_board_invitations_access_version",
        ),
        sa.CheckConstraint(
            "use_count >= 0",
            name="ck_board_invitations_use_count",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "board_document_id"],
            ["board_documents.organization_id", "board_documents.id"],
            name="fk_board_invitations_org_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("secret_digest", name="uq_board_invitations_secret_digest"),
    )
    op.create_index(
        "ix_board_invitations_org_board_created",
        "board_invitations",
        ["organization_id", "board_document_id", "created_at"],
    )
    op.create_index(
        "ix_board_invitations_expires",
        "board_invitations",
        ["expires_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    invitation_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM board_invitations")
    ).scalar_one()
    if int(invitation_count) > 0:
        raise RuntimeError(
            "Cannot downgrade standalone guest invitations while invitation rows exist. "
            "Roll back the application while keeping migration 0016, or explicitly "
            "revoke/export and remove invitation data first."
        )
    op.drop_index("ix_board_invitations_expires", table_name="board_invitations")
    op.drop_index("ix_board_invitations_org_board_created", table_name="board_invitations")
    op.drop_table("board_invitations")
