from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from pwdlib import PasswordHash
from sqlalchemy import select

from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.identity.models import (
    DEFAULT_ORGANIZATION_ID,
    Membership,
    MembershipRole,
    Organization,
    User,
)


@dataclass(frozen=True)
class Principal:
    user_id: str
    organization_id: str
    organization_name: str
    role: str
    email: str
    full_name: str


class IdentityService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.passwords = PasswordHash.recommended()

    def bootstrap(self, settings: Settings) -> None:
        email = settings.bootstrap_admin_email.strip().lower()
        with self.database.sessions() as session:
            organization = session.get(Organization, DEFAULT_ORGANIZATION_ID)
            if organization is None:
                organization = Organization(
                    id=DEFAULT_ORGANIZATION_ID,
                    name=settings.bootstrap_organization_name.strip() or "Tutor Workspace",
                    slug=settings.bootstrap_organization_slug.strip().lower() or "default",
                )
                session.add(organization)
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    email=email,
                    full_name=settings.bootstrap_admin_name.strip() or "Администратор",
                    password_hash=self.passwords.hash(settings.effective_bootstrap_password),
                )
                session.add(user)
                session.flush()
            membership = session.scalar(
                select(Membership).where(
                    Membership.organization_id == organization.id,
                    Membership.user_id == user.id,
                )
            )
            if membership is None:
                session.add(
                    Membership(
                        organization_id=organization.id,
                        user_id=user.id,
                        role=MembershipRole.admin.value,
                    )
                )
            session.commit()

    def authenticate(self, email: str, password: str) -> Principal | None:
        with self.database.sessions() as session:
            row = session.execute(
                select(User, Membership, Organization)
                .join(Membership, Membership.user_id == User.id)
                .join(Organization, Organization.id == Membership.organization_id)
                .where(
                    User.email == email.strip().lower(),
                    User.active.is_(True),
                    Membership.active.is_(True),
                    Organization.active.is_(True),
                )
                .order_by(Membership.created_at)
            ).first()
            if row is None:
                return None
            user, membership, organization = row
            if not self.passwords.verify(password, user.password_hash):
                return None
            user.last_login_at = datetime.now(UTC)
            session.commit()
            return Principal(
                user_id=user.id,
                organization_id=organization.id,
                organization_name=organization.name,
                role=membership.role,
                email=user.email,
                full_name=user.full_name,
            )
