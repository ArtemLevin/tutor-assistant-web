from __future__ import annotations

import argparse
import json

from tutor_assistant_web.bootstrap.container import build_artifact_storage
from tutor_assistant_web.config import get_settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.materials.retention import (
    ArtifactLifecycleService,
    LocalArtifactMigrator,
)
from tutor_assistant_web.providers.artifacts import S3ArtifactStorage


def main() -> None:
    parser = argparse.ArgumentParser(description="Artifact storage operations")
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate-local")
    migrate.add_argument("--limit", type=int, default=100)
    migrate.add_argument("--dry-run", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--limit", type=int, default=100)
    purge = commands.add_parser("purge")
    purge.add_argument("--limit", type=int, default=100)
    remove = commands.add_parser("soft-delete")
    remove.add_argument("artifact_id")
    remove.add_argument("--organization", required=True)
    commands.add_parser("configure-bucket")
    args = parser.parse_args()
    settings = get_settings()
    database = Database.from_settings(settings)
    storage = build_artifact_storage(settings)
    try:
        if args.command == "configure-bucket":
            if not isinstance(storage, S3ArtifactStorage):
                raise SystemExit("S3 storage must be configured")
            storage.ensure_private_bucket()
            storage.configure_lifecycle(
                settings.artifact_retention_days,
                settings.artifact_abort_multipart_days,
            )
            result = {"bucket": storage.bucket, "private": True}
        elif args.command == "migrate-local":
            if not isinstance(storage, S3ArtifactStorage):
                raise SystemExit("S3 target storage must be configured")
            result = LocalArtifactMigrator(
                database, settings.artifact_storage_root, storage
            ).migrate(args.limit, dry_run=args.dry_run)
        else:
            lifecycle = ArtifactLifecycleService(
                database, storage, delete_grace_days=settings.artifact_delete_grace_days
            )
            if args.command == "verify":
                result = lifecycle.verify_integrity(args.limit)
            elif args.command == "soft-delete":
                artifact = lifecycle.soft_delete(args.organization, args.artifact_id)
                result = {
                    "artifact_id": artifact.id,
                    "status": artifact.storage_status,
                    "purge_after": artifact.purge_after.isoformat(),
                }
            else:
                result = {"purged": lifecycle.purge_due(args.limit)}
        print(json.dumps(result))
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
