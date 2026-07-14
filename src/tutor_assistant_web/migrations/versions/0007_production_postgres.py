"""Production PostgreSQL constraints and query indexes."""

from alembic import op

revision = "0007_production_postgres"
down_revision = "0006_portal_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("students") as batch:
        batch.create_unique_constraint("uq_students_org_id", ["organization_id", "id"])
        batch.create_index(
            "ix_students_org_active_name",
            ["organization_id", "active", "full_name"],
        )

    with op.batch_alter_table("lessons") as batch:
        batch.create_index("ix_lessons_org_starts_at", ["organization_id", "starts_at"])

    with op.batch_alter_table("generation_runs") as batch:
        batch.create_unique_constraint("uq_generation_runs_org_id", ["organization_id", "id"])

    with op.batch_alter_table("material_deliveries") as batch:
        batch.create_unique_constraint("uq_material_deliveries_org_id", ["organization_id", "id"])
        batch.create_foreign_key(
            "fk_material_deliveries_org_student",
            "students",
            ["organization_id", "student_id"],
            ["organization_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_material_deliveries_org_run",
            "generation_runs",
            ["organization_id", "generation_run_id"],
            ["organization_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_check_constraint(
            "ck_material_deliveries_status",
            "status IN ('pending', 'available', 'revoked')",
        )
        batch.create_index(
            "ix_deliveries_portal",
            ["organization_id", "student_id", "status", "published_at"],
        )

    with op.batch_alter_table("invitations") as batch:
        batch.create_foreign_key(
            "fk_invitations_org_student",
            "students",
            ["organization_id", "student_id"],
            ["organization_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_check_constraint(
            "ck_invitations_role",
            "role IN ('admin', 'tutor', 'student', 'parent')",
        )
        batch.create_index(
            "ix_invitations_pending_lookup",
            ["organization_id", "email", "student_id", "accepted_at", "revoked_at"],
        )

    with op.batch_alter_table("student_access") as batch:
        batch.create_foreign_key(
            "fk_student_access_org_student",
            "students",
            ["organization_id", "student_id"],
            ["organization_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_check_constraint("ck_student_access_role", "role IN ('student', 'parent')")

    with op.batch_alter_table("user_notifications") as batch:
        batch.create_foreign_key(
            "fk_user_notifications_org_student",
            "students",
            ["organization_id", "student_id"],
            ["organization_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_user_notifications_org_delivery",
            "material_deliveries",
            ["organization_id", "delivery_id"],
            ["organization_id", "id"],
            ondelete="CASCADE",
        )
        batch.create_check_constraint(
            "ck_user_notifications_kind",
            "kind IN ('material_available', 'material_replaced', 'material_revoked')",
        )
        batch.create_index(
            "ix_notifications_inbox",
            ["organization_id", "user_id", "read_at", "created_at"],
        )

    with op.batch_alter_table("outbox_events") as batch:
        batch.create_check_constraint(
            "ck_outbox_events_status",
            "status IN ('pending', 'dispatching', 'completed', 'dead')",
        )
        batch.create_index("ix_outbox_claim", ["status", "available_at", "created_at"])
        batch.create_index("ix_outbox_stale", ["status", "updated_at"])


def downgrade() -> None:
    with op.batch_alter_table("outbox_events") as batch:
        batch.drop_index("ix_outbox_stale")
        batch.drop_index("ix_outbox_claim")
        batch.drop_constraint("ck_outbox_events_status", type_="check")

    with op.batch_alter_table("user_notifications") as batch:
        batch.drop_index("ix_notifications_inbox")
        batch.drop_constraint("ck_user_notifications_kind", type_="check")
        batch.drop_constraint("fk_user_notifications_org_delivery", type_="foreignkey")
        batch.drop_constraint("fk_user_notifications_org_student", type_="foreignkey")

    with op.batch_alter_table("student_access") as batch:
        batch.drop_constraint("ck_student_access_role", type_="check")
        batch.drop_constraint("fk_student_access_org_student", type_="foreignkey")

    with op.batch_alter_table("invitations") as batch:
        batch.drop_index("ix_invitations_pending_lookup")
        batch.drop_constraint("ck_invitations_role", type_="check")
        batch.drop_constraint("fk_invitations_org_student", type_="foreignkey")

    with op.batch_alter_table("material_deliveries") as batch:
        batch.drop_index("ix_deliveries_portal")
        batch.drop_constraint("ck_material_deliveries_status", type_="check")
        batch.drop_constraint("fk_material_deliveries_org_run", type_="foreignkey")
        batch.drop_constraint("fk_material_deliveries_org_student", type_="foreignkey")
        batch.drop_constraint("uq_material_deliveries_org_id", type_="unique")

    with op.batch_alter_table("generation_runs") as batch:
        batch.drop_constraint("uq_generation_runs_org_id", type_="unique")

    with op.batch_alter_table("lessons") as batch:
        batch.drop_index("ix_lessons_org_starts_at")

    with op.batch_alter_table("students") as batch:
        batch.drop_index("ix_students_org_active_name")
        batch.drop_constraint("uq_students_org_id", type_="unique")
