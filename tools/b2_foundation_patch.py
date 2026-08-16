from pathlib import Path


def patch(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing patch anchor in {path}: {old[:80]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


# ORM relationship + invitation model.
path = "src/tutor_assistant_web/modules/boards/models.py"
patch(
    path,
    '    lesson: Mapped[Lesson | None] = relationship("Lesson")\n    command_batches:',
    '    lesson: Mapped[Lesson | None] = relationship("Lesson")\n'
    '    invitations: Mapped[list[BoardInvitation]] = relationship(\n'
    '        "BoardInvitation",\n'
    '        back_populates="document",\n'
    '        cascade="all, delete-orphan",\n'
    '        order_by="BoardInvitation.created_at",\n'
    '    )\n'
    '    command_batches:',
)
patch(
    path,
    '\n\nclass BoardCommandBatch(Base):\n',
    '''\n\nclass BoardInvitation(Base):
    __tablename__ = "board_invitations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "board_document_id"],
            ["board_documents.organization_id", "board_documents.id"],
            name="fk_board_invitations_org_document",
            ondelete="CASCADE",
        ),
        UniqueConstraint("secret_digest", name="uq_board_invitations_secret_digest"),
        CheckConstraint(
            "length(trim(display_name)) > 0",
            name="ck_board_invitations_display_name",
        ),
        CheckConstraint(
            "credential_version > 0",
            name="ck_board_invitations_credential_version",
        ),
        CheckConstraint(
            "access_version > 0",
            name="ck_board_invitations_access_version",
        ),
        CheckConstraint("use_count >= 0", name="ck_board_invitations_use_count"),
        Index(
            "ix_board_invitations_org_board_created",
            "organization_id",
            "board_document_id",
            "created_at",
        ),
        Index("ix_board_invitations_expires", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), index=True)
    board_document_id: Mapped[str] = mapped_column(String(128), index=True)
    secret_digest: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    write_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    credential_version: Mapped[int] = mapped_column(BigInteger, default=1)
    access_version: Mapped[int] = mapped_column(BigInteger, default=1)
    use_count: Mapped[int] = mapped_column(BigInteger, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    document: Mapped[BoardDocument] = relationship(
        "BoardDocument",
        back_populates="invitations",
    )


class BoardCommandBatch(Base):
''',
)

# Strict standalone problem errors.
path = "src/tutor_assistant_web/modules/boards/standalone_contracts.py"
patch(
    path,
    'from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator\n',
    'from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator\n\n'
    'from tutor_assistant_web.shared.errors import ApplicationError\n',
)
patch(
    path,
    '\n\nclass _StandaloneContextBase(BaseModel):\n',
    '''\n\nclass StandaloneBoardProblem(ApplicationError):
    def __init__(self, code: str, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.code = code
        self.status_code = status_code


class _StandaloneContextBase(BaseModel):
''',
)

# Config for a separate guest cookie. Production already mandates secure cookies.
path = "src/tutor_assistant_web/config.py"
patch(
    path,
    '    session_rotation_seconds: int = Field(default=15 * 60, ge=60)\n',
    '    session_rotation_seconds: int = Field(default=15 * 60, ge=60)\n'
    '    board_guest_cookie_name: str = "tutorboard_guest"\n'
    '    board_guest_session_max_age: int = Field(default=60 * 60 * 12, ge=300, le=60 * 60 * 24 * 30)\n',
)

# Container factory.
path = "src/tutor_assistant_web/bootstrap/container.py"
patch(
    path,
    '    def board_evidence_service(self, organization_id: str):\n',
    '''    def board_guest_access_service(self):
        from tutor_assistant_web.modules.boards.guest_access import BoardGuestAccessService

        return BoardGuestAccessService(self.database, self.settings)

    def board_evidence_service(self, organization_id: str):
''',
)

# Guest commands have no FK user; command contract actor remains opaque guest actor.
path = "src/tutor_assistant_web/modules/boards/application.py"
patch(
    path,
    '        actor_user_id: str,\n    ) -> BoardCommandBatch:\n',
    '        actor_user_id: str | None,\n    ) -> BoardCommandBatch:\n',
)
patch(
    path,
    '            self._require_active_membership(session, actor_user_id)\n            existing = session.scalar(\n',
    '            if actor_user_id is not None:\n                self._require_active_membership(session, actor_user_id)\n            existing = session.scalar(\n',
)

# Access policy understands board-scoped guest principals and contract problems.
path = "src/tutor_assistant_web/modules/boards/access.py"
text = Path(path).read_text(encoding="utf-8")
text = text.replace(
    'from tutor_assistant_web.modules.boards.models import BoardDocument\n',
    'from tutor_assistant_web.modules.boards.guest_access import GuestPrincipal\n'
    'from tutor_assistant_web.modules.boards.models import BoardDocument\n'
    'from tutor_assistant_web.modules.boards.standalone_contracts import StandaloneBoardProblem\n',
)
text = text.replace(
    '    def require_read(self, principal: Principal, document: BoardDocument) -> None:\n'
    '        if principal.role == MembershipRole.admin.value:\n',
    '''    def require_read(self, principal: Principal | GuestPrincipal, document: BoardDocument) -> None:
        if isinstance(principal, GuestPrincipal):
            if document.id != principal.board_id or not self._is_standalone(document):
                raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
            if document.deleted_at is not None:
                raise StandaloneBoardProblem("board_deleted", "Board is no longer available.", 410)
            if "board.read" not in principal.capabilities:
                raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
            return
        if principal.role == MembershipRole.admin.value:
''',
)
text = text.replace(
    '    def require_write(self, principal: Principal, document: BoardDocument) -> None:\n'
    '        self.require_read(principal, document)\n'
    '        if principal.role == MembershipRole.parent.value:\n',
    '''    def require_write(self, principal: Principal | GuestPrincipal, document: BoardDocument) -> None:
        self.require_read(principal, document)
        if isinstance(principal, GuestPrincipal):
            if "board.write" not in principal.capabilities:
                raise StandaloneBoardProblem("board_read_only", "Board is read-only.", 403)
            return
        if principal.role == MembershipRole.parent.value:
''',
)
text = text.replace(
    '    def require_manage(self, principal: Principal, document: BoardDocument) -> None:\n'
    '        self.require_read(principal, document)\n'
    '        if principal.role not in {MembershipRole.admin.value, MembershipRole.tutor.value}:\n',
    '''    def require_manage(self, principal: Principal | GuestPrincipal, document: BoardDocument) -> None:
        self.require_read(principal, document)
        if isinstance(principal, GuestPrincipal):
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
        if principal.role not in {MembershipRole.admin.value, MembershipRole.tutor.value}:
''',
)
Path(path).write_text(text, encoding="utf-8")
