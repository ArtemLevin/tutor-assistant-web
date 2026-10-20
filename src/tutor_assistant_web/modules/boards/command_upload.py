"""Durable multipart transport for one atomic board command envelope.

Staged chunks have no board revision. A completed envelope is validated by
the existing strict command contract and published by BoardPersistenceService
as one transaction/revision. Incomplete and expired uploads are inert.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import timedelta

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, func, select

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.application import BoardPersistenceService
from tutor_assistant_web.modules.boards.contracts import BoardCommandEnvelopeInput, envelope_commands
from tutor_assistant_web.modules.boards.models import (
    BoardCommandBatch,
    BoardCommandUpload,
    BoardCommandUploadChunk,
)
from tutor_assistant_web.shared.errors import ConflictError, NotFoundError, ValidationError
from tutor_assistant_web.shared.models import new_id, utcnow

CHUNK_BYTES = 512 * 1024
MAX_UPLOAD_BYTES = 48 * 1024 * 1024
MAX_CHUNKS = MAX_UPLOAD_BYTES // CHUNK_BYTES
UPLOAD_TTL = timedelta(hours=1)
MAX_ACTIVE_UPLOADS_PER_ACTOR = 3
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class BoardCommandUploadService:
    def __init__(self, database: Database, organization_id: str) -> None:
        self.database = database
        self.organization_id = organization_id

    def _upload(
        self,
        session,
        document_id: str,
        actor_id: str,
        access_epoch: str,
        upload_id: str,
        *,
        for_update: bool = False,
    ) -> BoardCommandUpload:
        query = select(BoardCommandUpload).where(
            BoardCommandUpload.id == upload_id,
            BoardCommandUpload.organization_id == self.organization_id,
            BoardCommandUpload.board_document_id == document_id,
            BoardCommandUpload.actor_id == actor_id,
            BoardCommandUpload.access_epoch == access_epoch,
        )
        if for_update:
            query = query.with_for_update()
        upload = session.scalar(query)
        if upload is None:
            raise NotFoundError("Передача команд не найдена")
        expiry = upload.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=utcnow().tzinfo)
        if expiry <= utcnow():
            raise NotFoundError("Срок передачи команд истёк")
        return upload

    def start(
        self,
        document_id: str,
        actor_id: str,
        access_epoch: str,
        upload_id: str,
        *,
        byte_size: int,
        sha256: str,
    ) -> BoardCommandUpload:
        if not _ID.fullmatch(upload_id) or not 1 <= len(access_epoch) <= 512:
            raise ValidationError("Некорректный идентификатор передачи")
        if not _SHA.fullmatch(sha256) or byte_size <= 0 or byte_size > MAX_UPLOAD_BYTES:
            raise ValidationError("Размер или SHA-256 передачи неверен")
        chunk_count = math.ceil(byte_size / CHUNK_BYTES)
        with self.database.sessions() as session:
            # A bounded active set also cleans abandoned uploads on new work.
            session.execute(
                delete(BoardCommandUpload).where(
                    BoardCommandUpload.expires_at < utcnow()
                )
            )
            existing = session.scalar(
                select(BoardCommandUpload)
                .where(
                    BoardCommandUpload.id == upload_id,
                )
                .with_for_update()
            )
            if existing is not None:
                if (
                    existing.organization_id != self.organization_id
                    or existing.board_document_id != document_id
                    or existing.actor_id != actor_id
                    or existing.access_epoch != access_epoch
                ):
                    raise NotFoundError("Передача команд не найдена")
                if existing.byte_size != byte_size or existing.sha256 != sha256:
                    raise ConflictError("Idempotency ID уже связан с другим содержимым")
                return existing
            active_count = session.scalar(
                select(func.count())
                .select_from(BoardCommandUpload)
                .where(
                    BoardCommandUpload.organization_id == self.organization_id,
                    BoardCommandUpload.board_document_id == document_id,
                    BoardCommandUpload.actor_id == actor_id,
                )
            )
            if (active_count or 0) >= MAX_ACTIVE_UPLOADS_PER_ACTOR:
                raise ValidationError("Слишком много незавершённых передач")
            record = BoardCommandUpload(
                id=upload_id,
                organization_id=self.organization_id,
                board_document_id=document_id,
                actor_id=actor_id,
                access_epoch=access_epoch,
                byte_size=byte_size,
                chunk_count=chunk_count,
                sha256=sha256,
                created_at=utcnow(),
                expires_at=utcnow() + UPLOAD_TTL,
            )
            session.add(record)
            session.commit()
            return record

    def put_part(
        self,
        document_id: str,
        actor_id: str,
        access_epoch: str,
        upload_id: str,
        index: int,
        content: bytes,
    ) -> None:
        if not content or len(content) > CHUNK_BYTES:
            raise ValidationError("Недопустимый размер части")
        with self.database.sessions() as session:
            upload = self._upload(
                session, document_id, actor_id, access_epoch, upload_id,
                for_update=True,
            )
            expected = CHUNK_BYTES if index + 1 < upload.chunk_count else (
                upload.byte_size - index * CHUNK_BYTES
            )
            if index < 0 or index >= upload.chunk_count or len(content) != expected:
                raise ValidationError("Неправильный индекс или размер части")
            digest = hashlib.sha256(content).hexdigest()
            existing = session.scalar(
                select(BoardCommandUploadChunk).where(
                    BoardCommandUploadChunk.upload_id == upload_id,
                    BoardCommandUploadChunk.chunk_index == index,
                )
            )
            if existing is not None:
                if existing.sha256 != digest or existing.content != content:
                    raise ConflictError("Часть уже была загружена с другим содержимым")
                return
            session.add(
                BoardCommandUploadChunk(
                    id=new_id(),
                    upload_id=upload_id,
                    chunk_index=index,
                    byte_size=len(content),
                    sha256=digest,
                    content=content,
                )
            )
            session.commit()

    def validated_envelope(
        self,
        document_id: str,
        actor_id: str,
        access_epoch: str,
        upload_id: str,
    ) -> BoardCommandEnvelopeInput:
        with self.database.sessions() as session:
            upload = self._upload(session, document_id, actor_id, access_epoch, upload_id)
            parts = list(
                session.scalars(
                    select(BoardCommandUploadChunk).where(
                        BoardCommandUploadChunk.upload_id == upload_id
                    ).order_by(BoardCommandUploadChunk.chunk_index)
                )
            )
            if len(parts) != upload.chunk_count:
                raise ValidationError("Передача команд неполная")
            for index, part in enumerate(parts):
                if part.chunk_index != index or hashlib.sha256(part.content).hexdigest() != part.sha256:
                    raise ValidationError("Передача команд повреждена")
            payload = b"".join(part.content for part in parts)
            if (
                len(payload) != upload.byte_size
                or hashlib.sha256(payload).hexdigest() != upload.sha256
            ):
                raise ValidationError("Контрольная сумма передачи не совпадает")
        try:
            envelope = BoardCommandEnvelopeInput.model_validate_json(payload)
        except PydanticValidationError as exc:
            raise ValidationError("Некорректный пакет команд") from exc
        if envelope.root.document_id.root != document_id:
            raise ValidationError("Документ пакета не совпадает с маршрутом")
        if envelope.root.actor_id.root != actor_id or any(
            item.root.actor_id.root != actor_id
            for item in envelope_commands(envelope.root)
        ):
            raise ValidationError("Пакет содержит команды другого пользователя")
        if envelope.root.idempotency_key != upload_id:
            raise ValidationError("Idempotency key пакета не совпадает с передачей")
        return envelope

    def finalize(
        self,
        boards: BoardPersistenceService,
        document_id: str,
        actor_id: str,
        access_epoch: str,
        upload_id: str,
        actor_user_id: str | None,
    ) -> BoardCommandBatch:
        envelope = self.validated_envelope(
            document_id, actor_id, access_epoch, upload_id,
        )
        # The board's row lock, revision guard and idempotency key are
        # enforced by append_commands. No partial revision is ever published.
        return boards.append_commands(
            envelope.root,
            actor_user_id,
            max_bytes_override=MAX_UPLOAD_BYTES,
        )

    def abort(
        self, document_id: str, actor_id: str, access_epoch: str, upload_id: str,
    ) -> None:
        with self.database.sessions() as session:
            upload = self._upload(
                session, document_id, actor_id, access_epoch, upload_id,
                for_update=True,
            )
            session.delete(upload)
            session.commit()
