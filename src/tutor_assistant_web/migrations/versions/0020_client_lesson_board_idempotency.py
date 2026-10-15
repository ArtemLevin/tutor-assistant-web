"""Persist exactly one standalone board per desktop lesson and organization.

Nullable on legacy and manually created boards. Unique database index makes
repeated requests safe even when workers or browsers restart mid-response.
Downgrading with active keyed boards is blocked to preserve the idempotency
contract and avoid data loss.
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_client_lesson_board_idempotency"
down_revision = "0019_board_media_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("board_documents") as batch:
        batch.add_column(sa.Column("client_lesson_id", sa.String(length=32), nullable=True))
    op.create_index(
        "uq_board_documents_org_client_lesson",
        "board_documents",
        ["organization_id", "client_lesson_id"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM board_documents "
            "WHERE client_lesson_id IS NOT NULL"
        )
    ).scalar_one()
    if count:
        raise RuntimeError(
            "Cannot downgrade while lesson-keyed boards exist; keep schema 0020 "
            "during application rollback or explicitly migrate keyed boards first."
        )
    op.drop_index("uq_board_documents_org_client_lesson", table_name="board_documents")
    with op.batch_alter_table("board_documents") as batch:
        batch.drop_column("client_lesson_id")
