"""Add board-scoped immutable media asset authority."""

import sqlalchemy as sa
from alembic import op

revision = "0019_board_media_assets"
down_revision = "0018_practice_analytics_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "board_media_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("board_document_id", sa.String(length=128), nullable=False),
        sa.Column("asset_id", sa.String(length=128), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=256), nullable=False),
        sa.Column("intrinsic_width", sa.Integer(), nullable=False),
        sa.Column("intrinsic_height", sa.Integer(), nullable=False),
        sa.Column(
            "storage_status",
            sa.String(length=24),
            nullable=False,
            server_default="uploading",
        ),
        sa.Column("upload_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_actor_id", sa.String(length=128), nullable=False),
        sa.Column("first_referenced_revision", sa.Integer(), nullable=True),
        sa.Column("upload_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("byte_size > 0", name="ck_board_media_assets_byte_size"),
        sa.CheckConstraint(
            "intrinsic_width > 0 AND intrinsic_height > 0",
            name="ck_board_media_assets_dimensions",
        ),
        sa.CheckConstraint(
            "storage_status IN ('uploading', 'available', 'quarantined', 'deleted')",
            name="ck_board_media_assets_storage_status",
        ),
        sa.CheckConstraint(
            "first_referenced_revision IS NULL OR first_referenced_revision >= 0",
            name="ck_board_media_assets_first_revision",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "board_document_id"],
            ["board_documents.organization_id", "board_documents.id"],
            name="fk_board_media_assets_org_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_board_media_assets_creator",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "board_document_id",
            "asset_id",
            name="uq_board_media_assets_org_document_asset",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "board_document_id",
            "upload_idempotency_key",
            name="uq_board_media_assets_org_document_idempotency",
        ),
        sa.UniqueConstraint("storage_key", name="uq_board_media_assets_storage_key"),
    )
    op.create_index(
        "ix_board_media_assets_org_document_status",
        "board_media_assets",
        ["organization_id", "board_document_id", "storage_status"],
    )
    op.create_index(
        "ix_board_media_assets_purge",
        "board_media_assets",
        ["deleted_at", "purge_after"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    count = int(connection.execute(sa.text("SELECT COUNT(*) FROM board_media_assets")).scalar_one())
    if count:
        raise RuntimeError(
            "Cannot downgrade board media storage while media assets exist. "
            "Export or purge board media first."
        )
    op.drop_index("ix_board_media_assets_purge", table_name="board_media_assets")
    op.drop_index(
        "ix_board_media_assets_org_document_status",
        table_name="board_media_assets",
    )
    op.drop_table("board_media_assets")
