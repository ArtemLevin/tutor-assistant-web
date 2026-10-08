from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO

from sqlalchemy import func, select

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.models import (
    BoardDocument,
    BoardMediaAsset,
    BoardMediaAssetStatus,
)
from tutor_assistant_web.providers.artifacts import (
    ArtifactChecksumMismatch,
    ArtifactMimeMismatch,
    ArtifactQuarantined,
    ArtifactTooLarge,
    validate_mime,
)
from tutor_assistant_web.shared.contracts import ArtifactStorage
from tutor_assistant_web.shared.errors import (
    ApplicationError,
    ConflictError,
    GoneError,
    NotFoundError,
    ValidationError,
)
from tutor_assistant_web.shared.models import new_id

_LOGGER = logging.getLogger(__name__)
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MEDIA_MIME_TYPES = {"image/png", "image/jpeg", "image/gif"}


class BoardMediaDisabled(ApplicationError):
    status_code = 503


class BoardMediaTooLarge(ApplicationError):
    status_code = 413


class BoardMediaQuotaExceeded(ApplicationError):
    status_code = 413


@dataclass(frozen=True)
class BoardMediaAnalysis:
    byte_size: int
    content_sha256: str
    height: int
    mime_type: str
    width: int


@dataclass(frozen=True)
class BoardMediaUnreferencedReport:
    """Durable per-board inventory. Available assets remain usable by queued commands."""

    uploading_count: int
    uploading_bytes: int
    available_count: int
    available_bytes: int


class BoardMediaService:
    def __init__(
        self,
        database: Database,
        storage: ArtifactStorage,
        organization_id: str,
        *,
        uploads_enabled: bool,
        max_asset_bytes: int,
        max_assets_per_board: int,
        max_bytes_per_board: int,
        max_dimension: int,
        max_pixels: int,
        gif_max_frames: int,
        gif_max_decoded_pixels: int,
    ) -> None:
        self.database = database
        self.storage = storage
        self.organization_id = organization_id
        self.uploads_enabled = uploads_enabled
        self.max_asset_bytes = max_asset_bytes
        self.max_assets_per_board = max_assets_per_board
        self.max_bytes_per_board = max_bytes_per_board
        self.max_dimension = max_dimension
        self.max_pixels = max_pixels
        self.gif_max_frames = gif_max_frames
        self.gif_max_decoded_pixels = gif_max_decoded_pixels

    def upload(
        self,
        document_id: str,
        stream: BinaryIO,
        *,
        declared_mime_type: str,
        file_name: str,
        expected_sha256: str,
        idempotency_key: str,
        created_by_user_id: str | None,
        created_by_actor_id: str,
        reauthorize: Callable[[], None] | None = None,
    ) -> BoardMediaAsset:
        if not self.uploads_enabled:
            raise BoardMediaDisabled("Board media uploads are disabled")
        if not _SHA256.fullmatch(expected_sha256):
            raise ValidationError("X-Content-SHA256 must contain a lowercase SHA-256 digest")
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValidationError("X-Idempotency-Key is invalid")
        if not 1 <= len(created_by_actor_id) <= 128:
            raise ValidationError("Media creator actor identifier is invalid")

        mime_type = declared_mime_type.partition(";")[0].strip().lower()
        if mime_type not in _MEDIA_MIME_TYPES:
            raise ValidationError("Supported board media types are PNG, JPEG and GIF")
        normalized_name = _safe_file_name(file_name)
        analysis = self._analyze(stream, mime_type, expected_sha256)

        asset, upload_required = self._reserve(
            document_id,
            analysis,
            normalized_name,
            idempotency_key,
            created_by_user_id,
            created_by_actor_id,
        )
        if not upload_required:
            return asset

        stream.seek(0)
        try:
            stored = self.storage.put_stream(
                asset.storage_key,
                stream,
                analysis.mime_type,
                expected_sha256=analysis.content_sha256,
                max_bytes=self.max_asset_bytes,
            )
        except ArtifactTooLarge as exc:
            self._delete_storage_quietly(asset.storage_key)
            self._mark_deleted(asset.id, "Artifact storage rejected the media size")
            raise BoardMediaTooLarge("Media file exceeds the configured storage limit") from exc
        except ArtifactChecksumMismatch as exc:
            self._delete_storage_quietly(asset.storage_key)
            self._mark_quarantined(asset.id, "Artifact storage checksum mismatch")
            raise ValidationError("Media checksum changed during storage") from exc
        except ArtifactMimeMismatch as exc:
            self._delete_storage_quietly(asset.storage_key)
            self._mark_quarantined(asset.id, "Artifact storage MIME mismatch")
            raise ValidationError("Media type changed during storage") from exc
        except ArtifactQuarantined as exc:
            self._delete_storage_quietly(asset.storage_key)
            self._mark_quarantined(asset.id, "Antivirus rejected uploaded media")
            raise ValidationError("Uploaded media was rejected by security scanning") from exc
        except Exception as exc:
            # A provider may persist bytes before reporting failure (for example,
            # after a lost S3 acknowledgement). The asset key is upload-unique.
            self._delete_storage_quietly(asset.storage_key)
            self._mark_deleted(asset.id, f"Storage upload failed: {exc}")
            raise

        if (
            stored.sha256 != analysis.content_sha256
            or stored.size != analysis.byte_size
            or stored.media_type.partition(";")[0].strip().lower() != analysis.mime_type
        ):
            self._delete_storage_quietly(asset.storage_key)
            self._mark_quarantined(
                asset.id,
                "Artifact storage returned media metadata different from the validated upload",
            )
            raise ConflictError("Stored media metadata does not match the validated upload")

        try:
            if reauthorize is not None:
                reauthorize()
            return self._finalize(asset.id, document_id)
        except Exception:
            self._delete_storage_quietly(asset.storage_key)
            self._mark_deleted(asset.id, "Upload authorization changed before finalization")
            raise

    def get(self, document_id: str, asset_id: str) -> BoardMediaAsset:
        if len(asset_id) > 128:
            raise NotFoundError("Board media asset not found")
        with self.database.sessions() as session:
            asset = session.scalar(
                select(BoardMediaAsset).where(
                    BoardMediaAsset.organization_id == self.organization_id,
                    BoardMediaAsset.board_document_id == document_id,
                    BoardMediaAsset.asset_id == asset_id,
                    BoardMediaAsset.storage_status == BoardMediaAssetStatus.available.value,
                    BoardMediaAsset.deleted_at.is_(None),
                )
            )
            if asset is None:
                raise NotFoundError("Board media asset not found")
            return asset

    def unreferenced_report(self, document_id: str) -> BoardMediaUnreferencedReport:
        """Report quota-consuming uploads without journal references.

        No automatic deletion: an AVAILABLE asset may still be referenced by
        a delayed offline command. The report is tenant- and board-scoped.
        """
        with self.database.sessions() as session:
            self._locked_active_document(session, document_id)
            totals = session.execute(
                select(
                    BoardMediaAsset.storage_status,
                    func.count(BoardMediaAsset.id),
                    func.coalesce(func.sum(BoardMediaAsset.byte_size), 0),
                )
                .where(
                    BoardMediaAsset.organization_id == self.organization_id,
                    BoardMediaAsset.board_document_id == document_id,
                    BoardMediaAsset.first_referenced_revision.is_(None),
                    BoardMediaAsset.deleted_at.is_(None),
                    BoardMediaAsset.storage_status.in_(
                        (
                            BoardMediaAssetStatus.uploading.value,
                            BoardMediaAssetStatus.available.value,
                        )
                    ),
                )
                .group_by(BoardMediaAsset.storage_status)
            ).all()
        by_status = {status: (int(count), int(size)) for status, count, size in totals}
        uploading = by_status.get(BoardMediaAssetStatus.uploading.value, (0, 0))
        available = by_status.get(BoardMediaAssetStatus.available.value, (0, 0))
        return BoardMediaUnreferencedReport(
            uploading_count=uploading[0],
            uploading_bytes=uploading[1],
            available_count=available[0],
            available_bytes=available[1],
        )

    def iter_content(
        self,
        asset: BoardMediaAsset,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        return self.storage.iter_bytes(asset.storage_key, chunk_size)

    def _analyze(
        self,
        stream: BinaryIO,
        mime_type: str,
        expected_sha256: str,
    ) -> BoardMediaAnalysis:
        stream.seek(0)
        digest = hashlib.sha256()
        total = 0
        head = bytearray()
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > self.max_asset_bytes:
                raise BoardMediaTooLarge(f"Media file exceeds {self.max_asset_bytes} bytes")
            if len(head) < 8192:
                head.extend(chunk[: 8192 - len(head)])
            digest.update(chunk)
        if total == 0:
            raise ValidationError("Media file is empty")

        checksum = digest.hexdigest()
        if checksum != expected_sha256:
            raise ValidationError("X-Content-SHA256 does not match the uploaded media")
        try:
            validate_mime(bytes(head), mime_type, _MEDIA_MIME_TYPES)
        except ArtifactMimeMismatch as exc:
            raise ValidationError("Declared media MIME type does not match its bytes") from exc

        if mime_type == "image/png":
            width, height = _png_dimensions(stream)
        elif mime_type == "image/jpeg":
            width, height = _jpeg_dimensions(stream)
        else:
            width, height = _gif_dimensions(
                stream,
                max_frames=self.gif_max_frames,
                max_frame_pixels=self.max_pixels,
                max_decoded_pixels=self.gif_max_decoded_pixels,
            )

        if width > self.max_dimension or height > self.max_dimension:
            raise ValidationError("Media dimensions exceed the configured limit")
        if width * height > self.max_pixels:
            raise ValidationError("Media pixel count exceeds the configured limit")
        stream.seek(0)
        return BoardMediaAnalysis(
            byte_size=total,
            content_sha256=checksum,
            height=height,
            mime_type=mime_type,
            width=width,
        )

    def _reserve(
        self,
        document_id: str,
        analysis: BoardMediaAnalysis,
        file_name: str,
        idempotency_key: str,
        created_by_user_id: str | None,
        created_by_actor_id: str,
    ) -> tuple[BoardMediaAsset, bool]:
        with self.database.sessions() as session:
            self._locked_active_document(session, document_id)
            existing = session.scalar(
                select(BoardMediaAsset)
                .where(
                    BoardMediaAsset.organization_id == self.organization_id,
                    BoardMediaAsset.board_document_id == document_id,
                    BoardMediaAsset.upload_idempotency_key == idempotency_key,
                )
                .with_for_update()
            )
            if existing is not None:
                if not _same_upload(existing, analysis, file_name):
                    raise ConflictError(
                        "Media upload idempotency key is already used for another payload"
                    )
                if existing.storage_status == BoardMediaAssetStatus.available.value:
                    return existing, False
                if existing.storage_status == BoardMediaAssetStatus.uploading.value:
                    raise ConflictError("Media upload with this idempotency key is in progress")
                if existing.storage_status == BoardMediaAssetStatus.quarantined.value:
                    raise ConflictError("Media upload with this idempotency key was quarantined")
                self._enforce_quota(session, document_id, analysis.byte_size)
                existing.storage_status = BoardMediaAssetStatus.uploading.value
                existing.upload_error = ""
                existing.deleted_at = None
                existing.purge_after = None
                session.commit()
                return existing, True

            self._enforce_quota(session, document_id, analysis.byte_size)
            record_id = new_id()
            asset_id = f"asset:{record_id}"
            asset = BoardMediaAsset(
                id=record_id,
                organization_id=self.organization_id,
                board_document_id=document_id,
                asset_id=asset_id,
                storage_key=(
                    f"{self.organization_id}/boards/{document_id}/media/"
                    f"{asset_id}/{analysis.content_sha256}"
                ),
                content_sha256=analysis.content_sha256,
                byte_size=analysis.byte_size,
                mime_type=analysis.mime_type,
                file_name=file_name,
                intrinsic_width=analysis.width,
                intrinsic_height=analysis.height,
                storage_status=BoardMediaAssetStatus.uploading.value,
                upload_idempotency_key=idempotency_key,
                created_by_user_id=created_by_user_id,
                created_by_actor_id=created_by_actor_id,
            )
            session.add(asset)
            session.commit()
            return asset, True

    def _enforce_quota(self, session, document_id: str, incoming_bytes: int) -> None:
        active = (
            BoardMediaAssetStatus.uploading.value,
            BoardMediaAssetStatus.available.value,
        )
        count, total = session.execute(
            select(
                func.count(BoardMediaAsset.id),
                func.coalesce(func.sum(BoardMediaAsset.byte_size), 0),
            ).where(
                BoardMediaAsset.organization_id == self.organization_id,
                BoardMediaAsset.board_document_id == document_id,
                BoardMediaAsset.storage_status.in_(active),
                BoardMediaAsset.deleted_at.is_(None),
            )
        ).one()
        if int(count) >= self.max_assets_per_board:
            raise BoardMediaQuotaExceeded("Board media asset count quota is exceeded")
        if int(total) + incoming_bytes > self.max_bytes_per_board:
            raise BoardMediaQuotaExceeded("Board media byte quota is exceeded")

    def _finalize(self, asset_row_id: str, document_id: str) -> BoardMediaAsset:
        with self.database.sessions() as session:
            self._locked_active_document(session, document_id)
            asset = session.scalar(
                select(BoardMediaAsset)
                .where(
                    BoardMediaAsset.id == asset_row_id,
                    BoardMediaAsset.organization_id == self.organization_id,
                    BoardMediaAsset.board_document_id == document_id,
                )
                .with_for_update()
            )
            if asset is None:
                raise ConflictError("Media metadata disappeared during upload")
            if asset.storage_status != BoardMediaAssetStatus.uploading.value:
                raise ConflictError("Media upload changed state during finalization")
            asset.storage_status = BoardMediaAssetStatus.available.value
            asset.upload_error = ""
            asset.verified_at = datetime.now(UTC)
            session.commit()
            return asset

    def _locked_active_document(self, session, document_id: str) -> BoardDocument:
        document = session.scalar(
            select(BoardDocument)
            .where(
                BoardDocument.organization_id == self.organization_id,
                BoardDocument.id == document_id,
            )
            .with_for_update()
        )
        if document is None:
            raise NotFoundError("Board not found")
        if document.deleted_at is not None:
            raise GoneError("Board is deleted")
        if document.archived_at is not None:
            raise GoneError("Board is archived")
        return document

    def _mark_quarantined(self, asset_row_id: str, reason: str) -> None:
        self._set_failed_status(
            asset_row_id,
            BoardMediaAssetStatus.quarantined,
            reason,
        )

    def _mark_deleted(self, asset_row_id: str, reason: str) -> None:
        self._set_failed_status(asset_row_id, BoardMediaAssetStatus.deleted, reason)

    def _set_failed_status(
        self,
        asset_row_id: str,
        status: BoardMediaAssetStatus,
        reason: str,
    ) -> None:
        with self.database.sessions() as session:
            asset = session.scalar(
                select(BoardMediaAsset)
                .where(
                    BoardMediaAsset.id == asset_row_id,
                    BoardMediaAsset.organization_id == self.organization_id,
                )
                .with_for_update()
            )
            if asset is None:
                return
            asset.storage_status = status.value
            asset.upload_error = reason[:2000]
            if status == BoardMediaAssetStatus.deleted:
                asset.deleted_at = datetime.now(UTC)
            session.commit()

    def _delete_storage_quietly(self, storage_key: str) -> None:
        try:
            self.storage.delete(storage_key)
        except Exception:
            _LOGGER.exception(
                "Failed to clean up board media storage object",
                extra={"event": "board.media.cleanup_failed"},
            )


def _same_upload(
    asset: BoardMediaAsset,
    analysis: BoardMediaAnalysis,
    file_name: str,
) -> bool:
    return bool(
        asset.content_sha256 == analysis.content_sha256
        and asset.byte_size == analysis.byte_size
        and asset.mime_type == analysis.mime_type
        and asset.file_name == file_name
        and asset.intrinsic_width == analysis.width
        and asset.intrinsic_height == analysis.height
    )


def _safe_file_name(value: str) -> str:
    leaf = value.replace("\\", "/").split("/")[-1]
    filtered = "".join(
        character for character in leaf if ord(character) > 31 and ord(character) != 127
    ).strip()
    return (filtered or "media")[:256]


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise ValidationError("Media file is truncated")
    return data


def _discard_exact(stream: BinaryIO, size: int) -> None:
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, 64 * 1024))
        if not chunk:
            raise ValidationError("Media file is truncated")
        remaining -= len(chunk)


def _png_dimensions(stream: BinaryIO) -> tuple[int, int]:
    stream.seek(0)
    if _read_exact(stream, 8) != b"\x89PNG\r\n\x1a\n":
        raise ValidationError("PNG signature is invalid")
    length = int.from_bytes(_read_exact(stream, 4), "big")
    chunk_type = _read_exact(stream, 4)
    if length != 13 or chunk_type != b"IHDR":
        raise ValidationError("PNG IHDR chunk is invalid")
    ihdr = _read_exact(stream, 13)
    _read_exact(stream, 4)
    width = int.from_bytes(ihdr[0:4], "big")
    height = int.from_bytes(ihdr[4:8], "big")
    if width <= 0 or height <= 0:
        raise ValidationError("PNG dimensions are invalid")

    while True:
        length_bytes = stream.read(4)
        if not length_bytes:
            raise ValidationError("PNG IEND chunk is missing")
        if len(length_bytes) != 4:
            raise ValidationError("Media file is truncated")
        length = int.from_bytes(length_bytes, "big")
        chunk_type = _read_exact(stream, 4)
        _discard_exact(stream, length)
        _read_exact(stream, 4)
        if chunk_type == b"IEND":
            if length != 0:
                raise ValidationError("PNG IEND chunk is invalid")
            return width, height


_JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


def _jpeg_dimensions(stream: BinaryIO) -> tuple[int, int]:
    stream.seek(0)
    if _read_exact(stream, 2) != b"\xff\xd8":
        raise ValidationError("JPEG signature is invalid")
    dimensions: tuple[int, int] | None = None
    while dimensions is None:
        prefix = stream.read(1)
        if not prefix:
            raise ValidationError("JPEG frame header is missing")
        if prefix != b"\xff":
            continue
        marker_byte = stream.read(1)
        while marker_byte == b"\xff":
            marker_byte = stream.read(1)
        if not marker_byte:
            raise ValidationError("JPEG marker is truncated")
        marker = marker_byte[0]
        if marker in {0x01, 0xD8} or 0xD0 <= marker <= 0xD7:
            continue
        if marker in {0xD9, 0xDA}:
            raise ValidationError("JPEG frame header is missing")
        segment_length = int.from_bytes(_read_exact(stream, 2), "big")
        if segment_length < 2:
            raise ValidationError("JPEG segment length is invalid")
        payload_length = segment_length - 2
        if marker in _JPEG_SOF_MARKERS:
            if payload_length < 5:
                raise ValidationError("JPEG frame header is invalid")
            frame = _read_exact(stream, 5)
            height = int.from_bytes(frame[1:3], "big")
            width = int.from_bytes(frame[3:5], "big")
            if width <= 0 or height <= 0:
                raise ValidationError("JPEG dimensions are invalid")
            dimensions = (width, height)
        else:
            _discard_exact(stream, payload_length)

    stream.seek(0, 2)
    file_size = stream.tell()
    if file_size < 4:
        raise ValidationError("JPEG file is truncated")
    stream.seek(-2, 2)
    if _read_exact(stream, 2) != b"\xff\xd9":
        raise ValidationError("JPEG end marker is missing")
    return dimensions


def _gif_dimensions(
    stream: BinaryIO,
    *,
    max_frames: int,
    max_frame_pixels: int,
    max_decoded_pixels: int,
) -> tuple[int, int]:
    stream.seek(0)
    header = _read_exact(stream, 13)
    if header[:6] not in {b"GIF87a", b"GIF89a"}:
        raise ValidationError("GIF signature is invalid")
    width = int.from_bytes(header[6:8], "little")
    height = int.from_bytes(header[8:10], "little")
    if width <= 0 or height <= 0:
        raise ValidationError("GIF dimensions are invalid")

    packed = header[10]
    if packed & 0x80:
        _discard_exact(stream, 3 * (2 ** ((packed & 0x07) + 1)))

    frame_count = 0
    decoded_pixels = 0
    while True:
        introducer = stream.read(1)
        if not introducer:
            raise ValidationError("GIF trailer is missing")
        value = introducer[0]
        if value == 0x3B:
            if frame_count == 0:
                raise ValidationError("GIF contains no image frames")
            return width, height
        if value == 0x21:
            _read_exact(stream, 1)
            _discard_gif_sub_blocks(stream)
            continue
        if value != 0x2C:
            raise ValidationError("GIF block structure is invalid")

        descriptor = _read_exact(stream, 9)
        frame_width = int.from_bytes(descriptor[4:6], "little")
        frame_height = int.from_bytes(descriptor[6:8], "little")
        if frame_width <= 0 or frame_height <= 0:
            raise ValidationError("GIF frame dimensions are invalid")
        frame_pixels = frame_width * frame_height
        if frame_pixels > max_frame_pixels:
            raise ValidationError("GIF frame pixel count exceeds the configured limit")
        frame_count += 1
        decoded_pixels += frame_pixels
        if frame_count > max_frames:
            raise ValidationError("GIF frame count exceeds the configured limit")
        if decoded_pixels > max_decoded_pixels:
            raise ValidationError("GIF decoded pixel budget exceeds the configured limit")

        local_packed = descriptor[8]
        if local_packed & 0x80:
            _discard_exact(stream, 3 * (2 ** ((local_packed & 0x07) + 1)))
        lzw_minimum_code_size = _read_exact(stream, 1)[0]
        if not 2 <= lzw_minimum_code_size <= 8:
            raise ValidationError("GIF LZW code size is invalid")
        _discard_gif_sub_blocks(stream)


def _discard_gif_sub_blocks(stream: BinaryIO) -> None:
    while True:
        size = _read_exact(stream, 1)[0]
        if size == 0:
            return
        _discard_exact(stream, size)
