from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.shared.security import make_meeting_credentials


def seed_data(session, organization_id: str) -> None:
    if session.scalar(
        select(Student.id).where(Student.organization_id == organization_id).limit(1)
    ):
        return
    student = Student(
        organization_id=organization_id,
        full_name="Анна Смирнова",
        grade="9 класс",
        subject="Математика",
        goal="Подготовка к ОГЭ, уверенная работа с геометрией",
        guardian_name="Елена Смирнова",
        guardian_phone="+7 900 000-00-00",
        hourly_rate=1800,
        notes="Демонстрационная запись — её можно удалить после знакомства с пилотом.",
    )
    session.add(student)
    session.flush()
    meeting_id, attendee, moderator = make_meeting_credentials()
    starts = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    session.add(
        Lesson(
            organization_id=organization_id,
            student_id=student.id,
            title="Геометрия: подобие треугольников",
            topic="Признаки подобия треугольников",
            starts_at=starts + timedelta(hours=2),
            ends_at=starts + timedelta(hours=3),
            price_snapshot=student.hourly_rate,
            bbb_meeting_id=meeting_id,
            attendee_password=attendee,
            moderator_password=moderator,
        )
    )
    session.commit()
