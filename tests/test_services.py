from datetime import UTC, datetime, timedelta

from tutor_assistant_web.config import Settings
from tutor_assistant_web.models import Lesson, Student
from tutor_assistant_web.services import evidence_payload, request_materials


def test_fallback_materials_are_marked_as_drafts():
    student = Student(id="student", full_name="Иван", grade="8 класс", subject="Математика")
    lesson = Lesson(
        id="lesson",
        student=student,
        student_id=student.id,
        title="Алгебра",
        topic="Квадратные уравнения",
        starts_at=datetime.now(UTC),
        ends_at=datetime.now(UTC) + timedelta(hours=1),
        bbb_meeting_id="bbb-lesson",
        attendee_password="attendee",
        moderator_password="moderator",
        tutor_notes="Повторили дискриминант",
    )
    lesson.recordings = []

    payload = evidence_payload(lesson)
    materials = request_materials(payload, Settings(seed_demo_data=False))

    assert {item["kind"] for item in materials} == {"summary", "homework"}
    assert "Квадратные уравнения" in materials[0]["content"]
    assert "черновик" in materials[1]["content"].lower()
