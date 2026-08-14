"""Scope ordered Board command clocks by durable client origin."""

import sqlalchemy as sa
from alembic import op

revision = "0014_board_origins"
down_revision = "0013_board_ordering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("board_command_batches") as batch:
        batch.drop_index("ix_board_commands_actor_lamport")
        batch.add_column(sa.Column("origin_id", sa.String(length=128), nullable=True))
        batch.create_index(
            "ix_board_commands_actor_lamport",
            [
                "organization_id",
                "board_document_id",
                "contract_actor_id",
                "origin_id",
                "lamport_max",
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table("board_command_batches") as batch:
        batch.drop_index("ix_board_commands_actor_lamport")
        batch.drop_column("origin_id")
        batch.create_index(
            "ix_board_commands_actor_lamport",
            [
                "organization_id",
                "board_document_id",
                "contract_actor_id",
                "lamport_max",
            ],
        )
