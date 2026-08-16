from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path} but found {count}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


root = Path(__file__).parents[1]
models = root / "src/tutor_assistant_web/modules/boards/models.py"
application = root / "src/tutor_assistant_web/modules/boards/application.py"
routes = root / "src/tutor_assistant_web/modules/boards/routes.py"
access = root / "src/tutor_assistant_web/modules/boards/access.py"

replace_once(
    models,
    "from sqlalchemy import (\n    JSON,\n    CheckConstraint,",
    "from sqlalchemy import (\n    JSON,\n    BigInteger,\n    Boolean,\n    CheckConstraint,",
)
replace_once(
    models,
    '''        ForeignKeyConstraint(\n            ["organization_id", "student_id", "lesson_id"],\n            ["lessons.organization_id", "lessons.student_id", "lessons.id"],\n            name="fk_board_documents_org_student_lesson",\n            ondelete="CASCADE",\n        ),\n        CheckConstraint("current_revision >= 0", name="ck_board_documents_current_revision"),''',
    '''        ForeignKeyConstraint(\n            ["organization_id", "student_id", "lesson_id"],\n            ["lessons.organization_id", "lessons.student_id", "lessons.id"],\n            name="fk_board_documents_org_student_lesson",\n            ondelete="CASCADE",\n        ),\n        ForeignKeyConstraint(\n            ["organization_id", "owner_user_id"],\n            ["memberships.organization_id", "memberships.user_id"],\n            name="fk_board_documents_org_owner_membership",\n            ondelete="RESTRICT",\n        ),\n        CheckConstraint(\n            "((lesson_id IS NOT NULL AND student_id IS NOT NULL) OR "\n            "(lesson_id IS NULL AND student_id IS NULL))",\n            name="ck_board_documents_linkage",\n        ),\n        CheckConstraint(\n            "lesson_id IS NOT NULL OR "\n            "(owner_user_id IS NOT NULL AND title IS NOT NULL "\n            "AND length(trim(title)) > 0)",\n            name="ck_board_documents_standalone_owner",\n        ),\n        CheckConstraint("access_version > 0", name="ck_board_documents_access_version"),\n        CheckConstraint("current_revision >= 0", name="ck_board_documents_current_revision"),''',
)
replace_once(
    models,
    '''        Index(\n            "ix_board_documents_org_student_updated",\n            "organization_id",\n            "student_id",\n            "updated_at",\n        ),''',
    '''        Index(\n            "ix_board_documents_org_owner_updated",\n            "organization_id",\n            "owner_user_id",\n            "updated_at",\n        ),\n        Index(\n            "ix_board_documents_org_student_updated",\n            "organization_id",\n            "student_id",\n            "updated_at",\n        ),''',
)
replace_once(
    models,
    '''    student_id: Mapped[str] = mapped_column(String(36), index=True)\n    lesson_id: Mapped[str] = mapped_column(String(36), index=True)\n    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")''',
    '''    student_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)\n    lesson_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)\n    owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)\n    title: Mapped[str | None] = mapped_column(String(200), nullable=True)\n    guest_writes_enabled: Mapped[bool] = mapped_column(Boolean, default=True)\n    access_version: Mapped[int] = mapped_column(BigInteger, default=1)\n    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")''',
)
replace_once(
    models,
    '    lesson: Mapped[Lesson] = relationship("Lesson")',
    '    lesson: Mapped[Lesson | None] = relationship("Lesson")',
)

replace_once(
    application,
    "from tutor_assistant_web.shared.errors import (\n",
    "from tutor_assistant_web.shared.models import new_id\nfrom tutor_assistant_web.shared.errors import (\n",
)
replace_once(
    application,
    '_LOGGER = logging.getLogger(__name__)\n',
    '_LOGGER = logging.getLogger(__name__)\n_DEFAULT_STANDALONE_BOARD_TITLE = "Новая доска"\n',
)
insert_marker = '''    def get(self, document_id: str, *, include_deleted: bool = False) -> BoardDocument:\n'''
standalone_methods = '''    def create_standalone(\n        self,\n        owner_user_id: str,\n        title: str | None = None,\n    ) -> BoardDocument:\n        normalized_title = _normalize_standalone_title(title)\n        with self.database.sessions() as session:\n            self._require_active_membership(session, owner_user_id)\n            document = BoardDocument(\n                id=new_id(),\n                organization_id=self.organization_id,\n                student_id=None,\n                lesson_id=None,\n                owner_user_id=owner_user_id,\n                title=normalized_title,\n                guest_writes_enabled=True,\n                access_version=1,\n            )\n            session.add(document)\n            try:\n                session.commit()\n            except IntegrityError as exc:\n                session.rollback()\n                raise ConflictError("Не удалось создать standalone-доску") from exc\n            return document\n\n    def list_owned_standalone(\n        self,\n        owner_user_id: str,\n        *,\n        include_archived: bool = True,\n    ) -> list[BoardDocument]:\n        with self.database.sessions() as session:\n            query = select(BoardDocument).where(\n                BoardDocument.organization_id == self.organization_id,\n                BoardDocument.owner_user_id == owner_user_id,\n                BoardDocument.lesson_id.is_(None),\n                BoardDocument.student_id.is_(None),\n                BoardDocument.deleted_at.is_(None),\n            )\n            if not include_archived:\n                query = query.where(BoardDocument.archived_at.is_(None))\n            return list(session.scalars(query.order_by(BoardDocument.updated_at.desc())))\n\n    def update_standalone(\n        self,\n        document_id: str,\n        *,\n        title: str | None = None,\n        guest_writes_enabled: bool | None = None,\n    ) -> BoardDocument:\n        with self.database.sessions() as session:\n            document = self._locked_document(\n                session,\n                document_id,\n                allow_archived=True,\n            )\n            if document.lesson_id is not None or document.student_id is not None:\n                raise NotFoundError("Standalone-доска не найдена")\n            if title is not None:\n                document.title = _normalize_standalone_title(title)\n            if (\n                guest_writes_enabled is not None\n                and document.guest_writes_enabled != guest_writes_enabled\n            ):\n                document.guest_writes_enabled = guest_writes_enabled\n                document.access_version += 1\n            session.commit()\n            return document\n\n'''
replace_once(application, insert_marker, standalone_methods + insert_marker)
replace_once(
    application,
    '''            document.archived_at = document.archived_at or datetime.now(UTC)\n            session.commit()''',
    '''            if document.archived_at is None:\n                document.archived_at = datetime.now(UTC)\n                document.access_version += 1\n            session.commit()''',
)
replace_once(
    application,
    '''            document.archived_at = None\n            session.commit()''',
    '''            if document.archived_at is not None:\n                document.archived_at = None\n                document.access_version += 1\n            session.commit()''',
)
replace_once(
    application,
    '''            document.deleted_at = now\n            document.purge_after = purge_after\n            for snapshot in session.scalars(''',
    '''            document.deleted_at = now\n            document.purge_after = purge_after\n            document.access_version += 1\n            for snapshot in session.scalars(''',
)
replace_once(
    application,
    '''def _validate_identifier(value: str) -> None:\n    if value in _UNSAFE_IDENTIFIERS or not _IDENTIFIER.fullmatch(value):\n        raise ValidationError("Некорректный идентификатор документа")\n''',
    '''def _normalize_standalone_title(value: str | None) -> str:\n    normalized = (value or _DEFAULT_STANDALONE_BOARD_TITLE).strip()\n    if not normalized:\n        raise ValidationError("Название доски не может быть пустым")\n    if len(normalized) > 200:\n        raise ValidationError("Название доски не может быть длиннее 200 символов")\n    return normalized\n\n\ndef _validate_identifier(value: str) -> None:\n    if value in _UNSAFE_IDENTIFIERS or not _IDENTIFIER.fullmatch(value):\n        raise ValidationError("Некорректный идентификатор документа")\n''',
)

access.write_text('''from __future__ import annotations\n\nfrom sqlalchemy import select\n\nfrom tutor_assistant_web.db import Database\nfrom tutor_assistant_web.modules.boards.models import BoardDocument\nfrom tutor_assistant_web.modules.identity.application import Principal\nfrom tutor_assistant_web.modules.identity.models import MembershipRole, StudentAccess\nfrom tutor_assistant_web.shared.errors import ForbiddenError, NotFoundError\n\n\nclass BoardAccessPolicy:\n    """Authorize board operations without revealing inaccessible board identifiers."""\n\n    def __init__(self, database: Database) -> None:\n        self.database = database\n\n    @staticmethod\n    def require_create(principal: Principal) -> None:\n        if principal.role not in {MembershipRole.admin.value, MembershipRole.tutor.value}:\n            raise ForbiddenError("Создавать доски могут только преподаватели и администраторы")\n\n    def require_read(self, principal: Principal, document: BoardDocument) -> None:\n        if principal.role == MembershipRole.admin.value:\n            return\n        if principal.role == MembershipRole.tutor.value:\n            if self._is_standalone(document) and document.owner_user_id != principal.user_id:\n                raise NotFoundError("Доска не найдена")\n            return\n        if principal.role not in {\n            MembershipRole.student.value,\n            MembershipRole.parent.value,\n        }:\n            raise ForbiddenError("Роль не поддерживает доступ к доскам")\n        if self._is_standalone(document) or document.student_id is None:\n            raise NotFoundError("Доска не найдена")\n        if not self._has_student_access(principal, document.student_id):\n            raise NotFoundError("Доска не найдена")\n\n    def require_write(self, principal: Principal, document: BoardDocument) -> None:\n        self.require_read(principal, document)\n        if principal.role == MembershipRole.parent.value:\n            raise ForbiddenError("Родительский доступ к доске доступен только для чтения")\n\n    def require_manage(self, principal: Principal, document: BoardDocument) -> None:\n        self.require_read(principal, document)\n        if principal.role not in {MembershipRole.admin.value, MembershipRole.tutor.value}:\n            raise ForbiddenError("Удалять доски могут только преподаватели и администраторы")\n\n    @staticmethod\n    def _is_standalone(document: BoardDocument) -> bool:\n        return document.lesson_id is None and document.student_id is None\n\n    def _has_student_access(self, principal: Principal, student_id: str) -> bool:\n        with self.database.sessions() as session:\n            access = session.scalar(\n                select(StudentAccess.id).where(\n                    StudentAccess.organization_id == principal.organization_id,\n                    StudentAccess.student_id == student_id,\n                    StudentAccess.user_id == principal.user_id,\n                    StudentAccess.role == principal.role,\n                    StudentAccess.active.is_(True),\n                    StudentAccess.revoked_at.is_(None),\n                )\n            )\n            return access is not None\n''', encoding="utf-8")

replace_once(
    routes,
    '''class CreateBoardRequest(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n\n    document_id: str = Field(alias="documentId", min_length=1, max_length=128)\n''',
    '''class CreateLessonBoardRequest(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n\n    document_id: str = Field(alias="documentId", min_length=1, max_length=128)\n\n\nclass CreateStandaloneBoardRequest(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n\n    title: str = Field(default="Новая доска", min_length=1, max_length=200)\n\n\nclass UpdateStandaloneBoardRequest(BaseModel):\n    model_config = ConfigDict(extra="forbid")\n\n    title: str | None = Field(default=None, min_length=1, max_length=200)\n    guest_writes_enabled: bool | None = Field(default=None, alias="guestWritesEnabled")\n''',
)
replace_once(
    routes,
    '''        body = await _validated_body(request, CreateBoardRequest, _CREATE_REQUEST_MAX_BYTES)''',
    '''        body = await _validated_body(\n            request, CreateLessonBoardRequest, _CREATE_REQUEST_MAX_BYTES\n        )''',
)
route_marker = '''    @router.post("/lessons/{lesson_id}/board", status_code=201)\n'''
standalone_routes = '''    @router.post("/boards", status_code=201)\n    async def create_standalone_board(request: Request):\n        actor = principal(request)\n        access.require_create(actor)\n        web.validate_csrf_header(request)\n        body = await _validated_body(\n            request,\n            CreateStandaloneBoardRequest,\n            _CREATE_REQUEST_MAX_BYTES,\n        )\n        document = service(actor).create_standalone(actor.user_id, body.title)\n        audit(actor, "board.created", document, {"mode": "standalone"})\n        return JSONResponse(\n            _standalone_board_payload(document),\n            status_code=201,\n            headers=_board_headers(document, web.csrf_token(request)),\n        )\n\n    @router.get("/boards")\n    def list_standalone_boards(\n        request: Request,\n        include_archived: bool = Query(default=False, alias="includeArchived"),\n    ):\n        actor = principal(request)\n        access.require_create(actor)\n        documents = service(actor).list_owned_standalone(\n            actor.user_id,\n            include_archived=include_archived,\n        )\n        return JSONResponse(\n            {"items": [_standalone_board_payload(item) for item in documents]},\n            headers={"Cache-Control": "private, no-store"},\n        )\n\n    @router.patch("/boards/{document_id}")\n    async def update_standalone_board(request: Request, document_id: str):\n        actor = principal(request)\n        boards, document = document_for(actor, document_id, operation="manage")\n        if document.lesson_id is not None or document.student_id is not None:\n            raise NotFoundError("Standalone-доска не найдена")\n        web.validate_csrf_header(request)\n        body = await _validated_body(\n            request,\n            UpdateStandaloneBoardRequest,\n            _CREATE_REQUEST_MAX_BYTES,\n        )\n        if not body.model_fields_set:\n            raise HTTPException(422, "Нужно изменить хотя бы одно поле")\n        if "title" in body.model_fields_set and body.title is None:\n            raise HTTPException(422, "title не может быть null")\n        updated = boards.update_standalone(\n            document_id,\n            title=body.title if "title" in body.model_fields_set else None,\n            guest_writes_enabled=(\n                body.guest_writes_enabled\n                if "guest_writes_enabled" in body.model_fields_set\n                else None\n            ),\n        )\n        audit(\n            actor,\n            "board.updated",\n            updated,\n            {"access_version": updated.access_version},\n        )\n        return JSONResponse(\n            _standalone_board_payload(updated),\n            headers=_board_headers(updated, web.csrf_token(request)),\n        )\n\n'''
replace_once(routes, route_marker, standalone_routes + route_marker)
replace_once(
    routes,
    '''def _board_response(\n    document: BoardDocument,\n    csrf_token: str,\n    *,\n    status_code: int,\n) -> JSONResponse:\n    return JSONResponse(\n        _board_payload(document, False),\n        status_code=status_code,\n        headers=_board_headers(document, csrf_token),\n    )\n''',
    '''def _standalone_board_payload(document: BoardDocument) -> dict:\n    if document.lesson_id is not None or document.student_id is not None:\n        raise ValueError("Standalone descriptor requested for a lesson-bound board")\n    if document.title is None:\n        raise ValueError("Standalone board is missing title")\n    return {\n        "schemaVersion": "1.0",\n        "boardId": document.id,\n        "title": document.title,\n        "currentRevision": document.current_revision,\n        "guestWritesEnabled": document.guest_writes_enabled,\n        "archivedAt": document.archived_at.isoformat() if document.archived_at else None,\n        "deletedAt": document.deleted_at.isoformat() if document.deleted_at else None,\n        "createdAt": document.created_at.isoformat(),\n        "updatedAt": document.updated_at.isoformat(),\n    }\n\n\ndef _board_response(\n    document: BoardDocument,\n    csrf_token: str,\n    *,\n    status_code: int,\n) -> JSONResponse:\n    payload = (\n        _standalone_board_payload(document)\n        if document.lesson_id is None and document.student_id is None\n        else _board_payload(document, False)\n    )\n    return JSONResponse(\n        payload,\n        status_code=status_code,\n        headers=_board_headers(document, csrf_token),\n    )\n''',
)
