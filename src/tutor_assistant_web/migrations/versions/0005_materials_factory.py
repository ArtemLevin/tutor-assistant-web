"""Versioned materials factory and review lifecycle."""

import sqlalchemy as sa
from alembic import op

revision = "0005_materials_factory"
down_revision = "0004_post_lesson_automation"
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
    op.create_table(
        "lesson_evidence_bundles",
        sa.Column("id", sa.String(36), primary_key=True),
        _tenant_column(),
        sa.Column("lesson_id", sa.String(36), sa.ForeignKey("lessons.id"), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="1.0"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "content_hash", name="uq_evidence_org_hash"),
    )
    for column in ("organization_id", "lesson_id", "content_hash"):
        op.create_index(f"ix_lesson_evidence_bundles_{column}", "lesson_evidence_bundles", [column])

    op.create_table(
        "generation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        _tenant_column(),
        sa.Column("lesson_id", sa.String(36), sa.ForeignKey("lessons.id"), nullable=False),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "evidence_bundle_id",
            sa.String(36),
            sa.ForeignKey("lesson_evidence_bundles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="building"),
        sa.Column("generator", sa.String(100), nullable=False, server_default=""),
        sa.Column("engine", sa.String(100), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(40), nullable=False, server_default="materials-v1"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    for column in (
        "organization_id",
        "lesson_id",
        "job_id",
        "evidence_bundle_id",
        "idempotency_key",
        "status",
    ):
        op.create_index(f"ix_generation_runs_{column}", "generation_runs", [column])

    op.create_table(
        "artifact_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        _tenant_column(),
        sa.Column("lesson_id", sa.String(36), sa.ForeignKey("lessons.id"), nullable=False),
        sa.Column(
            "generation_run_id",
            sa.String(36),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="review_required"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("generation_run_id", "kind", name="uq_artifact_version_run_kind"),
    )
    for column in ("organization_id", "lesson_id", "generation_run_id", "kind", "status"):
        op.create_index(f"ix_artifact_versions_{column}", "artifact_versions", [column])

    op.create_table(
        "build_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        _tenant_column(),
        sa.Column(
            "generation_run_id",
            sa.String(36),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("organization_id", "generation_run_id", "stage", "status"):
        op.create_index(f"ix_build_logs_{column}", "build_logs", [column])


def downgrade() -> None:
    op.drop_table("build_logs")
    op.drop_table("artifact_versions")
    op.drop_table("generation_runs")
    op.drop_table("lesson_evidence_bundles")
