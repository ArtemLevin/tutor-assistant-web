"""Persist ordered Board command Lamport ranges."""

import sqlalchemy as sa
from alembic import op

revision = "0013_board_ordering"
down_revision = "0012_board_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("board_command_batches") as batch:
        batch.add_column(sa.Column("lamport_min", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("lamport_max", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "ck_board_commands_lamport_pair",
            "(lamport_min IS NULL AND lamport_max IS NULL) OR "
            "(lamport_min IS NOT NULL AND lamport_max IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_board_commands_lamport_range",
            "lamport_min IS NULL OR (lamport_min > 0 AND lamport_max >= lamport_min)",
        )
        batch.create_index(
            "ix_board_commands_actor_lamport",
            [
                "organization_id",
                "board_document_id",
                "contract_actor_id",
                "lamport_max",
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table("board_command_batches") as batch:
        batch.drop_index("ix_board_commands_actor_lamport")
        batch.drop_constraint("ck_board_commands_lamport_range", type_="check")
        batch.drop_constraint("ck_board_commands_lamport_pair", type_="check")
        batch.drop_column("lamport_max")
        batch.drop_column("lamport_min")
