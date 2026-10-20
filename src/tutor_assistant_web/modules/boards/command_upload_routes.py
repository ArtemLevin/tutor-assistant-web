"""Shared routes for authenticated, bounded atomic board command uploads."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeAlias

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.application import (
    BoardLamportConflict,
    BoardPersistenceService,
    BoardRevisionConflict,
)
from tutor_assistant_web.modules.boards.command_upload import (
    CHUNK_BYTES,
    MAX_UPLOAD_BYTES,
    BoardCommandUploadService,
)
from tutor_assistant_web.modules.boards.guest_access import GuestPrincipal
from tutor_assistant_web.modules.boards.models import BoardCommandBatch, BoardDocument
from tutor_assistant_web.modules.identity.application import Principal

Actor: TypeAlias = Principal | GuestPrincipal
BoardLookup: TypeAlias = Callable[[Actor, str], tuple[BoardPersistenceService, BoardDocument]]


class StartUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str = Field(alias="uploadId", min_length=1, max_length=128)
    byte_size: int = Field(alias="byteSize", gt=0, le=MAX_UPLOAD_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


async def _read_bytes(request: Request, maximum: int, content_type: str) -> bytes:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != content_type:
        raise HTTPException(415, "Unexpected Content-Type")
    declared = request.headers.get("content-length")
    if declared:
        try:
            length = int(declared)
        except ValueError as exc:
            raise HTTPException(400, "Invalid Content-Length") from exc
        if length < 0:
            raise HTTPException(400, "Invalid Content-Length")
        if length > maximum:
            raise HTTPException(413, "Upload request is too large")
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > maximum:
            raise HTTPException(413, "Upload request is too large")
    if not buffer:
        raise HTTPException(400, "Request body is empty")
    return bytes(buffer)


def create_command_upload_router(
    *,
    database: Database,
    board_principal: Callable[[Request], Actor],
    document_for_write: BoardLookup,
    validate_mutation: Callable[[Request, Actor], None],
    on_committed: Callable[[Actor, BoardDocument, BoardCommandBatch], Awaitable[None]],
    serialize_batch: Callable[[BoardCommandBatch], dict],
) -> APIRouter:
    router = APIRouter(tags=["boards"])

    def authorized(
        request: Request,
        document_id: str,
    ) -> tuple[Actor, BoardPersistenceService, BoardDocument, str]:
        actor = board_principal(request)
        boards, document = document_for_write(actor, document_id)
        validate_mutation(request, actor)
        epoch = (
            actor.access_epoch
            if isinstance(actor, GuestPrincipal)
            else f"teacher:{actor.user_id}"
        )
        return actor, boards, document, epoch

    def uploads(actor: Actor) -> BoardCommandUploadService:
        return BoardCommandUploadService(database, actor.organization_id)

    @router.post("/boards/{document_id}/command-uploads", status_code=201)
    async def start_upload(request: Request, document_id: str):
        actor, _, _, epoch = authorized(request, document_id)
        raw = await _read_bytes(request, 2_048, "application/json")
        try:
            data = StartUpload.model_validate_json(raw)
        except PydanticValidationError as exc:
            raise HTTPException(422, "Invalid upload descriptor") from exc
        upload = uploads(actor).start(
            document_id, actor.user_id, epoch, data.upload_id,
            byte_size=data.byte_size, sha256=data.sha256,
        )
        return JSONResponse(
            {
                "uploadId": upload.id,
                "chunkCount": upload.chunk_count,
                "chunkBytes": CHUNK_BYTES,
                "expiresAt": upload.expires_at.isoformat(),
            },
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    @router.put("/boards/{document_id}/command-uploads/{upload_id}/parts/{index}", status_code=204)
    async def upload_part(request: Request, document_id: str, upload_id: str, index: int):
        actor, _, _, epoch = authorized(request, document_id)
        content = await _read_bytes(request, CHUNK_BYTES, "application/octet-stream")
        uploads(actor).put_part(document_id, actor.user_id, epoch, upload_id, index, content)
        return JSONResponse(None, status_code=204)

    @router.post("/boards/{document_id}/command-uploads/{upload_id}/finalize")
    async def finalize_upload(request: Request, document_id: str, upload_id: str):
        actor, boards, previous, epoch = authorized(request, document_id)
        service = uploads(actor)
        try:
            batch = service.finalize(
                boards, document_id, actor.user_id, epoch, upload_id,
                None if isinstance(actor, GuestPrincipal) else actor.user_id,
            )
        except BoardLamportConflict as exc:
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
            )
        except BoardRevisionConflict as exc:
            missing = boards.commands_after(document_id, exc.expected_revision, limit=500)
            return JSONResponse(
                {
                    "error": {
                        "code": "board_revision_conflict",
                        "message": str(exc),
                        "currentRevision": exc.current_revision,
                        "expectedRevision": exc.expected_revision,
                    },
                    "missingCommandBatches": [serialize_batch(item) for item in missing],
                    "hasMore": bool(
                        missing and missing[-1].revision < exc.current_revision
                    ),
                },
                status_code=409,
            )
        document = boards.get(document_id)
        if batch.revision > previous.current_revision:
            await on_committed(actor, document, batch)
        return JSONResponse(
            {
                "documentId": document.id,
                "revision": batch.revision,
                "idempotencyKey": batch.idempotency_key,
                "currentDocumentSha256": document.current_document_sha256,
                "snapshotDue": boards.snapshot_due(document.id),
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.delete("/boards/{document_id}/command-uploads/{upload_id}", status_code=204)
    async def abort_upload(request: Request, document_id: str, upload_id: str):
        actor, _, _, epoch = authorized(request, document_id)
        uploads(actor).abort(document_id, actor.user_id, epoch, upload_id)
        return JSONResponse(None, status_code=204)

    return router
