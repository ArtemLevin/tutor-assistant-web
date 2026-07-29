from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from defusedxml import ElementTree
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import distinct, select

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.models import (
    BoardCommandBatch,
    BoardDocument,
    BoardEvidence,
    BoardEvidenceStatus,
    BoardGeometryImport,
    BoardSnapshot,
    BoardSnapshotStatus,
)
from tutor_assistant_web.shared.contracts import ArtifactStorage
from tutor_assistant_web.shared.errors import ConflictError, NotFoundError, ValidationError

_SHA256 = r"^[0-9a-f]{64}$"
_DENIED_SVG_ELEMENTS = {"script", "foreignObject", "iframe", "object", "embed"}
_REMOTE_REFERENCE_ATTRIBUTES = {"href", "{http://www.w3.org/1999/xlink}href"}


class BoardEvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranscriptLink(BoardEvidenceModel):
    label: str = Field(min_length=1, max_length=160)
    start_ms: int = Field(alias="startMs", ge=0, le=86_400_000)
    end_ms: int | None = Field(default=None, alias="endMs", ge=0, le=86_400_000)

    @field_validator("end_ms")
    @classmethod
    def validate_end(cls, value: int | None, info):
        start = info.data.get("start_ms")
        if value is not None and start is not None and value < start:
            raise ValueError("endMs must not be earlier than startMs")
        return value


class FinalizeBoardEvidenceRequest(BoardEvidenceModel):
    schema_version: Literal["1.0"] = Field(default="1.0", alias="schemaVersion")
    revision: int = Field(ge=0)
    document_sha256: str = Field(alias="documentSha256", pattern=_SHA256)
    preview_svg: str = Field(alias="previewSvg", min_length=40)
    preview_png_base64: str = Field(default="", alias="previewPngBase64")
    transcript_links: list[TranscriptLink] = Field(
        default_factory=list,
        alias="transcriptLinks",
        max_length=100,
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _utc_milliseconds(value: datetime) -> str:
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def validate_preview_svg(content: bytes, *, max_bytes: int, max_nodes: int = 20_000) -> None:
    if len(content) > max_bytes:
        raise ValidationError("SVG preview exceeds the configured size limit")
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, ValueError) as exc:
        raise ValidationError("SVG preview is not well formed") from exc
    if _local_name(root.tag) != "svg":
        raise ValidationError("SVG preview must have an svg root")
    for index, node in enumerate(root.iter(), start=1):
        if index > max_nodes:
            raise ValidationError("SVG preview exceeds the node limit")
        if _local_name(node.tag) in _DENIED_SVG_ELEMENTS:
            raise ValidationError("SVG preview contains a forbidden element")
        for name, value in node.attrib.items():
            local = _local_name(name).lower()
            if local.startswith("on"):
                raise ValidationError("SVG preview contains an event handler")
            if name in _REMOTE_REFERENCE_ATTRIBUTES or local == "href":
                normalized = value.strip().lower()
                if normalized and not normalized.startswith("#"):
                    raise ValidationError("SVG preview contains an external reference")


@dataclass(frozen=True)
class FinalizeBoardEvidenceResult:
    evidence: BoardEvidence
    became_available: bool


class BoardEvidenceService:
    """Finalize immutable board evidence through deterministic two-phase storage."""

    def __init__(
        self,
        database: Database,
        storage: ArtifactStorage,
        organization_id: str,
        *,
        max_svg_bytes: int = 5 * 1024 * 1024,
        max_png_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.database = database
        self.storage = storage
        self.organization_id = organization_id
        self.max_svg_bytes = max_svg_bytes
        self.max_png_bytes = max_png_bytes

    def finalize(
        self,
        document_id: str,
        request: FinalizeBoardEvidenceRequest,
        actor_user_id: str,
    ) -> FinalizeBoardEvidenceResult:
        svg = request.preview_svg.encode()
        validate_preview_svg(svg, max_bytes=self.max_svg_bytes)
        png = self._decode_png(request.preview_png_base64)
        with self.database.sessions() as session:
            document = session.scalar(
                select(BoardDocument)
                .where(
                    BoardDocument.organization_id == self.organization_id,
                    BoardDocument.id == document_id,
                    BoardDocument.deleted_at.is_(None),
                )
                .with_for_update()
            )
            if document is None:
                raise NotFoundError("Board not found")
            if request.revision > document.current_revision:
                raise ConflictError("Evidence revision is newer than the board")
            snapshot = session.scalar(
                select(BoardSnapshot).where(
                    BoardSnapshot.organization_id == self.organization_id,
                    BoardSnapshot.board_document_id == document_id,
                    BoardSnapshot.revision == request.revision,
                    BoardSnapshot.deleted_at.is_(None),
                    BoardSnapshot.storage_status == BoardSnapshotStatus.available.value,
                )
            )
            if snapshot is None:
                raise ConflictError("An available snapshot is required for the evidence revision")
            if snapshot.document_sha256 != request.document_sha256:
                raise ConflictError("Evidence document hash does not match the snapshot")
            existing = session.scalar(
                select(BoardEvidence).where(
                    BoardEvidence.organization_id == self.organization_id,
                    BoardEvidence.board_document_id == document_id,
                    BoardEvidence.revision == request.revision,
                )
            )
            if existing is not None:
                self._require_same_payload(existing, svg, png, request)
                if existing.storage_status == BoardEvidenceStatus.available.value:
                    return FinalizeBoardEvidenceResult(existing, became_available=False)
                evidence = existing
            else:
                evidence_id = self._evidence_id(document_id, request.revision)
                prefix = (
                    f"{self.organization_id}/lessons/{document.lesson_id}/"
                    f"board-evidence/{evidence_id}"
                )
                geometry_summary = self._geometry_summary(session, document_id, request.revision)
                participants = self._participants(session, document_id, request.revision)
                operation_summary = self._operation_summary(
                    session,
                    document_id,
                    request.revision,
                )
                manifest = self._manifest(
                    evidence_id=evidence_id,
                    document=document,
                    snapshot=snapshot,
                    svg=svg,
                    png=png,
                    geometry_summary=geometry_summary,
                    participants=participants,
                    operation_summary=operation_summary,
                    transcript_links=[
                        item.model_dump(mode="json", by_alias=True)
                        for item in request.transcript_links
                    ],
                    finalized_at=datetime.now(UTC),
                )
                manifest_bytes = _canonical_json(manifest)
                evidence = BoardEvidence(
                    id=evidence_id,
                    organization_id=self.organization_id,
                    student_id=document.student_id,
                    lesson_id=document.lesson_id,
                    board_document_id=document.id,
                    snapshot_id=snapshot.id,
                    revision=request.revision,
                    document_schema_version=document.schema_version,
                    document_sha256=request.document_sha256,
                    snapshot_sha256=snapshot.sha256,
                    manifest_storage_key=f"{prefix}/manifest.json",
                    manifest_sha256=_sha256(manifest_bytes),
                    manifest_size=len(manifest_bytes),
                    svg_storage_key=f"{prefix}/preview.svg",
                    svg_sha256=_sha256(svg),
                    svg_size=len(svg),
                    png_storage_key=f"{prefix}/preview.png" if png else "",
                    png_sha256=_sha256(png) if png else "",
                    png_size=len(png),
                    geometry_summary=geometry_summary,
                    transcript_links=[
                        item.model_dump(mode="json", by_alias=True)
                        for item in request.transcript_links
                    ],
                    participants=participants,
                    operation_summary=operation_summary,
                    finalized_by_user_id=actor_user_id,
                    finalized_at=datetime.fromisoformat(manifest["finalizedAt"]),
                )
                session.add(evidence)
            session.commit()

        manifest_bytes = _canonical_json(self._manifest_for(evidence))
        try:
            self._put_verified(evidence.svg_storage_key, svg, "image/svg+xml", evidence.svg_sha256)
            if png:
                self._put_verified(evidence.png_storage_key, png, "image/png", evidence.png_sha256)
            self._put_verified(
                evidence.manifest_storage_key,
                manifest_bytes,
                "application/json",
                evidence.manifest_sha256,
            )
        except Exception as exc:
            self._record_failure(evidence.id, exc)
            raise
        with self.database.sessions() as session:
            stored = session.scalar(
                select(BoardEvidence)
                .where(
                    BoardEvidence.organization_id == self.organization_id,
                    BoardEvidence.id == evidence.id,
                )
                .with_for_update()
            )
            if stored is None:
                raise ConflictError("Evidence metadata disappeared during upload")
            became_available = stored.storage_status != BoardEvidenceStatus.available.value
            stored.storage_status = BoardEvidenceStatus.available.value
            stored.upload_error = ""
            session.commit()
            return FinalizeBoardEvidenceResult(stored, became_available=became_available)

    def list_for_lesson(self, lesson_id: str) -> list[BoardEvidence]:
        with self.database.sessions() as session:
            return list(
                session.scalars(
                    select(BoardEvidence)
                    .where(
                        BoardEvidence.organization_id == self.organization_id,
                        BoardEvidence.lesson_id == lesson_id,
                        BoardEvidence.storage_status == BoardEvidenceStatus.available.value,
                    )
                    .order_by(BoardEvidence.finalized_at.desc())
                )
            )

    def get(self, evidence_id: str) -> BoardEvidence:
        with self.database.sessions() as session:
            evidence = session.scalar(
                select(BoardEvidence).where(
                    BoardEvidence.organization_id == self.organization_id,
                    BoardEvidence.id == evidence_id,
                )
            )
            if evidence is None:
                raise NotFoundError("Board evidence not found")
            return evidence

    def publish(self, evidence_id: str) -> BoardEvidence:
        with self.database.sessions() as session:
            evidence = session.scalar(
                select(BoardEvidence)
                .where(
                    BoardEvidence.organization_id == self.organization_id,
                    BoardEvidence.id == evidence_id,
                    BoardEvidence.storage_status == BoardEvidenceStatus.available.value,
                )
                .with_for_update()
            )
            if evidence is None:
                raise NotFoundError("Board evidence not found")
            evidence.published_at = evidence.published_at or datetime.now(UTC)
            evidence.revoked_at = None
            session.commit()
            return evidence

    def revoke(self, evidence_id: str) -> BoardEvidence:
        with self.database.sessions() as session:
            evidence = session.scalar(
                select(BoardEvidence)
                .where(
                    BoardEvidence.organization_id == self.organization_id,
                    BoardEvidence.id == evidence_id,
                )
                .with_for_update()
            )
            if evidence is None:
                raise NotFoundError("Board evidence not found")
            evidence.revoked_at = datetime.now(UTC)
            session.commit()
            return evidence

    def read_artifact(self, evidence: BoardEvidence, kind: str) -> tuple[bytes, str, str]:
        mapping = {
            "manifest": (
                evidence.manifest_storage_key,
                evidence.manifest_sha256,
                "application/json",
            ),
            "svg": (evidence.svg_storage_key, evidence.svg_sha256, "image/svg+xml"),
            "png": (evidence.png_storage_key, evidence.png_sha256, "image/png"),
        }
        key, expected, media_type = mapping.get(kind, ("", "", ""))
        if not key:
            raise NotFoundError("Evidence artifact not found")
        content = self.storage.read(key)
        if _sha256(content) != expected:
            self._quarantine(evidence.id, "Evidence artifact SHA-256 mismatch")
            raise ConflictError("Evidence artifact failed integrity verification")
        return content, media_type, expected

    def _decode_png(self, encoded: str) -> bytes:
        if not encoded:
            return b""
        try:
            content = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValidationError("previewPngBase64 is not valid base64") from exc
        if len(content) > self.max_png_bytes:
            raise ValidationError("PNG preview exceeds the configured size limit")
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValidationError("PNG preview has an invalid signature")
        return content

    def _require_same_payload(
        self,
        evidence: BoardEvidence,
        svg: bytes,
        png: bytes,
        request: FinalizeBoardEvidenceRequest,
    ) -> None:
        if (
            evidence.document_sha256 != request.document_sha256
            or evidence.svg_sha256 != _sha256(svg)
            or evidence.png_sha256 != (_sha256(png) if png else "")
            or evidence.transcript_links
            != [item.model_dump(mode="json", by_alias=True) for item in request.transcript_links]
        ):
            raise ConflictError("Evidence already exists with different immutable content")

    @staticmethod
    def _evidence_id(document_id: str, revision: int) -> str:
        return hashlib.sha256(f"{document_id}:{revision}".encode()).hexdigest()[:36]

    def _geometry_summary(self, session, document_id: str, revision: int) -> list[dict]:
        return [
            {
                "importId": item.import_id,
                "girSha256": item.gir_sha256,
                "layoutSha256": item.layout_sha256,
                "requestId": item.request_id,
            }
            for item in session.scalars(
                select(BoardGeometryImport)
                .where(
                    BoardGeometryImport.organization_id == self.organization_id,
                    BoardGeometryImport.board_document_id == document_id,
                    BoardGeometryImport.base_revision <= revision,
                )
                .order_by(BoardGeometryImport.created_at)
            )
        ]

    def _participants(self, session, document_id: str, revision: int) -> list[str]:
        return [
            value
            for value in session.scalars(
                select(distinct(BoardCommandBatch.actor_user_id))
                .where(
                    BoardCommandBatch.organization_id == self.organization_id,
                    BoardCommandBatch.board_document_id == document_id,
                    BoardCommandBatch.revision <= revision,
                    BoardCommandBatch.actor_user_id.is_not(None),
                )
                .order_by(BoardCommandBatch.actor_user_id)
            )
            if value is not None
        ]

    def _operation_summary(self, session, document_id: str, revision: int) -> dict:
        payloads = list(
            session.scalars(
                select(BoardCommandBatch.payload).where(
                    BoardCommandBatch.organization_id == self.organization_id,
                    BoardCommandBatch.board_document_id == document_id,
                    BoardCommandBatch.revision <= revision,
                )
            )
        )
        return {
            "batchCount": len(payloads),
            "commandCount": sum(len(item.get("commands", ())) for item in payloads),
        }

    def _manifest(
        self,
        *,
        evidence_id: str,
        document: BoardDocument,
        snapshot: BoardSnapshot,
        svg: bytes,
        png: bytes,
        geometry_summary: list[dict],
        participants: list[str],
        operation_summary: dict,
        transcript_links: list[dict],
        finalized_at: datetime,
    ) -> dict:
        prefix = f"{self.organization_id}/lessons/{document.lesson_id}/board-evidence/{evidence_id}"
        return {
            "schemaVersion": "1.0",
            "evidenceId": evidence_id,
            "lessonId": document.lesson_id,
            "studentId": document.student_id,
            "documentId": document.id,
            "revision": snapshot.revision,
            "documentSchemaVersion": document.schema_version,
            "documentSha256": snapshot.document_sha256,
            "snapshot": {
                "storageKey": snapshot.storage_key,
                "sha256": snapshot.sha256,
                "size": snapshot.size,
            },
            "previewSvg": {
                "storageKey": f"{prefix}/preview.svg",
                "sha256": _sha256(svg),
                "size": len(svg),
            },
            "previewPng": (
                {
                    "storageKey": f"{prefix}/preview.png",
                    "sha256": _sha256(png),
                    "size": len(png),
                }
                if png
                else None
            ),
            "geometryImports": geometry_summary,
            "participants": participants,
            "operationSummary": operation_summary,
            "transcriptLinks": transcript_links,
            "finalizedAt": _utc_milliseconds(finalized_at),
        }

    def _manifest_for(self, evidence: BoardEvidence) -> dict:
        with self.database.sessions() as session:
            snapshot = session.scalar(
                select(BoardSnapshot).where(
                    BoardSnapshot.organization_id == self.organization_id,
                    BoardSnapshot.id == evidence.snapshot_id,
                )
            )
            if snapshot is None:
                raise ConflictError("Evidence snapshot no longer exists")
        return {
            "schemaVersion": evidence.schema_version,
            "evidenceId": evidence.id,
            "lessonId": evidence.lesson_id,
            "studentId": evidence.student_id,
            "documentId": evidence.board_document_id,
            "revision": evidence.revision,
            "documentSchemaVersion": evidence.document_schema_version,
            "documentSha256": evidence.document_sha256,
            "snapshot": {
                "storageKey": snapshot.storage_key,
                "sha256": evidence.snapshot_sha256,
                "size": snapshot.size,
            },
            "previewSvg": {
                "storageKey": evidence.svg_storage_key,
                "sha256": evidence.svg_sha256,
                "size": evidence.svg_size,
            },
            "previewPng": (
                {
                    "storageKey": evidence.png_storage_key,
                    "sha256": evidence.png_sha256,
                    "size": evidence.png_size,
                }
                if evidence.png_storage_key
                else None
            ),
            "geometryImports": evidence.geometry_summary,
            "participants": evidence.participants,
            "operationSummary": evidence.operation_summary,
            "transcriptLinks": evidence.transcript_links,
            "finalizedAt": _utc_milliseconds(evidence.finalized_at),
        }

    def _put_verified(self, key: str, content: bytes, media_type: str, expected: str) -> None:
        stored = self.storage.put(key, content, media_type)
        if stored.sha256 != expected or stored.size != len(content):
            raise ConflictError("Artifact storage returned a different digest or size")

    def _record_failure(self, evidence_id: str, exc: Exception) -> None:
        with self.database.sessions() as session:
            evidence = session.scalar(
                select(BoardEvidence)
                .where(
                    BoardEvidence.organization_id == self.organization_id,
                    BoardEvidence.id == evidence_id,
                )
                .with_for_update()
            )
            if evidence is not None:
                evidence.upload_error = str(exc)[:2000]
                session.commit()

    def _quarantine(self, evidence_id: str, reason: str) -> None:
        with self.database.sessions() as session:
            evidence = session.scalar(
                select(BoardEvidence)
                .where(
                    BoardEvidence.organization_id == self.organization_id,
                    BoardEvidence.id == evidence_id,
                )
                .with_for_update()
            )
            if evidence is not None:
                evidence.storage_status = BoardEvidenceStatus.quarantined.value
                evidence.upload_error = reason[:2000]
                session.commit()
