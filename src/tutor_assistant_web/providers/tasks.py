from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tutor_assistant_web.config import Settings
    from tutor_assistant_web.db import Database
    from tutor_assistant_web.shared.contracts import ConferenceProvider, MaterialGenerator


class InlineJobDispatcher:
    name = "inline"

    def __init__(
        self,
        database: Database,
        settings: Settings,
        conference: ConferenceProvider,
        materials: MaterialGenerator,
    ) -> None:
        self.database = database
        self.settings = settings
        self.conference = conference
        self.materials = materials

    def enqueue_lesson_processing(self, job_id: str) -> None:
        from tutor_assistant_web.modules.classroom.application import ClassroomService
        from tutor_assistant_web.modules.materials.application import MaterialsService

        classroom = ClassroomService(
            self.database,
            self.conference,
            self.settings.public_base_url,
            self.settings.app_secret_key,
        )
        MaterialsService(self.database, self.materials, classroom).process(job_id)


class CeleryJobDispatcher:
    name = "celery"

    def enqueue_lesson_processing(self, job_id: str) -> None:
        from tutor_assistant_web.worker import process_lesson_task

        process_lesson_task.delay(job_id)
