from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.collaboration import CollaborationBroker
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.providers.artifacts import LocalArtifactStorage, S3ArtifactStorage
from tutor_assistant_web.shared.contracts import ArtifactStorage
from tutor_assistant_web.shared.web import WebSupport


@dataclass(frozen=True)
class BoardAppContainer:
    settings: Settings
    database: Database
    timezone: ZoneInfo
    templates: Jinja2Templates
    web: WebSupport
    identity: IdentityService
    artifact_storage: ArtifactStorage
    collaboration: CollaborationBroker

    def audit_service(self, organization_id: str):
        from tutor_assistant_web.modules.audit.application import AuditService

        return AuditService(self.database, organization_id)

    def boards_service(self, organization_id: str):
        from tutor_assistant_web.modules.boards.application import BoardPersistenceService

        return BoardPersistenceService(
            self.database,
            self.artifact_storage,
            organization_id,
            max_command_bytes=self.settings.board_command_max_size_mb * 1024 * 1024,
            max_snapshot_bytes=self.settings.board_snapshot_max_size_mb * 1024 * 1024,
            snapshot_interval_commands=self.settings.board_snapshot_interval_commands,
            snapshot_interval_bytes=self.settings.board_snapshot_interval_mb * 1024 * 1024,
            delete_grace_days=self.settings.board_delete_grace_days,
        )

    def board_guest_access_service(self):
        from tutor_assistant_web.modules.boards.guest_access import BoardGuestAccessService

        return BoardGuestAccessService(self.database, self.settings)


def _allowed_mime_types(settings: Settings) -> set[str]:
    return {
        item.strip().lower()
        for item in settings.artifact_allowed_mime_types.split(",")
        if item.strip()
    }


def build_board_artifact_storage(settings: Settings) -> ArtifactStorage:
    common = {
        "max_bytes": settings.artifact_max_size_mb * 1024 * 1024,
        "allowed_mime_types": _allowed_mime_types(settings),
        "scanner": None,
    }
    if settings.artifact_storage_provider.lower() == "s3":
        return S3ArtifactStorage(
            settings.artifact_s3_bucket,
            endpoint_url=settings.artifact_s3_endpoint_url,
            region=settings.artifact_s3_region,
            access_key=settings.artifact_s3_access_key,
            secret_key=settings.artifact_s3_secret_key,
            server_side_encryption=settings.artifact_s3_server_side_encryption,
            **common,
        )
    return LocalArtifactStorage(settings.artifact_storage_root, **common)


def build_board_container(
    settings: Settings,
    database: Database,
    templates: Jinja2Templates,
    timezone: ZoneInfo,
) -> BoardAppContainer:
    identity = IdentityService(database)
    artifact_storage = build_board_artifact_storage(settings)
    collaboration = CollaborationBroker(
        settings.redis_url,
        distributed=True,
        presence_ttl_seconds=settings.board_collaboration_presence_ttl_seconds,
        ticket_ttl_seconds=settings.board_collaboration_ticket_ttl_seconds,
    )
    return BoardAppContainer(
        settings=settings,
        database=database,
        timezone=timezone,
        templates=templates,
        web=WebSupport(settings, templates, timezone, identity),
        identity=identity,
        artifact_storage=artifact_storage,
        collaboration=collaboration,
    )
