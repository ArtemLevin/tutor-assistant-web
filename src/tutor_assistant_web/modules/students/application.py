from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.shared.errors import NotFoundError, ValidationError


@dataclass(frozen=True)
class StudentData:
    full_name: str
    grade: str = ""
    subject: str = "Математика"
    goal: str = ""
    guardian_name: str = ""
    guardian_phone: str = ""
    email: str = ""
    social_links: str = ""
    hourly_rate: Decimal = Decimal("0")
    notes: str = ""


class StudentService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_active(self, query: str = "") -> list[Student]:
        statement = select(Student).where(Student.active.is_(True)).order_by(Student.full_name)
        if query.strip():
            statement = statement.where(Student.full_name.ilike(f"%{query.strip()}%"))
        with self.database.sessions() as session:
            return list(session.scalars(statement))

    def get(self, student_id: str, *, with_lessons: bool = False) -> Student:
        statement = select(Student).where(Student.id == student_id)
        if with_lessons:
            statement = statement.options(selectinload(Student.lessons))
        with self.database.sessions() as session:
            student = session.scalar(statement)
            if student is None:
                raise NotFoundError("Ученик не найден")
            if with_lessons:
                student.lessons.sort(key=lambda item: item.starts_at, reverse=True)
            return student

    def create(self, data: StudentData) -> Student:
        if len(data.full_name.strip()) < 2:
            raise ValidationError("Укажите имя ученика")
        student = Student(**self._values(data))
        with self.database.sessions() as session:
            session.add(student)
            session.commit()
        return student

    def update(self, student_id: str, data: StudentData) -> Student:
        if len(data.full_name.strip()) < 2:
            raise ValidationError("Укажите имя ученика")
        with self.database.sessions() as session:
            student = session.get(Student, student_id)
            if student is None:
                raise NotFoundError("Ученик не найден")
            for field, value in self._values(data).items():
                setattr(student, field, value)
            session.commit()
            return student

    @staticmethod
    def _values(data: StudentData) -> dict[str, object]:
        return {
            "full_name": data.full_name.strip()[:160],
            "grade": data.grade[:32],
            "subject": data.subject[:120],
            "goal": data.goal[:4000],
            "guardian_name": data.guardian_name[:160],
            "guardian_phone": data.guardian_phone[:80],
            "email": data.email[:254],
            "social_links": data.social_links[:2000],
            "hourly_rate": max(data.hourly_rate, Decimal("0")),
            "notes": data.notes[:8000],
        }
