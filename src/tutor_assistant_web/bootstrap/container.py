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
from tutor_assistant_web.providers.materials import (
    LocalTemplateMaterialGenerator,
    WebhookMaterialGenerator,
)
from tutor_assistant_web.providers.tasks import CeleryJobDispatcher, InlineJobDispatcher
from tutor_assistant_web.shared.contracts import (
    ConferenceProvider,
    JobDispatcher,
    MaterialGenerator,
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
    jobs: JobDispatcher

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
        )

    def audit_service(self, organization_id: str):
        from tutor_assistant_web.modules.audit.application import AuditService

        return AuditService(self.database, organization_id)


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


def build_container(
    settings: Settings,
    database: Database,
    templates: Jinja2Templates,
    timezone: ZoneInfo,
) -> AppContainer:
    conference = build_conference_provider(settings)
    materials = build_material_generator(settings)
    identity = IdentityService(database)
    jobs: JobDispatcher
    if settings.task_eager:
        jobs = InlineJobDispatcher(database, settings, conference, materials)
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
        jobs=jobs,
    )
