from pathlib import Path


def patch(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing anchor in {path}: {old[:100]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


path = "src/tutor_assistant_web/modules/boards/guest_access.py"
patch(
    path,
    '''        events: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for invitation in invitations:
            if invitation.expires_at is not None and invitation.expires_at <= now:
                continue
''',
    '''        events: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for invitation in invitations:
            expires_at = self._as_utc(invitation.expires_at)
            if expires_at is not None and expires_at <= now:
                continue
''',
)
patch(
    path,
    '''    def _cookie_max_age(self, invitation: BoardInvitation, now: datetime) -> int:
        max_age = self.settings.board_guest_session_max_age
        if invitation.expires_at is None:
            return max_age
        remaining = int((invitation.expires_at - now).total_seconds())
        return max(1, min(max_age, remaining))

    @staticmethod
    def _normalize_display_name(value: str) -> str:
''',
    '''    def _cookie_max_age(self, invitation: BoardInvitation, now: datetime) -> int:
        max_age = self.settings.board_guest_session_max_age
        expires_at = self._as_utc(invitation.expires_at)
        if expires_at is None:
            return max_age
        remaining = int((expires_at - self._as_utc(now)).total_seconds())
        return max(1, min(max_age, remaining))

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _normalize_display_name(value: str) -> str:
''',
)
patch(
    path,
    '''        if invitation.revoked_at is not None:
            return False
        return invitation.expires_at is None or invitation.expires_at > now
''',
    '''        if invitation.revoked_at is not None:
            return False
        expires_at = BoardGuestAccessService._as_utc(invitation.expires_at)
        normalized_now = BoardGuestAccessService._as_utc(now)
        assert normalized_now is not None
        return expires_at is None or expires_at > normalized_now
''',
)
patch(
    path,
    '''            access_expires_at=invitation.expires_at,
''',
    '''            access_expires_at=self._as_utc(invitation.expires_at),
''',
)

path = "src/tutor_assistant_web/modules/boards/routes.py"
patch(
    path,
    '''    CollaborationTicket,
    run_collaboration_socket,
''',
    '''    run_collaboration_socket,
''',
)
patch(
    path,
    'from tutor_assistant_web.shared.errors import NotFoundError\n',
    'from tutor_assistant_web.shared.errors import ApplicationError, NotFoundError\n',
)
patch(
    path,
    '''            try:
                access.require_read(teacher, issue.document)
            except Exception:
                pass
            else:
                return response
''',
    '''            try:
                access.require_read(teacher, issue.document)
            except ApplicationError:
                pass
            else:
                return response
''',
)
