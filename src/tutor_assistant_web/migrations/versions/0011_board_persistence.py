"""Persistent TutorBoard documents, command revisions, and snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0011_board_persistence"
down_revision = "0010_security_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("lessons") as batch:
        batch.create_unique_constraint(
            "uq_lessons_org_student_id",
            ["organization_id", "student_id", "id"],
        )

    op.create_table(
        "board_documents",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("student_id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="1.0"),
        sa.Column("current_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "current_document_sha256",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
        sa.Column("last_snapshot_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("commands_since_snapshot", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes_since_snapshot", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "bytes_since_snapshot >= 0",
            name="ck_board_documents_bytes_since_snapshot",
        ),
        sa.CheckConstraint(
            "commands_since_snapshot >= 0",
            name="ck_board_documents_commands_since_snapshot",
        ),
        sa.CheckConstraint(
            "current_revision >= 0",
            name="ck_board_documents_current_revision",
        ),
        sa.CheckConstraint(
            "last_snapshot_revision >= 0 AND last_snapshot_revision <= current_revision",
            name="ck_board_documents_snapshot_revision",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "student_id", "lesson_id"],
            ["lessons.organization_id", "lessons.student_id", "lessons.id"],
            name="fk_board_documents_org_student_lesson",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organization_id", "id"),
        sa.UniqueConstraint(
            "organization_id",
            "lesson_id",
            name="uq_board_documents_org_lesson",
        ),
    )
    op.create_index(
        "ix_board_documents_lesson_id",
        "board_documents",
        ["lesson_id"],
    )
    op.create_index(
        "ix_board_documents_organization_id",
        "board_documents",
        ["organization_id"],
    )
    op.create_index(
        "ix_board_documents_org_student_updated",
        "board_documents",
        ["organization_id", "student_id", "updated_at"],
    )
    op.create_index(
        "ix_board_documents_purge",
        "board_documents",
        ["deleted_at", "purge_after"],
    )
    op.create_index(
        "ix_board_documents_purge_after",
        "board_documents",
        ["purge_after"],
    )
    op.create_index(
        "ix_board_documents_student_id",
        "board_documents",
        ["student_id"],
    )

    op.create_table(
        "board_command_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("board_document_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("contract_actor_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="1.0"),
        sa.Column("expected_document_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_size", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "base_revision >= 0",
            name="ck_board_commands_base_revision",
        ),
        sa.CheckConstraint("payload_size > 0", name="ck_board_commands_payload_size"),
        sa.CheckConstraint("revision > 0", name="ck_board_commands_revision"),
        sa.CheckConstraint(
            "revision = base_revision + 1",
            name="ck_board_commands_revision_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "board_document_id"],
            ["board_documents.organization_id", "board_documents.id"],
            name="fk_board_commands_org_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "board_document_id",
            "idempotency_key",
            name="uq_board_commands_org_document_idempotency",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "board_document_id",
            "revision",
            name="uq_board_commands_org_document_revision",
        ),
    )
    op.create_index(
        "ix_board_command_batches_actor_user_id",
        "board_command_batches",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_board_command_batches_board_document_id",
        "board_command_batches",
        ["board_document_id"],
    )
    op.create_index(
        "ix_board_command_batches_organization_id",
        "board_command_batches",
        ["organization_id"],
    )
    op.create_index(
        "ix_board_commands_org_document_created",
        "board_command_batches",
        ["organization_id", "board_document_id", "created_at"],
    )

    op.create_table(
        "board_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("board_document_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="1.0"),
        sa.Column("document_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column(
            "storage_status",
            sa.String(length=24),
            nullable=False,
            server_default="uploading",
        ),
        sa.Column("upload_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("revision >= 0", name="ck_board_snapshots_revision"),
        sa.CheckConstraint("size > 0", name="ck_board_snapshots_size"),
        sa.CheckConstraint(
            "storage_status IN ('uploading', 'available', 'quarantined', 'deleted')",
            name="ck_board_snapshots_storage_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "board_document_id"],
            ["board_documents.organization_id", "board_documents.id"],
            name="fk_board_snapshots_org_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "board_document_id",
            "revision",
            name="uq_board_snapshots_org_document_revision",
        ),
        sa.UniqueConstraint("storage_key", name="uq_board_snapshots_storage_key"),
    )
    op.create_index(
        "ix_board_snapshots_board_document_id",
        "board_snapshots",
        ["board_document_id"],
    )
    op.create_index(
        "ix_board_snapshots_organization_id",
        "board_snapshots",
        ["organization_id"],
    )
    op.create_index(
        "ix_board_snapshots_org_document_created",
        "board_snapshots",
        ["organization_id", "board_document_id", "created_at"],
    )
    op.create_index(
        "ix_board_snapshots_purge",
        "board_snapshots",
        ["deleted_at", "purge_after"],
    )
    op.create_index(
        "ix_board_snapshots_purge_after",
        "board_snapshots",
        ["purge_after"],
    )
    op.create_index(
        "ix_board_snapshots_storage_status",
        "board_snapshots",
        ["storage_status"],
    )

    op.create_table(
        "board_geometry_imports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("board_document_id", sa.String(length=128), nullable=False),
        sa.Column("import_id", sa.String(length=128), nullable=False),
        sa.Column("command_id", sa.String(length=128), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="1.0"),
        sa.Column("request_id", sa.String(length=220), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("contract_sha256", sa.String(length=64), nullable=False),
        sa.Column("service_version", sa.String(length=32), nullable=False),
        sa.Column("api_version", sa.String(length=32), nullable=False),
        sa.Column("gir_schema_version", sa.String(length=32), nullable=False),
        sa.Column("gir_sha256", sa.String(length=64), nullable=False),
        sa.Column("layout_document_version", sa.String(length=32), nullable=False),
        sa.Column("layout_sha256", sa.String(length=64), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "base_revision >= 0",
            name="ck_board_geometry_imports_base_revision",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "board_document_id"],
            ["board_documents.organization_id", "board_documents.id"],
            name="fk_board_geometry_imports_org_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "board_document_id",
            "import_id",
            name="uq_board_geometry_imports_org_document_import",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "request_id",
            name="uq_board_geometry_imports_org_request",
        ),
    )
    op.create_index(
        "ix_board_geometry_imports_board_document_id",
        "board_geometry_imports",
        ["board_document_id"],
    )
    op.create_index(
        "ix_board_geometry_imports_organization_id",
        "board_geometry_imports",
        ["organization_id"],
    )
    op.create_index(
        "ix_board_geometry_imports_org_document_created",
        "board_geometry_imports",
        ["organization_id", "board_document_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_board_geometry_imports_org_document_created",
        table_name="board_geometry_imports",
    )
    op.drop_index(
        "ix_board_geometry_imports_organization_id",
        table_name="board_geometry_imports",
    )
    op.drop_index(
        "ix_board_geometry_imports_board_document_id",
        table_name="board_geometry_imports",
    )
    op.drop_table("board_geometry_imports")

    op.drop_index("ix_board_snapshots_storage_status", table_name="board_snapshots")
    op.drop_index("ix_board_snapshots_purge_after", table_name="board_snapshots")
    op.drop_index("ix_board_snapshots_purge", table_name="board_snapshots")
    op.drop_index(
        "ix_board_snapshots_org_document_created",
        table_name="board_snapshots",
    )
    op.drop_index(
        "ix_board_snapshots_organization_id",
        table_name="board_snapshots",
    )
    op.drop_index(
        "ix_board_snapshots_board_document_id",
        table_name="board_snapshots",
    )
    op.drop_table("board_snapshots")

    op.drop_index(
        "ix_board_commands_org_document_created",
        table_name="board_command_batches",
    )
    op.drop_index(
        "ix_board_command_batches_organization_id",
        table_name="board_command_batches",
    )
    op.drop_index(
        "ix_board_command_batches_board_document_id",
        table_name="board_command_batches",
    )
    op.drop_index(
        "ix_board_command_batches_actor_user_id",
        table_name="board_command_batches",
    )
    op.drop_table("board_command_batches")

    op.drop_index("ix_board_documents_student_id", table_name="board_documents")
    op.drop_index("ix_board_documents_purge_after", table_name="board_documents")
    op.drop_index("ix_board_documents_purge", table_name="board_documents")
    op.drop_index(
        "ix_board_documents_org_student_updated",
        table_name="board_documents",
    )
    op.drop_index(
        "ix_board_documents_organization_id",
        table_name="board_documents",
    )
    op.drop_index("ix_board_documents_lesson_id", table_name="board_documents")
    op.drop_table("board_documents")

    with op.batch_alter_table("lessons") as batch:
        batch.drop_constraint("uq_lessons_org_student_id", type_="unique")
