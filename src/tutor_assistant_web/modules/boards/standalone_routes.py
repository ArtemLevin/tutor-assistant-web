from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, Response, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tutor_assistant_web.bootstrap.board_container import BoardAppContainer
from tutor_assistant_web.modules.boards.application import (
    BoardLamportConflict,
    BoardPersistenceService,
    BoardRevisionConflict,
    canonical_json,
)
from tutor_assistant_web.modules.boards.collaboration import (
    run_collaboration_socket,
    validate_websocket_origin,
)
from tutor_assistant_web.modules.boards.contracts import (
    BoardCommandEnvelope,
    BoardCommandEnvelopeInput,
    envelope_commands,
)
from tutor_assistant_web.modules.boards.guest_access import (
    GuestPrincipal,
    GuestSessionInvalid,
    GuestSessionVersionMismatch,
    InvitationLinkInvalid,
)
from tutor_assistant_web.modules.boards.models import BoardCommandBatch, BoardDocument
from tutor_assistant_web.modules.boards.standalone_access import StandaloneBoardAccessPolicy
from tutor_assistant_web.modules.boards.standalone_contracts import StandaloneBoardProblem
from tutor_assistant_web.modules.identity.application import Principal
from tutor_assistant_web.observability import BOARD_SYNC_EVENTS
from tutor_assistant_web.shared.board_contracts.board_snapshot_schema import BoardSnapshot14
from tutor_assistant_web.shared.errors import ApplicationError, NotFoundError

_CREATE_REQUEST_MAX_BYTES = 16 * 1024


class CreateStandaloneBoardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="Новая доска", min_length=1, max_length=200)


class UpdateStandaloneBoardRequest(BaseModel):
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
    display_name: str | None = Field(
        default=None, alias="displayName", min_length=1, max_length=160
    )
    write_enabled: bool | None = Field(default=None, alias="writeEnabled")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")


class CollaborationTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(
        alias="clientId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    )


def create_standalone_router(container: BoardAppContainer) -> APIRouter:
    root = APIRouter()
    router = APIRouter(prefix="/api/v1", tags=["boards"])
    web = container.web
    access = StandaloneBoardAccessPolicy()
    guest_access = container.board_guest_access_service()

    def principal(request: Request) -> Principal:
        actor = web.principal_required(request)
        access.require_create(actor)
        return actor

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
        document_id: str,
        *,
        operation: str,
    ) -> tuple[BoardPersistenceService, BoardDocument]:
        boards = service(actor)
        document = boards.get(document_id)
        if operation == "read":
            access.require_read(actor, document)
        elif operation == "write":
            access.require_write(actor, document)
        else:
            access.require_manage(actor, document)
        return boards, document

    def validate_mutation(request: Request, actor: Principal | GuestPrincipal) -> None:
        if isinstance(actor, GuestPrincipal):
            guest_access.validate_csrf_header(request, actor)
            guest_access.validate_access_epoch_header(request, actor)
        else:
            web.validate_csrf_header(request)

    def validate_csrf(request: Request, actor: Principal | GuestPrincipal) -> None:
        if isinstance(actor, GuestPrincipal):
            guest_access.validate_csrf_header(request, actor)
        else:
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
            {"guest_actor_id": actor.user_id, "invitation_id": actor.invitation_id}
            if isinstance(actor, GuestPrincipal)
            else {}
        )
        container.audit_service(actor.organization_id).record(
            None if isinstance(actor, GuestPrincipal) else actor.user_id,
            action,
            "board_document",
            document.id,
            {"lesson_id": None, "student_id": None, **guest_details, **(details or {})},
        )

    async def publish_capability_changes(
        document: BoardDocument,
        *,
        invitation_id: str | None = None,
    ) -> None:
        for event in guest_access.capability_change_events(document, invitation_id=invitation_id):
            await container.collaboration.publish(document.organization_id, document.id, event)

    async def publish_revocations(
        document: BoardDocument,
        *,
        invitation_id: str | None = None,
    ) -> None:
        for event in guest_access.revocation_events(document, invitation_id=invitation_id):
            await container.collaboration.publish(document.organization_id, document.id, event)

    @router.get("/boards/context")
    def board_context(
        request: Request,
        board_id: str | None = Query(default=None, alias="boardId"),
    ):
        teacher = web.principal(request)
        if teacher is not None:
            access.require_create(teacher)
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
            _, document = document_for(teacher, board_id, operation="read")
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
                {"code": "invitation_invalid", "detail": "This invitation link is unavailable."},
                status_code=404,
                headers=security_headers,
            )
        response = RedirectResponse(
            f"/b/{issue.document.id}#/board",
            status_code=303,
            headers=security_headers,
        )
        teacher = web.principal(request)
        if teacher is not None:
            try:
                access.require_read(teacher, issue.document)
            except ApplicationError:
                pass
            else:
                return response
        guest_access.set_guest_cookie(response, issue)
        return response

    @router.post("/boards", status_code=201)
    async def create_board(request: Request):
        actor = principal(request)
        web.validate_csrf_header(request)
        body = await _validated_body(
            request, CreateStandaloneBoardRequest, _CREATE_REQUEST_MAX_BYTES
        )
        document = service(actor).create_standalone(actor.user_id, body.title)
        audit(actor, "board.created", document, {"mode": "standalone"})
        return JSONResponse(
            _standalone_board_payload(document),
            status_code=201,
            headers=_board_headers(document, web.csrf_token(request)),
        )

    @router.get("/boards")
    def list_boards(
        request: Request,
        include_archived: bool = Query(default=False, alias="includeArchived"),
    ):
        actor = principal(request)
        documents = service(actor).list_owned_standalone(
            actor.user_id,
            include_archived=include_archived,
        )
        return JSONResponse(
            {"items": [_standalone_board_payload(item) for item in documents]},
            headers={"Cache-Control": "private, no-store"},
        )

    @router.patch("/boards/{document_id}")
    async def update_board(request: Request, document_id: str):
        actor = principal(request)
        boards, document = document_for(actor, document_id, operation="manage")
        web.validate_csrf_header(request)
        body = await _validated_body(
            request, UpdateStandaloneBoardRequest, _CREATE_REQUEST_MAX_BYTES
        )
        if not body.model_fields_set:
            raise HTTPException(422, "Нужно изменить хотя бы одно поле")
        if "title" in body.model_fields_set and body.title is None:
            raise HTTPException(422, "title не может быть null")
        previous_access_version = document.access_version
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
        audit(actor, "board.updated", updated, {"access_version": updated.access_version})
        return JSONResponse(
            _standalone_board_payload(updated),
            headers=_board_headers(updated, web.csrf_token(request)),
        )

    @router.post("/boards/{document_id}/archive")
    async def archive_board(request: Request, document_id: str):
        actor = principal(request)
        boards, document = document_for(actor, document_id, operation="manage")
        web.validate_csrf_header(request)
        archived = boards.archive(document_id)
        if document.archived_at is None:
            audit(actor, "board.archived", archived)
            await publish_capability_changes(archived)
        return _board_response(archived, web.csrf_token(request), status_code=200)

    @router.post("/boards/{document_id}/unarchive")
    async def unarchive_board(request: Request, document_id: str):
        actor = principal(request)
        boards, document = document_for(actor, document_id, operation="manage")
        web.validate_csrf_header(request)
        restored = boards.unarchive(document_id)
        if document.archived_at is not None:
            audit(actor, "board.unarchived", restored)
            await publish_capability_changes(restored)
        return _board_response(restored, web.csrf_token(request), status_code=200)

    @router.delete("/boards/{document_id}", status_code=204)
    async def delete_board(request: Request, document_id: str):
        actor = principal(request)
        boards, document = document_for(actor, document_id, operation="manage")
        web.validate_csrf_header(request)
        deleted = boards.soft_delete(document_id)
        audit(actor, "board.deleted", deleted)
        await publish_revocations(deleted)
        return Response(status_code=204)

    @router.post("/boards/{document_id}/invitations", status_code=201)
    async def create_invitation(request: Request, document_id: str):
        actor = principal(request)
        _, document = document_for(actor, document_id, operation="manage")
        web.validate_csrf_header(request)
        body = await _validated_body(
            request, CreateBoardInvitationRequest, _CREATE_REQUEST_MAX_BYTES
        )
        try:
            invitation, raw_secret = guest_access.create_invitation(
                document_id,
                actor.organization_id,
                display_name=body.display_name,
                write_enabled=body.write_enabled,
                expires_at=body.expires_at,
            )
        except LookupError as exc:
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404) from exc
        except ValueError as exc:
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
    def list_invitations(request: Request, document_id: str):
        actor = principal(request)
        _, _ = document_for(actor, document_id, operation="manage")
        try:
            invitations = guest_access.list_invitations(document_id, actor.organization_id)
        except LookupError as exc:
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404) from exc
        return JSONResponse(
            {"items": [guest_access.invitation_summary(item) for item in invitations]},
            headers={"Cache-Control": "private, no-store"},
        )

    @router.patch("/boards/{document_id}/invitations/{invitation_id}")
    async def update_invitation(request: Request, document_id: str, invitation_id: str):
        actor = principal(request)
        _, document = document_for(actor, document_id, operation="manage")
        web.validate_csrf_header(request)
        body = await _validated_body(
            request, UpdateBoardInvitationRequest, _CREATE_REQUEST_MAX_BYTES
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
                display_name=body.display_name if "display_name" in body.model_fields_set else None,
                write_enabled=body.write_enabled
                if "write_enabled" in body.model_fields_set
                else None,
                expires_at=body.expires_at if "expires_at" in body.model_fields_set else ...,
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
    async def revoke_invitation(request: Request, document_id: str, invitation_id: str):
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
    async def rotate_invitation(request: Request, document_id: str, invitation_id: str):
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
        audit(actor, "board.invitation.rotated", document, {"invitation_id": invitation.id})
        await publish_revocations(document, invitation_id=invitation.id)
        return JSONResponse(
            {
                "invitation": guest_access.invitation_summary(invitation),
                "joinUrl": guest_access.join_url(raw_secret),
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/boards/{document_id}")
    def get_board(request: Request, document_id: str):
        actor = board_principal(request)
        boards, _ = document_for(actor, document_id, operation="read")
        recovery = boards.recovery(document_id)
        return JSONResponse(
            {
                "board": _board_payload(
                    recovery.document, _snapshot_due(boards, recovery.document)
                ),
                "snapshot": canonical_json(recovery.snapshot)[0]
                if recovery.snapshot is not None
                else None,
                "commandBatches": [_command_payload(item) for item in recovery.command_batches],
            },
            headers=_board_headers(recovery.document, csrf_token(request, actor)),
        )

    @router.get("/boards/{document_id}/commands")
    def get_commands(
        request: Request,
        document_id: str,
        after_revision: int = Query(default=0, alias="afterRevision", ge=0),
        limit: int = Query(default=500, ge=1, le=500),
    ):
        actor = board_principal(request)
        boards, document = document_for(actor, document_id, operation="read")
        batches = boards.commands_after(document_id, after_revision, limit=limit)
        document = boards.get(document_id)
        return JSONResponse(
            {
                "documentId": document.id,
                "currentRevision": document.current_revision,
                "items": [_command_payload(item) for item in batches],
                "hasMore": bool(batches and batches[-1].revision < document.current_revision),
            },
            headers=_board_headers(document, csrf_token(request, actor)),
        )

    @router.post("/boards/{document_id}/commands")
    async def append_commands(request: Request, document_id: str):
        actor = board_principal(request)
        boards, previous_document = document_for(actor, document_id, operation="write")
        previous_revision = previous_document.current_revision
        validate_mutation(request, actor)
        envelope_input = await _validated_body(
            request,
            BoardCommandEnvelopeInput,
            container.settings.board_command_max_size_mb * 1024 * 1024,
        )
        envelope = envelope_input.root
        BOARD_SYNC_EVENTS.labels(event=f"envelope_{envelope.schema_version}_received").inc()
        if envelope.document_id.root != document_id:
            raise HTTPException(422, "documentId не совпадает с идентификатором маршрута")
        _validate_actor(envelope, actor)
        try:
            batch = boards.append_commands(
                envelope,
                None if isinstance(actor, GuestPrincipal) else actor.user_id,
            )
        except BoardLamportConflict as exc:
            BOARD_SYNC_EVENTS.labels(event="lamport_conflict").inc()
            return JSONResponse(
                {
                    "error": {
                        "code": "board_lamport_conflict",
                        "message": str(exc),
                        "actorId": exc.actor_id,
                        "previousLamport": exc.previous_lamport,
                        "incomingLamport": exc.incoming_lamport,
                    }
                },
                status_code=409,
                headers={"ETag": _etag(previous_revision)},
            )
        except BoardRevisionConflict as exc:
            missing = boards.commands_after(document_id, exc.expected_revision, limit=500)
            return JSONResponse(
                {
                    "error": {
                        "code": "board_revision_conflict",
                        "message": str(exc),
                        "expectedRevision": exc.expected_revision,
                        "currentRevision": exc.current_revision,
                    },
                    "missingCommandBatches": [_command_payload(item) for item in missing],
                    "hasMore": bool(missing and missing[-1].revision < exc.current_revision),
                },
                status_code=409,
                headers={"ETag": _etag(exc.current_revision)},
            )
        document = boards.get(document_id)
        if batch.revision > previous_revision:
            BOARD_SYNC_EVENTS.labels(event="revision_committed").inc()
            audit(
                actor,
                "board.commands.appended",
                document,
                {
                    "revision": batch.revision,
                    "command_count": len(envelope_commands(envelope)),
                    "payload_sha256": batch.payload_sha256,
                },
            )
            await container.collaboration.publish(
                actor.organization_id,
                document.id,
                {
                    "type": "board.revision",
                    "protocolVersion": "1.1",
                    "documentId": document.id,
                    "revision": batch.revision,
                    "baseRevision": batch.base_revision,
                    "idempotencyKey": batch.idempotency_key,
                    "actorId": actor.user_id,
                },
            )
        else:
            BOARD_SYNC_EVENTS.labels(event="idempotent_retry").inc()
        return JSONResponse(
            {
                "documentId": document.id,
                "revision": batch.revision,
                "idempotencyKey": batch.idempotency_key,
                "currentDocumentSha256": document.current_document_sha256,
                "snapshotDue": boards.snapshot_due(document.id),
            },
            headers=_board_headers(document, csrf_token(request, actor)),
        )

    @router.post("/boards/{document_id}/snapshots", status_code=201)
    async def save_snapshot(request: Request, document_id: str):
        actor = board_principal(request)
        boards, _ = document_for(actor, document_id, operation="write")
        if isinstance(actor, GuestPrincipal) and "board.snapshot.write" not in actor.capabilities:
            raise StandaloneBoardProblem("board_read_only", "Board is read-only.", 403)
        validate_mutation(request, actor)
        snapshot = await _validated_body(
            request,
            BoardSnapshot14,
            container.settings.board_snapshot_max_size_mb * 1024 * 1024,
        )
        if snapshot.document_id.root != document_id:
            raise HTTPException(422, "documentId не совпадает с идентификатором маршрута")
        stored = boards.save_snapshot(snapshot)
        document = boards.get(document_id)
        audit(
            actor,
            "board.snapshot.saved",
            document,
            {"revision": stored.revision, "sha256": stored.sha256, "size": stored.size},
        )
        return JSONResponse(
            {
                "documentId": document.id,
                "revision": stored.revision,
                "sha256": stored.sha256,
                "size": stored.size,
                "status": stored.storage_status,
            },
            status_code=201,
            headers=_board_headers(document, csrf_token(request, actor)),
        )

    @router.post("/boards/{document_id}/collaboration-ticket")
    async def collaboration_ticket(request: Request, document_id: str):
        actor = board_principal(request)
        _, _ = document_for(actor, document_id, operation="read")
        if isinstance(actor, GuestPrincipal) and "collaboration.connect" not in actor.capabilities:
            raise StandaloneBoardProblem("board_not_found", "Board not found.", 404)
        validate_csrf(request, actor)
        body = await _validated_body(request, CollaborationTicketRequest, 4096)
        ticket = await container.collaboration.issue_ticket(actor, document_id, body.client_id)
        return JSONResponse(
            {
                "protocolVersion": "1.1",
                "ticket": ticket,
                "expiresInSeconds": container.settings.board_collaboration_ticket_ttl_seconds,
                "websocketPath": f"/api/v1/boards/{document_id}/collaboration",
            },
            headers={"Cache-Control": "private, no-store"},
        )

    @router.websocket("/boards/{document_id}/collaboration")
    async def collaboration_socket(
        websocket: WebSocket,
        document_id: str,
        ticket: str = Query(min_length=1, max_length=256),
    ):
        try:
            validate_websocket_origin(
                websocket,
                container.settings.public_base_url,
                production=container.settings.app_env.lower() == "production",
            )
            issued = await container.collaboration.consume_ticket(ticket)
            if issued is None or issued.document_id != document_id:
                await websocket.close(code=4401, reason="Invalid or expired ticket")
                return
            if issued.principal_type == "guest":
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
            document = container.boards_service(actor.organization_id).get(document_id)
            access.require_read(actor, document)
            await run_collaboration_socket(
                websocket,
                container.collaboration,
                issued,
                current_revision=document.current_revision,
            )
        except Exception:
            if websocket.client_state.name != "DISCONNECTED":
                await websocket.close(code=4403, reason="Collaboration access denied")

    root.include_router(router)
    return root


async def _validated_body[ModelT: BaseModel](
    request: Request,
    model: type[ModelT],
    max_bytes: int,
) -> ModelT:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(415, "Требуется Content-Type: application/json")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            parsed_content_length = int(content_length)
            if parsed_content_length < 0:
                raise ValueError
            if parsed_content_length > max_bytes:
                raise HTTPException(413, "Тело запроса превышает допустимый размер")
        except ValueError as exc:
            raise HTTPException(400, "Некорректный Content-Length") from exc
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > max_bytes:
            raise HTTPException(413, "Тело запроса превышает допустимый размер")
    if not content:
        raise HTTPException(400, "Тело запроса не может быть пустым")
    try:
        return model.model_validate_json(content)
    except ValidationError as exc:
        details = [
            {
                "type": item["type"],
                "loc": ["body", *item["loc"]],
                "msg": item["msg"],
            }
            for item in exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )
        ]
        raise HTTPException(422, details) from exc


def _validate_actor(envelope: BoardCommandEnvelope, principal: Principal | GuestPrincipal) -> None:
    if envelope.actor_id.root != principal.user_id:
        raise HTTPException(403, "actorId не соответствует авторизованному пользователю")
    if any(
        command.root.actor_id.root != principal.user_id for command in envelope_commands(envelope)
    ):
        raise HTTPException(403, "actorId команды не соответствует авторизованному пользователю")


def _board_payload(document: BoardDocument, snapshot_due: bool) -> dict:
    return {
        "documentId": document.id,
        "schemaVersion": document.schema_version,
        "lessonId": None,
        "studentId": None,
        "currentRevision": document.current_revision,
        "currentDocumentSha256": document.current_document_sha256,
        "lastSnapshotRevision": document.last_snapshot_revision,
        "snapshotDue": snapshot_due,
        "archivedAt": document.archived_at.isoformat() if document.archived_at else None,
        "createdAt": document.created_at.isoformat(),
        "updatedAt": document.updated_at.isoformat(),
    }


def _snapshot_due(boards: BoardPersistenceService, document: BoardDocument) -> bool:
    return (
        document.commands_since_snapshot >= boards.snapshot_interval_commands
        or document.bytes_since_snapshot >= boards.snapshot_interval_bytes
    )


def _standalone_board_payload(document: BoardDocument) -> dict:
    if document.lesson_id is not None or document.student_id is not None:
        raise ValueError("Standalone descriptor requested for a lesson-bound board")
    if document.title is None:
        raise ValueError("Standalone board is missing title")
    return {
        "schemaVersion": "1.0",
        "boardId": document.id,
        "title": document.title,
        "currentRevision": document.current_revision,
        "guestWritesEnabled": document.guest_writes_enabled,
        "archivedAt": document.archived_at.isoformat() if document.archived_at else None,
        "deletedAt": document.deleted_at.isoformat() if document.deleted_at else None,
        "createdAt": document.created_at.isoformat(),
        "updatedAt": document.updated_at.isoformat(),
    }


def _board_response(
    document: BoardDocument,
    csrf_token: str,
    *,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        _standalone_board_payload(document),
        status_code=status_code,
        headers=_board_headers(document, csrf_token),
    )


def _command_payload(batch: BoardCommandBatch) -> dict:
    return {
        "revision": batch.revision,
        "baseRevision": batch.base_revision,
        "idempotencyKey": batch.idempotency_key,
        "actorUserId": batch.actor_user_id,
        "schemaVersion": batch.schema_version,
        "lamportMin": batch.lamport_min,
        "lamportMax": batch.lamport_max,
        "payloadSha256": batch.payload_sha256,
        "envelope": batch.payload,
        "createdAt": batch.created_at.isoformat(),
    }


def _board_headers(document: BoardDocument, csrf_token: str) -> dict[str, str]:
    return {
        "Cache-Control": "private, no-store",
        "ETag": _etag(document.current_revision),
        "X-Board-Revision": str(document.current_revision),
        "X-CSRF-Token": csrf_token,
    }


def _etag(revision: int) -> str:
    return f'"board-revision-{revision}"'
