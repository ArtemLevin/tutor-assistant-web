from __future__ import annotations

import threading
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from tutor_assistant_web.bbb import BigBlueButtonClient
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.providers.artifacts import (
    ClamAVScanner,
    LocalArtifactStorage,
    S3ArtifactStorage,
)
from tutor_assistant_web.providers.conference import (
    BigBlueButtonConferenceProvider,
    DemoConferenceProvider,
)
from tutor_assistant_web.providers.documents import (
    LatexedDocumentEngine,
    LocalDocumentEngine,
)
from tutor_assistant_web.providers.materials import (
    LocalTemplateMaterialGenerator,
    WebhookMaterialGenerator,
)
from tutor_assistant_web.providers.resilience import CircuitBreaker
from tutor_assistant_web.providers.tasks import CeleryJobDispatcher, InlineJobDispatcher
from tutor_assistant_web.providers.transcription import (
    DemoTranscriptionProvider,
    DisabledTranscriptionProvider,
    FasterWhisperTranscriptionProvider,
    WebhookTranscriptionProvider,
)
from tutor_assistant_web.shared.contracts import (
    ArtifactStorage,
    ConferenceProvider,
    DocumentEngine,
    JobDispatcher,
    MaterialGenerator,
    TranscriptionProvider,
)
from tutor_assistant_web.shared.web import WebSupport

_CIRCUITS: dict[tuple[str, int, int], CircuitBreaker] = {}
_CIRCUITS_LOCK = threading.Lock()


@dataclass(frozen=True)
class AppContainer:
    settings: Settings
    database: Database
    timezone: ZoneInfo
    templates: Jinja2Templates
    web: WebSupport
    identity: IdentityService
    conference: ConferenceProvider
    materials: MaterialGenerator
    transcription: TranscriptionProvider
    jobs: JobDispatcher
    document_engine: DocumentEngine
    artifact_storage: ArtifactStorage

    def classroom_service(self, organization_id: str | None):
        from tutor_assistant_web.modules.classroom.application import ClassroomService

        return ClassroomService(
            self.database,
            self.conference,
            self.settings.public_base_url,
            self.settings.app_secret_key,
            organization_id,
        )

    def materials_service(self, organization_id: str):
        from tutor_assistant_web.modules.materials.application import MaterialsService

        return MaterialsService(
            self.database,
            self.materials,
            self.classroom_service(organization_id),
            self.jobs,
            organization_id,
            self.document_engine,
            self.artifact_storage,
        )

    def audit_service(self, organization_id: str):
        from tutor_assistant_web.modules.audit.application import AuditService

        return AuditService(self.database, organization_id)

    def recording_ready_service(self):
        from tutor_assistant_web.modules.automation.application import RecordingReadyService

        return RecordingReadyService(self.database)

    def outbox_service(self):
        from tutor_assistant_web.modules.automation.application import OutboxService
        from tutor_assistant_web.modules.portal.application import PortalEventHandler

        return OutboxService(
            self.database,
            self.jobs,
            max_attempts=self.settings.outbox_max_attempts,
            retry_base_seconds=self.settings.workflow_retry_base_seconds,
            event_handlers=(PortalEventHandler(self.database),),
            dispatch_lease_seconds=self.settings.outbox_dispatch_lease_seconds,
        )

    def durable_jobs(self):
        from tutor_assistant_web.modules.automation.durability import DurableJobService

        return DurableJobService(
            self.database,
            lease_seconds=self.settings.job_lease_seconds,
            max_attempts=self.settings.workflow_max_attempts,
            retry_base_seconds=self.settings.workflow_retry_base_seconds,
            retry_max_seconds=self.settings.workflow_retry_max_seconds,
        )

    def publication_service(self, organization_id: str):
        from tutor_assistant_web.modules.portal.application import PublicationService

        return PublicationService(self.database, organization_id)

    def portal_service(self, principal):
        from tutor_assistant_web.modules.portal.application import PortalService

        return PortalService(self.database, self.artifact_storage, principal)

    def workflow_service(self, organization_id: str):
        from tutor_assistant_web.modules.automation.application import PostLessonWorkflowService

        classroom = self.classroom_service(organization_id)
        return PostLessonWorkflowService(
            self.database,
            classroom,
            self.materials_service(organization_id),
            self.transcription,
            organization_id,
        )


def build_conference_provider(settings: Settings) -> ConferenceProvider:
    if settings.bbb_demo_mode:
        return DemoConferenceProvider()
    client = BigBlueButtonClient(
        settings.bbb_base_url,
        settings.bbb_secret,
        settings.bbb_request_timeout,
        _circuit(settings, "bigbluebutton"),
    )
    return BigBlueButtonConferenceProvider(client)


def build_material_generator(settings: Settings) -> MaterialGenerator:
    if not settings.materials_webhook_url:
        return LocalTemplateMaterialGenerator()
    return WebhookMaterialGenerator(
        settings.materials_webhook_url,
        settings.materials_webhook_token,
        settings.materials_request_timeout,
        _circuit(settings, "materials-webhook"),
    )


def build_transcription_provider(settings: Settings) -> TranscriptionProvider:
    provider = settings.transcription_provider.lower()
    if provider == "auto":
        if settings.bbb_demo_mode:
            provider = "demo"
        else:
            provider = "webhook" if settings.transcription_webhook_url else "disabled"
    if provider == "demo":
        return DemoTranscriptionProvider()
    if provider == "webhook":
        return WebhookTranscriptionProvider(
            settings.transcription_webhook_url,
            settings.transcription_webhook_token,
            settings.transcription_request_timeout,
            _circuit(settings, "transcription-webhook"),
        )
    if provider == "faster-whisper":
        return FasterWhisperTranscriptionProvider(
            model=settings.transcription_model,
            language=settings.transcription_language,
            device=settings.transcription_device,
            compute_type=settings.transcription_compute_type,
            timeout=settings.transcription_request_timeout,
            max_download_mb=settings.transcription_max_download_mb,
        )
    return DisabledTranscriptionProvider()


def build_document_engine(settings: Settings) -> DocumentEngine:
    if settings.document_engine_provider.lower() == "latex-for-everyone":
        return LatexedDocumentEngine(
            settings.document_engine_url,
            settings.document_engine_token,
            settings.document_engine_timeout,
            max_pdf_mb=settings.document_max_pdf_mb,
            circuit_breaker=_circuit(settings, "document-engine"),
        )
    return LocalDocumentEngine()


def build_artifact_storage(settings: Settings) -> ArtifactStorage:
    scanner = (
        ClamAVScanner(
            settings.artifact_clamav_host,
            settings.artifact_clamav_port,
            settings.artifact_clamav_timeout,
        )
        if settings.artifact_clamav_enabled
        else None
    )
    common = {
        "max_bytes": settings.artifact_max_size_mb * 1024 * 1024,
        "allowed_mime_types": {
            item.strip().lower()
            for item in settings.artifact_allowed_mime_types.split(",")
            if item.strip()
        },
        "scanner": scanner,
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


def _circuit(settings: Settings, name: str) -> CircuitBreaker:
    key = (
        name,
        settings.circuit_breaker_failure_threshold,
        settings.circuit_breaker_recovery_seconds,
    )
    with _CIRCUITS_LOCK:
        if key not in _CIRCUITS:
            _CIRCUITS[key] = CircuitBreaker(
                name,
                failure_threshold=settings.circuit_breaker_failure_threshold,
                recovery_seconds=settings.circuit_breaker_recovery_seconds,
            )
        return _CIRCUITS[key]


def build_container(
    settings: Settings,
    database: Database,
    templates: Jinja2Templates,
    timezone: ZoneInfo,
) -> AppContainer:
    conference = build_conference_provider(settings)
    materials = build_material_generator(settings)
    transcription = build_transcription_provider(settings)
    document_engine = build_document_engine(settings)
    artifact_storage = build_artifact_storage(settings)
    identity = IdentityService(database)
    jobs: JobDispatcher
    if settings.task_eager:
        jobs = InlineJobDispatcher(
            database,
            settings,
            conference,
            materials,
            transcription,
            document_engine,
            artifact_storage,
        )
    else:
        jobs = CeleryJobDispatcher()
    return AppContainer(
        settings=settings,
        database=database,
        timezone=timezone,
        templates=templates,
        web=WebSupport(settings, templates, timezone, identity),
        identity=identity,
        conference=conference,
        materials=materials,
        transcription=transcription,
        jobs=jobs,
        document_engine=document_engine,
        artifact_storage=artifact_storage,
    )
