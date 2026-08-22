from __future__ import annotations

from sqlalchemy import select

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.guest_access import GuestPrincipal
from tutor_assistant_web.modules.boards.models import BoardDocument
from tutor_assistant_web.modules.boards.standalone_contracts import StandaloneBoardProblem
from tutor_assistant_web.modules.identity.application import Principal
from tutor_assistant_web.modules.identity.models import MembershipRole
from tutor_assistant_web.shared.errors import ForbiddenError, NotFoundError


class BoardAccessPolicy:
    """Authorize board operations without revealing inaccessible board identifiers."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def require_create(principal: Principal) -> None:
        if principal.role not in {MembershipRole.admin.value, MembershipRole.tutor.value}:
            raise ForbiddenError("Создавать доски могут только преподаватели и администраторы")

    def require_read(self, principal: Principal | GuestPrincipal, document: BoardDocument) -> None:
        if isinstance(principal, GuestPrincipal):
            if document.id != principal.board_id or not self._is_standalone(document):
                raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
            if document.deleted_at is not None:
                raise StandaloneBoardProblem("board_deleted", "Board is no longer available.", 410)
            if "board.read" not in principal.capabilities:
                raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
            return
        if principal.role == MembershipRole.admin.value:
            return
        if principal.role == MembershipRole.tutor.value:
            if self._is_standalone(document) and document.owner_user_id != principal.user_id:
                raise NotFoundError("Доска не найдена")
            return
        if principal.role not in {
            MembershipRole.student.value,
            MembershipRole.parent.value,
        }:
            raise ForbiddenError("Роль не поддерживает доступ к доскам")
        if self._is_standalone(document) or document.student_id is None:
            raise NotFoundError("Доска не найдена")
        if not self._has_student_access(principal, document.student_id):
            raise NotFoundError("Доска не найдена")

    def require_write(self, principal: Principal | GuestPrincipal, document: BoardDocument) -> None:
        self.require_read(principal, document)
        if isinstance(principal, GuestPrincipal):
            if "board.write" not in principal.capabilities:
                raise StandaloneBoardProblem("board_read_only", "Board is read-only.", 403)
            return
        if principal.role == MembershipRole.parent.value:
            raise ForbiddenError("Родительский доступ к доске доступен только для чтения")

    def require_manage(
        self, principal: Principal | GuestPrincipal, document: BoardDocument
    ) -> None:
        self.require_read(principal, document)
        if isinstance(principal, GuestPrincipal):
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
        if principal.role not in {MembershipRole.admin.value, MembershipRole.tutor.value}:
            raise ForbiddenError("Удалять доски могут только преподаватели и администраторы")

    @staticmethod
    def _is_standalone(document: BoardDocument) -> bool:
        return document.lesson_id is None and document.student_id is None

    def _has_student_access(self, principal: Principal, student_id: str) -> bool:
        from tutor_assistant_web.modules.identity.models import StudentAccess

        with self.database.sessions() as session:
            access = session.scalar(
                select(StudentAccess.id).where(
                    StudentAccess.organization_id == principal.organization_id,
                    StudentAccess.student_id == student_id,
                    StudentAccess.user_id == principal.user_id,
                    StudentAccess.role == principal.role,
                    StudentAccess.active.is_(True),
                    StudentAccess.revoked_at.is_(None),
                )
            )
            return access is not None


class StandaloneBoardAccessPolicy:
    """Authorize standalone boards without loading lesson/student access paths."""

    @staticmethod
    def require_create(principal: Principal) -> None:
        BoardAccessPolicy.require_create(principal)

    @staticmethod
    def require_read(
        principal: Principal | GuestPrincipal,
        document: BoardDocument,
    ) -> None:
        if document.lesson_id is not None or document.student_id is not None:
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
        if document.deleted_at is not None:
            if isinstance(principal, GuestPrincipal):
                raise StandaloneBoardProblem("board_deleted", "Board is no longer available.", 410)
            raise NotFoundError("Доска не найдена")
        if isinstance(principal, GuestPrincipal):
            if document.id != principal.board_id or "board.read" not in principal.capabilities:
                raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
            return
        if principal.role == MembershipRole.admin.value:
            return
        if (
            principal.role == MembershipRole.tutor.value
            and document.owner_user_id == principal.user_id
        ):
            return
        raise NotFoundError("Доска не найдена")

    def require_write(
        self,
        principal: Principal | GuestPrincipal,
        document: BoardDocument,
    ) -> None:
        self.require_read(principal, document)
        if isinstance(principal, GuestPrincipal) and "board.write" not in principal.capabilities:
            raise StandaloneBoardProblem("board_read_only", "Board is read-only.", 403)

    def require_manage(
        self,
        principal: Principal | GuestPrincipal,
        document: BoardDocument,
    ) -> None:
        self.require_read(principal, document)
        if isinstance(principal, GuestPrincipal):
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
        if principal.role not in {MembershipRole.admin.value, MembershipRole.tutor.value}:
            raise NotFoundError("Доска не найдена")
