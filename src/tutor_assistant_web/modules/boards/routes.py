from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tutor_assistant_web.bootstrap.container import AppContainer
from tutor_assistant_web.modules.boards.access import BoardAccessPolicy
from tutor_assistant_web.modules.boards.application import (
    BoardPersistenceService,
    BoardRevisionConflict,
    canonical_json,
)
from tutor_assistant_web.modules.boards.models import BoardCommandBatch, BoardDocument
from tutor_assistant_web.modules.identity.application import Principal
from tutor_assistant_web.shared.board_contracts.board_command_envelope_schema import (
    BoardCommandEnvelope10,
)
from tutor_assistant_web.shared.board_contracts.board_snapshot_schema import BoardSnapshot10
from tutor_assistant_web.shared.errors import NotFoundError

_CREATE_REQUEST_MAX_BYTES = 16 * 1024


class CreateBoardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(alias="documentId", min_length=1, max_length=128)


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["boards"])
    web = container.web
    access = BoardAccessPolicy(container.database)

    def principal(request: Request) -> Principal:
        return web.principal_required(request)

    def service(actor: Principal) -> BoardPersistenceService:
        return container.boards_service(actor.organization_id)

    def document_for(
        actor: Principal,
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

    def audit(
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

    @router.post("/lessons/{lesson_id}/board", status_code=201)
    async def create_board(request: Request, lesson_id: str):
        actor = principal(request)
        access.require_create(actor)
        web.validate_csrf_header(request)
        body = await _validated_body(request, CreateBoardRequest, _CREATE_REQUEST_MAX_BYTES)
        boards = service(actor)
        try:
            existing = boards.get(body.document_id)
        except NotFoundError:
            existing = None
        if existing is not None and existing.lesson_id == lesson_id:
            return _board_response(existing, web.csrf_token(request), status_code=200)
        document = boards.create_for_lesson(lesson_id, body.document_id)
        audit(actor, "board.created", document)
        return _board_response(document, web.csrf_token(request), status_code=201)

    @router.get("/boards/{document_id}")
    def get_board(request: Request, document_id: str):
        actor = principal(request)
        boards, _ = document_for(actor, document_id, operation="read")
        recovery = boards.recovery(document_id)
        payload = {
            "board": _board_payload(recovery.document, _snapshot_due(boards, recovery.document)),
            "snapshot": (
                canonical_json(recovery.snapshot)[0] if recovery.snapshot is not None else None
            ),
            "commandBatches": [_command_payload(item) for item in recovery.command_batches],
        }
        return JSONResponse(
            payload,
            headers=_board_headers(recovery.document, web.csrf_token(request)),
        )

    @router.get("/boards/{document_id}/commands")
    def get_commands(
        request: Request,
        document_id: str,
        after_revision: int = Query(default=0, alias="afterRevision", ge=0),
        limit: int = Query(default=500, ge=1, le=500),
    ):
        actor = principal(request)
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
            headers=_board_headers(document, web.csrf_token(request)),
        )

    @router.post("/boards/{document_id}/commands")
    async def append_commands(request: Request, document_id: str):
        actor = principal(request)
        boards, previous_document = document_for(actor, document_id, operation="write")
        previous_revision = previous_document.current_revision
        web.validate_csrf_header(request)
        envelope = await _validated_body(
            request,
            BoardCommandEnvelope10,
            container.settings.board_command_max_size_mb * 1024 * 1024,
        )
        if envelope.document_id.root != document_id:
            raise HTTPException(422, "documentId не совпадает с идентификатором маршрута")
        _validate_actor(envelope, actor)
        try:
            batch = boards.append_commands(envelope, actor.user_id)
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
            audit(
                actor,
                "board.commands.appended",
                document,
                {
                    "revision": batch.revision,
                    "command_count": len(envelope.commands),
                    "payload_sha256": batch.payload_sha256,
                },
            )
        return JSONResponse(
            {
                "documentId": document.id,
                "revision": batch.revision,
                "idempotencyKey": batch.idempotency_key,
                "currentDocumentSha256": document.current_document_sha256,
                "snapshotDue": boards.snapshot_due(document.id),
            },
            headers=_board_headers(document, web.csrf_token(request)),
        )

    @router.post("/boards/{document_id}/snapshots", status_code=201)
    async def save_snapshot(request: Request, document_id: str):
        actor = principal(request)
        boards, _ = document_for(actor, document_id, operation="write")
        web.validate_csrf_header(request)
        snapshot = await _validated_body(
            request,
            BoardSnapshot10,
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
            {
                "revision": stored.revision,
                "sha256": stored.sha256,
                "size": stored.size,
            },
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
            headers=_board_headers(document, web.csrf_token(request)),
        )

    @router.delete("/boards/{document_id}", status_code=204)
    def delete_board(request: Request, document_id: str):
        actor = principal(request)
        boards, document = document_for(actor, document_id, operation="manage")
        web.validate_csrf_header(request)
        deleted = boards.soft_delete(document_id)
        audit(actor, "board.deleted", deleted)
        return Response(status_code=204)

    return router


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


def _validate_actor(envelope: BoardCommandEnvelope10, principal: Principal) -> None:
    if envelope.actor_id.root != principal.user_id:
        raise HTTPException(403, "actorId не соответствует авторизованному пользователю")
    if any(command.root.actor_id.root != principal.user_id for command in envelope.commands):
        raise HTTPException(403, "actorId команды не соответствует авторизованному пользователю")


def _board_payload(document: BoardDocument, snapshot_due: bool) -> dict:
    return {
        "documentId": document.id,
        "schemaVersion": document.schema_version,
        "lessonId": document.lesson_id,
        "studentId": document.student_id,
        "currentRevision": document.current_revision,
        "currentDocumentSha256": document.current_document_sha256,
        "lastSnapshotRevision": document.last_snapshot_revision,
        "snapshotDue": snapshot_due,
        "createdAt": document.created_at.isoformat(),
        "updatedAt": document.updated_at.isoformat(),
    }


def _snapshot_due(boards: BoardPersistenceService, document: BoardDocument) -> bool:
    return (
        document.commands_since_snapshot >= boards.snapshot_interval_commands
        or document.bytes_since_snapshot >= boards.snapshot_interval_bytes
    )


def _board_response(
    document: BoardDocument,
    csrf_token: str,
    *,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        _board_payload(document, False),
        status_code=status_code,
        headers=_board_headers(document, csrf_token),
    )


def _command_payload(batch: BoardCommandBatch) -> dict:
    return {
        "revision": batch.revision,
        "baseRevision": batch.base_revision,
        "idempotencyKey": batch.idempotency_key,
        "actorUserId": batch.actor_user_id,
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
