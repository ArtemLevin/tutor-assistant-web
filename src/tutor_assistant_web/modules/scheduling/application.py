from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.scheduling.models import Lesson, LessonStatus
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.shared.errors import ConflictError, NotFoundError, ValidationError
from tutor_assistant_web.shared.security import make_meeting_credentials


@dataclass(frozen=True)
class CreateLesson:
    student_id: str
    title: str
    topic: str
    starts_at: datetime
    ends_at: datetime
    record_enabled: bool


@dataclass(frozen=True)
class WeekSchedule:
    monday: date
    days: list[date]
    lessons_by_day: dict[date, list[Lesson]]


class SchedulingService:
    def __init__(self, database: Database, timezone: ZoneInfo, organization_id: str) -> None:
        self.database = database
        self.timezone = timezone
        self.organization_id = organization_id

    def week(self, selected: date) -> WeekSchedule:
        monday = selected - timedelta(days=selected.weekday())
        next_monday = monday + timedelta(days=7)
        start_utc = datetime.combine(monday, time.min, self.timezone).astimezone(UTC)
        end_utc = datetime.combine(next_monday, time.min, self.timezone).astimezone(UTC)
        with self.database.sessions() as session:
            lessons = list(
                session.scalars(
                    select(Lesson)
                    .options(selectinload(Lesson.student))
                    .where(
                        Lesson.organization_id == self.organization_id,
                        Lesson.starts_at >= start_utc,
                        Lesson.starts_at < end_utc,
                    )
                    .order_by(Lesson.starts_at)
                )
            )
        days = [monday + timedelta(days=index) for index in range(7)]
        lessons_by_day = {
            day: [item for item in lessons if self._local_date(item.starts_at) == day]
            for day in days
        }
        return WeekSchedule(monday=monday, days=days, lessons_by_day=lessons_by_day)

    def create(self, command: CreateLesson) -> Lesson:
        if command.ends_at <= command.starts_at:
            raise ValidationError("Окончание должно быть позже начала")
        with self.database.sessions() as session:
            student = session.scalar(
                select(Student).where(
                    Student.id == command.student_id,
                    Student.organization_id == self.organization_id,
                )
            )
            if student is None or not student.active:
                raise NotFoundError("Ученик не найден")
            overlap = session.scalar(
                select(Lesson.id).where(
                    Lesson.status != LessonStatus.cancelled.value,
                    Lesson.organization_id == self.organization_id,
                    Lesson.starts_at < command.ends_at,
                    Lesson.ends_at > command.starts_at,
                )
            )
            if overlap:
                raise ConflictError("В это время уже запланировано занятие")
            meeting_id, attendee, moderator = make_meeting_credentials()
            lesson = Lesson(
                organization_id=self.organization_id,
                student_id=student.id,
                title=command.title[:200] or "Занятие",
                topic=command.topic[:300],
                starts_at=command.starts_at,
                ends_at=command.ends_at,
                price_snapshot=student.hourly_rate,
                bbb_meeting_id=meeting_id,
                attendee_password=attendee,
                moderator_password=moderator,
                record_enabled=command.record_enabled,
            )
            session.add(lesson)
            session.commit()
            return lesson

    def _local_date(self, value: datetime) -> date:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(self.timezone).date()
