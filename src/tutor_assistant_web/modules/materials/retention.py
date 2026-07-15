from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.materials.models import (
    ArtifactStorageStatus,
    ArtifactVersion,
)
from tutor_assistant_web.shared.contracts import ArtifactStorage
from tutor_assistant_web.shared.errors import NotFoundError


class ArtifactLifecycleService:
    def __init__(
        self,
        database: Database,
        storage: ArtifactStorage,
        *,
        delete_grace_days: int = 30,
    ) -> None:
        self.database = database
        self.storage = storage
        self.delete_grace_days = delete_grace_days

    def soft_delete(self, organization_id: str, artifact_id: str) -> ArtifactVersion:
        now = datetime.now(UTC)
        with self.database.sessions() as session:
            artifact = session.scalar(
                select(ArtifactVersion)
                .where(
                    ArtifactVersion.id == artifact_id,
                    ArtifactVersion.organization_id == organization_id,
                )
                .with_for_update()
            )
            if artifact is None:
                raise NotFoundError("Файл не найден")
            artifact.storage_status = ArtifactStorageStatus.deleted.value
            artifact.deleted_at = now
            artifact.purge_after = now + timedelta(days=self.delete_grace_days)
            session.commit()
            return artifact

    def purge_due(self, limit: int = 100) -> int:
        now = datetime.now(UTC)
        with self.database.sessions() as session:
            artifacts = list(
                session.scalars(
                    select(ArtifactVersion)
                    .where(
                        ArtifactVersion.storage_status == ArtifactStorageStatus.deleted.value,
                        ArtifactVersion.purge_after <= now,
                    )
                    .order_by(ArtifactVersion.purge_after)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for artifact in artifacts:
                self.storage.delete(artifact.storage_key)
                artifact.purge_after = None
            session.commit()
            return len(artifacts)

    def expire_retention(self, retention_days: int, limit: int = 100) -> int:
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=retention_days)
        with self.database.sessions() as session:
            artifacts = list(
                session.scalars(
                    select(ArtifactVersion)
                    .where(
                        ArtifactVersion.storage_status == ArtifactStorageStatus.available.value,
                        ArtifactVersion.created_at <= cutoff,
                    )
                    .order_by(ArtifactVersion.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for artifact in artifacts:
                artifact.storage_status = ArtifactStorageStatus.deleted.value
                artifact.deleted_at = now
                artifact.purge_after = now + timedelta(days=self.delete_grace_days)
            session.commit()
            return len(artifacts)

    def verify_integrity(self, limit: int = 100) -> dict[str, int]:
        checked = quarantined = 0
        with self.database.sessions() as session:
            artifacts = list(
                session.scalars(
                    select(ArtifactVersion)
                    .where(ArtifactVersion.storage_status == ArtifactStorageStatus.available.value)
                    .order_by(ArtifactVersion.verified_at.asc().nullsfirst())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for artifact in artifacts:
                digest = hashlib.sha256()
                size = 0
                try:
                    for chunk in self.storage.iter_bytes(artifact.storage_key):
                        digest.update(chunk)
                        size += len(chunk)
                    if digest.hexdigest() != artifact.sha256 or size != artifact.size:
                        raise ValueError("stored size or SHA-256 differs from database")
                except Exception as exc:
                    artifact.storage_status = ArtifactStorageStatus.quarantined.value
                    artifact.quarantine_reason = str(exc)[:2000]
                    quarantined += 1
                else:
                    artifact.verified_at = datetime.now(UTC)
                checked += 1
            session.commit()
        return {"checked": checked, "quarantined": quarantined}


class LocalArtifactMigrator:
    def __init__(self, database: Database, local_root: str | Path, target: ArtifactStorage) -> None:
        self.database = database
        self.local_root = Path(local_root).resolve()
        self.target = target

    def migrate(self, limit: int = 100, *, dry_run: bool = False) -> dict[str, int]:
        migrated = missing = skipped = 0
        with self.database.sessions() as session:
            artifacts = list(
                session.scalars(
                    select(ArtifactVersion)
                    .where(
                        ArtifactVersion.storage_status.in_(
                            [
                                ArtifactStorageStatus.available.value,
                                ArtifactStorageStatus.uploading.value,
                            ]
                        )
                    )
                    .order_by(ArtifactVersion.created_at)
                    .limit(limit)
                )
            )
            for artifact in artifacts:
                path = (self.local_root / artifact.storage_key).resolve()
                if self.local_root not in path.parents or not path.is_file():
                    missing += 1
                    continue
                try:
                    already_stored = self.target.stat(artifact.storage_key)
                except (FileNotFoundError, OSError):
                    already_stored = None
                except Exception as exc:
                    if exc.__class__.__name__ not in {"ClientError", "NoSuchKey"}:
                        raise
                    already_stored = None
                if already_stored and already_stored.sha256 == artifact.sha256:
                    skipped += 1
                    continue
                if dry_run:
                    migrated += 1
                    continue
                with path.open("rb") as source:
                    stored = self.target.put_stream(
                        artifact.storage_key,
                        source,
                        artifact.media_type,
                        expected_sha256=artifact.sha256,
                        max_bytes=artifact.size,
                    )
                artifact.sha256 = stored.sha256
                artifact.size = stored.size
                artifact.storage_status = ArtifactStorageStatus.available.value
                artifact.verified_at = datetime.now(UTC)
                migrated += 1
            if not dry_run:
                session.commit()
        return {"migrated": migrated, "missing": missing, "skipped": skipped}
