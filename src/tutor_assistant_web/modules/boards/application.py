from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.contracts import (
    BoardCommandEnvelope,
    envelope_lamport_range,
    envelope_origin_id,
)
from tutor_assistant_web.modules.boards.models import (
    BoardCommandBatch,
    BoardDocument,
    BoardEvidence,
    BoardGeometryImport,
    BoardSnapshot,
    BoardSnapshotStatus,
)
from tutor_assistant_web.modules.identity.models import Membership
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.shared.board_contracts.board_geometry_import_schema import (
    BoardGeometryImport11,
)
from tutor_assistant_web.shared.board_contracts.board_snapshot_schema import BoardSnapshot14
from tutor_assistant_web.shared.contracts import ArtifactStorage
from tutor_assistant_web.shared.errors import (
    ConflictError,
    GoneError,
    NotFoundError,
    ValidationError,
)
from tutor_assistant_web.shared.models import new_id

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_UNSAFE_IDENTIFIERS = {"__proto__", "constructor", "prototype"}
_LOGGER = logging.getLogger(__name__)
_DEFAULT_STANDALONE_BOARD_TITLE = "Новая доска"


class BoardRevisionConflict(ConflictError):
    def __init__(self, expected_revision: int, current_revision: int) -> None:
        super().__init__(
            f"Версия доски устарела: ожидалась {expected_revision}, "
            f"текущая версия {current_revision}"
        )
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class BoardLamportConflict(ConflictError):
    def __init__(
        self,
        actor_id: str,
        previous_lamport: int,
        incoming_lamport: int,
    ) -> None:
        super().__init__("Lamport пакета должен возрастать для actor доски")
        self.actor_id = actor_id
        self.previous_lamport = previous_lamport
        self.incoming_lamport = incoming_lamport


@dataclass(frozen=True)
class BoardRecovery:
    document: BoardDocument
    snapshot: BoardSnapshot14 | None
    command_batches: list[BoardCommandBatch]


def canonical_json(value: BaseModel | dict[str, Any]) -> tuple[dict[str, Any], bytes, str]:
    raw = (
        value.model_dump(mode="python", by_alias=True, exclude_unset=True)
        if isinstance(value, BaseModel)
        else value
    )
    payload = _canonical_value(raw)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return payload, encoded, hashlib.sha256(encoded).hexdigest()


def _canonical_value(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = value.astimezone(UTC)
        return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("Canonical JSON не поддерживает NaN или Infinity")
        if value.is_integer():
            return int(value)
        return value
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


class BoardPersistenceService:
    def __init__(
        self,
        database: Database,
        storage: ArtifactStorage,
        organization_id: str,
        *,
        max_command_bytes: int = 5 * 1024 * 1024,
        max_snapshot_bytes: int = 50 * 1024 * 1024,
        snapshot_interval_commands: int = 100,
        snapshot_interval_bytes: int = 5 * 1024 * 1024,
        delete_grace_days: int = 30,
    ) -> None:
        self.database = database
        self.storage = storage
        self.organization_id = organization_id
        self.max_command_bytes = max_command_bytes
        self.max_snapshot_bytes = max_snapshot_bytes
        self.snapshot_interval_commands = snapshot_interval_commands
        self.snapshot_interval_bytes = snapshot_interval_bytes
        self.delete_grace_days = delete_grace_days

    def create_for_lesson(self, lesson_id: str, document_id: str) -> BoardDocument:
        _validate_identifier(document_id)
        try:
            with self.database.sessions() as session:
                lesson = session.scalar(
                    select(Lesson).where(
                        Lesson.id == lesson_id,
                        Lesson.organization_id == self.organization_id,
                    )
                )
                if lesson is None:
                    raise NotFoundError("Занятие не найдено")
                existing = self._document_for_lesson(session, lesson_id)
                if existing is not None:
                    return self._resolve_existing_document(existing, document_id)
                document = BoardDocument(
                    id=document_id,
                    organization_id=self.organization_id,
                    student_id=lesson.student_id,
                    lesson_id=lesson.id,
                )
                session.add(document)
                session.commit()
                return document
        except IntegrityError as exc:
            with self.database.sessions() as session:
                existing = self._document_for_lesson(session, lesson_id)
                if existing is not None:
                    return self._resolve_existing_document(existing, document_id)
            raise ConflictError("Идентификатор доски уже используется") from exc

    def create_standalone(
        self,
        owner_user_id: str,
        title: str | None = None,
    ) -> BoardDocument:
        normalized_title = _normalize_standalone_title(title)
        with self.database.sessions() as session:
            self._require_active_membership(session, owner_user_id)
            document = BoardDocument(
                id=new_id(),
                organization_id=self.organization_id,
                student_id=None,
                lesson_id=None,
                owner_user_id=owner_user_id,
                title=normalized_title,
                guest_writes_enabled=True,
                access_version=1,
            )
            session.add(document)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ConflictError("Не удалось создать standalone-доску") from exc
            return document

    def list_owned_standalone(
        self,
        owner_user_id: str,
        *,
        include_archived: bool = True,
    ) -> list[BoardDocument]:
        with self.database.sessions() as session:
            query = select(BoardDocument).where(
                BoardDocument.organization_id == self.organization_id,
                BoardDocument.owner_user_id == owner_user_id,
                BoardDocument.lesson_id.is_(None),
                BoardDocument.student_id.is_(None),
                BoardDocument.deleted_at.is_(None),
            )
            if not include_archived:
                query = query.where(BoardDocument.archived_at.is_(None))
            return list(session.scalars(query.order_by(BoardDocument.updated_at.desc())))

    def update_standalone(
        self,
        document_id: str,
        *,
        title: str | None = None,
        guest_writes_enabled: bool | None = None,
    ) -> BoardDocument:
        with self.database.sessions() as session:
            document = self._locked_document(
                session,
                document_id,
                allow_archived=True,
            )
            if document.lesson_id is not None or document.student_id is not None:
                raise NotFoundError("Standalone-доска не найдена")
            if title is not None:
                document.title = _normalize_standalone_title(title)
            if (
                guest_writes_enabled is not None
                and document.guest_writes_enabled != guest_writes_enabled
            ):
                document.guest_writes_enabled = guest_writes_enabled
                document.access_version += 1
            session.commit()
            return document

    def get(self, document_id: str, *, include_deleted: bool = False) -> BoardDocument:
        with self.database.sessions() as session:
            document = session.scalar(
                select(BoardDocument).where(
                    BoardDocument.id == document_id,
                    BoardDocument.organization_id == self.organization_id,
                )
            )
            if document is None:
                raise NotFoundError("Доска не найдена")
            if document.deleted_at is not None and not include_deleted:
                raise GoneError("Доска удалена")
            return document

    def list_for_lesson(
        self,
        lesson_id: str,
        *,
        include_archived: bool = True,
    ) -> list[BoardDocument]:
        with self.database.sessions() as session:
            query = select(BoardDocument).where(
                BoardDocument.organization_id == self.organization_id,
                BoardDocument.lesson_id == lesson_id,
                BoardDocument.deleted_at.is_(None),
            )
            if not include_archived:
                query = query.where(BoardDocument.archived_at.is_(None))
            return list(session.scalars(query.order_by(BoardDocument.updated_at.desc())))

    def append_commands(
        self,
        envelope: BoardCommandEnvelope,
        actor_user_id: str,
    ) -> BoardCommandBatch:
        payload, encoded, payload_sha256 = canonical_json(envelope)
        if len(encoded) > self.max_command_bytes:
            raise ValidationError("Пакет команд превышает допустимый размер")
        document_id = envelope.document_id.root
        contract_actor_id = envelope.actor_id.root
        origin_id = envelope_origin_id(envelope)
        try:
            lamport_range = envelope_lamport_range(envelope)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if lamport_range is None:
            lamport_min = None
            lamport_max = None
        else:
            lamport_min, lamport_max = lamport_range
        with self.database.sessions() as session:
            self._require_active_membership(session, actor_user_id)
            existing = session.scalar(
                select(BoardCommandBatch).where(
                    BoardCommandBatch.organization_id == self.organization_id,
                    BoardCommandBatch.board_document_id == document_id,
                    BoardCommandBatch.idempotency_key == envelope.idempotency_key,
                )
            )
            if existing is not None:
                if existing.payload_sha256 != payload_sha256:
                    raise ConflictError("Idempotency key уже использован для другого пакета команд")
                return existing
            document = self._locked_document(session, document_id)
            existing = session.scalar(
                select(BoardCommandBatch).where(
                    BoardCommandBatch.organization_id == self.organization_id,
                    BoardCommandBatch.board_document_id == document_id,
                    BoardCommandBatch.idempotency_key == envelope.idempotency_key,
                )
            )
            if existing is not None:
                if existing.payload_sha256 != payload_sha256:
                    raise ConflictError("Idempotency key уже использован для другого пакета команд")
                return existing
            if envelope.base_revision != document.current_revision:
                raise BoardRevisionConflict(
                    envelope.base_revision,
                    document.current_revision,
                )
            if lamport_min is not None:
                latest_lamport = (
                    session.scalar(
                        select(func.max(BoardCommandBatch.lamport_max)).where(
                            BoardCommandBatch.organization_id == self.organization_id,
                            BoardCommandBatch.board_document_id == document.id,
                            BoardCommandBatch.contract_actor_id == contract_actor_id,
                            BoardCommandBatch.origin_id == origin_id,
                        )
                    )
                    or 0
                )
                if lamport_min <= latest_lamport:
                    raise BoardLamportConflict(
                        contract_actor_id,
                        latest_lamport,
                        lamport_min,
                    )
            revision = document.current_revision + 1
            batch = BoardCommandBatch(
                organization_id=self.organization_id,
                board_document_id=document.id,
                revision=revision,
                base_revision=envelope.base_revision,
                idempotency_key=envelope.idempotency_key,
                actor_user_id=actor_user_id,
                contract_actor_id=contract_actor_id,
                origin_id=origin_id,
                schema_version=envelope.schema_version,
                lamport_min=lamport_min,
                lamport_max=lamport_max,
                expected_document_sha256=envelope.expected_document_sha256,
                payload_sha256=payload_sha256,
                payload_size=len(encoded),
                payload=payload,
            )
            document.current_revision = revision
            document.current_document_sha256 = envelope.expected_document_sha256
            document.commands_since_snapshot += 1
            document.bytes_since_snapshot += len(encoded)
            session.add(batch)
            session.commit()
            _LOGGER.info(
                "Board command batch committed",
                extra={
                    "event": "board.command_batch.committed",
                    "document_id": document.id,
                    "actor_id": contract_actor_id,
                    "origin_id": origin_id,
                    "schema_version": envelope.schema_version,
                    "base_revision": envelope.base_revision,
                    "revision": revision,
                    "lamport_min": lamport_min,
                    "lamport_max": lamport_max,
                    "command_count": len(envelope.commands),
                    "payload_sha256": payload_sha256,
                },
            )
            return batch

    def commands_after(
        self,
        document_id: str,
        revision: int,
        *,
        limit: int = 500,
    ) -> list[BoardCommandBatch]:
        if revision < 0:
            raise ValidationError("Revision не может быть отрицательным")
        if not 1 <= limit <= 1000:
            raise ValidationError("Limit должен быть от 1 до 1000")
        self.get(document_id)
        with self.database.sessions() as session:
            return list(
                session.scalars(
                    select(BoardCommandBatch)
                    .where(
                        BoardCommandBatch.organization_id == self.organization_id,
                        BoardCommandBatch.board_document_id == document_id,
                        BoardCommandBatch.revision > revision,
                    )
                    .order_by(BoardCommandBatch.revision)
                    .limit(limit)
                )
            )

    def snapshot_due(self, document_id: str) -> bool:
        document = self.get(document_id)
        return (
            document.commands_since_snapshot >= self.snapshot_interval_commands
            or document.bytes_since_snapshot >= self.snapshot_interval_bytes
        )

    def save_snapshot(self, snapshot: BoardSnapshot14) -> BoardSnapshot:
        document_id = snapshot.document_id.root
        if snapshot.document.id.root != document_id:
            raise ValidationError("Snapshot содержит документ с другим идентификатором")
        _, _, document_sha256 = canonical_json(snapshot.document)
        if document_sha256 != snapshot.document_sha256:
            raise ValidationError("SHA-256 документа не соответствует snapshot")
        payload, encoded, snapshot_sha256 = canonical_json(snapshot)
        if len(encoded) > self.max_snapshot_bytes:
            raise ValidationError("Snapshot превышает допустимый размер")
        storage_key = (
            f"{self.organization_id}/boards/{document_id}/snapshots/"
            f"{snapshot.revision:020d}-{snapshot_sha256}.json"
        )
        with self.database.sessions() as session:
            document = self._locked_document(session, document_id)
            expected_sha256 = self._revision_document_sha256(
                session,
                document,
                snapshot.revision,
            )
            if expected_sha256 and expected_sha256 != snapshot.document_sha256:
                raise ConflictError("Snapshot не соответствует сохранённой revision")
            existing = session.scalar(
                select(BoardSnapshot).where(
                    BoardSnapshot.organization_id == self.organization_id,
                    BoardSnapshot.board_document_id == document_id,
                    BoardSnapshot.revision == snapshot.revision,
                )
            )
            if existing is not None:
                if existing.sha256 != snapshot_sha256:
                    raise ConflictError("Для revision уже сохранён другой snapshot")
                if existing.storage_status == BoardSnapshotStatus.available.value:
                    return existing
                if existing.storage_status == BoardSnapshotStatus.deleted.value:
                    raise GoneError("Snapshot удалён")
                stored_snapshot = existing
            else:
                stored_snapshot = BoardSnapshot(
                    organization_id=self.organization_id,
                    board_document_id=document.id,
                    revision=snapshot.revision,
                    schema_version=snapshot.schema_version,
                    document_sha256=snapshot.document_sha256,
                    storage_key=storage_key,
                    sha256=snapshot_sha256,
                    size=len(encoded),
                )
                session.add(stored_snapshot)
            session.commit()
        try:
            stored = self.storage.put(
                storage_key,
                encoded,
                "application/json",
            )
        except Exception as exc:
            self._record_snapshot_upload_error(stored_snapshot.id, exc)
            raise
        if stored.sha256 != snapshot_sha256 or stored.size != len(encoded):
            self._quarantine_snapshot(
                stored_snapshot.id,
                "Artifact storage returned a different size or SHA-256",
            )
            raise ConflictError("Хранилище вернуло некорректный snapshot")
        with self.database.sessions() as session:
            document = self._locked_document(session, document_id)
            stored_snapshot = session.scalar(
                select(BoardSnapshot)
                .where(
                    BoardSnapshot.id == stored_snapshot.id,
                    BoardSnapshot.organization_id == self.organization_id,
                    BoardSnapshot.board_document_id == document_id,
                )
                .with_for_update()
            )
            if stored_snapshot is None:
                raise ConflictError("Метаданные snapshot были удалены во время загрузки")
            if stored_snapshot.sha256 != snapshot_sha256:
                raise ConflictError("Метаданные snapshot изменились во время загрузки")
            stored_snapshot.storage_status = BoardSnapshotStatus.available.value
            stored_snapshot.upload_error = ""
            stored_snapshot.verified_at = datetime.now(UTC)
            if snapshot.revision >= document.last_snapshot_revision:
                document.last_snapshot_revision = snapshot.revision
                outstanding = select(
                    func.count(BoardCommandBatch.id),
                    func.coalesce(func.sum(BoardCommandBatch.payload_size), 0),
                ).where(
                    BoardCommandBatch.organization_id == self.organization_id,
                    BoardCommandBatch.board_document_id == document.id,
                    BoardCommandBatch.revision > snapshot.revision,
                )
                count, size = session.execute(outstanding).one()
                document.commands_since_snapshot = int(count)
                document.bytes_since_snapshot = int(size)
            if document.current_revision == 0 and not document.current_document_sha256:
                document.current_document_sha256 = snapshot.document_sha256
            session.commit()
            return stored_snapshot

    def load_latest_snapshot(self, document_id: str) -> BoardSnapshot14 | None:
        self.get(document_id)
        with self.database.sessions() as session:
            stored = session.scalar(
                select(BoardSnapshot)
                .where(
                    BoardSnapshot.organization_id == self.organization_id,
                    BoardSnapshot.board_document_id == document_id,
                    BoardSnapshot.deleted_at.is_(None),
                    BoardSnapshot.storage_status == BoardSnapshotStatus.available.value,
                )
                .order_by(BoardSnapshot.revision.desc())
                .limit(1)
            )
            if stored is None:
                return None
        return self._read_stored_snapshot(stored)

    def recovery(
        self,
        document_id: str,
        *,
        target_revision: int | None = None,
    ) -> BoardRecovery:
        with self.database.sessions() as session:
            document = session.scalar(
                select(BoardDocument).where(
                    BoardDocument.id == document_id,
                    BoardDocument.organization_id == self.organization_id,
                )
            )
            if document is None:
                raise NotFoundError("Доска не найдена")
            if document.deleted_at is not None:
                raise GoneError("Доска удалена")
            resolved_revision = (
                document.current_revision if target_revision is None else target_revision
            )
            if resolved_revision < 0 or resolved_revision > document.current_revision:
                raise BoardRevisionConflict(resolved_revision, document.current_revision)
            stored = session.scalar(
                select(BoardSnapshot)
                .where(
                    BoardSnapshot.organization_id == self.organization_id,
                    BoardSnapshot.board_document_id == document_id,
                    BoardSnapshot.revision <= resolved_revision,
                    BoardSnapshot.deleted_at.is_(None),
                    BoardSnapshot.storage_status == BoardSnapshotStatus.available.value,
                )
                .order_by(BoardSnapshot.revision.desc())
                .limit(1)
            )
            snapshot_revision = stored.revision if stored is not None else 0
            command_batches = list(
                session.scalars(
                    select(BoardCommandBatch)
                    .where(
                        BoardCommandBatch.organization_id == self.organization_id,
                        BoardCommandBatch.board_document_id == document_id,
                        BoardCommandBatch.revision > snapshot_revision,
                        BoardCommandBatch.revision <= resolved_revision,
                    )
                    .order_by(BoardCommandBatch.revision)
                )
            )
        snapshot = self._read_stored_snapshot(stored) if stored is not None else None
        return BoardRecovery(
            document=document,
            snapshot=snapshot,
            command_batches=command_batches,
        )

    def revision_history(self, document_id: str, *, limit: int = 500) -> list[dict]:
        document = self.get(document_id)
        with self.database.sessions() as session:
            snapshots = {
                item.revision: item
                for item in session.scalars(
                    select(BoardSnapshot).where(
                        BoardSnapshot.organization_id == self.organization_id,
                        BoardSnapshot.board_document_id == document_id,
                        BoardSnapshot.storage_status == BoardSnapshotStatus.available.value,
                        BoardSnapshot.deleted_at.is_(None),
                    )
                )
            }
            batches = list(
                session.scalars(
                    select(BoardCommandBatch)
                    .where(
                        BoardCommandBatch.organization_id == self.organization_id,
                        BoardCommandBatch.board_document_id == document_id,
                    )
                    .order_by(BoardCommandBatch.revision.desc())
                    .limit(limit)
                )
            )
        rows = [
            {
                "revision": 0,
                "actorUserId": None,
                "createdAt": document.created_at,
                "documentSha256": (snapshots[0].document_sha256 if 0 in snapshots else ""),
                "snapshotAvailable": 0 in snapshots,
            }
        ]
        rows.extend(
            {
                "revision": batch.revision,
                "actorUserId": batch.actor_user_id,
                "createdAt": batch.created_at,
                "documentSha256": batch.expected_document_sha256,
                "snapshotAvailable": batch.revision in snapshots,
            }
            for batch in reversed(batches)
        )
        return rows

    def archive(self, document_id: str) -> BoardDocument:
        with self.database.sessions() as session:
            document = self._locked_document(
                session,
                document_id,
                allow_archived=True,
            )
            if document.archived_at is None:
                document.archived_at = datetime.now(UTC)
                document.access_version += 1
            session.commit()
            return document

    def unarchive(self, document_id: str) -> BoardDocument:
        with self.database.sessions() as session:
            document = self._locked_document(
                session,
                document_id,
                allow_archived=True,
            )
            if document.archived_at is not None:
                document.archived_at = None
                document.access_version += 1
            session.commit()
            return document

    def record_geometry_import(
        self,
        geometry_import: BoardGeometryImport11,
    ) -> BoardGeometryImport:
        payload, _, contract_sha256 = canonical_json(geometry_import)
        document_id = geometry_import.document_id.root
        prompt_sha256 = hashlib.sha256(geometry_import.prompt.encode("utf-8")).hexdigest()
        provenance = dict(payload)
        provenance.pop("prompt", None)
        with self.database.sessions() as session:
            existing = session.scalar(
                select(BoardGeometryImport).where(
                    BoardGeometryImport.organization_id == self.organization_id,
                    BoardGeometryImport.board_document_id == document_id,
                    BoardGeometryImport.import_id == geometry_import.import_id.root,
                )
            )
            if existing is not None:
                if existing.contract_sha256 != contract_sha256:
                    raise ConflictError("Import ID уже использован для другого построения")
                return existing
            document = self._locked_document(session, document_id)
            existing = session.scalar(
                select(BoardGeometryImport).where(
                    BoardGeometryImport.organization_id == self.organization_id,
                    BoardGeometryImport.board_document_id == document_id,
                    BoardGeometryImport.import_id == geometry_import.import_id.root,
                )
            )
            if existing is not None:
                if existing.contract_sha256 != contract_sha256:
                    raise ConflictError("Import ID уже использован для другого построения")
                return existing
            if geometry_import.base_revision != document.current_revision:
                raise BoardRevisionConflict(
                    geometry_import.base_revision,
                    document.current_revision,
                )
            source = geometry_import.geometry_os
            stored_import = BoardGeometryImport(
                organization_id=self.organization_id,
                board_document_id=document.id,
                import_id=geometry_import.import_id.root,
                command_id=geometry_import.command_id.root,
                base_revision=geometry_import.base_revision,
                schema_version=geometry_import.schema_version,
                request_id=source.request_id,
                prompt_sha256=prompt_sha256,
                contract_sha256=contract_sha256,
                service_version=source.service_version,
                api_version=source.api_version,
                gir_schema_version=source.gir_schema_version,
                gir_sha256=source.gir_sha256,
                layout_document_version=source.layout_document_version,
                layout_sha256=source.layout_sha256,
                provenance=provenance,
            )
            session.add(stored_import)
            session.commit()
            return stored_import

    def soft_delete(self, document_id: str) -> BoardDocument:
        now = datetime.now(UTC)
        purge_after = now + timedelta(days=self.delete_grace_days)
        with self.database.sessions() as session:
            document = self._locked_document(
                session,
                document_id,
                allow_deleted=True,
                allow_archived=True,
            )
            if document.deleted_at is not None:
                return document
            document.deleted_at = now
            document.purge_after = purge_after
            document.access_version += 1
            for snapshot in session.scalars(
                select(BoardSnapshot).where(
                    BoardSnapshot.organization_id == self.organization_id,
                    BoardSnapshot.board_document_id == document_id,
                    ~select(BoardEvidence.id)
                    .where(
                        BoardEvidence.organization_id == self.organization_id,
                        BoardEvidence.snapshot_id == BoardSnapshot.id,
                    )
                    .exists(),
                )
            ):
                snapshot.deleted_at = now
                snapshot.purge_after = purge_after
                snapshot.storage_status = BoardSnapshotStatus.deleted.value
            session.commit()
            return document

    def expire_retention(self, retention_days: int, *, limit: int = 100) -> int:
        if retention_days < 1:
            raise ValidationError("Retention должен составлять хотя бы один день")
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=retention_days)
        purge_after = now + timedelta(days=self.delete_grace_days)
        with self.database.sessions() as session:
            documents = list(
                session.scalars(
                    select(BoardDocument)
                    .where(
                        BoardDocument.organization_id == self.organization_id,
                        BoardDocument.deleted_at.is_(None),
                        BoardDocument.updated_at <= cutoff,
                    )
                    .order_by(BoardDocument.updated_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            if not documents:
                return 0
            document_ids = {item.id for item in documents}
            for document in documents:
                document.deleted_at = now
                document.purge_after = purge_after
            for snapshot in session.scalars(
                select(BoardSnapshot).where(
                    BoardSnapshot.organization_id == self.organization_id,
                    BoardSnapshot.board_document_id.in_(document_ids),
                )
            ):
                snapshot.deleted_at = now
                snapshot.purge_after = purge_after
                snapshot.storage_status = BoardSnapshotStatus.deleted.value
            session.commit()
            return len(documents)

    def purge_due(self, *, limit: int = 100) -> int:
        now = datetime.now(UTC)
        with self.database.sessions() as session:
            documents = list(
                session.scalars(
                    select(BoardDocument)
                    .where(
                        BoardDocument.organization_id == self.organization_id,
                        BoardDocument.deleted_at.is_not(None),
                        BoardDocument.purge_after <= now,
                        ~select(BoardEvidence.id)
                        .where(
                            BoardEvidence.organization_id == self.organization_id,
                            BoardEvidence.board_document_id == BoardDocument.id,
                        )
                        .exists(),
                    )
                    .order_by(BoardDocument.purge_after)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for document in documents:
                snapshots = list(
                    session.scalars(
                        select(BoardSnapshot).where(
                            BoardSnapshot.organization_id == self.organization_id,
                            BoardSnapshot.board_document_id == document.id,
                        )
                    )
                )
                for snapshot in snapshots:
                    self.storage.delete(snapshot.storage_key)
                session.delete(document)
            session.commit()
            return len(documents)

    def verify_integrity(self, *, limit: int = 100) -> dict[str, int]:
        checked = quarantined = 0
        with self.database.sessions() as session:
            snapshots = list(
                session.scalars(
                    select(BoardSnapshot)
                    .where(
                        BoardSnapshot.organization_id == self.organization_id,
                        BoardSnapshot.storage_status == BoardSnapshotStatus.available.value,
                    )
                    .order_by(BoardSnapshot.verified_at.asc().nullsfirst())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for snapshot in snapshots:
                digest = hashlib.sha256()
                size = 0
                try:
                    for chunk in self.storage.iter_bytes(snapshot.storage_key):
                        digest.update(chunk)
                        size += len(chunk)
                    if digest.hexdigest() != snapshot.sha256 or size != snapshot.size:
                        raise ValueError("stored size or SHA-256 differs from database")
                except Exception as exc:
                    snapshot.storage_status = BoardSnapshotStatus.quarantined.value
                    snapshot.upload_error = str(exc)[:2000]
                    quarantined += 1
                else:
                    snapshot.verified_at = datetime.now(UTC)
                    snapshot.upload_error = ""
                checked += 1
            session.commit()
        return {"checked": checked, "quarantined": quarantined}

    def _locked_document(
        self,
        session: Session,
        document_id: str,
        *,
        allow_deleted: bool = False,
        allow_archived: bool = False,
    ) -> BoardDocument:
        document = session.scalar(
            select(BoardDocument)
            .where(
                BoardDocument.id == document_id,
                BoardDocument.organization_id == self.organization_id,
            )
            .with_for_update()
        )
        if document is None:
            raise NotFoundError("Доска не найдена")
        if document.deleted_at is not None and not allow_deleted:
            raise GoneError("Доска удалена")
        if document.archived_at is not None and not allow_archived:
            raise GoneError("Доска находится в архиве")
        return document

    def _document_for_lesson(
        self,
        session: Session,
        lesson_id: str,
    ) -> BoardDocument | None:
        return session.scalar(
            select(BoardDocument).where(
                BoardDocument.organization_id == self.organization_id,
                BoardDocument.lesson_id == lesson_id,
            )
        )

    @staticmethod
    def _resolve_existing_document(
        existing: BoardDocument,
        document_id: str,
    ) -> BoardDocument:
        if existing.deleted_at is not None:
            raise GoneError("Доска удалена и ожидает очистки")
        if existing.archived_at is not None:
            raise GoneError("Доска находится в архиве")
        if existing.id != document_id:
            raise ConflictError("Для занятия уже создана другая доска")
        return existing

    def _require_active_membership(self, session: Session, actor_user_id: str) -> None:
        membership = session.scalar(
            select(Membership.id).where(
                Membership.organization_id == self.organization_id,
                Membership.user_id == actor_user_id,
                Membership.active.is_(True),
            )
        )
        if membership is None:
            raise NotFoundError("Участник рабочей области не найден")

    def _revision_document_sha256(
        self,
        session: Session,
        document: BoardDocument,
        revision: int,
    ) -> str:
        if revision < 0 or revision > document.current_revision:
            raise BoardRevisionConflict(revision, document.current_revision)
        if revision == document.current_revision:
            return document.current_document_sha256
        if revision == 0:
            existing = session.scalar(
                select(BoardSnapshot.document_sha256).where(
                    BoardSnapshot.organization_id == self.organization_id,
                    BoardSnapshot.board_document_id == document.id,
                    BoardSnapshot.revision == 0,
                )
            )
            return existing or ""
        value = session.scalar(
            select(BoardCommandBatch.expected_document_sha256).where(
                BoardCommandBatch.organization_id == self.organization_id,
                BoardCommandBatch.board_document_id == document.id,
                BoardCommandBatch.revision == revision,
            )
        )
        if not value:
            raise ConflictError("История revision неполна")
        return value

    def _record_snapshot_upload_error(self, snapshot_id: str, exc: Exception) -> None:
        with self.database.sessions() as session:
            snapshot = session.scalar(
                select(BoardSnapshot)
                .where(
                    BoardSnapshot.id == snapshot_id,
                    BoardSnapshot.organization_id == self.organization_id,
                )
                .with_for_update()
            )
            if snapshot is None:
                return
            snapshot.upload_error = str(exc)[:2000]
            session.commit()

    def _read_stored_snapshot(self, stored: BoardSnapshot) -> BoardSnapshot14:
        content = self.storage.read(stored.storage_key)
        if len(content) != stored.size or hashlib.sha256(content).hexdigest() != stored.sha256:
            self._quarantine_snapshot(stored.id, "Stored size or SHA-256 differs from database")
            raise ConflictError("Сохранённый snapshot повреждён")
        try:
            return BoardSnapshot14.model_validate_json(content)
        except ValueError as exc:
            self._quarantine_snapshot(stored.id, "Snapshot does not match board/v1")
            raise ConflictError("Сохранённый snapshot не соответствует контракту") from exc

    def _quarantine_snapshot(self, snapshot_id: str, reason: str) -> None:
        with self.database.sessions() as session:
            snapshot = session.scalar(
                select(BoardSnapshot)
                .where(
                    BoardSnapshot.id == snapshot_id,
                    BoardSnapshot.organization_id == self.organization_id,
                )
                .with_for_update()
            )
            if snapshot is None:
                return
            snapshot.storage_status = BoardSnapshotStatus.quarantined.value
            snapshot.upload_error = reason[:2000]
            session.commit()


def _normalize_standalone_title(value: str | None) -> str:
    normalized = (value or _DEFAULT_STANDALONE_BOARD_TITLE).strip()
    if not normalized:
        raise ValidationError("Название доски не может быть пустым")
    if len(normalized) > 200:
        raise ValidationError("Название доски не может быть длиннее 200 символов")
    return normalized


def _validate_identifier(value: str) -> None:
    if value in _UNSAFE_IDENTIFIERS or not _IDENTIFIER.fullmatch(value):
        raise ValidationError("Некорректный идентификатор документа")
