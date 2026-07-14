from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tutor_assistant_web.app import create_app
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.audit.models import AuditEvent
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.modules.identity.models import (
    Membership,
    MembershipRole,
    Organization,
    User,
)


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def login(client: TestClient) -> None:
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf_from(page.text),
            "email": "admin@localhost",
            "password": "test-password",
            "next": "/",
        },
    )
    assert response.status_code == 303


def test_admin_invites_tutor_and_audit_is_visible(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'admin.db'}")
    settings = Settings(
        app_secret_key="test-secret",
        database_url=f"sqlite:///{tmp_path / 'admin.db'}",
        seed_demo_data=False,
        bootstrap_admin_password="test-password",
    )
    app = create_app(settings, database)

    with TestClient(app, follow_redirects=False) as client:
        login(client)
        team = client.get("/settings/team")
        assert team.status_code == 200
        invited = client.post(
            "/settings/team/invitations",
            data={
                "csrf_token": csrf_from(team.text),
                "email": "new-tutor@example.test",
                "role": "tutor",
            },
        )
        assert invited.status_code == 201
        token_match = re.search(r"/accept-invitation/([A-Za-z0-9_-]+)", invited.text)
        assert token_match
        token = token_match.group(1)

        client.cookies.clear()
        invitation_page = client.get(f"/accept-invitation/{token}")
        accepted = client.post(
            f"/accept-invitation/{token}",
            data={
                "csrf_token": csrf_from(invitation_page.text),
                "full_name": "Новый преподаватель",
                "password": "strong-password",
            },
        )
        assert accepted.status_code == 303
        assert client.get("/").status_code == 200
        assert client.get("/settings/team").status_code == 403

        with database.sessions() as session:
            invited_user = session.scalar(
                select(User).where(User.email == "new-tutor@example.test")
            )
            assert invited_user is not None
            invited_membership = session.scalar(
                select(Membership).where(Membership.user_id == invited_user.id)
            )
            assert invited_membership is not None
            membership_id = invited_membership.id

        with TestClient(app, follow_redirects=False) as admin_client:
            login(admin_client)
            team = admin_client.get("/settings/team")
            changed = admin_client.post(
                f"/settings/team/members/{membership_id}",
                data={
                    "csrf_token": csrf_from(team.text),
                    "role": "student",
                    "active": "on",
                },
            )
            assert changed.status_code == 303
            audit = admin_client.get("/settings/audit")
            assert audit.status_code == 200
            assert "invitation.created" in audit.text
            assert "membership.updated" in audit.text

        assert client.get("/").status_code == 403

    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 3


def test_user_can_switch_only_to_own_workspace(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'switch.db'}")
    settings = Settings(
        app_secret_key="test-secret",
        database_url=f"sqlite:///{tmp_path / 'switch.db'}",
        seed_demo_data=False,
        bootstrap_admin_password="test-password",
    )
    database.migrate()
    IdentityService(database).bootstrap(settings)
    with database.sessions() as session:
        user = session.scalar(select(User).where(User.email == "admin@localhost"))
        assert user is not None
        organization = Organization(name="Second Workspace", slug="second-workspace")
        session.add(organization)
        session.flush()
        session.add(
            Membership(
                organization_id=organization.id,
                user_id=user.id,
                role=MembershipRole.admin.value,
            )
        )
        session.commit()
        organization_id = organization.id

    with TestClient(create_app(settings, database), follow_redirects=False) as client:
        login(client)
        dashboard = client.get("/")
        switched = client.post(
            "/workspace/switch",
            data={"csrf_token": csrf_from(dashboard.text), "organization_id": organization_id},
        )
        assert switched.status_code == 303
        team = client.get("/settings/team")
        assert "Second Workspace" in team.text

        blocked = client.post(
            "/workspace/switch",
            data={"csrf_token": csrf_from(team.text), "organization_id": "unknown"},
        )
        assert blocked.status_code == 404
