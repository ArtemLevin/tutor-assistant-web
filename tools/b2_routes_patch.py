from pathlib import Path

path = Path("src/tutor_assistant_web/modules/boards/routes.py")
text = path.read_text(encoding="utf-8")

text = text.replace(
    'from typing import Literal\n',
    'from datetime import datetime\nfrom typing import Literal\n',
)
text = text.replace(
    'from fastapi.responses import JSONResponse\n',
    'from fastapi.responses import JSONResponse, RedirectResponse\n',
)
text = text.replace(
    'from tutor_assistant_web.modules.boards.geometry_gateway import (\n    create_geometry_gateway_router,\n)\n',
    'from tutor_assistant_web.modules.boards.geometry_gateway import (\n    create_geometry_gateway_router,\n)\n'
    'from tutor_assistant_web.modules.boards.guest_access import (\n'
    '    GuestPrincipal,\n'
    '    GuestSessionInvalid,\n'
    '    GuestSessionVersionMismatch,\n'
    '    InvitationLinkInvalid,\n'
    ')\n',
)
text = text.replace(
    'from tutor_assistant_web.modules.boards.standalone_contracts import',
    'from tutor_assistant_web.modules.boards.standalone_contracts import',
) if 'from tutor_assistant_web.modules.boards.standalone_contracts import' in text else text
text = text.replace(
    'from tutor_assistant_web.modules.identity.application import Principal\n',
    'from tutor_assistant_web.modules.boards.standalone_contracts import StandaloneBoardProblem\n'
    'from tutor_assistant_web.modules.identity.application import Principal\n',
)

# Request models.
anchor = '''class UpdateStandaloneBoardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    guest_writes_enabled: bool | None = Field(default=None, alias="guestWritesEnabled")


class CollaborationTicketRequest(BaseModel):
'''
replacement = '''class UpdateStandaloneBoardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    guest_writes_enabled: bool | None = Field(default=None, alias="guestWritesEnabled")


class CreateBoardInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(alias="displayName", min_length=1, max_length=160)
    write_enabled: bool = Field(alias="writeEnabled")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")


class UpdateBoardInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, alias="displayName", min_length=1, max_length=160)
    write_enabled: bool | None = Field(default=None, alias="writeEnabled")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")


class CollaborationTicketRequest(BaseModel):
'''
if anchor not in text:
    raise SystemExit("request model anchor missing")
text = text.replace(anchor, replacement, 1)

# Router composition and principal helpers.
anchor = '''def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["boards"])
    web = container.web
    access = BoardAccessPolicy(container.database)
    router.include_router(create_geometry_gateway_router(container))

    def principal(request: Request) -> Principal:
        return web.principal_required(request)

    def service(actor: Principal) -> BoardPersistenceService:
        return container.boards_service(actor.organization_id)

    def document_for(
        actor: Principal,
'''
replacement = '''def create_router(container: AppContainer) -> APIRouter:
    root = APIRouter()
    router = APIRouter(prefix="/api/v1", tags=["boards"])
    web = container.web
    access = BoardAccessPolicy(container.database)
    guest_access = container.board_guest_access_service()
    router.include_router(create_geometry_gateway_router(container))

    def principal(request: Request) -> Principal:
        return web.principal_required(request)

    def board_principal(request: Request) -> Principal | GuestPrincipal:
        teacher = web.principal(request)
        if teacher is not None:
            return teacher
        try:
            guest = guest_access.principal_from_request(request)
        except GuestSessionVersionMismatch as exc:
            raise StandaloneBoardProblem(
                "guest_session_version_mismatch",
                "Guest session credentials changed; reopen the invitation link.",
                401,
            ) from exc
        except GuestSessionInvalid as exc:
            raise StandaloneBoardProblem(
                "guest_session_invalid",
                "Guest session is invalid or expired.",
                401,
            ) from exc
        if guest is None:
            raise StandaloneBoardProblem(
                "guest_session_invalid",
                "Authentication or a valid guest session is required.",
                401,
            )
        return guest

    def service(actor: Principal | GuestPrincipal) -> BoardPersistenceService:
        return container.boards_service(actor.organization_id)

    def document_for(
        actor: Principal | GuestPrincipal,
'''
if anchor not in text:
    raise SystemExit("router helper anchor missing")
text = text.replace(anchor, replacement, 1)

# Mutation helpers and guest-safe audit.
anchor = '''    def audit(
        actor: Principal,
        action: str,
        document: BoardDocument,
        details: dict | None = None,
    ) -> None:
        container.audit_service(actor.organization_id).record(
            actor.user_id,
            action,
            "board_document",
            document.id,
            {
                "lesson_id": document.lesson_id,
                "student_id": document.student_id,
                **(details or {}),
            },
        )

    @router.get("/boards/context")
    def board_context(request: Request):
        actor = principal(request)
        return JSONResponse(
            {
                "userId": actor.user_id,
                "organizationId": actor.organization_id,
                "role": actor.role,
                "csrfToken": web.csrf_token(request),
            },
            headers={"Cache-Control": "private, no-store"},
        )
'''
replacement = '''    def validate_mutation(request: Request, actor: Principal | GuestPrincipal) -> None:
        if isinstance(actor, GuestPrincipal):
            guest_access.validate_csrf_header(request, actor)
            guest_access.validate_access_epoch_header(request, actor)
            return
        web.validate_csrf_header(request)

    def validate_csrf(request: Request, actor: Principal | GuestPrincipal) -> None:
        if isinstance(actor, GuestPrincipal):
            guest_access.validate_csrf_header(request, actor)
            return
        web.validate_csrf_header(request)

    def csrf_token(request: Request, actor: Principal | GuestPrincipal) -> str:
        return actor.csrf_token if isinstance(actor, GuestPrincipal) else web.csrf_token(request)

    def audit(
        actor: Principal | GuestPrincipal,
        action: str,
        document: BoardDocument,
        details: dict | None = None,
    ) -> None:
        guest_details = (
            {
                "guest_actor_id": actor.user_id,
                "invitation_id": actor.invitation_id,
            }
            if isinstance(actor, GuestPrincipal)
            else {}
        )
        container.audit_service(actor.organization_id).record(
            None if isinstance(actor, GuestPrincipal) else actor.user_id,
            action,
            "board_document",
            document.id,
            {
                "lesson_id": document.lesson_id,
                "student_id": document.student_id,
                **guest_details,
                **(details or {}),
            },
        )

    async def publish_capability_changes(
        document: BoardDocument,
        *,
        invitation_id: str | None = None,
    ) -> None:
        for event in guest_access.capability_change_events(
            document,
            invitation_id=invitation_id,
        ):
            await container.collaboration.publish(
                document.organization_id,
                document.id,
                event,
            )

    async def publish_revocations(
        document: BoardDocument,
        *,
        invitation_id: str | None = None,
    ) -> None:
        for event in guest_access.revocation_events(
            document,
            invitation_id=invitation_id,
        ):
            await container.collaboration.publish(
                document.organization_id,
                document.id,
                event,
            )

    @router.get("/boards/context")
    def board_context(
        request: Request,
        board_id: str | None = Query(default=None, alias="boardId"),
    ):
        teacher = web.principal(request)
        if teacher is not None:
            if board_id is None:
                return JSONResponse(
                    {
                        "userId": teacher.user_id,
                        "organizationId": teacher.organization_id,
                        "role": teacher.role,
                        "csrfToken": web.csrf_token(request),
                    },
                    headers={"Cache-Control": "private, no-store"},
                )
            boards, document = document_for(teacher, board_id, operation="read")
            if document.lesson_id is not None or document.student_id is not None:
                raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
            _ = boards
            return JSONResponse(
                guest_access.teacher_context(teacher, document, web.csrf_token(request)),
                headers={"Cache-Control": "private, no-store"},
            )
        actor = board_principal(request)
        if not isinstance(actor, GuestPrincipal):
            raise StandaloneBoardProblem("guest_session_invalid", "Guest session required.", 401)
        if board_id is not None and board_id != actor.board_id:
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
        _, document = document_for(actor, actor.board_id, operation="read")
        return JSONResponse(
            guest_access.guest_context(actor),
            headers={"Cache-Control": "private, no-store"},
        )
'''
if anchor not in text:
    raise SystemExit("context/audit anchor missing")
text = text.replace(anchor, replacement, 1)

# Invitation management endpoints inserted before board patch.
anchor = '''    @router.patch("/boards/{document_id}")
    async def update_standalone_board(request: Request, document_id: str):
'''
invites = '''    @router.post("/boards/{document_id}/invitations", status_code=201)
    async def create_board_invitation(request: Request, document_id: str):
        actor = principal(request)
        _, document = document_for(actor, document_id, operation="manage")
        if document.lesson_id is not None or document.student_id is not None:
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
        web.validate_csrf_header(request)
        body = await _validated_body(
            request,
            CreateBoardInvitationRequest,
            _CREATE_REQUEST_MAX_BYTES,
        )
        try:
            invitation, raw_secret = guest_access.create_invitation(
                document_id,
                actor.organization_id,
                display_name=body.display_name,
                write_enabled=body.write_enabled,
                expires_at=body.expires_at,
            )
        except (LookupError, ValueError) as exc:
            if isinstance(exc, LookupError):
                raise StandaloneBoardProblem("board_not_found", "Board not found.", 404) from exc
            raise HTTPException(422, str(exc)) from exc
        audit(
            actor,
            "board.invitation.created",
            document,
            {"invitation_id": invitation.id, "write_enabled": invitation.write_enabled},
        )
        return JSONResponse(
            {
                "invitation": guest_access.invitation_summary(invitation),
                "joinUrl": guest_access.join_url(raw_secret),
            },
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/boards/{document_id}/invitations")
    def list_board_invitations(request: Request, document_id: str):
        actor = principal(request)
        _, document = document_for(actor, document_id, operation="manage")
        if document.lesson_id is not None or document.student_id is not None:
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
        try:
            invitations = guest_access.list_invitations(document_id, actor.organization_id)
        except LookupError as exc:
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404) from exc
        return JSONResponse(
            {"items": [guest_access.invitation_summary(item) for item in invitations]},
            headers={"Cache-Control": "private, no-store"},
        )

    @router.patch("/boards/{document_id}/invitations/{invitation_id}")
    async def update_board_invitation(
        request: Request,
        document_id: str,
        invitation_id: str,
    ):
        actor = principal(request)
        _, document = document_for(actor, document_id, operation="manage")
        web.validate_csrf_header(request)
        body = await _validated_body(
            request,
            UpdateBoardInvitationRequest,
            _CREATE_REQUEST_MAX_BYTES,
        )
        if not body.model_fields_set:
            raise HTTPException(422, "Нужно изменить хотя бы одно поле")
        if "display_name" in body.model_fields_set and body.display_name is None:
            raise HTTPException(422, "displayName не может быть null")
        try:
            invitation, access_changed = guest_access.update_invitation(
                document_id,
                actor.organization_id,
                invitation_id,
                display_name=(
                    body.display_name if "display_name" in body.model_fields_set else None
                ),
                write_enabled=(
                    body.write_enabled if "write_enabled" in body.model_fields_set else None
                ),
                expires_at=(body.expires_at if "expires_at" in body.model_fields_set else ...),
            )
        except LookupError as exc:
            raise StandaloneBoardProblem("board_not_found", "Invitation not found.", 404) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit(
            actor,
            "board.invitation.updated",
            document,
            {"invitation_id": invitation.id, "access_changed": access_changed},
        )
        if access_changed:
            await publish_capability_changes(document, invitation_id=invitation.id)
        return JSONResponse(
            guest_access.invitation_summary(invitation),
            headers={"Cache-Control": "private, no-store"},
        )

    @router.post("/boards/{document_id}/invitations/{invitation_id}/revoke")
    async def revoke_board_invitation(
        request: Request,
        document_id: str,
        invitation_id: str,
    ):
        actor = principal(request)
        _, document = document_for(actor, document_id, operation="manage")
        web.validate_csrf_header(request)
        try:
            invitation, changed = guest_access.revoke_invitation(
                document_id,
                actor.organization_id,
                invitation_id,
            )
        except LookupError as exc:
            raise StandaloneBoardProblem("board_not_found", "Invitation not found.", 404) from exc
        audit(
            actor,
            "board.invitation.revoked",
            document,
            {"invitation_id": invitation.id, "changed": changed},
        )
        if changed:
            await publish_revocations(document, invitation_id=invitation.id)
        return JSONResponse(
            guest_access.invitation_summary(invitation),
            headers={"Cache-Control": "private, no-store"},
        )

    @router.post("/boards/{document_id}/invitations/{invitation_id}/rotate")
    async def rotate_board_invitation(
        request: Request,
        document_id: str,
        invitation_id: str,
    ):
        actor = principal(request)
        _, document = document_for(actor, document_id, operation="manage")
        web.validate_csrf_header(request)
        try:
            invitation, raw_secret = guest_access.rotate_invitation(
                document_id,
                actor.organization_id,
                invitation_id,
            )
        except LookupError as exc:
            raise StandaloneBoardProblem("board_not_found", "Invitation not found.", 404) from exc
        audit(
            actor,
            "board.invitation.rotated",
            document,
            {"invitation_id": invitation.id},
        )
        await publish_revocations(document, invitation_id=invitation.id)
        return JSONResponse(
            {
                "invitation": guest_access.invitation_summary(invitation),
                "joinUrl": guest_access.join_url(raw_secret),
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.patch("/boards/{document_id}")
    async def update_standalone_board(request: Request, document_id: str):
'''
if anchor not in text:
    raise SystemExit("standalone patch anchor missing")
text = text.replace(anchor, invites, 1)

# Board-wide guest switch publishes access epoch/capability event.
anchor = '''        updated = boards.update_standalone(
            document_id,
            title=body.title if "title" in body.model_fields_set else None,
            guest_writes_enabled=(
                body.guest_writes_enabled
                if "guest_writes_enabled" in body.model_fields_set
                else None
            ),
        )
        audit(
'''
replacement = '''        previous_access_version = document.access_version
        updated = boards.update_standalone(
            document_id,
            title=body.title if "title" in body.model_fields_set else None,
            guest_writes_enabled=(
                body.guest_writes_enabled
                if "guest_writes_enabled" in body.model_fields_set
                else None
            ),
        )
        if updated.access_version != previous_access_version:
            await publish_capability_changes(updated)
        audit(
'''
if anchor not in text:
    raise SystemExit("standalone update anchor missing")
text = text.replace(anchor, replacement, 1)

# Guest-capable board recovery.
text = text.replace(
    '''    def get_board(request: Request, document_id: str):
        actor = principal(request)
''',
    '''    def get_board(request: Request, document_id: str):
        actor = board_principal(request)
''',
    1,
)
text = text.replace(
    '            headers=_board_headers(recovery.document, web.csrf_token(request)),\n        )\n\n    @router.get("/boards/{document_id}/revisions")',
    '            headers=_board_headers(recovery.document, csrf_token(request, actor)),\n        )\n\n    @router.get("/boards/{document_id}/revisions")',
    1,
)

# Guest-capable command pull.
text = text.replace(
    '''    def get_commands(
        request: Request,
        document_id: str,
        after_revision: int = Query(default=0, alias="afterRevision", ge=0),
        limit: int = Query(default=500, ge=1, le=500),
    ):
        actor = principal(request)
''',
    '''    def get_commands(
        request: Request,
        document_id: str,
        after_revision: int = Query(default=0, alias="afterRevision", ge=0),
        limit: int = Query(default=500, ge=1, le=500),
    ):
        actor = board_principal(request)
''',
    1,
)
# The get_commands board header is the first matching occurrence after this point.
marker = '@router.get("/boards/{document_id}/commands")'
pos = text.index(marker)
sub = text[pos:]
sub = sub.replace(
    'headers=_board_headers(document, web.csrf_token(request))',
    'headers=_board_headers(document, csrf_token(request, actor))',
    1,
)
text = text[:pos] + sub

# Guest command write with CSRF + access epoch, null FK actor.
text = text.replace(
    '''    async def append_commands(request: Request, document_id: str):
        actor = principal(request)
        boards, previous_document = document_for(actor, document_id, operation="write")
        previous_revision = previous_document.current_revision
        web.validate_csrf_header(request)
''',
    '''    async def append_commands(request: Request, document_id: str):
        actor = board_principal(request)
        boards, previous_document = document_for(actor, document_id, operation="write")
        previous_revision = previous_document.current_revision
        validate_mutation(request, actor)
''',
    1,
)
text = text.replace(
    '            batch = boards.append_commands(envelope, actor.user_id)\n',
    '            batch = boards.append_commands(\n                envelope,\n                None if isinstance(actor, GuestPrincipal) else actor.user_id,\n            )\n',
    1,
)
# append response header.
marker = '@router.post("/boards/{document_id}/commands")'
pos = text.index(marker)
sub = text[pos:]
sub = sub.replace(
    'headers=_board_headers(document, web.csrf_token(request))',
    'headers=_board_headers(document, csrf_token(request, actor))',
    1,
)
text = text[:pos] + sub

# Archive/unarchive publish capability changes.
text = text.replace(
    '''    @router.post("/boards/{document_id}/archive")
    def archive_board(request: Request, document_id: str):
''',
    '''    @router.post("/boards/{document_id}/archive")
    async def archive_board(request: Request, document_id: str):
''',
    1,
)
text = text.replace(
    '''        archived = boards.archive(document_id)
        if document.archived_at is None:
            audit(actor, "board.archived", archived)
''',
    '''        archived = boards.archive(document_id)
        if document.archived_at is None:
            audit(actor, "board.archived", archived)
            await publish_capability_changes(archived)
''',
    1,
)
text = text.replace(
    '''    @router.post("/boards/{document_id}/unarchive")
    def unarchive_board(request: Request, document_id: str):
''',
    '''    @router.post("/boards/{document_id}/unarchive")
    async def unarchive_board(request: Request, document_id: str):
''',
    1,
)
text = text.replace(
    '''        restored = boards.unarchive(document_id)
        if document.archived_at is not None:
            audit(actor, "board.unarchived", restored)
''',
    '''        restored = boards.unarchive(document_id)
        if document.archived_at is not None:
            audit(actor, "board.unarchived", restored)
            await publish_capability_changes(restored)
''',
    1,
)

# Guest collaboration ticket.
text = text.replace(
    '''    async def collaboration_ticket(request: Request, document_id: str):
        actor = principal(request)
        _, _ = document_for(actor, document_id, operation="read")
        web.validate_csrf_header(request)
''',
    '''    async def collaboration_ticket(request: Request, document_id: str):
        actor = board_principal(request)
        _, _ = document_for(actor, document_id, operation="read")
        if isinstance(actor, GuestPrincipal) and "collaboration.connect" not in actor.capabilities:
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
        validate_csrf(request, actor)
''',
    1,
)

# WS ticket revalidation.
old = '''            actor = Principal(
                user_id=issued.user_id,
                organization_id=issued.organization_id,
                organization_name="",
                role=issued.role,
                email="",
                full_name="",
            )
            boards = container.boards_service(actor.organization_id)
            document = boards.get(document_id)
            access.require_read(actor, document)
            await run_collaboration_socket(
                websocket,
                container.collaboration,
                CollaborationTicket(
                    organization_id=issued.organization_id,
                    document_id=document_id,
                    user_id=issued.user_id,
                    role=issued.role,
                    client_id=issued.client_id,
                    display_name=issued.display_name,
                ),
                current_revision=document.current_revision,
            )
'''
new = '''            if issued.principal_type == "guest":
                actor: Principal | GuestPrincipal = guest_access.principal_from_ticket(issued)
            else:
                actor = Principal(
                    user_id=issued.user_id,
                    organization_id=issued.organization_id,
                    organization_name="",
                    role=issued.role,
                    email="",
                    full_name=issued.display_name,
                )
            boards = container.boards_service(actor.organization_id)
            document = boards.get(document_id)
            access.require_read(actor, document)
            await run_collaboration_socket(
                websocket,
                container.collaboration,
                issued,
                current_revision=document.current_revision,
            )
'''
if old not in text:
    raise SystemExit("websocket actor anchor missing")
text = text.replace(old, new, 1)

# Guest snapshot persistence.
text = text.replace(
    '''    async def save_snapshot(request: Request, document_id: str):
        actor = principal(request)
        boards, _ = document_for(actor, document_id, operation="write")
        web.validate_csrf_header(request)
''',
    '''    async def save_snapshot(request: Request, document_id: str):
        actor = board_principal(request)
        boards, _ = document_for(actor, document_id, operation="write")
        if isinstance(actor, GuestPrincipal) and "board.snapshot.write" not in actor.capabilities:
            raise StandaloneBoardProblem("board_read_only", "Board is read-only.", 403)
        validate_mutation(request, actor)
''',
    1,
)
marker = '@router.post("/boards/{document_id}/snapshots"'
pos = text.index(marker)
sub = text[pos:]
sub = sub.replace(
    'headers=_board_headers(document, web.csrf_token(request))',
    'headers=_board_headers(document, csrf_token(request, actor))',
    1,
)
text = text[:pos] + sub

# Deletion terminates all guest sockets.
text = text.replace(
    '''    @router.delete("/boards/{document_id}", status_code=204)
    def delete_board(request: Request, document_id: str):
''',
    '''    @router.delete("/boards/{document_id}", status_code=204)
    async def delete_board(request: Request, document_id: str):
''',
    1,
)
text = text.replace(
    '''        deleted = boards.soft_delete(document_id)
        audit(actor, "board.deleted", deleted)
        return Response(status_code=204)

    return router
''',
    '''        deleted = boards.soft_delete(document_id)
        audit(actor, "board.deleted", deleted)
        await publish_revocations(deleted)
        return Response(status_code=204)

    root.include_router(router)
    return root
''',
    1,
)

# Actor validator accepts guest principal shape.
text = text.replace(
    'def _validate_actor(envelope: BoardCommandEnvelope, principal: Principal) -> None:',
    'def _validate_actor(\n    envelope: BoardCommandEnvelope,\n    principal: Principal | GuestPrincipal,\n) -> None:',
    1,
)

# Root invitation exchange. Insert before API router return area, after helpers exist.
join_route = '''
    @root.get("/j/{secret}")
    def join_board(request: Request, secret: str):
        security_headers = {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        }
        try:
            issue = guest_access.exchange_secret(secret)
        except InvitationLinkInvalid:
            return JSONResponse(
                {
                    "code": "invitation_invalid",
                    "detail": "This invitation link is unavailable.",
                },
                status_code=404,
                headers=security_headers,
            )
        location = f"/b/{issue.document.id}#/board"
        response = RedirectResponse(location, status_code=303, headers=security_headers)
        teacher = web.principal(request)
        if teacher is not None:
            try:
                access.require_read(teacher, issue.document)
            except Exception:
                pass
            else:
                return response
        guest_access.set_guest_cookie(response, issue)
        return response

'''
insert_anchor = '    @router.post("/boards/client-events", status_code=204)\n'
if insert_anchor not in text:
    raise SystemExit("join insert anchor missing")
text = text.replace(insert_anchor, join_route + insert_anchor, 1)

path.write_text(text, encoding="utf-8")
