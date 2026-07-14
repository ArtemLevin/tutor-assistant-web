from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tutor_assistant_web.config import Settings
    from tutor_assistant_web.db import Database
    from tutor_assistant_web.shared.contracts import (
        ConferenceProvider,
        MaterialGenerator,
        TranscriptionProvider,
    )


class InlineJobDispatcher:
    name = "inline"

    def __init__(
        self,
        database: Database,
        settings: Settings,
        conference: ConferenceProvider,
        materials: MaterialGenerator,
        transcription: TranscriptionProvider,
    ) -> None:
        self.database = database
        self.settings = settings
        self.conference = conference
        self.materials = materials
        self.transcription = transcription

    def enqueue_lesson_processing(self, job_id: str) -> None:
        from sqlalchemy import select

        from tutor_assistant_web.modules.automation.application import PostLessonWorkflowService
        from tutor_assistant_web.modules.classroom.application import ClassroomService
        from tutor_assistant_web.modules.materials.application import MaterialsService
        from tutor_assistant_web.modules.materials.models import ProcessingJob

        with self.database.sessions() as session:
            row = session.execute(
                select(ProcessingJob.organization_id, ProcessingJob.kind).where(
                    ProcessingJob.id == job_id
                )
            ).one_or_none()
        if row is None:
            return
        organization_id, kind = row

        classroom = ClassroomService(
            self.database,
            self.conference,
            self.settings.public_base_url,
            self.settings.app_secret_key,
            organization_id,
        )
        materials_service = MaterialsService(
            self.database, self.materials, classroom, organization_id=organization_id
        )
        if kind == "post_lesson":
            PostLessonWorkflowService(
                self.database,
                classroom,
                materials_service,
                self.transcription,
                organization_id,
            ).process(job_id)
        else:
            materials_service.process(job_id)


class CeleryJobDispatcher:
    name = "celery"

    def enqueue_lesson_processing(self, job_id: str) -> None:
        from tutor_assistant_web.worker import process_lesson_task

        process_lesson_task.delay(job_id)
