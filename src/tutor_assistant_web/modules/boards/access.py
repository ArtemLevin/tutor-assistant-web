from __future__ import annotations

from sqlalchemy import select

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.models import BoardDocument
from tutor_assistant_web.modules.identity.application import Principal
from tutor_assistant_web.modules.identity.models import MembershipRole, StudentAccess
from tutor_assistant_web.shared.errors import ForbiddenError, NotFoundError


class BoardAccessPolicy:
    """Authorize board operations without revealing inaccessible board identifiers."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def require_create(principal: Principal) -> None:
        if principal.role not in {MembershipRole.admin.value, MembershipRole.tutor.value}:
            raise ForbiddenError("Создавать доски могут только преподаватели и администраторы")

    def require_read(self, principal: Principal, document: BoardDocument) -> None:
        if principal.role in {MembershipRole.admin.value, MembershipRole.tutor.value}:
            return
        if principal.role not in {
            MembershipRole.student.value,
            MembershipRole.parent.value,
        }:
            raise ForbiddenError("Роль не поддерживает доступ к доскам")
        if not self._has_student_access(principal, document.student_id):
            raise NotFoundError("Доска не найдена")

    def require_write(self, principal: Principal, document: BoardDocument) -> None:
        self.require_read(principal, document)
        if principal.role == MembershipRole.parent.value:
            raise ForbiddenError("Родительский доступ к доске доступен только для чтения")

    def require_manage(self, principal: Principal, document: BoardDocument) -> None:
        self.require_read(principal, document)
        if principal.role not in {MembershipRole.admin.value, MembershipRole.tutor.value}:
            raise ForbiddenError("Удалять доски могут только преподаватели и администраторы")

    def _has_student_access(self, principal: Principal, student_id: str) -> bool:
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
