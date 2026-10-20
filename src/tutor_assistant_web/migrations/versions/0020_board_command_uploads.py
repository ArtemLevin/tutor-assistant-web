"""Stage large ordered board envelopes without exposing partial revisions."""

import sqlalchemy as sa
from alembic import op

revision = "0020_board_command_uploads"
down_revision = "0019_board_media_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "board_command_uploads",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("board_document_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("access_epoch", sa.String(512), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("byte_size > 0 AND byte_size <= 50331648", name="ck_board_upload_bytes"),
        sa.CheckConstraint("chunk_count > 0 AND chunk_count <= 96", name="ck_board_upload_chunks"),
        sa.ForeignKeyConstraint(
            ["organization_id", "board_document_id"],
            ["board_documents.organization_id", "board_documents.id"],
            ondelete="CASCADE",
            name="fk_board_command_uploads_org_document",
        ),
    )
    op.create_index("ix_board_upload_expiry", "board_command_uploads", ["expires_at"])
    op.create_index(
        "ix_board_upload_actor",
        "board_command_uploads",
        ["organization_id", "board_document_id", "actor_id"],
    )
    op.create_table(
        "board_command_upload_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("upload_id", sa.String(128), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "byte_size > 0 AND byte_size <= 524288",
            name="ck_board_upload_chunk_bytes",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["board_command_uploads.id"],
            ondelete="CASCADE",
            name="fk_board_command_chunk_upload",
        ),
        sa.UniqueConstraint("upload_id", "chunk_index", name="uq_board_upload_part"),
    )
    op.create_index(
        "ix_board_command_upload_chunks_upload_id",
        "board_command_upload_chunks",
        ["upload_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_board_command_upload_chunks_upload_id",
        table_name="board_command_upload_chunks",
    )
    op.drop_table("board_command_upload_chunks")
    op.drop_index("ix_board_upload_actor", table_name="board_command_uploads")
    op.drop_index("ix_board_upload_expiry", table_name="board_command_uploads")
    op.drop_table("board_command_uploads")
