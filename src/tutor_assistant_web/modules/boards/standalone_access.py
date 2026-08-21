from __future__ import annotations

from tutor_assistant_web.modules.boards.guest_access import GuestPrincipal
from tutor_assistant_web.modules.boards.models import BoardDocument
from tutor_assistant_web.modules.boards.standalone_contracts import StandaloneBoardProblem
from tutor_assistant_web.modules.identity.application import Principal
from tutor_assistant_web.modules.identity.models import MembershipRole
from tutor_assistant_web.shared.errors import ForbiddenError, NotFoundError


class StandaloneBoardAccessPolicy:
    """Authorize standalone boards without importing lesson/student access models."""

    @staticmethod
    def _require_standalone(document: BoardDocument) -> None:
        if document.lesson_id is not None or document.student_id is not None:
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)

    @staticmethod
    def require_create(principal: Principal) -> None:
        if principal.role not in {MembershipRole.admin.value, MembershipRole.tutor.value}:
            raise ForbiddenError("Создавать доски могут только преподаватели и администраторы")

    def require_read(self, principal: Principal | GuestPrincipal, document: BoardDocument) -> None:
        self._require_standalone(document)
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
        if principal.role == MembershipRole.tutor.value and document.owner_user_id == principal.user_id:
            return
        raise NotFoundError("Доска не найдена")

    def require_write(self, principal: Principal | GuestPrincipal, document: BoardDocument) -> None:
        self.require_read(principal, document)
        if isinstance(principal, GuestPrincipal) and "board.write" not in principal.capabilities:
            raise StandaloneBoardProblem("board_read_only", "Board is read-only.", 403)

    def require_manage(self, principal: Principal | GuestPrincipal, document: BoardDocument) -> None:
        self.require_read(principal, document)
        if isinstance(principal, GuestPrincipal):
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
        if principal.role not in {MembershipRole.admin.value, MembershipRole.tutor.value}:
            raise ForbiddenError("Управлять досками могут только преподаватели и администраторы")
