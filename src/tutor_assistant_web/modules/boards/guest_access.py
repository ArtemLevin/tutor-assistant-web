from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select

from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.models import BoardDocument, BoardInvitation
from tutor_assistant_web.modules.boards.standalone_contracts import (
    GuestBoardAccessContext,
    TeacherBoardAccessContext,
)
from tutor_assistant_web.modules.identity.application import Principal
from tutor_assistant_web.shared.models import new_id

_GUEST_ROLE = "student"
_GUEST_SESSION_VERSION = 1
_GUEST_CAPABILITIES_BASE = ("board.read", "collaboration.connect")
_GUEST_CAPABILITIES_WRITE = ("board.write", "board.snapshot.write")
_TEACHER_CAPABILITIES_BASE = (
    "board.read",
    "collaboration.connect",
    "board.export",
    "board.history.read",
    "board.invites.manage",
    "board.archive",
    "board.delete",
)
_TEACHER_CAPABILITIES_WRITE = ("board.write", "board.snapshot.write")


class InvitationLinkInvalid(RuntimeError):
    pass


class GuestSessionInvalid(RuntimeError):
    pass


class GuestSessionVersionMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class GuestPrincipal:
    """Internal board-scoped principal. Tenant identifiers never enter the public context."""

    user_id: str
    organization_id: str
    organization_name: str
    role: str
    email: str
    full_name: str
    board_id: str
    invitation_id: str
    credential_version: int
    capabilities: frozenset[str]
    csrf_token: str
    cache_scope_id: str
    access_epoch: str
    access_expires_at: datetime | None

    @property
    def can_write(self) -> bool:
        return "board.write" in self.capabilities


@dataclass(frozen=True)
class GuestSessionIssue:
    invitation: BoardInvitation
    document: BoardDocument
    actor_id: str
    cookie_value: str
    cookie_max_age: int


class BoardGuestAccessService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        secret = settings.app_secret_key.encode("utf-8")
        self._invite_pepper = hmac.new(
            secret,
            b"tutorboard/standalone/invitation-pepper/v1",
            hashlib.sha256,
        ).digest()
        self._scope_key = hmac.new(
            secret,
            b"tutorboard/standalone/scope-key/v1",
            hashlib.sha256,
        ).digest()
        self._session_serializer = URLSafeTimedSerializer(
            settings.app_secret_key,
            salt="tutorboard-standalone-guest-session-v1",
        )

    def create_invitation(
        self,
        document_id: str,
        organization_id: str,
        *,
        display_name: str,
        write_enabled: bool,
        expires_at: datetime | None,
    ) -> tuple[BoardInvitation, str]:
        name = self._normalize_display_name(display_name)
        expires = self._normalize_expiry(expires_at)
        raw_secret = self._new_secret()
        with self.database.sessions() as session:
            document = self._standalone_document(
                session,
                document_id,
                organization_id,
                for_update=True,
            )
            invitation = BoardInvitation(
                id=new_id(),
                organization_id=organization_id,
                board_document_id=document.id,
                secret_digest=self._secret_digest(raw_secret),
                display_name=name,
                write_enabled=write_enabled,
                expires_at=expires,
                credential_version=1,
                access_version=1,
            )
            session.add(invitation)
            session.commit()
            return invitation, raw_secret

    def list_invitations(
        self,
        document_id: str,
        organization_id: str,
    ) -> list[BoardInvitation]:
        with self.database.sessions() as session:
            self._standalone_document(session, document_id, organization_id)
            return list(
                session.scalars(
                    select(BoardInvitation)
                    .where(
                        BoardInvitation.organization_id == organization_id,
                        BoardInvitation.board_document_id == document_id,
                    )
                    .order_by(BoardInvitation.created_at.desc())
                )
            )

    def update_invitation(
        self,
        document_id: str,
        organization_id: str,
        invitation_id: str,
        *,
        display_name: str | None = None,
        write_enabled: bool | None = None,
        expires_at: datetime | None | object = ...,
    ) -> tuple[BoardInvitation, bool]:
        changed_access = False
        with self.database.sessions() as session:
            invitation = self._locked_invitation(
                session,
                document_id,
                organization_id,
                invitation_id,
            )
            if display_name is not None:
                invitation.display_name = self._normalize_display_name(display_name)
            if write_enabled is not None and invitation.write_enabled != write_enabled:
                invitation.write_enabled = write_enabled
                invitation.access_version += 1
                changed_access = True
            if expires_at is not ...:
                normalized = self._normalize_expiry(expires_at)
                if invitation.expires_at != normalized:
                    invitation.expires_at = normalized
                    invitation.access_version += 1
                    changed_access = True
            invitation.updated_at = datetime.now(UTC)
            session.commit()
            return invitation, changed_access

    def revoke_invitation(
        self,
        document_id: str,
        organization_id: str,
        invitation_id: str,
    ) -> tuple[BoardInvitation, bool]:
        with self.database.sessions() as session:
            invitation = self._locked_invitation(
                session,
                document_id,
                organization_id,
                invitation_id,
            )
            if invitation.revoked_at is not None:
                return invitation, False
            now = datetime.now(UTC)
            invitation.revoked_at = now
            invitation.credential_version += 1
            invitation.access_version += 1
            invitation.updated_at = now
            session.commit()
            return invitation, True

    def rotate_invitation(
        self,
        document_id: str,
        organization_id: str,
        invitation_id: str,
    ) -> tuple[BoardInvitation, str]:
        raw_secret = self._new_secret()
        with self.database.sessions() as session:
            invitation = self._locked_invitation(
                session,
                document_id,
                organization_id,
                invitation_id,
            )
            invitation.secret_digest = self._secret_digest(raw_secret)
            invitation.credential_version += 1
            invitation.access_version += 1
            invitation.revoked_at = None
            invitation.updated_at = datetime.now(UTC)
            session.commit()
            return invitation, raw_secret

    def exchange_secret(self, raw_secret: str) -> GuestSessionIssue:
        if not 32 <= len(raw_secret) <= 512:
            raise InvitationLinkInvalid("Invitation link is invalid")
        digest = self._secret_digest(raw_secret)
        now = datetime.now(UTC)
        with self.database.sessions() as session:
            invitation = session.scalar(
                select(BoardInvitation)
                .where(BoardInvitation.secret_digest == digest)
                .with_for_update()
            )
            if invitation is None:
                raise InvitationLinkInvalid("Invitation link is invalid")
            document = session.scalar(
                select(BoardDocument).where(
                    BoardDocument.organization_id == invitation.organization_id,
                    BoardDocument.id == invitation.board_document_id,
                )
            )
            if not self._invitation_is_usable(invitation, document, now):
                raise InvitationLinkInvalid("Invitation link is invalid")
            actor_id = f"guest:{secrets.token_urlsafe(18)}"
            claims = {
                "v": _GUEST_SESSION_VERSION,
                "invitationId": invitation.id,
                "boardId": invitation.board_document_id,
                "credentialVersion": invitation.credential_version,
                "actorId": actor_id,
            }
            cookie_value = self._session_serializer.dumps(claims)
            cookie_max_age = self._cookie_max_age(invitation, now)
            invitation.last_used_at = now
            invitation.use_count += 1
            invitation.updated_at = now
            session.commit()
            assert document is not None
            return GuestSessionIssue(
                invitation=invitation,
                document=document,
                actor_id=actor_id,
                cookie_value=cookie_value,
                cookie_max_age=cookie_max_age,
            )

    def principal_from_request(self, request: Request) -> GuestPrincipal | None:
        raw_cookie = request.cookies.get(self.settings.board_guest_cookie_name, "")
        if not raw_cookie:
            return None
        try:
            claims = self._session_serializer.loads(
                raw_cookie,
                max_age=self.settings.board_guest_session_max_age,
            )
        except SignatureExpired as exc:
            raise GuestSessionInvalid("Guest session has expired") from exc
        except BadSignature as exc:
            raise GuestSessionInvalid("Guest session is invalid") from exc
        parsed = self._parse_claims(claims)
        with self.database.sessions() as session:
            invitation = session.get(BoardInvitation, parsed["invitationId"])
            if invitation is None:
                raise GuestSessionInvalid("Guest session is invalid")
            document = session.scalar(
                select(BoardDocument).where(
                    BoardDocument.organization_id == invitation.organization_id,
                    BoardDocument.id == invitation.board_document_id,
                )
            )
            if invitation.board_document_id != parsed["boardId"]:
                raise GuestSessionInvalid("Guest session is invalid")
            if invitation.credential_version != parsed["credentialVersion"]:
                raise GuestSessionVersionMismatch("Guest credential version changed")
            now = datetime.now(UTC)
            if not self._invitation_is_usable(invitation, document, now):
                raise GuestSessionInvalid("Guest session is no longer valid")
            assert document is not None
            return self._guest_principal(invitation, document, parsed["actorId"])

    def principal_from_ticket(self, ticket: Any) -> GuestPrincipal:
        invitation_id = str(getattr(ticket, "invitation_id", "") or "")
        credential_version = int(getattr(ticket, "credential_version", 0) or 0)
        actor_id = str(getattr(ticket, "user_id", "") or "")
        document_id = str(getattr(ticket, "document_id", "") or "")
        if not invitation_id or not credential_version or not actor_id or not document_id:
            raise GuestSessionInvalid("Guest collaboration ticket is invalid")
        with self.database.sessions() as session:
            invitation = session.get(BoardInvitation, invitation_id)
            if invitation is None:
                raise GuestSessionInvalid("Guest collaboration ticket is invalid")
            document = session.scalar(
                select(BoardDocument).where(
                    BoardDocument.organization_id == invitation.organization_id,
                    BoardDocument.id == document_id,
                )
            )
            if invitation.board_document_id != document_id:
                raise GuestSessionInvalid("Guest collaboration ticket is invalid")
            if invitation.credential_version != credential_version:
                raise GuestSessionVersionMismatch("Guest credential version changed")
            if not self._invitation_is_usable(invitation, document, datetime.now(UTC)):
                raise GuestSessionInvalid("Guest collaboration access is no longer valid")
            assert document is not None
            return self._guest_principal(invitation, document, actor_id)

    def guest_context(self, principal: GuestPrincipal) -> dict[str, Any]:
        context = GuestBoardAccessContext(
            schemaVersion="1.0",
            principalType="guest",
            actorId=principal.user_id,
            boardId=principal.board_id,
            role="student",
            displayName=principal.full_name,
            capabilities=list(self._ordered_capabilities(principal.capabilities)),
            csrfToken=principal.csrf_token,
            cacheScopeId=principal.cache_scope_id,
            accessEpoch=principal.access_epoch,
        )
        return context.model_dump(mode="json", by_alias=True)

    def teacher_context(
        self,
        principal: Principal,
        document: BoardDocument,
        csrf_token: str,
    ) -> dict[str, Any]:
        capabilities = set(_TEACHER_CAPABILITIES_BASE)
        if document.archived_at is None:
            capabilities.update(_TEACHER_CAPABILITIES_WRITE)
        context = TeacherBoardAccessContext(
            schemaVersion="1.0",
            principalType="teacher",
            actorId=principal.user_id,
            boardId=document.id,
            role=principal.role,
            displayName=principal.full_name or principal.user_id,
            capabilities=list(self._ordered_capabilities(capabilities)),
            csrfToken=csrf_token,
            cacheScopeId=self._opaque(
                "teacher-cache", principal.organization_id, principal.user_id
            ),
            accessEpoch=self._opaque("teacher-epoch", document.id, principal.user_id),
            organizationId=principal.organization_id,
            userId=principal.user_id,
        )
        return context.model_dump(mode="json", by_alias=True)

    def validate_csrf_header(self, request: Request, principal: GuestPrincipal) -> None:
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not secrets.compare_digest(supplied, principal.csrf_token):
            raise GuestSessionInvalid("Guest CSRF token is missing or stale")

    def validate_access_epoch_header(self, request: Request, principal: GuestPrincipal) -> None:
        supplied = request.headers.get("x-board-access-epoch", "")
        if not supplied or not secrets.compare_digest(supplied, principal.access_epoch):
            from tutor_assistant_web.modules.boards.standalone_contracts import (
                StandaloneBoardProblem,
            )

            raise StandaloneBoardProblem(
                "access_epoch_changed",
                "Board access permissions changed; refresh access context before retrying.",
                409,
            )

    def set_guest_cookie(self, response: Response, issue: GuestSessionIssue) -> None:
        response.set_cookie(
            self.settings.board_guest_cookie_name,
            issue.cookie_value,
            max_age=issue.cookie_max_age,
            httponly=True,
            secure=self.settings.session_cookie_secure,
            samesite="lax",
            path="/",
        )

    def clear_guest_cookie(self, response: Response) -> None:
        response.delete_cookie(
            self.settings.board_guest_cookie_name,
            path="/",
            secure=self.settings.session_cookie_secure,
            httponly=True,
            samesite="lax",
        )

    def invitation_summary(self, invitation: BoardInvitation) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "invitationId": invitation.id,
            "boardId": invitation.board_document_id,
            "displayName": invitation.display_name,
            "writeEnabled": invitation.write_enabled,
            "expiresAt": invitation.expires_at.isoformat() if invitation.expires_at else None,
            "revokedAt": invitation.revoked_at.isoformat() if invitation.revoked_at else None,
            "createdAt": invitation.created_at.isoformat(),
            "lastUsedAt": invitation.last_used_at.isoformat() if invitation.last_used_at else None,
            "useCount": invitation.use_count,
        }

    def join_url(self, raw_secret: str) -> str:
        return f"{self.settings.public_base_url.rstrip('/')}/j/{raw_secret}"

    def capability_change_events(
        self,
        document: BoardDocument,
        *,
        invitation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            query = select(BoardInvitation).where(
                BoardInvitation.organization_id == document.organization_id,
                BoardInvitation.board_document_id == document.id,
                BoardInvitation.revoked_at.is_(None),
            )
            if invitation_id is not None:
                query = query.where(BoardInvitation.id == invitation_id)
            invitations = list(session.scalars(query))
        events: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for invitation in invitations:
            if invitation.expires_at is not None and invitation.expires_at <= now:
                continue
            can_write = self._effective_write(invitation, document)
            events.append(
                {
                    "schemaVersion": "1.0",
                    "type": "access.capabilities.changed",
                    "boardId": document.id,
                    "accessEpoch": self._access_epoch(invitation, document),
                    "refreshRequired": True,
                    "_targetInvitationId": invitation.id,
                    "_canWrite": can_write,
                }
            )
        return events

    def revocation_events(
        self,
        document: BoardDocument,
        *,
        invitation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            query = select(BoardInvitation.id).where(
                BoardInvitation.organization_id == document.organization_id,
                BoardInvitation.board_document_id == document.id,
            )
            if invitation_id is not None:
                query = query.where(BoardInvitation.id == invitation_id)
            invitation_ids = list(session.scalars(query))
        return [
            {
                "schemaVersion": "1.0",
                "type": "access.revoked",
                "boardId": document.id,
                "terminal": True,
                "_targetInvitationId": item,
            }
            for item in invitation_ids
        ]

    def _guest_principal(
        self,
        invitation: BoardInvitation,
        document: BoardDocument,
        actor_id: str,
    ) -> GuestPrincipal:
        capabilities = set(_GUEST_CAPABILITIES_BASE)
        if self._effective_write(invitation, document):
            capabilities.update(_GUEST_CAPABILITIES_WRITE)
        csrf_token = self._opaque(
            "guest-csrf",
            invitation.id,
            str(invitation.credential_version),
            actor_id,
        )
        return GuestPrincipal(
            user_id=actor_id,
            organization_id=invitation.organization_id,
            organization_name="",
            role=_GUEST_ROLE,
            email="",
            full_name=invitation.display_name,
            board_id=document.id,
            invitation_id=invitation.id,
            credential_version=invitation.credential_version,
            capabilities=frozenset(capabilities),
            csrf_token=csrf_token,
            cache_scope_id=self._opaque(
                "guest-cache",
                invitation.id,
                str(invitation.credential_version),
            ),
            access_epoch=self._access_epoch(invitation, document),
            access_expires_at=invitation.expires_at,
        )

    @staticmethod
    def _effective_write(invitation: BoardInvitation, document: BoardDocument) -> bool:
        return bool(
            invitation.write_enabled
            and document.guest_writes_enabled
            and document.archived_at is None
            and document.deleted_at is None
            and invitation.revoked_at is None
        )

    def _access_epoch(self, invitation: BoardInvitation, document: BoardDocument) -> str:
        return self._opaque(
            "guest-epoch",
            document.id,
            str(document.access_version),
            invitation.id,
            str(invitation.access_version),
            str(invitation.credential_version),
        )

    @staticmethod
    def _ordered_capabilities(capabilities: set[str] | frozenset[str]) -> tuple[str, ...]:
        order = (
            "board.read",
            "board.write",
            "board.snapshot.write",
            "collaboration.connect",
            "board.export",
            "board.history.read",
            "board.invites.manage",
            "board.archive",
            "board.delete",
        )
        return tuple(item for item in order if item in capabilities)

    def _opaque(self, label: str, *parts: str) -> str:
        payload = "\0".join((label, *parts)).encode("utf-8")
        digest = hmac.new(self._scope_key, payload, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return f"{label.replace('-', '_')}_{encoded}"

    def _secret_digest(self, raw_secret: str) -> str:
        return hmac.new(
            self._invite_pepper,
            raw_secret.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _new_secret() -> str:
        return secrets.token_urlsafe(32)

    def _cookie_max_age(self, invitation: BoardInvitation, now: datetime) -> int:
        max_age = self.settings.board_guest_session_max_age
        if invitation.expires_at is None:
            return max_age
        remaining = int((invitation.expires_at - now).total_seconds())
        return max(1, min(max_age, remaining))

    @staticmethod
    def _normalize_display_name(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 160:
            raise ValueError("Guest display name must contain 1..160 characters")
        return normalized

    @staticmethod
    def _normalize_expiry(value: datetime | None | object) -> datetime | None:
        if value is ...:
            raise ValueError("Expiry sentinel is not a value")
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        normalized = value.astimezone(UTC)
        if normalized <= datetime.now(UTC):
            raise ValueError("Invitation expiry must be in the future")
        return normalized

    @staticmethod
    def _parse_claims(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise GuestSessionInvalid("Guest session is invalid")
        expected = {"v", "invitationId", "boardId", "credentialVersion", "actorId"}
        if set(value) != expected or value.get("v") != _GUEST_SESSION_VERSION:
            raise GuestSessionInvalid("Guest session is invalid")
        invitation_id = value.get("invitationId")
        board_id = value.get("boardId")
        actor_id = value.get("actorId")
        credential_version = value.get("credentialVersion")
        if not all(isinstance(item, str) and item for item in (invitation_id, board_id, actor_id)):
            raise GuestSessionInvalid("Guest session is invalid")
        if not isinstance(credential_version, int) or credential_version < 1:
            raise GuestSessionInvalid("Guest session is invalid")
        return value

    @staticmethod
    def _invitation_is_usable(
        invitation: BoardInvitation,
        document: BoardDocument | None,
        now: datetime,
    ) -> bool:
        if document is None or document.deleted_at is not None:
            return False
        if invitation.revoked_at is not None:
            return False
        return invitation.expires_at is None or invitation.expires_at > now

    @staticmethod
    def _standalone_document(
        session,
        document_id: str,
        organization_id: str,
        *,
        for_update: bool = False,
    ) -> BoardDocument:
        query = select(BoardDocument).where(
            BoardDocument.organization_id == organization_id,
            BoardDocument.id == document_id,
        )
        if for_update:
            query = query.with_for_update()
        document = session.scalar(query)
        if (
            document is None
            or document.deleted_at is not None
            or document.lesson_id is not None
            or document.student_id is not None
        ):
            raise LookupError("Standalone board not found")
        return document

    @classmethod
    def _locked_invitation(
        cls,
        session,
        document_id: str,
        organization_id: str,
        invitation_id: str,
    ) -> BoardInvitation:
        cls._standalone_document(session, document_id, organization_id, for_update=True)
        invitation = session.scalar(
            select(BoardInvitation)
            .where(
                BoardInvitation.id == invitation_id,
                BoardInvitation.organization_id == organization_id,
                BoardInvitation.board_document_id == document_id,
            )
            .with_for_update()
        )
        if invitation is None:
            raise LookupError("Invitation not found")
        return invitation
