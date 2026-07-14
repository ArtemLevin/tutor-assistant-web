from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, text

from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.modules.identity.models import (
    DEFAULT_ORGANIZATION_ID,
    Membership,
    MembershipRole,
    Organization,
    User,
)
from tutor_assistant_web.modules.students.application import StudentData, StudentService
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.shared.errors import NotFoundError, ValidationError


def test_password_authentication_and_tenant_scope(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'tenants.db'}")
    database.migrate()
    settings = Settings(seed_demo_data=False, bootstrap_admin_password="admin-password")
    identity = IdentityService(database)
    identity.bootstrap(settings)
    principal = identity.authenticate("admin@localhost", "admin-password")
    assert principal is not None
    assert principal.organization_id == DEFAULT_ORGANIZATION_ID
    assert identity.authenticate("admin@localhost", "wrong") is None

    second = Organization(name="Second Workspace", slug="second")
    user = User(
        email="second@example.test",
        full_name="Second Tutor",
        password_hash=identity.passwords.hash("second-password"),
    )
    with database.sessions() as session:
        session.add_all([second, user])
        session.flush()
        session.add(
            Membership(
                organization_id=second.id,
                user_id=user.id,
                role=MembershipRole.tutor.value,
            )
        )
        session.commit()

    first_student = StudentService(database, DEFAULT_ORGANIZATION_ID).create(
        StudentData(full_name="Первый ученик")
    )
    StudentService(database, second.id).create(StudentData(full_name="Второй ученик"))
    assert [item.full_name for item in StudentService(database, second.id).list_active()] == [
        "Второй ученик"
    ]
    try:
        StudentService(database, second.id).get(first_student.id)
    except NotFoundError:
        pass
    else:
        raise AssertionError("cross-tenant student access must be denied")


def test_legacy_pilot_database_is_upgraded_without_data_loss(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'legacy.db'}")
    database.migrate("0001_pilot")
    now = datetime.now(UTC).isoformat()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO students "
                "(id, full_name, grade, subject, goal, guardian_name, guardian_phone, "
                "email, social_links, hourly_rate, notes, active, created_at, updated_at) "
                "VALUES ('legacy', 'Legacy Student', '', 'Math', '', '', '', '', '', "
                "0, '', 1, :now, :now)"
            ),
            {"now": now},
        )
    database.migrate()
    with database.sessions() as session:
        student = session.scalar(select(Student).where(Student.id == "legacy"))
        assert student is not None
        assert student.organization_id == DEFAULT_ORGANIZATION_ID


def test_invitation_acceptance_and_last_admin_guard(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'invitations.db'}")
    database.migrate()
    settings = Settings(seed_demo_data=False, bootstrap_admin_password="admin-password")
    identity = IdentityService(database)
    identity.bootstrap(settings)
    admin = identity.authenticate("admin@localhost", "admin-password")
    assert admin is not None

    created = identity.create_invitation(
        admin.organization_id,
        admin.user_id,
        "tutor@example.test",
        MembershipRole.tutor.value,
        24,
    )
    assert created.invitation.token_hash != created.token
    invited = identity.accept_invitation(created.token, "Новый преподаватель", "strong-password")
    assert invited.role == MembershipRole.tutor.value
    assert invited.organization_id == admin.organization_id

    admin_membership = next(
        item
        for item in identity.workspaces(admin.user_id)
        if item.organization_id == admin.organization_id
    )
    try:
        identity.update_membership(
            admin.organization_id,
            admin.user_id,
            admin_membership.id,
            MembershipRole.tutor.value,
            True,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("the last administrator must remain active")
