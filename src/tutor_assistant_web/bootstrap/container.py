from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from tutor_assistant_web.bbb import BigBlueButtonClient
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.providers.conference import (
    BigBlueButtonConferenceProvider,
    DemoConferenceProvider,
)
from tutor_assistant_web.providers.documents import (
    LatexedDocumentEngine,
    LocalArtifactStorage,
    LocalDocumentEngine,
)
from tutor_assistant_web.providers.materials import (
    LocalTemplateMaterialGenerator,
    WebhookMaterialGenerator,
)
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

        return OutboxService(
            self.database,
            self.jobs,
            max_attempts=self.settings.outbox_max_attempts,
            retry_base_seconds=self.settings.workflow_retry_base_seconds,
        )

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
    )
    return BigBlueButtonConferenceProvider(client)


def build_material_generator(settings: Settings) -> MaterialGenerator:
    if not settings.materials_webhook_url:
        return LocalTemplateMaterialGenerator()
    return WebhookMaterialGenerator(
        settings.materials_webhook_url,
        settings.materials_webhook_token,
        settings.materials_request_timeout,
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
        )
    return LocalDocumentEngine()


def build_artifact_storage(settings: Settings) -> ArtifactStorage:
    return LocalArtifactStorage(settings.artifact_storage_root)


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
