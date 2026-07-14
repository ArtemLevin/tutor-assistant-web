from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.classroom.models import RecordingAsset
from tutor_assistant_web.modules.scheduling.models import Lesson, LessonStatus
from tutor_assistant_web.shared.contracts import (
    ConferenceProvider,
    CreateConference,
    JoinConference,
)
from tutor_assistant_web.shared.errors import GoneError, NotFoundError
from tutor_assistant_web.shared.security import join_token, verify_join_token


class ClassroomService:
    def __init__(
        self,
        database: Database,
        conference: ConferenceProvider,
        public_base_url: str,
        secret: str,
        organization_id: str | None,
    ) -> None:
        self.database = database
        self.conference = conference
        self.public_base_url = public_base_url.rstrip("/")
        self.secret = secret
        self.organization_id = organization_id

    def _tenant_filter(self):
        if self.organization_id is None:
            raise NotFoundError("Занятие не найдено")
        return Lesson.organization_id == self.organization_id

    def detail(self, lesson_id: str) -> Lesson:
        with self.database.sessions() as session:
            lesson = session.scalar(
                select(Lesson)
                .options(
                    selectinload(Lesson.student),
                    selectinload(Lesson.recordings),
                    selectinload(Lesson.jobs),
                    selectinload(Lesson.artifacts),
                    selectinload(Lesson.transcript),
                )
                .where(Lesson.id == lesson_id, self._tenant_filter())
            )
            if lesson is None:
                raise NotFoundError("Занятие не найдено")
            lesson.jobs.sort(key=lambda item: item.created_at, reverse=True)
            lesson.artifacts.sort(key=lambda item: item.created_at, reverse=True)
            return lesson

    def student_link(self, lesson: Lesson) -> str:
        token = join_token(lesson.id, lesson.student_id, self.secret)
        return f"{self.public_base_url}/join/{lesson.id}/{token}"

    def update_notes(self, lesson_id: str, topic: str, notes: str) -> None:
        with self.database.sessions() as session:
            lesson = session.scalar(
                select(Lesson).where(Lesson.id == lesson_id, self._tenant_filter())
            )
            if lesson is None:
                raise NotFoundError("Занятие не найдено")
            lesson.topic = topic[:300]
            lesson.tutor_notes = notes[:20000]
            session.commit()

    def join_tutor(self, lesson_id: str) -> str:
        with self.database.sessions() as session:
            lesson = session.scalar(
                select(Lesson)
                .options(selectinload(Lesson.student))
                .where(Lesson.id == lesson_id, self._tenant_filter())
            )
            if lesson is None:
                raise NotFoundError("Занятие не найдено")
            self._prepare(lesson)
            lesson.status = LessonStatus.live.value
            session.commit()
            return self.conference.join_url(
                JoinConference(
                    meeting_id=lesson.bbb_meeting_id,
                    full_name="Преподаватель",
                    password=lesson.moderator_password,
                    user_id="tutor",
                    role="MODERATOR",
                    logout_url=f"{self.public_base_url}/lessons/{lesson.id}",
                    demo_url=f"/demo-room/{lesson.id}?role=tutor",
                )
            )

    def join_student(self, lesson_id: str, token: str) -> str:
        with self.database.sessions() as session:
            lesson = session.scalar(
                select(Lesson).options(selectinload(Lesson.student)).where(Lesson.id == lesson_id)
            )
            if lesson is None or not verify_join_token(
                lesson.id, lesson.student_id, token, self.secret
            ):
                raise NotFoundError("Ссылка недействительна")
            if lesson.status == LessonStatus.cancelled.value:
                raise GoneError("Занятие отменено")
            self._prepare(lesson)
            return self.conference.join_url(
                JoinConference(
                    meeting_id=lesson.bbb_meeting_id,
                    full_name=lesson.student.full_name,
                    password=lesson.attendee_password,
                    user_id=f"student-{lesson.student_id}",
                    role="VIEWER",
                    logout_url=f"{self.public_base_url}/lesson-finished",
                    demo_url=f"/demo-room/{lesson.id}?role=student",
                )
            )

    def end(self, lesson_id: str) -> None:
        with self.database.sessions() as session:
            lesson = session.scalar(
                select(Lesson).where(Lesson.id == lesson_id, self._tenant_filter())
            )
            if lesson is None:
                raise NotFoundError("Занятие не найдено")
            self.conference.end_room(lesson.bbb_meeting_id)
            lesson.status = LessonStatus.completed.value
            session.commit()

    def demo_room(self, lesson_id: str) -> Lesson:
        if not self.conference.is_demo:
            raise NotFoundError("Demo-комната отключена")
        with self.database.sessions() as session:
            lesson = session.scalar(
                select(Lesson)
                .options(selectinload(Lesson.student))
                .where(Lesson.id == lesson_id, self._tenant_filter())
            )
            if lesson is None:
                raise NotFoundError("Занятие не найдено")
            return lesson

    def sync_recordings(self, lesson_id: str) -> int:
        with self.database.sessions() as session:
            lesson = session.scalar(
                select(Lesson).where(Lesson.id == lesson_id, self._tenant_filter())
            )
            if lesson is None:
                raise NotFoundError("Занятие не найдено")
            found = self.conference.recordings(lesson.bbb_meeting_id)
            for item in found:
                current = session.scalar(
                    select(RecordingAsset).where(
                        RecordingAsset.record_id == item.record_id,
                        RecordingAsset.organization_id == self.organization_id,
                    )
                )
                if current is None:
                    current = RecordingAsset(
                        organization_id=self.organization_id,
                        lesson_id=lesson.id,
                        record_id=item.record_id,
                    )
                    session.add(current)
                current.state = item.state
                current.playback_url = item.playback_url
                current.raw_metadata = item.metadata
                current.synced_at = datetime.now(UTC)
            session.commit()
            return len(found)

    def _prepare(self, lesson: Lesson) -> None:
        self.conference.create_room(
            CreateConference(
                meeting_id=lesson.bbb_meeting_id,
                name=lesson.title,
                attendee_password=lesson.attendee_password,
                moderator_password=lesson.moderator_password,
                record=lesson.record_enabled,
                recording_ready_url=(
                    f"{self.public_base_url}/webhooks/bigbluebutton/recording-ready"
                    if lesson.record_enabled and not self.conference.is_demo
                    else ""
                ),
            )
        )
