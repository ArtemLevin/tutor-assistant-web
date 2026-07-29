"""Board archive state and immutable lesson evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0012_board_evidence"
down_revision = "0011_board_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("board_documents") as batch:
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_board_documents_archived_at", ["archived_at"])

    with op.batch_alter_table("board_snapshots") as batch:
        batch.create_unique_constraint(
            "uq_board_snapshots_org_id",
            ["organization_id", "id"],
        )

    op.create_table(
        "board_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("board_document_id", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="1.0"),
        sa.Column("document_schema_version", sa.String(length=16), nullable=False),
        sa.Column("document_sha256", sa.String(length=64), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_storage_key", sa.String(length=1024), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_size", sa.Integer(), nullable=False),
        sa.Column("svg_storage_key", sa.String(length=1024), nullable=False),
        sa.Column("svg_sha256", sa.String(length=64), nullable=False),
        sa.Column("svg_size", sa.Integer(), nullable=False),
        sa.Column("png_storage_key", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("png_sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("png_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("geometry_summary", sa.JSON(), nullable=False),
        sa.Column("transcript_links", sa.JSON(), nullable=False),
        sa.Column("participants", sa.JSON(), nullable=False),
        sa.Column("operation_summary", sa.JSON(), nullable=False),
        sa.Column(
            "storage_status",
            sa.String(length=24),
            nullable=False,
            server_default="uploading",
        ),
        sa.Column("upload_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("finalized_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("revision >= 0", name="ck_board_evidence_revision"),
        sa.CheckConstraint(
            "storage_status IN ('uploading', 'available', 'quarantined')",
            name="ck_board_evidence_storage_status",
        ),
        sa.ForeignKeyConstraint(
            ["finalized_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "board_document_id"],
            ["board_documents.organization_id", "board_documents.id"],
            name="fk_board_evidence_org_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "snapshot_id"],
            ["board_snapshots.organization_id", "board_snapshots.id"],
            name="fk_board_evidence_org_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "student_id", "lesson_id"],
            ["lessons.organization_id", "lessons.student_id", "lessons.id"],
            name="fk_board_evidence_org_student_lesson",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "board_document_id",
            "revision",
            name="uq_board_evidence_org_document_revision",
        ),
        sa.UniqueConstraint(
            "manifest_storage_key",
            name="uq_board_evidence_manifest_key",
        ),
    )
    op.create_index(
        "ix_board_evidence_board_document_id",
        "board_evidence",
        ["board_document_id"],
    )
    op.create_index(
        "ix_board_evidence_finalized_by_user_id",
        "board_evidence",
        ["finalized_by_user_id"],
    )
    op.create_index("ix_board_evidence_lesson_id", "board_evidence", ["lesson_id"])
    op.create_index("ix_board_evidence_organization_id", "board_evidence", ["organization_id"])
    op.create_index(
        "ix_board_evidence_org_lesson_finalized",
        "board_evidence",
        ["organization_id", "lesson_id", "finalized_at"],
    )
    op.create_index("ix_board_evidence_published_at", "board_evidence", ["published_at"])
    op.create_index("ix_board_evidence_snapshot_id", "board_evidence", ["snapshot_id"])
    op.create_index("ix_board_evidence_storage_status", "board_evidence", ["storage_status"])
    op.create_index("ix_board_evidence_student_id", "board_evidence", ["student_id"])


def downgrade() -> None:
    op.drop_table("board_evidence")
    with op.batch_alter_table("board_snapshots") as batch:
        batch.drop_constraint("uq_board_snapshots_org_id", type_="unique")
    with op.batch_alter_table("board_documents") as batch:
        batch.drop_index("ix_board_documents_archived_at")
        batch.drop_column("archived_at")
