from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.identity.models import (
    DEFAULT_ORGANIZATION_ID,
    Invitation,
    Membership,
    MembershipRole,
    Organization,
    User,
)
from tutor_assistant_web.shared.errors import ConflictError, NotFoundError, ValidationError


@dataclass(frozen=True)
class Principal:
    user_id: str
    organization_id: str
    organization_name: str
    role: str
    email: str
    full_name: str


@dataclass(frozen=True)
class CreatedInvitation:
    invitation: Invitation
    token: str


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
        if not password or len(password) > 1024:
            return None
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

    def workspaces(self, user_id: str) -> list[Membership]:
        with self.database.sessions() as session:
            return list(
                session.scalars(
                    select(Membership)
                    .options(selectinload(Membership.organization))
                    .join(Organization, Organization.id == Membership.organization_id)
                    .where(
                        Membership.user_id == user_id,
                        Membership.active.is_(True),
                        Organization.active.is_(True),
                    )
                    .order_by(Organization.name)
                )
            )

    def switch_workspace(self, user_id: str, organization_id: str) -> Principal:
        with self.database.sessions() as session:
            row = session.execute(
                select(User, Membership, Organization)
                .join(Membership, Membership.user_id == User.id)
                .join(Organization, Organization.id == Membership.organization_id)
                .where(
                    User.id == user_id,
                    User.active.is_(True),
                    Membership.organization_id == organization_id,
                    Membership.active.is_(True),
                    Organization.active.is_(True),
                )
            ).first()
            if row is None:
                raise NotFoundError("Рабочее пространство недоступно")
            user, membership, organization = row
            return self._principal(user, membership, organization)

    def team(self, organization_id: str) -> tuple[list[Membership], list[Invitation]]:
        with self.database.sessions() as session:
            memberships = list(
                session.scalars(
                    select(Membership)
                    .options(selectinload(Membership.user))
                    .where(Membership.organization_id == organization_id)
                    .order_by(Membership.active.desc(), Membership.created_at)
                )
            )
            invitations = list(
                session.scalars(
                    select(Invitation)
                    .options(selectinload(Invitation.invited_by))
                    .where(Invitation.organization_id == organization_id)
                    .order_by(Invitation.created_at.desc())
                    .limit(100)
                )
            )
            return memberships, invitations

    def create_invitation(
        self,
        organization_id: str,
        actor_user_id: str,
        email: str,
        role: str,
        ttl_hours: int,
    ) -> CreatedInvitation:
        normalized_email = email.strip().lower()
        self._validate_email(normalized_email)
        self._validate_role(role)
        token = secrets.token_urlsafe(32)
        token_hash = self._token_hash(token)
        now = datetime.now(UTC)
        with self.database.sessions() as session:
            existing_user = session.scalar(select(User).where(User.email == normalized_email))
            if existing_user and session.scalar(
                select(Membership.id).where(
                    Membership.organization_id == organization_id,
                    Membership.user_id == existing_user.id,
                    Membership.active.is_(True),
                )
            ):
                raise ConflictError("Пользователь уже состоит в этой организации")
            pending = list(
                session.scalars(
                    select(Invitation).where(
                        Invitation.organization_id == organization_id,
                        Invitation.email == normalized_email,
                        Invitation.accepted_at.is_(None),
                        Invitation.revoked_at.is_(None),
                    )
                )
            )
            for item in pending:
                item.revoked_at = now
            invitation = Invitation(
                organization_id=organization_id,
                email=normalized_email,
                role=role,
                token_hash=token_hash,
                invited_by_user_id=actor_user_id,
                expires_at=now + timedelta(hours=ttl_hours),
            )
            session.add(invitation)
            session.commit()
            return CreatedInvitation(invitation=invitation, token=token)

    def invitation(self, token: str) -> Invitation:
        with self.database.sessions() as session:
            invitation = session.scalar(
                select(Invitation)
                .options(selectinload(Invitation.organization))
                .where(Invitation.token_hash == self._token_hash(token))
                .with_for_update()
            )
            self._ensure_invitation_active(invitation)
            return invitation

    def accept_invitation(
        self,
        token: str,
        full_name: str,
        password: str,
    ) -> Principal:
        now = datetime.now(UTC)
        with self.database.sessions() as session:
            invitation = session.scalar(
                select(Invitation)
                .options(selectinload(Invitation.organization))
                .where(Invitation.token_hash == self._token_hash(token))
            )
            self._ensure_invitation_active(invitation)
            assert invitation is not None
            if len(password) > 1024:
                raise ValidationError("Некорректный пароль")
            user = session.scalar(select(User).where(User.email == invitation.email))
            if user is None:
                if len(full_name.strip()) < 2:
                    raise ValidationError("Укажите имя")
                if len(password) < 10:
                    raise ValidationError("Пароль должен содержать минимум 10 символов")
                user = User(
                    email=invitation.email,
                    full_name=full_name.strip()[:160],
                    password_hash=self.passwords.hash(password),
                )
                session.add(user)
                session.flush()
            elif not user.active or not self.passwords.verify(password, user.password_hash):
                raise ValidationError("Для существующей учётной записи укажите действующий пароль")
            membership = session.scalar(
                select(Membership).where(
                    Membership.organization_id == invitation.organization_id,
                    Membership.user_id == user.id,
                )
            )
            if membership is None:
                membership = Membership(
                    organization_id=invitation.organization_id,
                    user_id=user.id,
                    role=invitation.role,
                )
                session.add(membership)
            else:
                membership.active = True
                membership.role = invitation.role
            invitation.accepted_at = now
            session.commit()
            return self._principal(user, membership, invitation.organization)

    def update_membership(
        self,
        organization_id: str,
        actor_user_id: str,
        membership_id: str,
        role: str,
        active: bool,
    ) -> Membership:
        self._validate_role(role)
        with self.database.sessions() as session:
            membership = session.scalar(
                select(Membership)
                .where(
                    Membership.id == membership_id,
                    Membership.organization_id == organization_id,
                )
                .with_for_update()
            )
            if membership is None:
                raise NotFoundError("Участник не найден")
            if membership.user_id == actor_user_id and not active:
                raise ValidationError("Нельзя отключить собственное членство")
            removes_admin = membership.role == MembershipRole.admin.value and (
                role != MembershipRole.admin.value or not active
            )
            if removes_admin and self._active_admin_count(session, organization_id) <= 1:
                raise ValidationError("В организации должен оставаться хотя бы один администратор")
            membership.role = role
            membership.active = active
            session.commit()
            return membership

    def revoke_invitation(self, organization_id: str, invitation_id: str) -> Invitation:
        with self.database.sessions() as session:
            invitation = session.scalar(
                select(Invitation)
                .where(
                    Invitation.id == invitation_id,
                    Invitation.organization_id == organization_id,
                )
                .with_for_update()
            )
            if invitation is None:
                raise NotFoundError("Приглашение не найдено")
            if invitation.accepted_at is None:
                invitation.revoked_at = datetime.now(UTC)
            session.commit()
            return invitation

    @staticmethod
    def _principal(user: User, membership: Membership, organization: Organization) -> Principal:
        return Principal(
            user_id=user.id,
            organization_id=organization.id,
            organization_name=organization.name,
            role=membership.role,
            email=user.email,
            full_name=user.full_name,
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _validate_email(email: str) -> None:
        if "@" not in email or len(email) > 254:
            raise ValidationError("Укажите корректный email")

    @staticmethod
    def _validate_role(role: str) -> None:
        if role not in {item.value for item in MembershipRole}:
            raise ValidationError("Некорректная роль")

    @staticmethod
    def _ensure_invitation_active(invitation: Invitation | None) -> None:
        if invitation is None:
            raise NotFoundError("Приглашение не найдено")
        expires_at = invitation.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if invitation.accepted_at or invitation.revoked_at or expires_at <= datetime.now(UTC):
            raise ValidationError("Приглашение уже использовано или срок его действия истёк")

    @staticmethod
    def _active_admin_count(session, organization_id: str) -> int:
        return len(
            list(
                session.scalars(
                    select(Membership)
                    .where(
                        Membership.organization_id == organization_id,
                        Membership.role == MembershipRole.admin.value,
                        Membership.active.is_(True),
                    )
                    .order_by(Membership.id)
                    .with_for_update()
                )
            )
        )
